from __future__ import annotations

import pytest

from codecairn.evaluation.artifacts import canonical_sha256
from codecairn.locomo_worker import _validate_worker_paid_scoring_preflight


def test_worker_accepts_a_manifest_bound_paid_scoring_receipt() -> None:
    raw, manifest = _paid_worker_contract()

    _validate_worker_paid_scoring_preflight(raw, manifest, mode="full")


def test_worker_rejects_a_paid_scoring_receipt_removed_from_its_spec() -> None:
    raw, manifest = _paid_worker_contract()
    raw["paid_scoring_preflight_sha256"] = None

    with pytest.raises(ValueError, match="paid-scoring preflight"):
        _validate_worker_paid_scoring_preflight(raw, manifest, mode="full")


def test_worker_rejects_a_v18_manifest_with_no_paid_scoring_receipt() -> None:
    raw, manifest = _paid_worker_contract()
    raw["paid_scoring_preflight_sha256"] = None
    manifest["paid_scoring_preflight"] = None

    with pytest.raises(ValueError, match="paid-scoring preflight"):
        _validate_worker_paid_scoring_preflight(raw, manifest, mode="full")


def test_retrieval_worker_rejects_a_paid_scoring_receipt() -> None:
    raw, manifest = _paid_worker_contract()

    with pytest.raises(ValueError, match="retrieval mode"):
        _validate_worker_paid_scoring_preflight(raw, manifest, mode="retrieval")


def test_worker_rejects_a_self_hashed_receipt_with_an_incomplete_schema() -> None:
    raw, manifest = _paid_worker_contract()
    receipt = manifest["paid_scoring_preflight"]
    assert isinstance(receipt, dict)
    receipt.pop("sources")
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    raw["paid_scoring_preflight_sha256"] = receipt["receipt_sha256"]

    with pytest.raises(ValueError, match="receipt schema"):
        _validate_worker_paid_scoring_preflight(raw, manifest, mode="full")


def _paid_worker_contract() -> tuple[dict[str, object], dict[str, object]]:
    protocol_sha256 = "e" * 64
    receipt: dict[str, object] = {
        "schema_version": 1,
        "contract": "dual-retrieval-context-coverage-v1",
        "repository_commit": "abc123",
        "dataset_sha256": "d" * 64,
        "target_question_set_sha256": "a" * 64,
        "target_selection_sha256": "b" * 64,
        "target_question_count": 200,
        "scored_question_set_sha256": "a" * 64,
        "scored_selection_sha256": "b" * 64,
        "scored_question_count": 200,
        "protocol_sha256": protocol_sha256,
        "corpus_content_sha256": "c" * 64,
        "query_vectors_content_sha256": "d" * 64,
        "minimum_context_all_coverage": 0.85,
        "maximum_context_tokens": 4_000,
        "maximum_retrieval_p95_ms": 2_500.0,
        "maximum_process_rss_bytes_exclusive": 2 * 1024 * 1024 * 1024,
        "sources": [
            _paid_source(
                run_id="canary",
                selection_id="canary",
                question_set_sha256="1" * 64,
                selection_sha256="2" * 64,
                question_count=40,
            ),
            _paid_source(
                run_id="holdout",
                selection_id="holdout",
                question_set_sha256="3" * 64,
                selection_sha256="4" * 64,
                question_count=160,
            ),
        ],
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return (
        {
            "paid_scoring_preflight_sha256": receipt["receipt_sha256"],
        },
        {
            "repository_commit": "abc123",
            "paid_scoring_gate": "dual-retrieval-context-coverage-v1",
            "selection": {
                "question_set": {
                    "dataset_sha256": "d" * 64,
                    "definition_sha256": "a" * 64,
                    "selection_sha256": "b" * 64,
                    "question_count": 200,
                    "protocol_sha256": protocol_sha256,
                }
            },
            "corpus": {"content_sha256": "c" * 64},
            "query_vectors": {"content_sha256": "d" * 64},
            "paid_scoring_preflight": receipt,
        },
    )


def _paid_source(
    *,
    run_id: str,
    selection_id: str,
    question_set_sha256: str,
    selection_sha256: str,
    question_count: int,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "selection_id": selection_id,
        "question_set_sha256": question_set_sha256,
        "selection_sha256": selection_sha256,
        "question_count": question_count,
        "context_all_coverage": 0.9,
        "maximum_context_tokens": 4_000,
        "retrieval_p95_ms": 2_000.0,
        "max_process_rss_bytes": 1_000_000_000,
        "manifest_sha256": "5" * 64,
        "summary_sha256": "6" * 64,
        "evidence_report_sha256": "7" * 64,
        "resource_usage_sha256": "8" * 64,
    }
