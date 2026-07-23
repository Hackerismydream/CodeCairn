from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from codecairn.evaluation.artifacts import (
    canonical_json,
    file_sha256,
    read_json,
    write_json_exclusive,
)
from codecairn.evaluation.model import ModelResponse, TextModel

MODEL_ATTEMPT_JOURNAL_CONTRACT = "locomo-model-attempt-journal-v1"
UNKNOWN_PROVIDER_SPEND_ERROR = "UnknownProviderSpend"
_SAFE_QUESTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_INTEGER_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "reasoning_tokens",
)
_COST_USAGE_FIELDS = ("cost_usd", "cost_cny")
_KNOWN_COUNT_BY_FIELD = {
    "input_tokens": "known_input_tokens_count",
    "output_tokens": "known_output_tokens_count",
    "cached_input_tokens": "known_cached_input_tokens_count",
    "uncached_input_tokens": "known_uncached_input_tokens_count",
    "reasoning_tokens": "known_reasoning_tokens_count",
    "cost_usd": "known_cost_count",
    "cost_cny": "known_cost_cny_count",
}

AttemptStage = Literal["answer", "judge"]


class JournaledProviderError(RuntimeError):
    """A provider failure recovered from an immutable attempt outcome."""

    def __init__(self, error_type: str) -> None:
        super().__init__(f"Journaled provider invocation failed: {error_type}")
        self.journal_error_type = error_type


class UnknownProviderSpendError(JournaledProviderError):
    """An invocation started but never published a durable outcome."""

    def __init__(self, entry_id: str) -> None:
        super().__init__(UNKNOWN_PROVIDER_SPEND_ERROR)
        self.entry_id = entry_id


class ModelAttemptJournal:
    """Durably bracket paid model invocations and replay completed outcomes.

    A start receipt is linked and fsynced before calling the provider. The outcome is written
    immediately after the call returns or raises a normal exception. A start without an outcome
    is intentionally not retried: the provider may have charged for work that the process never
    observed.
    """

    def __init__(self, root: Path, *, question_id: str) -> None:
        if _SAFE_QUESTION_ID.fullmatch(question_id) is None:
            raise ValueError("LoCoMo attempt journal question ID is unsafe")
        self._root = root
        self._question_id = question_id

    def invoke(
        self,
        model: TextModel,
        *,
        stage: AttemptStage,
        application_attempt: int,
        seed: int,
        system: str,
        user: str,
        response_format: str,
        vote_index: int | None = None,
    ) -> ModelResponse:
        entry_id = _entry_id(
            stage=stage,
            vote_index=vote_index,
            application_attempt=application_attempt,
        )
        start_path = self._root / f"{entry_id}.start.json"
        outcome_path = self._root / f"{entry_id}.outcome.json"
        start = _start_payload(
            question_id=self._question_id,
            entry_id=entry_id,
            stage=stage,
            vote_index=vote_index,
            application_attempt=application_attempt,
            seed=seed,
            system=system,
            user=user,
            response_format=response_format,
            model=model,
        )
        if start_path.exists():
            observed_start = _required_mapping(read_json(start_path), field="attempt start")
            if observed_start != start:
                raise ValueError("LoCoMo attempt journal request binding changed")
            if not outcome_path.is_file():
                raise UnknownProviderSpendError(entry_id)
            return _replay_outcome(start_path=start_path, outcome_path=outcome_path)
        if outcome_path.exists():
            raise ValueError("LoCoMo attempt journal outcome has no start")
        _validate_root(self._root)
        write_json_exclusive(start_path, start)
        observer_context = getattr(model, "observe_provider_attempts", None)
        provider_observer = self._provider_observer(
            entry_id=entry_id,
            application_start_path=start_path,
        )
        try:
            if callable(observer_context):
                with observer_context(provider_observer):
                    response = model.generate(
                        system=system,
                        user=user,
                        seed=seed,
                        response_format=response_format,
                    )
            else:
                response = model.generate(
                    system=system,
                    user=user,
                    seed=seed,
                    response_format=response_format,
                )
        except Exception as error:
            provider_attempts = _require_complete_provider_attempts(
                self._root,
                start=start,
                start_path=start_path,
                observed_attempt_count=_provider_attempt_count(model),
            )
            if any(attempt["status"] == "completed" for attempt in provider_attempts):
                # The provider returned a successful HTTP response, but the Adapter could not
                # produce a usage-bearing ModelResponse. Preserve the application start-only:
                # the request may be billed and its exact cost is no longer observable.
                raise UnknownProviderSpendError(f"{entry_id}.response") from error
            write_json_exclusive(
                outcome_path,
                {
                    "schema_version": 1,
                    "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
                    "question_id": self._question_id,
                    "entry_id": entry_id,
                    "start_sha256": file_sha256(start_path),
                    "status": "provider_failed",
                    "provider_attempt_count": _provider_attempt_count(model),
                    "error_type": type(error).__name__,
                    "error_message_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
                    "response": None,
                },
            )
            raise
        _require_complete_provider_attempts(
            self._root,
            start=start,
            start_path=start_path,
            observed_attempt_count=_provider_attempt_count(model),
        )
        write_json_exclusive(
            outcome_path,
            {
                "schema_version": 1,
                "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
                "question_id": self._question_id,
                "entry_id": entry_id,
                "start_sha256": file_sha256(start_path),
                "status": "responded",
                "provider_attempt_count": _provider_attempt_count(model),
                "error_type": None,
                "error_message_sha256": None,
                "response": {
                    **asdict(response),
                    "text_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
                },
            },
        )
        return response

    def _provider_observer(
        self,
        *,
        entry_id: str,
        application_start_path: Path,
    ) -> Callable[
        [int, Literal["started", "completed", "failed"], str | None],
        None,
    ]:
        def observe(
            provider_attempt: int,
            status: Literal["started", "completed", "failed"],
            error_type: str | None,
        ) -> None:
            if type(provider_attempt) is not int or provider_attempt < 1:
                raise ValueError("LoCoMo provider attempt index is invalid")
            prefix = f"{entry_id}.provider-{provider_attempt:03d}"
            start_path = self._root / f"{prefix}.start.json"
            outcome_path = self._root / f"{prefix}.outcome.json"
            if status == "started":
                write_json_exclusive(
                    start_path,
                    {
                        "schema_version": 1,
                        "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
                        "question_id": self._question_id,
                        "entry_id": entry_id,
                        "provider_attempt": provider_attempt,
                        "application_start_sha256": file_sha256(application_start_path),
                    },
                )
                return
            if status not in {"completed", "failed"}:
                raise ValueError("LoCoMo provider attempt status is invalid")
            if not start_path.is_file():
                raise ValueError("LoCoMo provider attempt outcome has no start")
            write_json_exclusive(
                outcome_path,
                {
                    "schema_version": 1,
                    "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
                    "question_id": self._question_id,
                    "entry_id": entry_id,
                    "provider_attempt": provider_attempt,
                    "provider_start_sha256": file_sha256(start_path),
                    "status": status,
                    "error_type": error_type,
                },
            )

        return observe

    def snapshot(self) -> dict[str, object]:
        return validate_model_attempt_journal(self._root, question_id=self._question_id)


def validate_model_attempt_journal(root: Path, *, question_id: str) -> dict[str, object]:
    if _SAFE_QUESTION_ID.fullmatch(question_id) is None:
        raise ValueError("LoCoMo attempt journal question ID is unsafe")
    if not root.exists():
        return _snapshot(question_id=question_id, entries=[])
    _validate_root(root)
    children = sorted(root.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in children):
        raise ValueError("LoCoMo attempt journal must not contain symlinks")
    files = [path for path in children if ".provider-" not in path.name]
    provider_files = [path for path in children if ".provider-" in path.name]
    if any(not path.name.endswith((".start.json", ".outcome.json")) for path in files):
        raise ValueError("LoCoMo attempt journal contains an unexpected file")
    if any(not path.name.endswith((".start.json", ".outcome.json")) for path in provider_files):
        raise ValueError("LoCoMo attempt journal contains an unexpected provider file")
    starts = {
        path.name.removesuffix(".start.json"): path
        for path in files
        if path.name.endswith(".start.json")
    }
    outcomes = {
        path.name.removesuffix(".outcome.json"): path
        for path in files
        if path.name.endswith(".outcome.json")
    }
    if set(outcomes) - set(starts):
        raise ValueError("LoCoMo attempt journal outcome has no start")
    if any(
        not any(path.name.startswith(f"{entry_id}.provider-") for entry_id in starts)
        for path in provider_files
    ):
        raise ValueError("LoCoMo provider attempt has no application start")
    parsed: list[dict[str, object]] = []
    for entry_id, start_path in starts.items():
        start = _validate_start(start_path, question_id=question_id, entry_id=entry_id)
        outcome_path = outcomes.get(entry_id)
        provider_attempts = _provider_attempt_snapshots(
            root,
            question_id=question_id,
            entry_id=entry_id,
            application_start_sha256=file_sha256(start_path),
            provider_files=provider_files,
        )
        parsed.append(
            _entry_snapshot(
                start,
                start_path=start_path,
                outcome_path=outcome_path,
                provider_attempts=provider_attempts,
            )
        )
    parsed.sort(key=_entry_sort_key)
    _validate_sequence(parsed)
    return _snapshot(question_id=question_id, entries=parsed)


def validate_model_attempt_journal_snapshot(
    value: object,
    *,
    root: Path,
    question_id: str,
) -> dict[str, object]:
    observed = validate_model_attempt_journal(root, question_id=question_id)
    if value != observed:
        raise ValueError("LoCoMo question attempt journal snapshot does not match its files")
    return observed


def _start_payload(
    *,
    question_id: str,
    entry_id: str,
    stage: AttemptStage,
    vote_index: int | None,
    application_attempt: int,
    seed: int,
    system: str,
    user: str,
    response_format: str,
    model: TextModel,
) -> dict[str, object]:
    if application_attempt < 1:
        raise ValueError("LoCoMo application attempt must be positive")
    request = {
        "system": system,
        "user": user,
        "seed": seed,
        "response_format": response_format,
    }
    public_config = model.public_config
    return {
        "schema_version": 1,
        "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
        "question_id": question_id,
        "entry_id": entry_id,
        "stage": stage,
        "vote_index": vote_index,
        "application_attempt": application_attempt,
        "seed": seed,
        "response_format": response_format,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "model_id": model.model_id,
        "model_config_sha256": hashlib.sha256(canonical_json(public_config).encode()).hexdigest(),
        "provider_attempt_journal": callable(getattr(model, "observe_provider_attempts", None)),
    }


def _entry_id(
    *,
    stage: AttemptStage,
    vote_index: int | None,
    application_attempt: int,
) -> str:
    if application_attempt < 1:
        raise ValueError("LoCoMo application attempt must be positive")
    if stage == "answer":
        if vote_index is not None:
            raise ValueError("LoCoMo answer attempts must not have a vote index")
        return f"answer.app-{application_attempt:03d}"
    if stage != "judge" or type(vote_index) is not int or vote_index < 0:
        raise ValueError("LoCoMo judge attempts require a non-negative vote index")
    return f"judge-vote-{vote_index:03d}.app-{application_attempt:03d}"


def _replay_outcome(*, start_path: Path, outcome_path: Path) -> ModelResponse:
    start = _required_mapping(read_json(start_path), field="attempt start")
    outcome = _validate_outcome(
        outcome_path,
        question_id=_required_string(start, "question_id"),
        entry_id=_required_string(start, "entry_id"),
        start_sha256=file_sha256(start_path),
    )
    _require_complete_provider_attempts(
        start_path.parent,
        start=start,
        start_path=start_path,
        observed_attempt_count=cast(int, outcome["provider_attempt_count"]),
    )
    if outcome["status"] == "provider_failed":
        raise JournaledProviderError(_required_string(outcome, "error_type"))
    return _response_from_outcome(outcome)


def _require_complete_provider_attempts(
    root: Path,
    *,
    start: dict[str, object],
    start_path: Path,
    observed_attempt_count: int,
) -> list[dict[str, object]]:
    if start.get("provider_attempt_journal") is not True:
        return []
    entry_id = _required_string(start, "entry_id")
    attempts = _provider_attempt_snapshots(
        root,
        question_id=_required_string(start, "question_id"),
        entry_id=entry_id,
        application_start_sha256=file_sha256(start_path),
    )
    incomplete = next(
        (item for item in attempts if item["outcome_sha256"] is None),
        None,
    )
    if incomplete is not None:
        raise UnknownProviderSpendError(
            f"{entry_id}.provider-{cast(int, incomplete['provider_attempt']):03d}"
        )
    if len(attempts) != observed_attempt_count:
        raise ValueError("LoCoMo provider attempt count does not match its journal")
    return attempts


def _validate_start(
    path: Path,
    *,
    question_id: str,
    entry_id: str,
) -> dict[str, object]:
    start = _required_mapping(read_json(path), field="attempt start")
    expected_fields = {
        "schema_version",
        "contract",
        "question_id",
        "entry_id",
        "stage",
        "vote_index",
        "application_attempt",
        "seed",
        "response_format",
        "request_sha256",
        "model_id",
        "model_config_sha256",
        "provider_attempt_journal",
    }
    if (
        set(start) != expected_fields
        or start.get("schema_version") != 1
        or start.get("contract") != MODEL_ATTEMPT_JOURNAL_CONTRACT
        or start.get("question_id") != question_id
        or start.get("entry_id") != entry_id
    ):
        raise ValueError("LoCoMo attempt journal start does not match its schema")
    stage = start.get("stage")
    application_attempt = start.get("application_attempt")
    vote_index = start.get("vote_index")
    if (
        stage not in {"answer", "judge"}
        or type(application_attempt) is not int
        or application_attempt < 1
        or _entry_id(
            stage=cast(AttemptStage, stage),
            vote_index=cast(int | None, vote_index),
            application_attempt=application_attempt,
        )
        != entry_id
        or type(start.get("seed")) is not int
        or not _nonempty_string(start.get("response_format"))
        or not _nonempty_string(start.get("model_id"))
        or not _sha256(start.get("request_sha256"))
        or not _sha256(start.get("model_config_sha256"))
        or type(start.get("provider_attempt_journal")) is not bool
    ):
        raise ValueError("LoCoMo attempt journal start has invalid metadata")
    return start


def _validate_outcome(
    path: Path,
    *,
    question_id: str,
    entry_id: str,
    start_sha256: str,
) -> dict[str, object]:
    outcome = _required_mapping(read_json(path), field="attempt outcome")
    expected_fields = {
        "schema_version",
        "contract",
        "question_id",
        "entry_id",
        "start_sha256",
        "status",
        "provider_attempt_count",
        "error_type",
        "error_message_sha256",
        "response",
    }
    if (
        set(outcome) != expected_fields
        or outcome.get("schema_version") != 1
        or outcome.get("contract") != MODEL_ATTEMPT_JOURNAL_CONTRACT
        or outcome.get("question_id") != question_id
        or outcome.get("entry_id") != entry_id
        or outcome.get("start_sha256") != start_sha256
        or type(outcome.get("provider_attempt_count")) is not int
        or cast(int, outcome["provider_attempt_count"]) < 1
    ):
        raise ValueError("LoCoMo attempt journal outcome does not match its start")
    status = outcome.get("status")
    if status == "provider_failed":
        if (
            not _nonempty_string(outcome.get("error_type"))
            or not _sha256(outcome.get("error_message_sha256"))
            or outcome.get("response") is not None
        ):
            raise ValueError("LoCoMo provider failure outcome is invalid")
    elif status == "responded":
        if outcome.get("error_type") is not None or outcome.get("error_message_sha256") is not None:
            raise ValueError("LoCoMo provider response outcome is invalid")
        _response_from_outcome(outcome)
    else:
        raise ValueError("LoCoMo attempt journal outcome status is invalid")
    return outcome


def _response_from_outcome(outcome: dict[str, object]) -> ModelResponse:
    raw = _required_mapping(outcome.get("response"), field="attempt response")
    expected_fields = {
        "text",
        "text_sha256",
        "model",
        *_INTEGER_USAGE_FIELDS,
        *_COST_USAGE_FIELDS,
    }
    if set(raw) != expected_fields:
        raise ValueError("LoCoMo attempt journal response does not match its schema")
    text = _required_string(raw, "text")
    if raw.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError("LoCoMo attempt journal response digest does not match")
    model = _required_string(raw, "model")
    return ModelResponse(
        text=text,
        model=model,
        input_tokens=_optional_nonnegative_int(
            raw.get("input_tokens"),
            field="input_tokens",
        ),
        output_tokens=_optional_nonnegative_int(
            raw.get("output_tokens"),
            field="output_tokens",
        ),
        cached_input_tokens=_optional_nonnegative_int(
            raw.get("cached_input_tokens"),
            field="cached_input_tokens",
        ),
        uncached_input_tokens=_optional_nonnegative_int(
            raw.get("uncached_input_tokens"),
            field="uncached_input_tokens",
        ),
        reasoning_tokens=_optional_nonnegative_int(
            raw.get("reasoning_tokens"),
            field="reasoning_tokens",
        ),
        cost_usd=_optional_nonnegative_number(raw.get("cost_usd"), field="cost_usd"),
        cost_cny=_optional_nonnegative_number(raw.get("cost_cny"), field="cost_cny"),
    )


def _entry_snapshot(
    start: dict[str, object],
    *,
    start_path: Path,
    outcome_path: Path | None,
    provider_attempts: list[dict[str, object]],
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "entry_id": start["entry_id"],
        "stage": start["stage"],
        "vote_index": start["vote_index"],
        "application_attempt": start["application_attempt"],
        "start_sha256": file_sha256(start_path),
        "outcome_sha256": None,
        "status": "unknown_spend",
        "provider_attempt_count": None,
        "error_type": UNKNOWN_PROVIDER_SPEND_ERROR,
        "model": None,
        "response_sha256": None,
        "provider_attempts": provider_attempts,
        **{field: None for field in (*_INTEGER_USAGE_FIELDS, *_COST_USAGE_FIELDS)},
    }
    if outcome_path is None:
        return snapshot
    outcome = _validate_outcome(
        outcome_path,
        question_id=cast(str, start["question_id"]),
        entry_id=cast(str, start["entry_id"]),
        start_sha256=file_sha256(start_path),
    )
    if start.get("provider_attempt_journal") is True and (
        any(item["outcome_sha256"] is None for item in provider_attempts)
        or outcome["provider_attempt_count"] != len(provider_attempts)
    ):
        raise ValueError("LoCoMo application outcome is not derived from provider attempts")
    snapshot.update(
        {
            "outcome_sha256": file_sha256(outcome_path),
            "status": outcome["status"],
            "provider_attempt_count": outcome["provider_attempt_count"],
            "error_type": outcome["error_type"],
        }
    )
    if outcome["status"] == "responded":
        response = _response_from_outcome(outcome)
        snapshot.update(
            {
                "model": response.model,
                "response_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
                **{
                    field: getattr(response, field)
                    for field in (*_INTEGER_USAGE_FIELDS, *_COST_USAGE_FIELDS)
                },
            }
        )
    return snapshot


def _provider_attempt_snapshots(
    root: Path,
    *,
    question_id: str,
    entry_id: str,
    application_start_sha256: str,
    provider_files: list[Path] | None = None,
) -> list[dict[str, object]]:
    files = (
        [path for path in root.iterdir() if path.is_file() and ".provider-" in path.name]
        if provider_files is None
        else provider_files
    )
    prefix = f"{entry_id}.provider-"
    relevant = [path for path in files if path.name.startswith(prefix)]
    starts = {
        path.name.removesuffix(".start.json"): path
        for path in relevant
        if path.name.endswith(".start.json")
    }
    outcomes = {
        path.name.removesuffix(".outcome.json"): path
        for path in relevant
        if path.name.endswith(".outcome.json")
    }
    if set(outcomes) - set(starts):
        raise ValueError("LoCoMo provider attempt outcome has no start")
    snapshots: list[dict[str, object]] = []
    for expected_index, (provider_id, start_path) in enumerate(
        sorted(starts.items()),
        start=1,
    ):
        if provider_id != f"{entry_id}.provider-{expected_index:03d}":
            raise ValueError("LoCoMo provider attempts are not contiguous")
        start = _required_mapping(read_json(start_path), field="provider attempt start")
        if (
            set(start)
            != {
                "schema_version",
                "contract",
                "question_id",
                "entry_id",
                "provider_attempt",
                "application_start_sha256",
            }
            or start.get("schema_version") != 1
            or start.get("contract") != MODEL_ATTEMPT_JOURNAL_CONTRACT
            or start.get("question_id") != question_id
            or start.get("entry_id") != entry_id
            or start.get("provider_attempt") != expected_index
            or start.get("application_start_sha256") != application_start_sha256
        ):
            raise ValueError("LoCoMo provider attempt start is invalid")
        outcome_path = outcomes.get(provider_id)
        snapshot: dict[str, object] = {
            "provider_attempt": expected_index,
            "start_sha256": file_sha256(start_path),
            "outcome_sha256": None,
            "status": "unknown_spend",
            "error_type": UNKNOWN_PROVIDER_SPEND_ERROR,
        }
        if outcome_path is not None:
            outcome = _required_mapping(
                read_json(outcome_path),
                field="provider attempt outcome",
            )
            if (
                set(outcome)
                != {
                    "schema_version",
                    "contract",
                    "question_id",
                    "entry_id",
                    "provider_attempt",
                    "provider_start_sha256",
                    "status",
                    "error_type",
                }
                or outcome.get("schema_version") != 1
                or outcome.get("contract") != MODEL_ATTEMPT_JOURNAL_CONTRACT
                or outcome.get("question_id") != question_id
                or outcome.get("entry_id") != entry_id
                or outcome.get("provider_attempt") != expected_index
                or outcome.get("provider_start_sha256") != file_sha256(start_path)
                or outcome.get("status") not in {"completed", "failed"}
                or (outcome.get("status") == "completed" and outcome.get("error_type") is not None)
                or (
                    outcome.get("status") == "failed"
                    and not _nonempty_string(outcome.get("error_type"))
                )
            ):
                raise ValueError("LoCoMo provider attempt outcome is invalid")
            snapshot.update(
                {
                    "outcome_sha256": file_sha256(outcome_path),
                    "status": outcome["status"],
                    "error_type": outcome["error_type"],
                }
            )
        snapshots.append(snapshot)
    return snapshots


def _snapshot(
    *,
    question_id: str,
    entries: list[dict[str, object]],
) -> dict[str, object]:
    usage: dict[str, object] = {
        "application_call_count": len(entries),
        "completed_outcome_count": sum(item["outcome_sha256"] is not None for item in entries),
        "response_count": sum(item["status"] == "responded" for item in entries),
        "provider_failed_count": sum(item["status"] == "provider_failed" for item in entries),
        "unknown_spend_count": sum(item["status"] == "unknown_spend" for item in entries),
        "provider_attempt_count": sum(
            cast(int, item["provider_attempt_count"])
            for item in entries
            if item["provider_attempt_count"] is not None
        ),
        "known_provider_attempt_count": sum(
            item["provider_attempt_count"] is not None for item in entries
        ),
    }
    for field in (*_INTEGER_USAGE_FIELDS, *_COST_USAGE_FIELDS):
        values = [item[field] for item in entries if item[field] is not None]
        usage[field] = None if not values else sum(cast(list[int | float], values))
        usage[_KNOWN_COUNT_BY_FIELD[field]] = len(values)
    content = {
        "contract": MODEL_ATTEMPT_JOURNAL_CONTRACT,
        "question_id": question_id,
        "entries": entries,
        "usage": usage,
    }
    return {
        "schema_version": 1,
        **content,
        "journal_sha256": hashlib.sha256(canonical_json(content).encode()).hexdigest(),
    }


def _validate_sequence(entries: list[dict[str, object]]) -> None:
    seen: dict[tuple[object, object], list[int]] = {}
    for entry in entries:
        key = (entry["stage"], entry["vote_index"])
        seen.setdefault(key, []).append(cast(int, entry["application_attempt"]))
    if any(indexes != list(range(1, len(indexes) + 1)) for indexes in seen.values()):
        raise ValueError("LoCoMo attempt journal application attempts are not contiguous")


def _entry_sort_key(entry: dict[str, object]) -> tuple[int, int, int]:
    stage = cast(str, entry["stage"])
    vote_index = cast(int | None, entry["vote_index"])
    return (
        0 if stage == "answer" else 1,
        -1 if vote_index is None else vote_index,
        cast(int, entry["application_attempt"]),
    )


def _provider_attempt_count(model: TextModel) -> int:
    observed = getattr(model, "last_provider_attempt_count", None)
    return observed if type(observed) is int and observed >= 1 else 1


def _validate_root(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("LoCoMo attempt journal root must not be a symlink")
    if root.exists() and not root.is_dir():
        raise ValueError("LoCoMo attempt journal root must be a directory")


def _required_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"LoCoMo {field} must be an object")
    return cast(dict[str, object], value)


def _required_string(value: dict[str, object], field: str) -> str:
    result = value.get(field)
    if not _nonempty_string(result):
        raise ValueError(f"LoCoMo attempt journal {field} must be a non-empty string")
    return cast(str, result)


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"LoCoMo attempt journal {field} must be a non-negative integer")
    return value


def _optional_nonnegative_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"LoCoMo attempt journal {field} must be a non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"LoCoMo attempt journal {field} must be a non-negative number")
    return result
