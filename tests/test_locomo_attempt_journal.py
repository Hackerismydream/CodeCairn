from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from codecairn.bootstrap import _copy_locomo_worker_attempt_journals
from codecairn.evaluation.artifacts import read_json, write_json_exclusive
from codecairn.evaluation.attempt_journal import (
    MODEL_ATTEMPT_JOURNAL_CONTRACT,
    JournaledProviderError,
    ModelAttemptJournal,
    UnknownProviderSpendError,
    validate_model_attempt_journal,
)
from codecairn.evaluation.model import ModelResponse
from codecairn.evaluation.providers import OpenAICompatibleTextModel


class _CountingModel:
    model_id = "fixture-model"
    public_config: ClassVar[dict[str, object]] = {
        "adapter": "fixture",
        "model": model_id,
    }

    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self._responses = responses
        self.calls = 0
        self.last_provider_attempt_count = 0

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        del system, user, seed, response_format
        response = self._responses[self.calls]
        self.calls += 1
        self.last_provider_attempt_count = 2
        if isinstance(response, Exception):
            raise response
        return response


def test_attempt_journal_replays_a_completed_response_without_a_second_call(
    tmp_path: Path,
) -> None:
    model = _CountingModel(
        [
            ModelResponse(
                text='{"answer":"Poppy"}',
                model="fixture-model-v1",
                input_tokens=20,
                output_tokens=4,
                cost_cny=0.001,
            )
        ]
    )
    journal = ModelAttemptJournal(tmp_path / "journal", question_id="q-1")

    first = journal.invoke(
        model,
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )
    replayed = journal.invoke(
        model,
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )

    assert first == replayed
    assert model.calls == 1
    snapshot = journal.snapshot()
    assert snapshot["contract"] == MODEL_ATTEMPT_JOURNAL_CONTRACT
    usage = snapshot["usage"]
    assert isinstance(usage, dict)
    assert usage["application_call_count"] == 1
    assert usage["provider_attempt_count"] == 2
    assert usage["input_tokens"] == 20
    assert usage["cost_cny"] == 0.001


def test_attempt_journal_fails_closed_when_a_started_call_has_no_outcome(
    tmp_path: Path,
) -> None:
    source_model = _CountingModel([RuntimeError("must not be called")])
    journal = ModelAttemptJournal(tmp_path / "journal", question_id="q-2")
    start = {
        "schema_version": 1,
        "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
        "question_id": "q-2",
        "entry_id": "answer.app-001",
        "stage": "answer",
        "vote_index": None,
        "application_attempt": 1,
        "seed": 17,
        "response_format": "json",
        "request_sha256": ("371009e75c119ac98a33e3ca9d409901eb8cf684c4aac6e71b4a28cfb88e182a"),
        "model_id": "fixture-model",
        "model_config_sha256": ("fb0bad9d971b7433739741e0a2de0e4e2c36df4cb9d41b04d30dc072024f3adc"),
    }
    # Obtain the exact request binding once, then simulate SIGKILL by removing only the outcome.
    completed = ModelAttemptJournal(tmp_path / "completed", question_id="q-2")
    completed_model = _CountingModel([ModelResponse(text="{}", model="fixture-model")])
    completed.invoke(
        completed_model,
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )
    observed_start = read_json(tmp_path / "completed" / "answer.app-001.start.json")
    assert isinstance(observed_start, dict)
    start.update(observed_start)
    write_json_exclusive(tmp_path / "journal" / "answer.app-001.start.json", start)

    with pytest.raises(UnknownProviderSpendError):
        journal.invoke(
            source_model,
            stage="answer",
            application_attempt=1,
            seed=17,
            system="system",
            user="user",
            response_format="json",
        )

    assert source_model.calls == 0
    snapshot = journal.snapshot()
    usage = snapshot["usage"]
    assert isinstance(usage, dict)
    assert usage["unknown_spend_count"] == 1
    assert usage["completed_outcome_count"] == 0


def test_attempt_journal_replays_a_known_provider_failure_without_rebilling(
    tmp_path: Path,
) -> None:
    failing = _CountingModel([TimeoutError("provider timeout")])
    journal = ModelAttemptJournal(tmp_path / "journal", question_id="q-3")
    with pytest.raises(TimeoutError):
        journal.invoke(
            failing,
            stage="judge",
            vote_index=0,
            application_attempt=1,
            seed=18,
            system="system",
            user="user",
            response_format="json",
        )
    assert failing.calls == 1

    replacement = _CountingModel([AssertionError("must not be called")])
    with pytest.raises(JournaledProviderError) as captured:
        journal.invoke(
            replacement,
            stage="judge",
            vote_index=0,
            application_attempt=1,
            seed=18,
            system="system",
            user="user",
            response_format="json",
        )

    assert captured.value.journal_error_type == "TimeoutError"
    assert replacement.calls == 0
    usage = journal.snapshot()["usage"]
    assert isinstance(usage, dict)
    assert usage["provider_failed_count"] == 1
    assert usage["provider_attempt_count"] == 2


def test_attempt_journal_rejects_a_tampered_outcome(tmp_path: Path) -> None:
    model = _CountingModel([ModelResponse(text="{}", model="fixture-model")])
    root = tmp_path / "journal"
    journal = ModelAttemptJournal(root, question_id="q-4")
    journal.invoke(
        model,
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )
    outcome_path = root / "answer.app-001.outcome.json"
    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
    outcome["response"]["text"] = '{"tampered":true}'
    outcome_path.write_text(json.dumps(outcome), encoding="utf-8")

    with pytest.raises(ValueError, match="response digest"):
        validate_model_attempt_journal(root, question_id="q-4")


def test_attempt_journal_rejects_an_unrecognized_provider_file(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    journal = ModelAttemptJournal(root, question_id="q-provider-extra")
    journal.invoke(
        _CountingModel([ModelResponse(text="{}", model="fixture-model")]),
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )
    (root / "answer.app-001.provider-001.debug.txt").write_text(
        "unbound",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected provider file"):
        validate_model_attempt_journal(root, question_id="q-provider-extra")


def test_worker_resume_copies_completed_and_unknown_attempts_without_rebilling(
    tmp_path: Path,
) -> None:
    source_questions = tmp_path / "attempt-1" / "questions" / "conv-1"
    source_root = source_questions / ".attempt-journal"
    completed = ModelAttemptJournal(source_root / "q-completed", question_id="q-completed")
    completed.invoke(
        _CountingModel([ModelResponse(text="{}", model="fixture-model")]),
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )
    unknown = ModelAttemptJournal(source_root / "q-unknown", question_id="q-unknown")
    unknown.invoke(
        _CountingModel([ModelResponse(text="{}", model="fixture-model")]),
        stage="judge",
        vote_index=0,
        application_attempt=1,
        seed=18,
        system="system",
        user="user",
        response_format="json",
    )
    (source_root / "q-unknown" / "judge-vote-000.app-001.outcome.json").unlink()
    target_questions = tmp_path / "attempt-2" / "questions" / "conv-1"

    copied = _copy_locomo_worker_attempt_journals(
        source_questions,
        target_questions,
        question_ids=("q-completed", "q-unknown"),
        excluded_question_ids=set(),
    )

    assert copied == ["q-completed", "q-unknown"]
    replay_model = _CountingModel([AssertionError("completed call must be replayed")])
    replayed = ModelAttemptJournal(
        target_questions / ".attempt-journal" / "q-completed",
        question_id="q-completed",
    ).invoke(
        replay_model,
        stage="answer",
        application_attempt=1,
        seed=17,
        system="system",
        user="user",
        response_format="json",
    )
    assert replayed.text == "{}"
    assert replay_model.calls == 0
    unknown_model = _CountingModel([AssertionError("unknown call must fail closed")])
    with pytest.raises(UnknownProviderSpendError):
        ModelAttemptJournal(
            target_questions / ".attempt-journal" / "q-unknown",
            question_id="q-unknown",
        ).invoke(
            unknown_model,
            stage="judge",
            vote_index=0,
            application_attempt=1,
            seed=18,
            system="system",
            user="user",
            response_format="json",
        )
    assert unknown_model.calls == 0


def test_worker_resume_rejects_a_nonempty_invalid_attempt_journal(tmp_path: Path) -> None:
    source_questions = tmp_path / "attempt-1" / "questions" / "conv-1"
    source_journal = source_questions / ".attempt-journal" / "q-invalid"
    source_journal.mkdir(parents=True)
    (source_journal / "answer.app-001.start.json").write_text(
        "{broken",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid model attempt journal"):
        _copy_locomo_worker_attempt_journals(
            source_questions,
            tmp_path / "attempt-2" / "questions" / "conv-1",
            question_ids=("q-invalid",),
            excluded_question_ids=set(),
        )


def test_openai_transport_retries_each_publish_their_own_attempt_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "model": "fixture-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}"},
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            }

    def post(*args: object, **kwargs: object) -> _Response:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary transport failure")
        return _Response()

    monkeypatch.setattr(httpx, "post", post)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="fixture-secret",
        model="fixture-model",
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    journal = ModelAttemptJournal(tmp_path / "journal", question_id="q-provider")

    journal.invoke(
        model,
        stage="judge",
        vote_index=0,
        application_attempt=1,
        seed=18,
        system="system",
        user="user",
        response_format="json",
    )

    snapshot = journal.snapshot()
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    assert [attempt["status"] for attempt in entries[0]["provider_attempts"]] == [
        "failed",
        "completed",
    ]
    assert sorted(path.name for path in (tmp_path / "journal").glob("*.provider-*.json")) == [
        "judge-vote-000.app-001.provider-001.outcome.json",
        "judge-vote-000.app-001.provider-001.start.json",
        "judge-vote-000.app-001.provider-002.outcome.json",
        "judge-vote-000.app-001.provider-002.start.json",
    ]


def test_openai_read_timeout_is_unknown_spend_and_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise httpx.ReadTimeout("provider may have completed the request")

    monkeypatch.setattr(httpx, "post", post)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="fixture-secret",
        model="fixture-model",
        max_attempts=3,
        retry_backoff_seconds=0,
    )
    journal = ModelAttemptJournal(tmp_path / "journal", question_id="q-read-timeout")

    with pytest.raises(UnknownProviderSpendError):
        journal.invoke(
            model,
            stage="answer",
            application_attempt=1,
            seed=17,
            system="system",
            user="user",
            response_format="json",
        )

    assert calls == 1
    snapshot = journal.snapshot()
    usage = snapshot["usage"]
    assert isinstance(usage, dict)
    assert usage["unknown_spend_count"] == 1
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    provider_attempts = entries[0]["provider_attempts"]
    assert isinstance(provider_attempts, list)
    assert len(provider_attempts) == 1
    assert provider_attempts[0]["provider_attempt"] == 1
    assert provider_attempts[0]["outcome_sha256"] is None
    assert provider_attempts[0]["status"] == "unknown_spend"
    assert provider_attempts[0]["error_type"] == "UnknownProviderSpend"


def test_openai_unparseable_success_response_is_unknown_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "model": "fixture-model",
                "usage": {"prompt_tokens": 8, "completion_tokens": 2},
            }

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _Response())
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="fixture-secret",
        model="fixture-model",
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    root = tmp_path / "journal"

    with pytest.raises(UnknownProviderSpendError):
        ModelAttemptJournal(root, question_id="q-unparseable").invoke(
            model,
            stage="answer",
            application_attempt=1,
            seed=17,
            system="system",
            user="user",
            response_format="json",
        )

    snapshot = validate_model_attempt_journal(root, question_id="q-unparseable")
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    assert entries[0]["status"] == "unknown_spend"
    assert entries[0]["outcome_sha256"] is None
    assert entries[0]["provider_attempts"][0]["status"] == "completed"
