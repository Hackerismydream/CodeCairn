from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, write_json_exclusive
from codecairn.evaluation.locomo_repair import (
    LoCoMoRepairConfig,
    build_locomo_repair_report,
)


def test_repair_report_reuses_only_scored_base_questions_and_replaces_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_ids = ["q-1", "q-2", "q-3"]
    repair_ids = ["q-2"]
    target_question_set = _write_question_set(
        tmp_path / "target.json",
        selection_id="target",
        question_ids=target_ids,
        category_targets={"1": 2, "2": 1},
    )
    repair_question_set = _write_question_set(
        tmp_path / "repair.json",
        selection_id="repair",
        question_ids=repair_ids,
        category_targets={"1": 1},
    )
    base_run = _write_run(
        tmp_path / "base",
        run_id="base",
        repository_commit="a" * 40,
        question_set=target_question_set,
        question_ids_by_conversation={"conv-1": target_ids},
        statuses={"q-1": "completed", "q-2": "infrastructure_failed", "q-3": "completed"},
    )
    repair_run = _write_run(
        tmp_path / "repair-run",
        run_id="repair",
        repository_commit="b" * 40,
        question_set=repair_question_set,
        question_ids_by_conversation={"conv-1": repair_ids},
        statuses={"q-2": "completed"},
    )
    reports = {
        "base": _report(
            run_id="base",
            scored=2,
            failed=1,
            correct=1,
            by_category={
                "1": {"name": "multi-hop", "correct": 0, "count": 1, "accuracy": 0.0},
                "2": {"name": "temporal", "correct": 1, "count": 1, "accuracy": 1.0},
            },
            cost_cny=1.25,
        ),
        "repair": _report(
            run_id="repair",
            scored=1,
            failed=0,
            correct=1,
            by_category={"1": {"name": "multi-hop", "correct": 1, "count": 1, "accuracy": 1.0}},
            cost_cny=0.5,
        ),
    }
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_repair.report_locomo",
        lambda run_dir: reports[run_dir.name.removesuffix("-run")],
    )
    output = tmp_path / "composite.json"

    report = build_locomo_repair_report(
        LoCoMoRepairConfig(
            target_question_set_path=target_question_set,
            repair_question_set_path=repair_question_set,
            base_run=base_run,
            repair_run=repair_run,
            output_path=output,
        )
    )

    assert report["formal_score"] is True
    assert report["question_count"] == 3
    assert report["scored_question_count"] == 3
    assert report["infrastructure_failed_count"] == 0
    assert report["correct_count"] == 2
    assert report["accuracy"] == pytest.approx(2 / 3, abs=1e-6)
    assert report["by_category"]["1"] == {
        "name": "multi-hop",
        "correct": 1,
        "count": 2,
        "accuracy": 0.5,
    }
    assert report["usage"]["cost_cny"] == 1.75
    assert report["sources"]["base"]["reused_scored_question_count"] == 2
    assert report["sources"]["repair"]["replacement_question_count"] == 1
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_repair_report_rejects_a_repair_selection_that_is_not_the_base_failure_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _write_question_set(
        tmp_path / "target.json",
        selection_id="target",
        question_ids=["q-1", "q-2"],
        category_targets={"1": 2},
    )
    repair = _write_question_set(
        tmp_path / "repair.json",
        selection_id="repair",
        question_ids=["q-1"],
        category_targets={"1": 1},
    )
    base_run = _write_run(
        tmp_path / "base",
        run_id="base",
        repository_commit="a" * 40,
        question_set=target,
        question_ids_by_conversation={"conv-1": ["q-1", "q-2"]},
        statuses={"q-1": "completed", "q-2": "infrastructure_failed"},
    )
    repair_run = _write_run(
        tmp_path / "repair-run",
        run_id="repair",
        repository_commit="b" * 40,
        question_set=repair,
        question_ids_by_conversation={"conv-1": ["q-1"]},
        statuses={"q-1": "completed"},
    )
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_repair.report_locomo",
        lambda run_dir: _report(
            run_id=run_dir.name.removesuffix("-run"),
            scored=1,
            failed=1 if run_dir == base_run else 0,
            correct=1,
            by_category={"1": {"name": "multi-hop", "correct": 1, "count": 1, "accuracy": 1.0}},
            cost_cny=0.1,
        ),
    )

    with pytest.raises(ValueError, match="exactly replace"):
        build_locomo_repair_report(
            LoCoMoRepairConfig(
                target_question_set_path=target,
                repair_question_set_path=repair,
                base_run=base_run,
                repair_run=repair_run,
                output_path=tmp_path / "composite.json",
            )
        )


def test_repair_report_rejects_a_question_set_protocol_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _write_question_set(
        tmp_path / "target.json",
        selection_id="target",
        question_ids=["q-1", "q-2"],
        category_targets={"1": 2},
    )
    repair = _write_question_set(
        tmp_path / "repair.json",
        selection_id="repair",
        question_ids=["q-2"],
        category_targets={"1": 1},
    )
    repair_definition = json.loads(repair.read_text(encoding="utf-8"))
    repair_definition["protocol"] = {"contract": "changed"}
    repair.unlink()
    write_json_exclusive(repair, repair_definition)
    base_run = _write_run(
        tmp_path / "base",
        run_id="base",
        repository_commit="a" * 40,
        question_set=target,
        question_ids_by_conversation={"conv-1": ["q-1", "q-2"]},
        statuses={"q-1": "completed", "q-2": "infrastructure_failed"},
    )
    repair_run = _write_run(
        tmp_path / "repair-run",
        run_id="repair",
        repository_commit="b" * 40,
        question_set=repair,
        question_ids_by_conversation={"conv-1": ["q-2"]},
        statuses={"q-2": "completed"},
    )
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_repair.report_locomo",
        lambda run_dir: _report(
            run_id=run_dir.name.removesuffix("-run"),
            scored=1,
            failed=1 if run_dir == base_run else 0,
            correct=1,
            by_category={"1": {"name": "multi-hop", "correct": 1, "count": 1, "accuracy": 1.0}},
            cost_cny=0.1,
        ),
    )

    with pytest.raises(ValueError, match="frozen question-set protocol"):
        build_locomo_repair_report(
            LoCoMoRepairConfig(
                target_question_set_path=target,
                repair_question_set_path=repair,
                base_run=base_run,
                repair_run=repair_run,
                output_path=tmp_path / "composite.json",
            )
        )


def _write_question_set(
    path: Path,
    *,
    selection_id: str,
    question_ids: list[str],
    category_targets: dict[str, int],
) -> Path:
    selection_sha256 = hashlib.sha256(
        json.dumps(sorted(question_ids), ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    write_json_exclusive(
        path,
        {
            "schema_version": 1,
            "selection_id": selection_id,
            "dataset_sha256": "d" * 64,
            "algorithm": "explicit-question-ids-v1",
            "seed": "test",
            "category_targets": category_targets,
            "question_ids": question_ids,
            "selection_sha256": selection_sha256,
            "protocol": {"contract": "same"},
        },
    )
    return path


def _write_run(
    path: Path,
    *,
    run_id: str,
    repository_commit: str,
    question_set: Path,
    question_ids_by_conversation: dict[str, list[str]],
    statuses: dict[str, str],
) -> Path:
    path.mkdir()
    definition = json.loads(question_set.read_text(encoding="utf-8"))
    all_ids = [
        question_id
        for question_ids in question_ids_by_conversation.values()
        for question_id in question_ids
    ]
    question_set_manifest = {
        "selection_id": definition["selection_id"],
        "definition_sha256": file_sha256(question_set),
        "dataset_sha256": definition["dataset_sha256"],
        "algorithm": definition["algorithm"],
        "seed": definition["seed"],
        "category_targets": definition["category_targets"],
        "question_count": len(all_ids),
        "question_ids": all_ids,
        "selection_sha256": definition["selection_sha256"],
        "protocol_sha256": canonical_sha256(definition["protocol"]),
    }
    write_json_exclusive(
        path / "manifest.json",
        {
            "schema_version": 1,
            "suite": "locomo",
            "run_id": run_id,
            "mode": "full",
            "scored": True,
            "repository_commit": repository_commit,
            "dataset": {"sha256": "d" * 64},
            "selection": {
                "conversation_ids": list(question_ids_by_conversation),
                "question_ids_by_conversation": question_ids_by_conversation,
                "question_set": question_set_manifest,
            },
            "retrieval": {"method": "same", "planner": {"mode": "hierarchy-no-neighbors"}},
            "corpus": {"content_sha256": "c" * 64},
            "query_vectors": {
                "content_sha256": "e" * 64,
                "coverage": "exact",
                "artifact_question_count": len(all_ids),
                "run_question_count": len(all_ids),
                "run_selection_sha256": definition["selection_sha256"],
                "selection_sha256": "f" * 64,
            },
            "answer_model": {"model": "answer"},
            "judge_model": {"model": "judge"},
            "answer_evidence_contract": "answer-contract",
            "answer_retry_contract": "answer-retry",
            "answer_response_max_attempts": 2,
            "judge_contract": "judge-contract",
            "judge_votes": 1,
            "judge_response_max_attempts": 2,
            "judge_response_max_chars": 100,
            "model_attempt_journal_contract": "journal-contract",
            "checkpoint_policy": "checkpoint-contract",
            "seed": 17,
            "max_workers": 10,
            "ingest_max_workers": 1,
            "retrieval_max_workers": 1,
            "retrieval_thread_count": 1,
            "execution_phase_contract": "worker-contract",
            "question_worker": {"name": "worker-contract", "max_rss_bytes": 100},
        },
    )
    for question_id, status in statuses.items():
        question_path = path / "checkpoints" / "questions" / "conv-1" / f"{question_id}.json"
        write_json_exclusive(
            question_path,
            {
                "sample_id": "conv-1",
                "question_id": question_id,
                "category": 1,
                "status": status,
                "judge_votes": (
                    [
                        {
                            "vote_index": 0,
                            "attempt_count": 1,
                            "failed_attempts": [],
                            "response_chars": 2,
                            "raw_response": "{}",
                            "label": "correct",
                            "cost_usd": None,
                            "known_cost_count": 0,
                            "cost_cny": 0.1,
                            "known_cost_cny_count": 1,
                        }
                    ]
                    if status == "completed"
                    else []
                ),
            },
        )
    return path


def _report(
    *,
    run_id: str,
    scored: int,
    failed: int,
    correct: int,
    by_category: dict[str, dict[str, object]],
    cost_cny: float,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": run_id,
        "mode": "full",
        "scored": True,
        "question_artifact_count": scored + failed,
        "completed_question_count": scored,
        "scored_question_count": scored,
        "infrastructure_failed_count": failed,
        "correct_count": correct,
        "accuracy": round(correct / scored, 6),
        "by_category": by_category,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "known_cost_count": 0,
            "cost_usd": None,
            "known_cost_cny_count": scored,
            "cost_cny": cost_cny,
        },
        "unscored_reason": None,
    }
