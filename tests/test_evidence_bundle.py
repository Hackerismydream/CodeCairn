from __future__ import annotations

from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import write_json_exclusive
from codecairn.evaluation.coding import report_coding_runs
from codecairn.evaluation.evidence_bundle import (
    EvidenceBundleConfig,
    build_evidence_bundle,
    verify_evidence_bundle,
)
from codecairn.evaluation.locomo import report_locomo
from codecairn.evaluation.retrieval import report_recovery, report_retrieval


def test_bundle_recomputes_metrics_copy_and_hashes_from_public_artifacts(
    tmp_path: Path,
) -> None:
    sources = _make_source_runs(tmp_path / "sources")
    output_root = tmp_path / "evidence"

    artifact = build_evidence_bundle(
        EvidenceBundleConfig(
            bundle_id="benchmark-test",
            output_root=output_root,
            locomo_run_dir=sources / "locomo",
            retrieval_run_dir=sources / "retrieval",
            recovery_run_dir=sources / "recovery",
            coding_run_dir=sources / "coding",
            quality_junit_path=sources / "junit.xml",
            quality_coverage_path=sources / "coverage.json",
            repository_root=Path(__file__).parents[1],
            generator_commit="abc123",
        )
    )

    counts = artifact.metrics["counts"]
    assert isinstance(counts, dict)
    assert counts == {
        "accepted_memory_count": 2,
        "coding_event_count": 5,
        "coding_file_change_count": 2,
        "coding_run_count": 2,
        "coding_tool_call_count": 2,
        "coding_trace_count": 2,
        "coding_verifier_count": 2,
        "locomo_conversation_count": 1,
        "locomo_question_run_count": 1,
        "locomo_session_count": 1,
        "locomo_turn_count": 2,
        "rejected_memory_count": 0,
        "retrieval_query_count": 1,
    }
    assert verify_evidence_bundle(artifact.bundle_dir)["verified"] is True
    assert "LoCoMo accuracy: pending" in (artifact.bundle_dir / "resume.md").read_text()
    assert "由 0% 提升至 100%" in (artifact.bundle_dir / "resume.zh-CN.md").read_text()
    assert not (artifact.bundle_dir / "raw" / "locomo" / "runtime").exists()

    query = artifact.bundle_dir / "raw" / "retrieval" / "queries" / "q-1.json"
    query.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_evidence_bundle(artifact.bundle_dir)


def _make_source_runs(root: Path) -> Path:
    locomo = root / "locomo"
    write_json_exclusive(
        locomo / "manifest.json",
        {
            "schema_version": 1,
            "suite": "locomo",
            "run_id": "locomo-test",
            "mode": "smoke",
            "judge_votes": 0,
            "repository_commit": "commit-locomo",
            "answer_model": {"adapter": "fake", "model": "answer"},
            "judge_model": None,
            "dataset": {
                "conversation_count": 1,
                "session_count": 1,
                "turn_count": 2,
                "question_count": 1,
                "license": "CC BY-NC 4.0",
            },
        },
    )
    write_json_exclusive(
        locomo / "checkpoints" / "ingest" / "conv-1.json",
        {
            "sample_id": "conv-1",
            "session_count": 1,
            "turn_count": 2,
            "accepted_memory_count": 2,
            "rejected_memory_count": 0,
        },
    )
    write_json_exclusive(
        locomo / "checkpoints" / "questions" / "conv-1" / "question-1.json",
        {
            "status": "completed",
            "category": 1,
            "answer": {
                "model": "answer",
                "text": "answer",
                "input_tokens": 10,
                "output_tokens": 2,
                "cost_usd": None,
            },
            "judge_votes": [],
        },
    )
    write_json_exclusive(locomo / "summary.json", report_locomo(locomo))

    retrieval = root / "retrieval"
    write_json_exclusive(
        retrieval / "manifest.json",
        {
            "schema_version": 1,
            "suite": "retrieval",
            "run_id": "retrieval-test",
            "query_count": 1,
            "repository_commit": "commit-retrieval",
        },
    )
    write_json_exclusive(retrieval / "corpus.json", [])
    write_json_exclusive(
        retrieval / "queries" / "q-1.json",
        {
            "schema_version": 1,
            "query_id": "q-1",
            "relevant_keys": ["memory-1"],
            "rankings": [{"key": "memory-1"}, {"key": "other"}],
            "latency_ms": 5.0,
            "repository_isolation_violation": False,
        },
    )
    write_json_exclusive(retrieval / "summary.json", report_retrieval(retrieval))

    recovery = root / "recovery"
    write_json_exclusive(
        recovery / "manifest.json",
        {
            "schema_version": 1,
            "suite": "storage-recovery",
            "run_id": "recovery-test",
            "repository_commit": "commit-recovery",
        },
    )
    write_json_exclusive(
        recovery / "checks.json",
        {
            "schema_version": 1,
            "checks": {"index_rebuild_parity": True, "queue_replay": True},
            "details": {},
        },
    )
    write_json_exclusive(recovery / "summary.json", report_recovery(recovery))

    coding = root / "coding"
    write_json_exclusive(
        coding / "experiment.json",
        {
            "schema_version": 1,
            "suite": "coding-memory-ab",
            "experiment_id": "coding-test",
            "planned_run_count": 2,
            "repository_commit": "commit-coding",
            "agent": {"adapter": "fake", "model": "agent"},
        },
    )
    _write_coding_run(coding, arm="memory-off", outcome="failed", tokens=100, steps=10)
    _write_coding_run(coding, arm="memory-on", outcome="passed", tokens=80, steps=8)
    write_json_exclusive(coding / "summary.json", report_coding_runs(coding))

    write_json_exclusive(root / "coverage.json", {"totals": {"percent_covered": 83.456}})
    (root / "junit.xml").write_text(
        '<testsuites><testsuite tests="9" failures="0" errors="0" skipped="1">'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return root


def _write_coding_run(root: Path, *, arm: str, outcome: str, tokens: int, steps: int) -> None:
    run_id = f"task-1-{arm}"
    run = root / run_id
    write_json_exclusive(run / "manifest.json", {"run_id": run_id, "arm": arm})
    write_json_exclusive(
        run / "result.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": "task-1",
            "arm": arm,
            "repeat": 1,
            "outcome": outcome,
            "repeated_file_reads": 0,
            "repeated_failed_commands": 0,
            "steps_to_first_useful_action": steps,
            "input_tokens": tokens - 10,
            "output_tokens": 10,
            "cached_input_tokens": 0,
            "cost_usd": None,
        },
    )
    events = [
        {"kind": "command", "step": 1},
        {"kind": "file_change", "step": 2},
    ]
    if arm == "memory-on":
        events.append({"kind": "message", "step": 3})
    write_json_exclusive(run / "trace.json", {"schema_version": 1, "events": events})
    write_json_exclusive(run / "verifier.json", {"passed": outcome == "passed"})
