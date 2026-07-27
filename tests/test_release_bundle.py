from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256
from codecairn.evaluation.coding import report_coding_runs
from codecairn.evaluation.locomo import report_locomo
from codecairn.evaluation.release_bundle import CONTRACT, _aggregate, verify_release_bundle

IMPLEMENTATION_SHA = "a" * 40
WHEEL_SHA256 = "b" * 64


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _offline(root: Path, name: str, aggregate: dict[str, object], outcomes: list[object]) -> str:
    target = root / f"raw/offline/{name}"
    _write(
        target / "manifest.json",
        {
            "schema_version": 1,
            "implementation_sha": IMPLEMENTATION_SHA,
            "aggregate_sha256": canonical_sha256(aggregate),
            "outcome_count": len(outcomes),
        },
    )
    _write(target / "outcomes.json", outcomes)
    _write(target / "aggregate.json", aggregate)
    return target.relative_to(root).as_posix()


def _locomo(root: Path, name: str, count: int, *, correct: int) -> str:
    target = root / f"raw/locomo/{name}"
    manifest = {
        "schema_version": 1,
        "implementation_sha": IMPLEMENTATION_SHA,
        "protocol": {
            "id": "codecairn-locomo-v01",
            "contract": {"answer": {"max_completion_tokens": 512}, "judge": {"max_completion_tokens": 512}},
        },
        "question_set": {"question_count": count},
        "budget": {"spend_ceiling_usd": count * 8 * 0.01, "max_call_cost_usd": 0.01, "max_completion_tokens": 512},
    }
    _write(target / "manifest.json", manifest)
    for index in range(count):
        _write(
            target / "questions" / f"question-{index:04d}.json",
            {
                "question_id": f"question-{index:04d}",
                "category": index % 4 + 1,
                "outcome": "correct" if index < correct else "wrong",
                "retrieval_latency_ms": 1.0,
            },
        )
    _write(target / "aggregate.json", report_locomo(target))
    return target.relative_to(root).as_posix()


def _coding(root: Path) -> str:
    target = root / "raw/coding"
    _write(
        target / "experiment.json",
        {"schema_version": 1, "experiment_id": "release", "repository_commit": IMPLEMENTATION_SHA, "planned_run_count": 40},
    )
    for task in range(20):
        for arm in ("memory-off", "memory-on"):
            run = target / f"task-{task:02d}-{arm}"
            _write(
                run / "result.json",
                {
                    "schema_version": 1,
                    "run_id": run.name,
                    "task_id": f"task-{task:02d}",
                    "arm": arm,
                    "repeat": 1,
                    "outcome": "passed",
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 1,
                    "cost_usd": None,
                    "repeated_file_reads": 0,
                    "repeated_failed_commands": 0,
                    "steps_to_first_useful_action": 1,
                },
            )
            _write(run / "manifest.json", {"schema_version": 1, "run_id": run.name})
            _write(run / "trace.json", {"schema_version": 1, "events": [{"step": 1, "kind": "file_change"}]})
    _write(target / "summary.json", report_coding_runs(target))
    return target.relative_to(root).as_posix()


def _report(root: Path, name: str, value: object) -> str:
    path = root / f"raw/reports/{name}.json"
    _write(path, value)
    return path.relative_to(root).as_posix()


def _bundle(root: Path) -> None:
    smoke_outcomes = [
        {
            "provider": provider,
            "read_your_writes": True,
            "repeat_created_memory_count": 0,
            "freshness": "fresh",
            "continuation_created_memory_count": 1,
            "committed_identity_preserved": True,
        }
        for provider in ("codex", "claude")
    ]
    scale_outcomes = [
        {
            "kind": "session",
            "provider": "codex" if index < 500 else "claude",
            "session_id": f"session-{index}",
            "source_sha256": f"{index:064x}",
            "raw_event_count": 100,
            "first_created_memory_count": 1,
            "repeat_created_memory_count": 0,
            "repaired_memory_count": 0,
        }
        for index in range(1_000)
    ]
    scale_outcomes.append(
        {
            "kind": "inventory",
            "memory_ids": [f"mem_{index}" for index in range(1_000)],
            "episode_ids": [f"ep_{index}" for index in range(1_000)],
        }
    )
    retrieval_outcomes = []
    for index in range(100):
        relevant = f"relevant-{index}"
        selected = relevant if index < 90 else f"other-{index}"
        retrieval_outcomes.append(
            {
                "query_id": f"query-{index}",
                "relevant_keys": [relevant],
                "candidates": [
                    {
                        "key": selected,
                        "status": "active",
                        "source_uri": f"codecairn://memory/{index}",
                        "content_sha256": f"{index:064x}",
                    }
                ],
                "recall_at_5": index < 90,
                "precision_at_5": 1.0 if index < 90 else 0.0,
                "provenance_covered": True,
                "stale_predecessor_count": 0,
                "latency_ms": 10.0,
            }
        )
    runs = {
        "smoke": _offline(
            root,
            "smoke",
            {
                "client_family_count": 2,
                "trigger_count": 204,
                "read_your_writes_rate": 1,
                "duplicate_memory_count": 0,
                "fresh_or_semantic_pending_count": 2,
                "continuation_success_rate": 1,
            },
            smoke_outcomes,
        ),
        "scale": _offline(
            root,
            "scale",
            {
                "session_count": 1_000,
                "codex_session_count": 500,
                "claude_session_count": 500,
                "raw_event_count": 100_000,
                "episode_count": 1_000,
                "memory_count": 1_000,
                "first_import_created_count": 1_000,
                "repeat_created_count": 0,
                "duplicate_episode_count": 0,
            },
            scale_outcomes,
        ),
        "retrieval": _offline(
            root,
            "retrieval",
            {
                "query_count": 100,
                "recall_at_5": 0.9,
                "precision_at_5": 0.9,
                "provenance_coverage": 1,
                "stale_predecessor_leakage": 0,
                "p95_latency_ms": 10.0,
            },
            retrieval_outcomes,
        ),
        "locomo_200": _locomo(root, "diagnostic", 200, correct=164),
        "locomo_full": _locomo(root, "full", 1_540, correct=1_263),
        "coding": _coding(root),
    }
    names = (
        "capture_after_intent_prepared",
        "capture_before_commit",
        "direct_memory_after_intent_prepared",
        "direct_memory_before_commit",
        "test_prepared_evolution_recovers_after_process_restart[evolution_after_intent_prepared]",
        "test_prepared_evolution_recovers_after_process_restart[evolution_before_commit]",
        "test_prepared_restore_recovers_after_process_restart[evolution_after_intent_prepared]",
        "test_prepared_restore_recovers_after_process_restart[evolution_before_commit]",
    )
    junit = root / "raw/reports/recovery.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.write_text("<testsuite>" + "".join(f'<testcase name="{name}"/>' for name in names) + "</testsuite>")
    reports = {
        "recovery_junit": _report(
            root, "recovery", {"implementation_sha": IMPLEMENTATION_SHA, "junit_path": junit.relative_to(root).as_posix()}
        ),
        "real_clients": _report(
            root,
            "clients",
            {
                "implementation_sha": IMPLEMENTATION_SHA,
                "wheel_sha256": WHEEL_SHA256,
                "clients": {
                    name: {
                        "hook_installed": True,
                        "receipt_verified": True,
                        "recall_verified": True,
                        "hook_removed": True,
                        "config_readback_verified": True,
                        "native_created_memory_count": 1,
                        "repeat_created_memory_count": 0,
                        **({"transcript_removed": True} if name == "claude" else {}),
                    }
                    for name in ("codex", "claude")
                },
            },
        ),
        "installed_smoke": _report(root, "installed", {"wheel_sha256": WHEEL_SHA256, "stages": [{"status": "pass"}]}),
        "artifact_repro": _report(
            root,
            "artifacts",
            {
                "implementation_sha": IMPLEMENTATION_SHA,
                "source_worktree_clean": True,
                "clean_checkout_count": 2,
                "comparisons": {"wheel": {"raw_equal": True}, "sdist": {"raw_equal": True}},
                "builds": [{"artifacts": {"wheel": {"sha256": WHEEL_SHA256}}}, {"artifacts": {"wheel": {"sha256": WHEEL_SHA256}}}],
            },
        ),
        "source_budget": _report(
            root, "source", {"commit": IMPLEMENTATION_SHA, "dirty": False, "passed": True, "core": 9_900, "total": 14_000}
        ),
        "quality": _report(
            root,
            "quality",
            {"implementation_sha": IMPLEMENTATION_SHA, "format": True, "check": True, "docs": True, "artifact_check": True},
        ),
    }
    manifest = {
        "schema_version": 1,
        "contract": CONTRACT,
        "bundle_id": "v0.1-test",
        "implementation_sha": IMPLEMENTATION_SHA,
        "runs": runs,
        "reports": reports,
    }
    _write(root / "bundle-manifest.json", manifest)
    _write(root / "metrics.json", _aggregate(root, manifest, IMPLEMENTATION_SHA))
    _write(
        root / "inventory.json",
        {
            "schema_version": 1,
            "files": {
                path.relative_to(root).as_posix(): file_sha256(path)
                for path in sorted(root.rglob("*"))
                if path.is_file() and path.name != "inventory.json"
            },
        },
    )


def test_release_bundle_recomputes_every_threshold_and_rejects_tampering(tmp_path: Path) -> None:
    _bundle(tmp_path)
    verified = verify_release_bundle(tmp_path)
    assert verified["verified"] is True
    assert verified["implementation_sha"] == IMPLEMENTATION_SHA

    metrics = tmp_path / "metrics.json"
    metrics.write_text(metrics.read_text().replace('"accuracy": 0.8201298701298702', '"accuracy": 1.0'))
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_release_bundle(tmp_path)


def test_release_bundle_builder_redacts_and_verifies_selected_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _bundle(source)
    _write(
        source / "raw/coding/task-00-memory-off/trace.json",
        {
            "schema_version": 1,
            "events": [
                {"step": 1, "kind": "file_change", "path": "answer.py"},
                {"step": 2, "kind": "command", "command": "cat private.txt", "exit_code": 0},
                {"step": 3, "kind": "file_read", "path": "private.txt"},
            ],
        },
    )
    notes = tmp_path / "notes.md"
    notes.write_text("# CodeCairn v0.1\n")
    command = [
        sys.executable,
        "scripts/build_release_bundle.py",
        "--bundle-id",
        "v0.1-test",
        "--output-root",
        str(tmp_path / "output"),
        "--implementation-sha",
        IMPLEMENTATION_SHA,
        "--smoke",
        str(source / "raw/offline/smoke"),
        "--scale",
        str(source / "raw/offline/scale"),
        "--retrieval",
        str(source / "raw/offline/retrieval"),
        "--locomo-200",
        str(source / "raw/locomo/diagnostic"),
        "--locomo-full",
        str(source / "raw/locomo/full"),
        "--coding",
        str(source / "raw/coding"),
        "--recovery-junit",
        str(source / "raw/reports/recovery.xml"),
        "--real-clients",
        str(source / "raw/reports/clients.json"),
        "--installed-smoke",
        str(source / "raw/reports/installed.json"),
        "--artifact-repro",
        str(source / "raw/reports/artifacts.json"),
        "--source-budget",
        str(source / "raw/reports/source.json"),
        "--quality",
        str(source / "raw/reports/quality.json"),
        "--release-notes",
        str(notes),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    built = tmp_path / "output/v0.1-test"
    assert verify_release_bundle(built)["verified"] is True
    assert not list(built.rglob("raw-agent-trace.log"))
    trace = json.loads((built / "raw/coding/task-00-memory-off/trace.json").read_text())
    assert all("command" not in event and "path" not in event for event in trace["events"])
    assert trace["events"][1]["command_sha256"]
    assert trace["events"][2]["path_sha256"]


def test_release_bundle_rejects_unrecomputed_coding_metrics_and_unbound_wheel(tmp_path: Path) -> None:
    _bundle(tmp_path)
    manifest = json.loads((tmp_path / "bundle-manifest.json").read_text())
    result_path = tmp_path / "raw/coding/task-00-memory-off/result.json"
    result = json.loads(result_path.read_text())
    result["repeated_file_reads"] = 1
    _write(result_path, result)
    with pytest.raises(ValueError, match="trace metrics"):
        _aggregate(tmp_path, manifest, IMPLEMENTATION_SHA)

    result["repeated_file_reads"] = 0
    _write(result_path, result)
    clients_path = tmp_path / "raw/reports/clients.json"
    clients = json.loads(clients_path.read_text())
    clients["wheel_sha256"] = "c" * 64
    _write(clients_path, clients)
    with pytest.raises(ValueError, match="reproducible wheel"):
        _aggregate(tmp_path, manifest, IMPLEMENTATION_SHA)


def test_release_bundle_rejects_unrecomputed_offline_metrics(tmp_path: Path) -> None:
    _bundle(tmp_path)
    manifest = json.loads((tmp_path / "bundle-manifest.json").read_text())
    retrieval_path = tmp_path / "raw/offline/retrieval/outcomes.json"
    outcomes = json.loads(retrieval_path.read_text())
    outcomes[0]["recall_at_5"] = False
    _write(retrieval_path, outcomes)

    with pytest.raises(ValueError, match="retrieval outcome metrics"):
        _aggregate(tmp_path, manifest, IMPLEMENTATION_SHA)


def test_real_client_smoke_requires_explicit_spend_ack_and_ceiling(tmp_path: Path) -> None:
    base = [
        sys.executable,
        "scripts/real_client_smoke.py",
        "--implementation-sha",
        IMPLEMENTATION_SHA,
        "--output",
        str(tmp_path / "must-not-exist.json"),
    ]
    missing = subprocess.run(base, check=False, capture_output=True, text=True)
    invalid = subprocess.run(
        [*base, "--spend-ack", "YES", "--claude-max-budget-usd", "nan"], check=False, capture_output=True, text=True
    )

    assert missing.returncode == 2
    assert "--spend-ack" in missing.stderr
    assert invalid.returncode == 2
    assert "positive finite" in invalid.stderr
    assert not (tmp_path / "must-not-exist.json").exists()
