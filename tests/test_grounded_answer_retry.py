from __future__ import annotations

import json

import pytest

from codecairn.evaluation.answer_retry import (
    GroundedAnswerRetryStatus,
    run_grounded_answer_attempts,
    validate_grounded_answer_retry_history,
    validate_grounded_answer_retry_receipt,
)
from codecairn.evaluation.grounded_answer import GroundedContext, RenderedEvidence
from codecairn.evaluation.model import ModelResponse


def test_malformed_answer_is_retried_once_and_every_response_is_accounted() -> None:
    responses = iter(
        (
            ModelResponse(
                text="not-json",
                model="fixture-answer",
                input_tokens=20,
                output_tokens=2,
                cached_input_tokens=5,
                uncached_input_tokens=15,
                cost_cny=0.001,
            ),
            ModelResponse(
                text=json.dumps(
                    {
                        "answer": "A beagle named Poppy.",
                        "supporting_evidence_ids": ["fact-poppy"],
                        "insufficient": False,
                    }
                ),
                model="fixture-answer",
                input_tokens=22,
                output_tokens=9,
                cached_input_tokens=7,
                uncached_input_tokens=15,
                cost_cny=0.002,
            ),
        )
    )
    recorded: list[int] = []

    def generate(attempt_index: int) -> ModelResponse:
        assert recorded == list(range(1, attempt_index))
        return next(responses)

    result = run_grounded_answer_attempts(
        generate=generate,
        context=_context(),
        max_attempts=2,
        record_attempt=lambda attempt: recorded.append(attempt.attempt_index),
    )

    assert result.status is GroundedAnswerRetryStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.answer == "A beagle named Poppy."
    assert result.response is not None
    assert result.response.text.startswith("{")
    assert recorded == [1, 2]
    assert result.receipt["attempt_count"] == 2
    assert result.receipt["accepted_attempt_index"] == 2
    assert result.receipt["usage"] == {
        "call_count": 2,
        "response_count": 2,
        "input_tokens": 42,
        "known_input_tokens_count": 2,
        "output_tokens": 11,
        "known_output_tokens_count": 2,
        "cached_input_tokens": 12,
        "known_cached_input_tokens_count": 2,
        "uncached_input_tokens": 30,
        "known_uncached_input_tokens_count": 2,
        "reasoning_tokens": None,
        "known_reasoning_tokens_count": 0,
        "cost_usd": None,
        "known_cost_count": 0,
        "cost_cny": 0.003,
        "known_cost_cny_count": 2,
    }
    assert (
        validate_grounded_answer_retry_receipt(
            result.receipt,
            expected_max_attempts=2,
        )
        == result.receipt
    )


def test_insufficient_answer_with_citations_is_safely_normalized_without_retry() -> None:
    calls = 0
    response = ModelResponse(
        text=json.dumps(
            {
                "answer": "The context does not specify the answer.",
                "supporting_evidence_ids": ["fact-poppy"],
                "insufficient": True,
            }
        ),
        model="fixture-answer",
        input_tokens=10,
        output_tokens=8,
        cost_cny=0.001,
    )

    def generate(_attempt_index: int) -> ModelResponse:
        nonlocal calls
        calls += 1
        return response

    result = run_grounded_answer_attempts(
        generate=generate,
        context=_context(),
        max_attempts=2,
    )

    assert calls == 1
    assert result.status is GroundedAnswerRetryStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.answer == "The context does not specify the answer."
    assert result.answer.insufficient is True
    assert result.answer.supporting_evidence_ids == ()
    assert result.receipt["attempt_count"] == 1
    attempts = result.receipt["attempts"]
    assert isinstance(attempts, list)
    assert attempts[0]["normalization"] == "insufficient-citations-removed-v1"
    assert validate_grounded_answer_retry_receipt(result.receipt) == result.receipt


def test_insufficient_answer_with_empty_list_is_safely_normalized_without_retry() -> None:
    response = ModelResponse(
        text=json.dumps(
            {
                "answer": [],
                "supporting_evidence_ids": [],
                "insufficient": True,
            }
        ),
        model="fixture-answer",
        input_tokens=10,
        output_tokens=4,
        cost_cny=0.001,
    )

    result = run_grounded_answer_attempts(
        generate=lambda _attempt_index: response,
        context=_context(),
        max_attempts=2,
    )

    assert result.status is GroundedAnswerRetryStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.answer == "The context is insufficient."
    assert result.answer.insufficient is True
    assert result.answer.supporting_evidence_ids == ()
    assert result.receipt["attempt_count"] == 1
    attempts = result.receipt["attempts"]
    assert isinstance(attempts, list)
    assert attempts[0]["normalization"] == "insufficient-empty-answer-replaced-v1"
    assert validate_grounded_answer_retry_receipt(result.receipt) == result.receipt


@pytest.mark.parametrize("normalization", ("invented-normalization-v1", []))
def test_retry_receipt_rejects_an_unknown_answer_normalization(
    normalization: object,
) -> None:
    response = ModelResponse(
        text=json.dumps(
            {
                "answer": "The context is insufficient.",
                "supporting_evidence_ids": [],
                "insufficient": True,
            }
        ),
        model="fixture-answer",
    )
    result = run_grounded_answer_attempts(
        generate=lambda _attempt_index: response,
        context=_context(),
    )
    tampered = json.loads(json.dumps(result.receipt))
    tampered["attempts"][0]["normalization"] = normalization

    with pytest.raises(ValueError, match="invalid metadata"):
        validate_grounded_answer_retry_receipt(tampered)


def test_exhausted_citation_failures_remain_retryable_and_keep_all_usage() -> None:
    response = ModelResponse(
        text=json.dumps(
            {
                "answer": "Poppy.",
                "supporting_evidence_ids": ["fact-forged"],
                "insufficient": False,
            }
        ),
        model="fixture-answer",
        input_tokens=10,
        output_tokens=4,
        cost_usd=0.01,
    )

    result = run_grounded_answer_attempts(
        generate=lambda _attempt_index: response,
        context=_context(),
        max_attempts=2,
    )

    assert result.status is GroundedAnswerRetryStatus.CONTRACT_EXHAUSTED
    assert result.retryable is True
    assert result.answer is None
    assert result.response is None
    assert result.receipt["terminal_error_type"] == "ValueError"
    attempts = result.receipt["attempts"]
    assert isinstance(attempts, list)
    assert [attempt["status"] for attempt in attempts] == [
        "contract_rejected",
        "contract_rejected",
    ]
    assert result.receipt["usage"] == {
        "call_count": 2,
        "response_count": 2,
        "input_tokens": 20,
        "known_input_tokens_count": 2,
        "output_tokens": 8,
        "known_output_tokens_count": 2,
        "cached_input_tokens": None,
        "known_cached_input_tokens_count": 0,
        "uncached_input_tokens": None,
        "known_uncached_input_tokens_count": 0,
        "reasoning_tokens": None,
        "known_reasoning_tokens_count": 0,
        "cost_usd": 0.02,
        "known_cost_count": 2,
        "cost_cny": None,
        "known_cost_cny_count": 0,
    }


def test_provider_failure_is_not_retried_by_the_contract_layer() -> None:
    calls = 0

    def generate(_attempt_index: int) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                text="not-json",
                model="fixture-answer",
                input_tokens=10,
                output_tokens=1,
            )
        raise TimeoutError("provider adapter exhausted its own transport retries")

    result = run_grounded_answer_attempts(
        generate=generate,
        context=_context(),
        max_attempts=2,
    )

    assert calls == 2
    assert result.status is GroundedAnswerRetryStatus.PROVIDER_FAILED
    assert result.retryable is False
    assert result.receipt["terminal_error_type"] == "TimeoutError"
    assert result.receipt["attempt_count"] == 2
    usage = result.receipt["usage"]
    assert isinstance(usage, dict)
    assert usage["call_count"] == 2
    assert usage["response_count"] == 1
    assert usage["input_tokens"] == 10
    assert usage["known_input_tokens_count"] == 1


def test_receipt_validation_rejects_usage_that_drops_a_paid_failed_attempt() -> None:
    response = ModelResponse(
        text="not-json",
        model="fixture-answer",
        input_tokens=10,
        output_tokens=1,
        cost_cny=0.001,
    )
    result = run_grounded_answer_attempts(
        generate=lambda _attempt_index: response,
        context=_context(),
        max_attempts=2,
    )
    tampered = json.loads(json.dumps(result.receipt))
    tampered["usage"]["input_tokens"] = 10
    tampered["usage"]["known_input_tokens_count"] = 1
    tampered["usage"]["cost_cny"] = 0.001
    tampered["usage"]["known_cost_cny_count"] = 1

    with pytest.raises(ValueError, match="not derived from its attempts"):
        validate_grounded_answer_retry_receipt(tampered, expected_max_attempts=2)


def test_retry_history_keeps_exhausted_usage_when_resume_later_succeeds() -> None:
    rejected = ModelResponse(
        text="not-json",
        model="fixture-answer",
        input_tokens=10,
        output_tokens=1,
        cost_cny=0.001,
    )
    exhausted = run_grounded_answer_attempts(
        generate=lambda _attempt_index: rejected,
        context=_context(),
        max_attempts=2,
    )
    accepted = ModelResponse(
        text=json.dumps(
            {
                "answer": "Poppy.",
                "supporting_evidence_ids": ["fact-poppy"],
                "insufficient": False,
            }
        ),
        model="fixture-answer",
        input_tokens=11,
        output_tokens=4,
        cost_cny=0.002,
    )
    completed = run_grounded_answer_attempts(
        generate=lambda _attempt_index: accepted,
        context=_context(),
        max_attempts=2,
    )

    history = validate_grounded_answer_retry_history(
        [exhausted.receipt, completed.receipt],
        expected_max_attempts=2,
    )

    assert history["status"] == "completed"
    assert history["retryable"] is False
    usage = history["usage"]
    assert isinstance(usage, dict)
    assert usage["call_count"] == 3
    assert usage["response_count"] == 3
    assert usage["input_tokens"] == 31
    assert usage["known_input_tokens_count"] == 3
    assert usage["cost_cny"] == pytest.approx(0.004)
    assert usage["known_cost_cny_count"] == 3


def _context() -> GroundedContext:
    return GroundedContext(
        markdown="# Recall Context\n\n- Poppy is a beagle.\n",
        evidence=(
            RenderedEvidence(
                source_fact_id="fact-poppy",
                text="Poppy is a beagle.",
                source_uri="locomo://conversation/session#1",
            ),
        ),
        token_count=12,
        token_limit=4_000,
    )
