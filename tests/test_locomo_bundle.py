from __future__ import annotations

from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo_bundle import report_locomo_composite_evidence


def test_public_composite_recomputes_exact_repair_score(tmp_path: Path) -> None:
    source = _write_public_composite(tmp_path / "locomo")

    report = report_locomo_composite_evidence(source)

    assert report["scored_question_count"] == 2
    assert report["infrastructure_failed_count"] == 0
    assert report["correct_count"] == 1
    assert report["accuracy"] == 0.5
    assert report["usage"]["cost_cny"] == 3.0
    assert report["by_category"]["1"] == {
        "name": "multi-hop",
        "correct": 1,
        "count": 2,
        "accuracy": 0.5,
    }


def test_public_composite_rejects_a_final_outcome_that_changes_its_source(
    tmp_path: Path,
) -> None:
    source = _write_public_composite(tmp_path / "locomo")
    outcome_path = source / "checkpoints" / "questions" / "conv-1" / "q-2.json"
    outcome = read_json(outcome_path)
    assert isinstance(outcome, dict)
    outcome["outcome"] = "correct"
    outcome_path.unlink()
    write_json_exclusive(outcome_path, outcome)

    with pytest.raises(ValueError, match="changes its source"):
        report_locomo_composite_evidence(source)


def _write_public_composite(root: Path) -> Path:
    target_definition = root / "target-question-set.json"
    repair_definition = root / "repair-question-set.json"
    write_json_exclusive(
        target_definition,
        {"schema_version": 1, "selection_id": "target", "protocol": {"same": True}},
    )
    write_json_exclusive(
        repair_definition,
        {
            "schema_version": 1,
            "selection_id": "repair",
            "question_ids": ["q-2"],
            "protocol": {"same": True},
        },
    )
    base_receipt = {
        "run_id": "base",
        "manifest_sha256": "a" * 64,
        "report_sha256": "b" * 64,
    }
    repair_receipt = {
        "run_id": "repair",
        "manifest_sha256": "c" * 64,
        "report_sha256": "d" * 64,
    }
    composite = {
        "schema_version": 1,
        "suite": "locomo-repair-composite",
        "contract": "failed-question-exact-replacement-v1",
        "formal_score": True,
        "model_output_scoring_contract": "contract-exhausted-answer-is-wrong-v1",
        "question_count": 2,
        "scored_question_count": 2,
        "infrastructure_failed_count": 0,
        "correct_count": 1,
        "accuracy": 0.5,
        "by_category": {
            "1": {
                "name": "multi-hop",
                "correct": 1,
                "count": 2,
                "accuracy": 0.5,
            }
        },
        "usage": {"cost_cny": 3.0, "input_tokens": 30},
        "target": {
            "selection_id": "target",
            "question_set_sha256": file_sha256(target_definition),
        },
        "repair_selection": {
            "selection_id": "repair",
            "question_set_sha256": file_sha256(repair_definition),
        },
        "sources": {"base": base_receipt, "repair": repair_receipt},
    }
    write_json_exclusive(root / "composite.json", composite)
    write_json_exclusive(
        root / "manifest.json",
        {
            "schema_version": 1,
            "suite": "locomo-public-composite",
            "contract": "public-exact-repair-outcomes-v1",
            "run_id": "target-composite",
            "judge_votes": 3,
        },
    )
    _write_public_source(
        root / "sources" / "base",
        manifest_sha256=base_receipt["manifest_sha256"],
        report_sha256=base_receipt["report_sha256"],
        outcomes={
            "q-1": _outcome("q-1", "correct"),
            "q-2": _outcome("q-2", "infrastructure_failed"),
        },
        report={
            "scored_question_count": 1,
            "infrastructure_failed_count": 1,
            "correct_count": 1,
            "accuracy": 1.0,
            "by_category": {
                "1": {
                    "name": "multi-hop",
                    "correct": 1,
                    "count": 1,
                    "accuracy": 1.0,
                }
            },
            "usage": {"cost_cny": 1.0, "input_tokens": 10},
        },
    )
    _write_public_source(
        root / "sources" / "repair",
        manifest_sha256=repair_receipt["manifest_sha256"],
        report_sha256=repair_receipt["report_sha256"],
        outcomes={"q-2": _outcome("q-2", "wrong")},
        report={
            "scored_question_count": 1,
            "infrastructure_failed_count": 0,
            "correct_count": 0,
            "accuracy": 0.0,
            "by_category": {
                "1": {
                    "name": "multi-hop",
                    "correct": 0,
                    "count": 1,
                    "accuracy": 0.0,
                }
            },
            "usage": {"cost_cny": 2.0, "input_tokens": 20},
        },
    )
    q1 = _outcome("q-1", "correct")
    q1["source"] = "base"
    q2 = _outcome("q-2", "wrong")
    q2["source"] = "repair"
    write_json_exclusive(root / "checkpoints" / "questions" / "conv-1" / "q-1.json", q1)
    write_json_exclusive(root / "checkpoints" / "questions" / "conv-1" / "q-2.json", q2)
    return root


def _write_public_source(
    root: Path,
    *,
    manifest_sha256: str,
    report_sha256: str,
    outcomes: dict[str, dict[str, object]],
    report: dict[str, object],
) -> None:
    write_json_exclusive(
        root / "manifest.json",
        {"source_manifest_sha256": manifest_sha256},
    )
    write_json_exclusive(
        root / "report.json",
        {**report, "source_report_sha256": report_sha256},
    )
    for question_id, outcome in outcomes.items():
        write_json_exclusive(
            root / "questions" / "conv-1" / f"{question_id}.json",
            outcome,
        )


def _outcome(question_id: str, outcome: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sample_id": "conv-1",
        "question_id": question_id,
        "category": 1,
        "category_name": "multi-hop",
        "outcome": outcome,
        "source_artifact_sha256": "f" * 64,
    }
