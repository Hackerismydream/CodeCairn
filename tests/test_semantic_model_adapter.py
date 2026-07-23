from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import ClassVar, cast

import httpx
import pytest

import codecairn.evaluation.semantic as semantic_module
from codecairn.evaluation.model import ModelResponse
from codecairn.evaluation.providers import OpenAICompatibleTextModel
from codecairn.evaluation.semantic import StructuredModelClauseProjectionAdapter
from codecairn.memory.semantic import ProjectionFact, ProjectionSource


def test_structured_semantic_adapter_returns_only_grounded_clause_drafts() -> None:
    model = _Model(
        response={
            "clauses": [
                {
                    "text": "Caroline adopted a beagle named Poppy.",
                    "source_fact_ids": ["fact-1"],
                },
                {
                    "text": "Caroline later completed a charity race.",
                    "source_fact_ids": ["fact-2"],
                },
            ]
        }
    )
    adapter = StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1")

    drafts = adapter.propose(_source())

    assert [draft.source_fact_ids for draft in drafts] == [("fact-1",), ("fact-2",)]
    assert model.calls[0]["response_format"] == "json"
    request = json.loads(str(model.calls[0]["user"]))
    assert request["facts"][0] == {
        "actor": "Caroline",
        "fact_id": "fact-1",
        "occurred_at": "2023-05-08T13:56:00+00:00",
        "role": "participant",
        "text": "I adopted a beagle named Poppy.",
    }
    assert adapter.identity.model_id == "test-model"
    assert adapter.usage.call_count == 1
    assert adapter.usage.input_tokens == 120
    assert adapter.usage.output_tokens == 30
    assert adapter.usage.cost_cny == pytest.approx(0.001)


def test_structured_semantic_adapter_allows_no_durable_clauses_for_small_talk() -> None:
    model = _Model(response={"clauses": []})
    adapter = StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1")

    drafts = adapter.propose(_source())

    assert drafts == ()
    assert "clauses may be empty" in str(model.calls[0]["system"])
    assert "cover every input" in str(model.calls[0]["system"])


def test_structured_semantic_adapter_rejects_unknown_response_fields() -> None:
    model = _Model(
        response={
            "clauses": [
                {
                    "text": "Grounded text.",
                    "source_fact_ids": ["fact-1"],
                    "timestamp": "forged",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="schema"):
        StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1").propose(_source())


def test_structured_semantic_adapter_rejects_a_response_over_its_limit() -> None:
    model = _Model(response={"clauses": []}, raw_text="x" * 101)
    adapter = StructuredModelClauseProjectionAdapter(
        model=model,
        revision="prompt-v1",
        max_response_chars=100,
    )

    with pytest.raises(ValueError, match="response exceeds"):
        adapter.propose(_source())


def test_structured_semantic_adapter_rejects_provider_model_route_drift() -> None:
    model = _Model(
        response={"clauses": []},
        response_model="routed-model-v2",
    )

    adapter = StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1")

    with pytest.raises(ValueError, match="model identity changed"):
        adapter.propose(_source())

    assert adapter.usage.call_count == 1


def test_structured_semantic_adapter_identity_hashes_its_canonical_output_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_prompt = semantic_module._SYSTEM_PROMPT
    first_model = _Model(response={"clauses": []})
    first_model.public_config = {"z": [2, 1], "a": {"enabled": True}}
    first = StructuredModelClauseProjectionAdapter(
        model=first_model,
        revision="prompt-v1",
        max_facts_per_request=24,
    )
    reordered_model = _Model(response={"clauses": []})
    reordered_model.public_config = {"a": {"enabled": True}, "z": [2, 1]}
    reordered = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v1",
        max_facts_per_request=24,
    )

    assert first.identity.config_sha256 == reordered.identity.config_sha256
    assert first.public_config["config_sha256"] == first.identity.config_sha256

    changed_limit = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v1",
        max_facts_per_request=25,
    )
    assert changed_limit.identity.config_sha256 != first.identity.config_sha256

    changed_request_limit = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v1",
        max_facts_per_request=24,
        max_request_chars=48_001,
    )
    changed_response_limit = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v1",
        max_facts_per_request=24,
        max_response_chars=96_001,
    )
    changed_revision = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v2",
        max_facts_per_request=24,
    )
    changed_model_config_model = _Model(response={"clauses": []})
    changed_model_config_model.public_config = {"a": {"enabled": False}, "z": [2, 1]}
    changed_model_config = StructuredModelClauseProjectionAdapter(
        model=changed_model_config_model,
        revision="prompt-v1",
        max_facts_per_request=24,
    )
    changed_model_id_model = _Model(response={"clauses": []})
    changed_model_id_model.model_id = "test-model-v2"
    changed_model_id = StructuredModelClauseProjectionAdapter(
        model=changed_model_id_model,
        revision="prompt-v1",
        max_facts_per_request=24,
    )
    assert all(
        adapter.identity.config_sha256 != first.identity.config_sha256
        for adapter in (
            changed_request_limit,
            changed_response_limit,
            changed_revision,
            changed_model_config,
            changed_model_id,
        )
    )

    monkeypatch.setattr(semantic_module, "_SYSTEM_PROMPT", "Changed grounded prompt")
    changed_prompt = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v1",
        max_facts_per_request=24,
    )
    assert changed_prompt.identity.config_sha256 != first.identity.config_sha256

    monkeypatch.setattr(semantic_module, "_SYSTEM_PROMPT", original_prompt)
    monkeypatch.setattr(semantic_module, "_WINDOW_CONTRACT", "semantic-window-v2")
    changed_window_contract = StructuredModelClauseProjectionAdapter(
        model=reordered_model,
        revision="prompt-v1",
        max_facts_per_request=24,
    )
    assert changed_window_contract.identity.config_sha256 != first.identity.config_sha256


def test_semantic_projection_usage_preserves_unknown_observations() -> None:
    model = _Model(
        response={"clauses": []},
        input_tokens=None,
        output_tokens=None,
        cost_cny=None,
    )
    adapter = StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1")

    adapter.propose(_source())

    assert adapter.usage.call_count == 1
    assert adapter.usage.input_tokens is None
    assert adapter.usage.known_input_tokens_count == 0
    assert adapter.usage.output_tokens is None
    assert adapter.usage.known_output_tokens_count == 0
    assert adapter.usage.cost_cny is None
    assert adapter.usage.known_cost_cny_count == 0


def test_semantic_projection_usage_counts_a_provider_failure_as_unknown() -> None:
    adapter = StructuredModelClauseProjectionAdapter(
        model=_FailingModel(),
        revision="prompt-v1",
    )

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        adapter.propose(_source())

    assert adapter.usage.call_count == 1
    assert adapter.usage.input_tokens is None
    assert adapter.usage.known_input_tokens_count == 0
    assert adapter.usage.cost_cny is None
    assert adapter.usage.known_cost_cny_count == 0


def test_semantic_projection_stops_after_an_unknown_cost_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"clauses":[]}'},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }

    def flaky_post(*args: object, **kwargs: object) -> Response:
        nonlocal attempts
        attempts += 1
        raise httpx.RemoteProtocolError("connection closed after request")

    monkeypatch.setattr(httpx, "post", flaky_post)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="secret",
        model="test-model",
        max_attempts=3,
        retry_backoff_seconds=0,
    )
    adapter = StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1")

    with pytest.raises(httpx.RemoteProtocolError, match="connection closed after request"):
        adapter.propose(_source())

    assert attempts == 1
    assert adapter.usage.call_count == 1
    assert adapter.usage.known_input_tokens_count == 0


def test_semantic_projection_usage_keeps_known_partial_totals_with_observation_counts() -> None:
    model = _Model(response={"clauses": []})
    adapter = StructuredModelClauseProjectionAdapter(model=model, revision="prompt-v1")

    adapter.propose(_source())
    model.input_tokens = None
    model.cost_cny = None
    adapter.propose(_source())

    assert adapter.usage.call_count == 2
    assert adapter.usage.input_tokens == 120
    assert adapter.usage.known_input_tokens_count == 1
    assert adapter.usage.cost_cny == pytest.approx(0.001)
    assert adapter.usage.known_cost_cny_count == 1


def test_semantic_projection_usage_is_thread_safe() -> None:
    barrier = Barrier(2)
    adapter = StructuredModelClauseProjectionAdapter(
        model=_InterleavingUsageModel(barrier),
        revision="prompt-v1",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(adapter.propose, _source()) for _ in range(2)]
        for future in futures:
            future.result()

    assert adapter.usage.call_count == 2
    assert adapter.usage.input_tokens == 2
    assert adapter.usage.known_input_tokens_count == 2


class _Model:
    model_id = "test-model"
    public_config: ClassVar[dict[str, object]] = {"adapter": "fixture"}

    def __init__(
        self,
        *,
        response: object,
        raw_text: str | None = None,
        response_model: str | None = None,
        input_tokens: int | None = 120,
        output_tokens: int | None = 30,
        cost_cny: float | None = 0.001,
    ) -> None:
        self._response = response
        self._raw_text = raw_text
        self._response_model = response_model or self.model_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_cny = cost_cny
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "seed": seed,
                "response_format": response_format,
            }
        )
        return ModelResponse(
            text=(
                self._raw_text
                if self._raw_text is not None
                else json.dumps(self._response, ensure_ascii=False)
            ),
            model=self._response_model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_cny=self.cost_cny,
        )


class _InterleavingUsageModel:
    model_id = "test-model"
    public_config: ClassVar[dict[str, object]] = {"adapter": "interleaving-fixture"}

    def __init__(self, barrier: Barrier) -> None:
        self._barrier = barrier

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        del system, user, seed, response_format
        return cast(ModelResponse, _InterleavingUsageResponse(self._barrier))


class _FailingModel:
    model_id = "test-model"
    public_config: ClassVar[dict[str, object]] = {"adapter": "failing-fixture"}

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        del system, user, seed, response_format
        raise RuntimeError("simulated provider failure")


class _InterleavingUsageResponse:
    text = '{"clauses":[]}'
    model = "test-model"
    output_tokens = None
    cached_input_tokens = None
    uncached_input_tokens = None
    reasoning_tokens = None
    cost_usd = None
    cost_cny = None

    def __init__(self, barrier: Barrier) -> None:
        self._barrier = barrier

    @property
    def input_tokens(self) -> int:
        self._barrier.wait(timeout=2)
        return 1


def _source() -> ProjectionSource:
    return ProjectionSource(
        episode_id="episode-1",
        source_digest="a" * 64,
        facts=(
            ProjectionFact(
                fact_id="fact-1",
                text="I adopted a beagle named Poppy.",
                actor="Caroline",
                role="participant",
                occurred_at="2023-05-08T13:56:00+00:00",
                source_order=1,
            ),
            ProjectionFact(
                fact_id="fact-2",
                text="I completed the charity race.",
                actor="Caroline",
                role="participant",
                occurred_at="2023-05-09T13:56:00+00:00",
                source_order=2,
            ),
        ),
    )
