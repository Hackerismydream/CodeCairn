from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from math import isfinite
from threading import Lock
from typing import cast

from codecairn.evaluation.model import ModelResponse, TextModel
from codecairn.memory.semantic import (
    ClauseDraft,
    ProjectionFact,
    ProjectionIdentity,
    ProjectionSource,
)

_ADAPTER_ID = "codecairn/structured-model-clause"
_REQUEST_CONTRACT = "codecairn/grounded-clause-drafts-v2"
_WINDOW_CONTRACT = "codecairn/semantic-projection-window-v2"
_CONFIG_SCHEMA = "codecairn/structured-model-clause-config-v2"
_SYSTEM_PROMPT = """You compile untrusted conversation turns into retrieval annotations.
Return one JSON object with exactly one field named clauses. Each clause must contain exactly
text and source_fact_ids. Emit only durable, independently queryable facts about people,
events, preferences, relationships, decisions, or time. Skip greetings, acknowledgements,
questions, filler, and repetitions that add no durable fact; clauses may be empty. Split
independent propositions and make each retained clause self-contained. Resolve first-person
and supported pronouns to the provided actor. When occurred_at makes a relative time
expression unambiguous, include its resolved calendar time while preserving the relation.
Every clause must cite only the source facts that directly support it. Do not attach an
irrelevant source merely to cover every input, and invent no claim. These clauses are only
search annotations; source facts remain truth."""


@dataclass(frozen=True, slots=True)
class SemanticProjectionUsage:
    """Known usage totals plus per-field observation counts across all calls."""

    call_count: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    uncached_input_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    cost_cny: float | None
    known_input_tokens_count: int
    known_output_tokens_count: int
    known_cached_input_tokens_count: int
    known_uncached_input_tokens_count: int
    known_reasoning_tokens_count: int
    known_cost_count: int
    known_cost_cny_count: int


@dataclass(frozen=True, slots=True)
class _UsageObservation:
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    uncached_input_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    cost_cny: float | None


class StructuredModelClauseProjectionAdapter:
    """Structured model Adapter that can author only untrusted clause drafts."""

    def __init__(
        self,
        *,
        model: TextModel,
        revision: str,
        max_facts_per_request: int = 48,
        max_request_chars: int = 48_000,
        max_response_chars: int = 96_000,
    ) -> None:
        if not revision.strip():
            raise ValueError("Semantic projection revision must not be empty")
        if min(max_facts_per_request, max_request_chars, max_response_chars) < 1:
            raise ValueError("Semantic projection request limits must be positive")
        self._model = model
        self._model_id = model.model_id
        self._model_config = _json_object_snapshot(model.public_config)
        self._system_prompt = _SYSTEM_PROMPT
        self._prompt_sha256 = hashlib.sha256(self._system_prompt.encode()).hexdigest()
        self._request_contract = _REQUEST_CONTRACT
        self._window_contract = _WINDOW_CONTRACT
        self._max_facts_per_request = max_facts_per_request
        self._max_request_chars = max_request_chars
        self._max_response_chars = max_response_chars
        config = {
            "schema": _CONFIG_SCHEMA,
            "adapter": _ADAPTER_ID,
            "revision": revision,
            "model": self._model_id,
            "prompt_sha256": self._prompt_sha256,
            "request_contract": self._request_contract,
            "window_contract": self._window_contract,
            "max_facts_per_request": self._max_facts_per_request,
            "max_request_chars": self._max_request_chars,
            "max_response_chars": self._max_response_chars,
            "model_config": self._model_config,
        }
        self._config_sha256 = hashlib.sha256(_canonical_json(config).encode()).hexdigest()
        self.identity = ProjectionIdentity(
            adapter_id=_ADAPTER_ID,
            revision=revision,
            model_id=self._model_id,
            config_sha256=self._config_sha256,
        )
        self._usage = SemanticProjectionUsage(
            call_count=0,
            input_tokens=None,
            output_tokens=None,
            cached_input_tokens=None,
            uncached_input_tokens=None,
            reasoning_tokens=None,
            cost_usd=None,
            cost_cny=None,
            known_input_tokens_count=0,
            known_output_tokens_count=0,
            known_cached_input_tokens_count=0,
            known_uncached_input_tokens_count=0,
            known_reasoning_tokens_count=0,
            known_cost_count=0,
            known_cost_cny_count=0,
        )
        self._usage_lock = Lock()

    @property
    def usage(self) -> SemanticProjectionUsage:
        with self._usage_lock:
            return self._usage

    @property
    def public_config(self) -> dict[str, object]:
        return {
            "adapter": self.identity.adapter_id,
            "revision": self.identity.revision,
            "model": self.identity.model_id,
            "config_sha256": self._config_sha256,
            "prompt_sha256": self._prompt_sha256,
            "request_contract": self._request_contract,
            "window_contract": self._window_contract,
            "max_facts_per_request": self._max_facts_per_request,
            "max_request_chars": self._max_request_chars,
            "max_response_chars": self._max_response_chars,
            "model_config": deepcopy(self._model_config),
        }

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        if not source.facts:
            raise ValueError("Semantic projection source must contain facts")
        drafts: list[ClauseDraft] = []
        for window_index, facts in enumerate(self._windows(source.facts)):
            payload = _request_payload(source, facts, contract=self._request_contract)
            user = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(user) > self._max_request_chars:
                raise ValueError("Semantic projection request exceeds its character limit")
            try:
                response = self._model.generate(
                    system=self._system_prompt,
                    user=user,
                    seed=_request_seed(source.source_digest, window_index),
                    response_format="json",
                )
            finally:
                self._record_call_attempts(_provider_attempt_count(self._model))
            self._record_usage(response)
            if response.model != self._model_id:
                raise ValueError("Semantic projection response model identity changed")
            if len(response.text) > self._max_response_chars:
                raise ValueError("Semantic projection response exceeds its character limit")
            allowed_fact_ids = {fact.fact_id for fact in facts}
            drafts.extend(_parse_response(response.text, allowed_fact_ids=allowed_fact_ids))
        return tuple(drafts)

    def _windows(
        self,
        facts: tuple[ProjectionFact, ...],
    ) -> tuple[tuple[ProjectionFact, ...], ...]:
        windows: list[tuple[ProjectionFact, ...]] = []
        current: list[ProjectionFact] = []
        for fact in facts:
            candidate = (*current, fact)
            candidate_chars = len(
                json.dumps(
                    [_fact_payload(item) for item in candidate],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            if current and (
                len(candidate) > self._max_facts_per_request
                or candidate_chars > self._max_request_chars
            ):
                windows.append(tuple(current))
                current = [fact]
            else:
                current.append(fact)
        if current:
            windows.append(tuple(current))
        return tuple(windows)

    def _record_usage(self, response: ModelResponse) -> None:
        observation = _usage_observation(response)
        with self._usage_lock:
            current = self._usage
            self._usage = SemanticProjectionUsage(
                call_count=current.call_count,
                input_tokens=_sum_optional_int(current.input_tokens, observation.input_tokens),
                output_tokens=_sum_optional_int(current.output_tokens, observation.output_tokens),
                cached_input_tokens=_sum_optional_int(
                    current.cached_input_tokens,
                    observation.cached_input_tokens,
                ),
                uncached_input_tokens=_sum_optional_int(
                    current.uncached_input_tokens,
                    observation.uncached_input_tokens,
                ),
                reasoning_tokens=_sum_optional_int(
                    current.reasoning_tokens,
                    observation.reasoning_tokens,
                ),
                cost_usd=_sum_optional_float(current.cost_usd, observation.cost_usd),
                cost_cny=_sum_optional_float(current.cost_cny, observation.cost_cny),
                known_input_tokens_count=current.known_input_tokens_count
                + int(observation.input_tokens is not None),
                known_output_tokens_count=current.known_output_tokens_count
                + int(observation.output_tokens is not None),
                known_cached_input_tokens_count=current.known_cached_input_tokens_count
                + int(observation.cached_input_tokens is not None),
                known_uncached_input_tokens_count=current.known_uncached_input_tokens_count
                + int(observation.uncached_input_tokens is not None),
                known_reasoning_tokens_count=current.known_reasoning_tokens_count
                + int(observation.reasoning_tokens is not None),
                known_cost_count=current.known_cost_count + int(observation.cost_usd is not None),
                known_cost_cny_count=current.known_cost_cny_count
                + int(observation.cost_cny is not None),
            )

    def _record_call_attempts(self, attempt_count: int) -> None:
        with self._usage_lock:
            self._usage = replace(
                self._usage,
                call_count=self._usage.call_count + attempt_count,
            )


def _request_payload(
    source: ProjectionSource,
    facts: tuple[ProjectionFact, ...],
    *,
    contract: str,
) -> dict[str, object]:
    return {
        "contract": contract,
        "episode_id": source.episode_id,
        "facts": [_fact_payload(fact) for fact in facts],
    }


def _provider_attempt_count(model: TextModel) -> int:
    observed = getattr(model, "last_provider_attempt_count", None)
    if observed is None:
        return 1
    if type(observed) is not int or observed < 0:
        raise ValueError("Semantic projection provider attempt count is invalid")
    return observed


def _fact_payload(fact: ProjectionFact) -> dict[str, object]:
    return {
        "fact_id": fact.fact_id,
        "text": fact.text,
        "actor": fact.actor,
        "role": fact.role,
        "occurred_at": fact.occurred_at,
    }


def _parse_response(text: str, *, allowed_fact_ids: set[str]) -> tuple[ClauseDraft, ...]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("Semantic projection response is not valid JSON") from error
    if not isinstance(value, dict) or set(value) != {"clauses"}:
        raise ValueError("Semantic projection response has an invalid schema")
    raw_clauses = value.get("clauses")
    if not isinstance(raw_clauses, list):
        raise ValueError("Semantic projection response has an invalid clause schema")
    drafts: list[ClauseDraft] = []
    for raw in raw_clauses:
        if not isinstance(raw, dict) or set(raw) != {"text", "source_fact_ids"}:
            raise ValueError("Semantic projection response has an invalid clause schema")
        clause_text = raw.get("text")
        source_fact_ids = raw.get("source_fact_ids")
        if (
            not isinstance(clause_text, str)
            or not isinstance(source_fact_ids, list)
            or not all(isinstance(item, str) for item in source_fact_ids)
        ):
            raise ValueError("Semantic projection response has an invalid clause schema")
        references = tuple(source_fact_ids)
        if any(fact_id not in allowed_fact_ids for fact_id in references):
            raise ValueError("Semantic projection response crosses its source window")
        drafts.append(ClauseDraft(text=clause_text, source_fact_ids=references))
    return tuple(drafts)


def _request_seed(source_digest: str, window_index: int) -> int:
    digest = hashlib.sha256(f"{source_digest}:{window_index}".encode()).hexdigest()
    return int(digest[:8], 16)


def _usage_observation(response: ModelResponse) -> _UsageObservation:
    return _UsageObservation(
        input_tokens=_optional_non_negative_int(response.input_tokens, field="input_tokens"),
        output_tokens=_optional_non_negative_int(response.output_tokens, field="output_tokens"),
        cached_input_tokens=_optional_non_negative_int(
            response.cached_input_tokens,
            field="cached_input_tokens",
        ),
        uncached_input_tokens=_optional_non_negative_int(
            response.uncached_input_tokens,
            field="uncached_input_tokens",
        ),
        reasoning_tokens=_optional_non_negative_int(
            response.reasoning_tokens,
            field="reasoning_tokens",
        ),
        cost_usd=_optional_non_negative_float(response.cost_usd, field="cost_usd"),
        cost_cny=_optional_non_negative_float(response.cost_cny, field="cost_cny"),
    )


def _optional_non_negative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"Semantic projection {field} usage must be a non-negative integer")
    return value


def _optional_non_negative_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Semantic projection {field} usage must be a finite non-negative number")
    resolved = float(value)
    if not isfinite(resolved) or resolved < 0:
        raise ValueError(f"Semantic projection {field} usage must be a finite non-negative number")
    return resolved


def _sum_optional_int(current: int | None, observed: int | None) -> int | None:
    if observed is None:
        return current
    return observed if current is None else current + observed


def _sum_optional_float(current: float | None, observed: float | None) -> float | None:
    if observed is None:
        return current
    return observed if current is None else current + observed


def _json_object_snapshot(value: dict[str, object]) -> dict[str, object]:
    decoded: object = json.loads(_canonical_json(value))
    if not isinstance(decoded, dict):
        raise ValueError("Semantic projection model config must be a JSON object")
    return cast(dict[str, object], decoded)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Semantic projection config must be finite canonical JSON") from error
