from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, cast

from codecairn.evaluation.grounded_answer import (
    INSUFFICIENT_CITATIONS_NORMALIZATION,
    INSUFFICIENT_EMPTY_ANSWER_NORMALIZATION,
    GroundedAnswer,
    GroundedContext,
    parse_grounded_answer_with_safe_normalization,
)
from codecairn.evaluation.model import ModelResponse

GROUNDED_ANSWER_RETRY_CONTRACT = "grounded-answer-contract-retry-v2"
GROUNDED_ANSWER_RETRY_HISTORY_CONTRACT = "grounded-answer-retry-history-v2"
_MAX_APPLICATION_ATTEMPTS = 2
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
_ATTEMPT_FIELDS = {
    "attempt_index",
    "status",
    "error_type",
    "model",
    "response_chars",
    "response_sha256",
    "normalization",
    *_INTEGER_USAGE_FIELDS,
    *_COST_USAGE_FIELDS,
}
_USAGE_FIELDS = {
    "call_count",
    "response_count",
    *_INTEGER_USAGE_FIELDS,
    *_COST_USAGE_FIELDS,
    *_KNOWN_COUNT_BY_FIELD.values(),
}
_RECEIPT_FIELDS = {
    "schema_version",
    "contract",
    "status",
    "retryable",
    "max_attempts",
    "attempt_count",
    "accepted_attempt_index",
    "terminal_error_type",
    "attempts",
    "usage",
}

AnswerAttemptStatus = Literal["accepted", "contract_rejected", "provider_failed"]


class GroundedAnswerRetryStatus(StrEnum):
    COMPLETED = "completed"
    CONTRACT_EXHAUSTED = "contract_exhausted"
    PROVIDER_FAILED = "provider_failed"


@dataclass(frozen=True, slots=True)
class GroundedAnswerAttempt:
    """One model invocation, including rejected responses that still incurred cost."""

    attempt_index: int
    status: AnswerAttemptStatus
    error_type: str | None
    model: str | None
    response_chars: int | None
    response_sha256: str | None
    normalization: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    uncached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    cost_cny: float | None = None


@dataclass(frozen=True, slots=True)
class GroundedAnswerRetryResult:
    """Application-level answer outcome; provider retries remain the adapter's concern."""

    status: GroundedAnswerRetryStatus
    max_attempts: int
    attempts: tuple[GroundedAnswerAttempt, ...]
    response: ModelResponse | None
    answer: GroundedAnswer | None

    @property
    def retryable(self) -> bool:
        return self.status is GroundedAnswerRetryStatus.CONTRACT_EXHAUSTED

    @property
    def receipt(self) -> dict[str, object]:
        accepted_attempt_index = next(
            (attempt.attempt_index for attempt in self.attempts if attempt.status == "accepted"),
            None,
        )
        terminal_error_type = (
            None
            if self.status is GroundedAnswerRetryStatus.COMPLETED
            else (self.attempts[-1].error_type)
        )
        return {
            "schema_version": 2,
            "contract": GROUNDED_ANSWER_RETRY_CONTRACT,
            "status": self.status.value,
            "retryable": self.retryable,
            "max_attempts": self.max_attempts,
            "attempt_count": len(self.attempts),
            "accepted_attempt_index": accepted_attempt_index,
            "terminal_error_type": terminal_error_type,
            "attempts": [_attempt_payload(attempt) for attempt in self.attempts],
            "usage": _aggregate_usage(self.attempts),
        }


def run_grounded_answer_attempts(
    *,
    generate: Callable[[int], ModelResponse],
    context: GroundedContext,
    max_attempts: int = 2,
    record_attempt: Callable[[GroundedAnswerAttempt], None] | None = None,
) -> GroundedAnswerRetryResult:
    """Retry only locally rejected answer contracts and retain every usage observation.

    The model adapter already owns transport retries. An exception raised by ``generate``
    therefore ends this layer immediately. JSON/schema/citation failures are deterministic local
    validation failures and receive at most one extra attempt by default. ``record_attempt`` is
    called before another paid invocation can start, allowing the caller to durably journal each
    observation outside the terminal question checkpoint.
    """

    _validate_max_attempts(max_attempts)
    attempts: list[GroundedAnswerAttempt] = []
    for attempt_index in range(1, max_attempts + 1):
        try:
            response = generate(attempt_index)
        except Exception as error:
            error_type = getattr(error, "journal_error_type", type(error).__name__)
            if not isinstance(error_type, str) or not error_type:
                error_type = type(error).__name__
            attempt = GroundedAnswerAttempt(
                attempt_index=attempt_index,
                status="provider_failed",
                error_type=error_type,
                model=None,
                response_chars=None,
                response_sha256=None,
            )
            _record(attempts, attempt, callback=record_attempt)
            return GroundedAnswerRetryResult(
                status=GroundedAnswerRetryStatus.PROVIDER_FAILED,
                max_attempts=max_attempts,
                attempts=tuple(attempts),
                response=None,
                answer=None,
            )
        try:
            answer, normalization = parse_grounded_answer_with_safe_normalization(
                response.text,
                context=context,
            )
        except (RecursionError, ValueError) as error:
            attempt = _response_attempt(
                attempt_index,
                status="contract_rejected",
                response=response,
                error_type=type(error).__name__,
                normalization=None,
            )
            _record(attempts, attempt, callback=record_attempt)
            if attempt_index < max_attempts:
                continue
            return GroundedAnswerRetryResult(
                status=GroundedAnswerRetryStatus.CONTRACT_EXHAUSTED,
                max_attempts=max_attempts,
                attempts=tuple(attempts),
                response=None,
                answer=None,
            )
        attempt = _response_attempt(
            attempt_index,
            status="accepted",
            response=response,
            error_type=None,
            normalization=normalization,
        )
        _record(attempts, attempt, callback=record_attempt)
        return GroundedAnswerRetryResult(
            status=GroundedAnswerRetryStatus.COMPLETED,
            max_attempts=max_attempts,
            attempts=tuple(attempts),
            response=response,
            answer=answer,
        )
    raise AssertionError("grounded answer attempt loop exhausted")


def validate_grounded_answer_retry_receipt(
    value: object,
    *,
    expected_max_attempts: int | None = None,
) -> dict[str, object]:
    """Validate retry metadata and prove aggregate usage derives from every invocation."""

    if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS:
        raise ValueError("Grounded answer retry receipt does not match its schema")
    receipt = cast(dict[str, object], value)
    if (
        receipt.get("schema_version") != 2
        or receipt.get("contract") != GROUNDED_ANSWER_RETRY_CONTRACT
    ):
        raise ValueError("Grounded answer retry receipt has an unsupported contract")
    max_attempts = _required_int(receipt, "max_attempts")
    _validate_max_attempts(max_attempts)
    if expected_max_attempts is not None and max_attempts != expected_max_attempts:
        raise ValueError("Grounded answer retry receipt changes the configured attempt limit")
    raw_attempts = receipt.get("attempts")
    if not isinstance(raw_attempts, list) or not raw_attempts:
        raise ValueError("Grounded answer retry receipt has no attempts")
    attempts = tuple(
        _parse_attempt(raw_attempt, expected_index=index)
        for index, raw_attempt in enumerate(raw_attempts, start=1)
    )
    if len(attempts) > max_attempts or receipt.get("attempt_count") != len(attempts):
        raise ValueError("Grounded answer retry receipt has an invalid attempt count")
    raw_status = receipt.get("status")
    if not isinstance(raw_status, str):
        raise ValueError("Grounded answer retry receipt has an invalid status")
    try:
        status = GroundedAnswerRetryStatus(raw_status)
    except (TypeError, ValueError) as error:
        raise ValueError("Grounded answer retry receipt has an invalid status") from error
    accepted = [attempt.attempt_index for attempt in attempts if attempt.status == "accepted"]
    provider_failures = [
        attempt.attempt_index for attempt in attempts if attempt.status == "provider_failed"
    ]
    if status is GroundedAnswerRetryStatus.COMPLETED:
        valid_shape = (
            accepted == [attempts[-1].attempt_index]
            and not provider_failures
            and receipt.get("accepted_attempt_index") == accepted[0]
            and receipt.get("terminal_error_type") is None
            and receipt.get("retryable") is False
        )
    elif status is GroundedAnswerRetryStatus.CONTRACT_EXHAUSTED:
        valid_shape = (
            len(attempts) == max_attempts
            and not accepted
            and not provider_failures
            and receipt.get("accepted_attempt_index") is None
            and receipt.get("terminal_error_type") == attempts[-1].error_type
            and receipt.get("retryable") is True
        )
    else:
        valid_shape = (
            provider_failures == [attempts[-1].attempt_index]
            and not accepted
            and receipt.get("accepted_attempt_index") is None
            and receipt.get("terminal_error_type") == attempts[-1].error_type
            and receipt.get("retryable") is False
        )
    if not valid_shape:
        raise ValueError("Grounded answer retry receipt status does not match its attempts")
    raw_usage = receipt.get("usage")
    if not isinstance(raw_usage, dict) or set(raw_usage) != _USAGE_FIELDS:
        raise ValueError("Grounded answer retry receipt usage does not match its schema")
    expected_usage = _aggregate_usage(attempts)
    if raw_usage != expected_usage:
        raise ValueError("Grounded answer retry receipt usage is not derived from its attempts")
    return {
        "schema_version": 2,
        "contract": GROUNDED_ANSWER_RETRY_CONTRACT,
        "status": status.value,
        "retryable": receipt["retryable"],
        "max_attempts": max_attempts,
        "attempt_count": len(attempts),
        "accepted_attempt_index": receipt["accepted_attempt_index"],
        "terminal_error_type": receipt["terminal_error_type"],
        "attempts": [_attempt_payload(attempt) for attempt in attempts],
        "usage": expected_usage,
    }


def validate_grounded_answer_retry_history(
    value: object,
    *,
    expected_max_attempts: int,
) -> dict[str, object]:
    """Validate resumable retry batches and aggregate usage without dropping old failures."""

    if not isinstance(value, list) or not value:
        raise ValueError("Grounded answer retry history must contain at least one receipt")
    receipts = [
        validate_grounded_answer_retry_receipt(
            item,
            expected_max_attempts=expected_max_attempts,
        )
        for item in value
    ]
    if any(
        receipt.get("status") != GroundedAnswerRetryStatus.CONTRACT_EXHAUSTED.value
        for receipt in receipts[:-1]
    ):
        raise ValueError("Only exhausted contract failures may precede the final retry batch")
    final = receipts[-1]
    return {
        "schema_version": 2,
        "contract": GROUNDED_ANSWER_RETRY_HISTORY_CONTRACT,
        "batch_count": len(receipts),
        "status": final["status"],
        "retryable": final["retryable"],
        "receipts": receipts,
        "usage": _aggregate_receipt_usage(receipts),
    }


def _response_attempt(
    attempt_index: int,
    *,
    status: Literal["accepted", "contract_rejected"],
    response: ModelResponse,
    error_type: str | None,
    normalization: str | None,
) -> GroundedAnswerAttempt:
    return GroundedAnswerAttempt(
        attempt_index=attempt_index,
        status=status,
        error_type=error_type,
        model=response.model,
        response_chars=len(response.text),
        response_sha256=hashlib.sha256(response.text.encode()).hexdigest(),
        normalization=normalization,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cached_input_tokens=response.cached_input_tokens,
        uncached_input_tokens=response.uncached_input_tokens,
        reasoning_tokens=response.reasoning_tokens,
        cost_usd=response.cost_usd,
        cost_cny=response.cost_cny,
    )


def _record(
    attempts: list[GroundedAnswerAttempt],
    attempt: GroundedAnswerAttempt,
    *,
    callback: Callable[[GroundedAnswerAttempt], None] | None,
) -> None:
    attempts.append(attempt)
    if callback is not None:
        callback(attempt)


def _attempt_payload(attempt: GroundedAnswerAttempt) -> dict[str, object]:
    return {
        "attempt_index": attempt.attempt_index,
        "status": attempt.status,
        "error_type": attempt.error_type,
        "model": attempt.model,
        "response_chars": attempt.response_chars,
        "response_sha256": attempt.response_sha256,
        "normalization": attempt.normalization,
        **{field: getattr(attempt, field) for field in _INTEGER_USAGE_FIELDS},
        **{field: getattr(attempt, field) for field in _COST_USAGE_FIELDS},
    }


def _parse_attempt(value: object, *, expected_index: int) -> GroundedAnswerAttempt:
    if not isinstance(value, dict) or set(value) != _ATTEMPT_FIELDS:
        raise ValueError("Grounded answer attempt does not match its schema")
    attempt = cast(dict[str, object], value)
    attempt_index = _required_int(attempt, "attempt_index")
    status = attempt.get("status")
    error_type = attempt.get("error_type")
    model = attempt.get("model")
    response_chars = attempt.get("response_chars")
    response_sha256 = attempt.get("response_sha256")
    normalization = attempt.get("normalization")
    if attempt_index != expected_index or status not in {
        "accepted",
        "contract_rejected",
        "provider_failed",
    }:
        raise ValueError("Grounded answer attempt has an invalid identity or status")
    if status == "provider_failed":
        if (
            not isinstance(error_type, str)
            or not error_type
            or model is not None
            or response_chars is not None
            or response_sha256 is not None
            or normalization is not None
        ):
            raise ValueError("Grounded answer provider failure has invalid metadata")
    elif (
        (status == "accepted" and error_type is not None)
        or (status == "contract_rejected" and (not isinstance(error_type, str) or not error_type))
        or (
            status == "accepted"
            and normalization is not None
            and (
                not isinstance(normalization, str)
                or normalization
                not in {
                    INSUFFICIENT_CITATIONS_NORMALIZATION,
                    INSUFFICIENT_EMPTY_ANSWER_NORMALIZATION,
                }
            )
        )
        or (status == "contract_rejected" and normalization is not None)
        or not isinstance(model, str)
        or not model
        or type(response_chars) is not int
        or response_chars < 0
        or not isinstance(response_sha256, str)
        or len(response_sha256) != 64
        or any(character not in "0123456789abcdef" for character in response_sha256)
    ):
        raise ValueError("Grounded answer response attempt has invalid metadata")
    return GroundedAnswerAttempt(
        attempt_index=attempt_index,
        status=cast(AnswerAttemptStatus, status),
        error_type=cast(str | None, error_type),
        model=model,
        response_chars=response_chars,
        response_sha256=response_sha256,
        normalization=cast(str | None, normalization),
        input_tokens=_optional_nonnegative_int(attempt.get("input_tokens"), field="input_tokens"),
        output_tokens=_optional_nonnegative_int(
            attempt.get("output_tokens"), field="output_tokens"
        ),
        cached_input_tokens=_optional_nonnegative_int(
            attempt.get("cached_input_tokens"), field="cached_input_tokens"
        ),
        uncached_input_tokens=_optional_nonnegative_int(
            attempt.get("uncached_input_tokens"), field="uncached_input_tokens"
        ),
        reasoning_tokens=_optional_nonnegative_int(
            attempt.get("reasoning_tokens"), field="reasoning_tokens"
        ),
        cost_usd=_optional_nonnegative_number(attempt.get("cost_usd"), field="cost_usd"),
        cost_cny=_optional_nonnegative_number(attempt.get("cost_cny"), field="cost_cny"),
    )


def _aggregate_usage(attempts: tuple[GroundedAnswerAttempt, ...]) -> dict[str, object]:
    usage: dict[str, object] = {
        "call_count": len(attempts),
        "response_count": sum(attempt.status != "provider_failed" for attempt in attempts),
    }
    for field in (*_INTEGER_USAGE_FIELDS, *_COST_USAGE_FIELDS):
        values = [getattr(attempt, field) for attempt in attempts]
        known_values = [value for value in values if value is not None]
        usage[field] = None if not known_values else sum(known_values)
        usage[_KNOWN_COUNT_BY_FIELD[field]] = len(known_values)
    return usage


def _aggregate_receipt_usage(receipts: list[dict[str, object]]) -> dict[str, object]:
    aggregate: dict[str, object] = {"call_count": 0, "response_count": 0}
    for field in (*_INTEGER_USAGE_FIELDS, *_COST_USAGE_FIELDS):
        aggregate[field] = None
        aggregate[_KNOWN_COUNT_BY_FIELD[field]] = 0
    for receipt in receipts:
        usage = cast(dict[str, object], receipt["usage"])
        aggregate["call_count"] = cast(int, aggregate["call_count"]) + cast(
            int, usage["call_count"]
        )
        aggregate["response_count"] = cast(int, aggregate["response_count"]) + cast(
            int, usage["response_count"]
        )
        for field in (*_INTEGER_USAGE_FIELDS, *_COST_USAGE_FIELDS):
            known_field = _KNOWN_COUNT_BY_FIELD[field]
            observed = usage[field]
            if observed is not None:
                prior = aggregate[field]
                aggregate[field] = (
                    observed
                    if prior is None
                    else cast(int | float, prior) + cast(int | float, observed)
                )
            aggregate[known_field] = cast(int, aggregate[known_field]) + cast(
                int, usage[known_field]
            )
    return aggregate


def _validate_max_attempts(value: int) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_APPLICATION_ATTEMPTS:
        raise ValueError(
            f"Grounded answer max_attempts must be between 1 and {_MAX_APPLICATION_ATTEMPTS}"
        )


def _required_int(value: dict[str, object], field: str) -> int:
    result = value.get(field)
    if type(result) is not int:
        raise ValueError(f"Grounded answer retry {field} must be an integer")
    return result


def _optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"Grounded answer retry {field} must be a non-negative integer")
    return value


def _optional_nonnegative_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Grounded answer retry {field} must be a non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Grounded answer retry {field} must be a non-negative number")
    return result
