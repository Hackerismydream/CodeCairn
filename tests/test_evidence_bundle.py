from __future__ import annotations

from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
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
    private_question_path = (
        sources / "locomo" / "checkpoints" / "questions" / "conv-1" / "question-1.json"
    )
    private_question = read_json(private_question_path)
    assert isinstance(private_question, dict)
    private_answer = private_question["answer"]
    assert isinstance(private_answer, dict)
    private_question["category_name"] = "single-hop"
    private_answer["provider_debug"] = {"authorization": "Bearer must-not-be-public"}
    private_question["judge_votes"] = [
        {
            "vote_index": 0,
            "label": "correct",
            "raw_response": '{"label":"CORRECT"}',
            "attempt_count": 2,
            "response_chars": 19,
            "provider_debug": {
                "api_key": "must-not-be-public",
                "local_path": "/private/runtime",
            },
            "failed_attempts": [
                {
                    "attempt_index": 1,
                    "error_type": "JSONDecodeError",
                    "raw_response": "private malformed response",
                    "response_chars": 26,
                    "request_debug": {
                        "authorization": "Bearer must-not-be-public",
                        "gold_answer": "private gold",
                    },
                }
            ],
        }
    ]
    _replace_json(private_question_path, private_question)
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
    public_question = read_json(
        artifact.bundle_dir
        / "raw"
        / "locomo"
        / "checkpoints"
        / "questions"
        / "conv-1"
        / "question-1.json"
    )
    assert isinstance(public_question, dict)
    assert (
        not {
            "question",
            "golden_answer",
            "recall_markdown",
            "retrieval",
        }
        & public_question.keys()
    )
    public_vote = public_question["judge_votes"][0]
    assert "provider_debug" not in public_question["answer"]
    assert "raw_response" not in public_vote
    assert "provider_debug" not in public_vote
    assert "raw_response" not in public_vote["failed_attempts"][0]
    assert "request_debug" not in public_vote["failed_attempts"][0]
    assert public_vote["attempt_count"] == 2
    assert public_question["category_name"] == "multi-hop"
    assert len(public_question["source_artifact_sha256"]) == 64
    public_ingest = read_json(
        artifact.bundle_dir / "raw" / "locomo" / "checkpoints" / "ingest" / "conv-1.json"
    )
    assert isinstance(public_ingest, dict)
    assert not {"speaker_a", "speaker_b", "memory_root"} & public_ingest.keys()
    assert len(public_ingest["source_artifact_sha256"]) == 64
    verifier = read_json(
        artifact.bundle_dir / "raw" / "coding" / "task-1-memory-on" / "verifier.json"
    )
    assert isinstance(verifier, dict)
    assert verifier["passed"] is True
    assert len(verifier["source_artifact_sha256"]) == 64
    assert "workspace" not in verifier

    query = artifact.bundle_dir / "raw" / "retrieval" / "queries" / "q-1.json"
    query.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_evidence_bundle(artifact.bundle_dir)


def test_full_locomo_bundle_publishes_accuracy_cny_cost_and_resume_evidence(
    tmp_path: Path,
) -> None:
    sources = _make_legacy_full_locomo_source(tmp_path / "sources")
    locomo = sources / "locomo"

    artifact = build_evidence_bundle(
        EvidenceBundleConfig(
            bundle_id="benchmark-full-test",
            output_root=tmp_path / "evidence",
            locomo_run_dir=locomo,
            retrieval_run_dir=sources / "retrieval",
            recovery_run_dir=sources / "recovery",
            coding_run_dir=sources / "coding",
            quality_junit_path=sources / "junit.xml",
            quality_coverage_path=sources / "coverage.json",
            repository_root=Path(__file__).parents[1],
            generator_commit="abc123",
        )
    )

    claims = artifact.metrics["claims"]
    assert isinstance(claims, list)
    accuracy = next(item for item in claims if item["id"] == "locomo_accuracy")
    assert accuracy["value"] == 100.0
    locomo_metrics = artifact.metrics["locomo"]
    assert isinstance(locomo_metrics, dict)
    assert locomo_metrics["by_category"]["1"]["name"] == "multi-hop"
    assert all(item["measurement"] != "LoCoMo accuracy" for item in artifact.metrics["pending"])
    bundle_manifest = read_json(artifact.bundle_dir / "bundle-manifest.json")
    assert isinstance(bundle_manifest, dict)
    amendments = bundle_manifest["amendments"]
    assert isinstance(amendments, list)
    assert amendments[0]["kind"] == "locomo_category_label_correction"
    assert amendments[0]["numeric_metrics_changed"] is False
    assert len(amendments[0]["source_summary_sha256"]) == 64
    assert bundle_manifest["costs"]["locomo"] == {"amount": 0.001, "currency": "CNY"}
    resume = (artifact.bundle_dir / "resume.md").read_text()
    assert "with 3 judge votes each" in resume
    assert "LoCoMo accuracy of 100.00%" in resume
    resume_zh = (artifact.bundle_dir / "resume.zh-CN.md").read_text()
    assert "LoCoMo 准确率 100.00%" in resume_zh
    assert "3 次重复裁判投票" in resume_zh
    assert "独立评审" not in resume_zh


def test_bundle_rejects_numeric_drift_disguised_as_a_category_label_amendment(
    tmp_path: Path,
) -> None:
    sources = _make_legacy_full_locomo_source(tmp_path / "sources")
    summary_path = sources / "locomo" / "summary.json"
    summary = read_json(summary_path)
    assert isinstance(summary, dict)
    summary["accuracy"] = 0.5
    _replace_json(summary_path, summary)

    with pytest.raises(ValueError, match="source LoCoMo report"):
        build_evidence_bundle(
            EvidenceBundleConfig(
                bundle_id="benchmark-drift-test",
                output_root=tmp_path / "evidence",
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


def test_bundle_rejects_tampered_category_label_amendment(tmp_path: Path) -> None:
    sources = _make_legacy_full_locomo_source(tmp_path / "sources")
    artifact = build_evidence_bundle(
        EvidenceBundleConfig(
            bundle_id="benchmark-amendment-test",
            output_root=tmp_path / "evidence",
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
    amendment_path = artifact.bundle_dir / "raw" / "locomo" / "amendment.json"
    amendment = read_json(amendment_path)
    assert isinstance(amendment, dict)
    corrections = amendment["corrected_categories"]
    assert isinstance(corrections, list)
    correction = corrections[0]
    assert isinstance(correction, dict)
    correction["to"] = "single-hop"
    _replace_json(amendment_path, amendment)

    inventory_path = artifact.bundle_dir / "inventory.json"
    inventory = read_json(inventory_path)
    assert isinstance(inventory, dict)
    inventory_files = inventory["files"]
    assert isinstance(inventory_files, dict)
    inventory_files["raw/locomo/amendment.json"] = file_sha256(amendment_path)
    _replace_json(inventory_path, inventory)

    with pytest.raises(ValueError, match="correction mapping"):
        verify_evidence_bundle(artifact.bundle_dir)


def test_bundle_rejects_type_confusion_in_public_locomo_fields(tmp_path: Path) -> None:
    sources = _make_source_runs(tmp_path / "sources")
    question_path = sources / "locomo" / "checkpoints" / "questions" / "conv-1" / "question-1.json"
    question = read_json(question_path)
    assert isinstance(question, dict)
    answer = question["answer"]
    assert isinstance(answer, dict)
    answer["model"] = {"api_key": "must-not-be-public"}
    _replace_json(question_path, question)

    with pytest.raises(ValueError, match="model"):
        build_evidence_bundle(
            EvidenceBundleConfig(
                bundle_id="benchmark-type-confusion",
                output_root=tmp_path / "evidence",
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


def test_bundle_rejects_nested_data_in_public_locomo_usage_fields(tmp_path: Path) -> None:
    sources = _make_source_runs(tmp_path / "sources")
    question_path = sources / "locomo" / "checkpoints" / "questions" / "conv-1" / "question-1.json"
    question = read_json(question_path)
    assert isinstance(question, dict)
    question["judge_votes"] = [
        {
            "vote_index": 0,
            "label": "correct",
            "attempt_count": 1,
            "response_chars": 19,
            "failed_attempts": [],
            "raw_response": '{"label":"CORRECT"}',
            "input_tokens": {"private_prompt": "must-not-be-public"},
        }
    ]
    _replace_json(question_path, question)

    with pytest.raises(ValueError, match="input_tokens"):
        build_evidence_bundle(
            EvidenceBundleConfig(
                bundle_id="benchmark-usage-type-confusion",
                output_root=tmp_path / "evidence",
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


def _make_legacy_full_locomo_source(root: Path) -> Path:
    sources = _make_source_runs(root)
    locomo = sources / "locomo"
    manifest = read_json(locomo / "manifest.json")
    assert isinstance(manifest, dict)
    manifest.update(
        {
            "mode": "full",
            "scored": True,
            "judge_votes": 3,
            "judge_response_max_attempts": 3,
            "judge_response_max_chars": 32_768,
            "judge_model": {"adapter": "fake", "model": "judge"},
            "selection": {
                "categories": [1, 2, 3, 4],
                "question_counts": {"1": 1, "2": 0, "3": 0, "4": 0},
            },
        }
    )
    _replace_json(locomo / "manifest.json", manifest)
    question_path = locomo / "checkpoints" / "questions" / "conv-1" / "question-1.json"
    question = read_json(question_path)
    assert isinstance(question, dict)
    answer = question["answer"]
    assert isinstance(answer, dict)
    answer.update(
        {
            "cached_input_tokens": 6,
            "uncached_input_tokens": 4,
            "reasoning_tokens": 2,
            "cost_cny": 0.001,
        }
    )
    question["judge_votes"] = [
        {
            "vote_index": index,
            "label": "correct",
            "attempt_count": 1,
            "response_chars": 19,
            "failed_attempts": [],
            "raw_response": '{"label":"CORRECT"}',
            "input_tokens": 2,
            "output_tokens": 1,
            "known_cost_count": 0,
            "known_cost_cny_count": 0,
        }
        for index in range(3)
    ]
    _replace_json(question_path, question)
    _replace_json(locomo / "summary.json", report_locomo(locomo))
    legacy_summary = read_json(locomo / "summary.json")
    assert isinstance(legacy_summary, dict)
    legacy_categories = legacy_summary["by_category"]
    assert isinstance(legacy_categories, dict)
    legacy_category_one = legacy_categories["1"]
    assert isinstance(legacy_category_one, dict)
    legacy_category_one["name"] = "single-hop"
    _replace_json(locomo / "summary.json", legacy_summary)
    return sources


def _replace_json(path: Path, value: object) -> None:
    path.unlink()
    write_json_exclusive(path, value)


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
            "speaker_a": "Private Speaker A",
            "speaker_b": "Private Speaker B",
            "memory_root": "runtime/conv-1",
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
            "question": "What private detail was discussed?",
            "golden_answer": "A private answer",
            "recall_markdown": "# Recall Context\n\nPrivate source conversation.\n",
            "retrieval": {
                "query": "What private detail was discussed?",
                "ranked": [{"quote": "Private source conversation."}],
            },
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
