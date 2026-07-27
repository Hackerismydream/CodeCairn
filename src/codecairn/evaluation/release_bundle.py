"""Pure verifier for the version 0.1 release evidence contract."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from math import ceil
from pathlib import Path
from typing import Any, cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json
from codecairn.evaluation.historical_reader import report_coding

CONTRACT = "codecairn-v01-release-evidence-v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RECOVERY_CASES = (
    "capture_after_intent_prepared",
    "capture_before_commit",
    "direct_memory_after_intent_prepared",
    "direct_memory_before_commit",
    "test_prepared_evolution_recovers_after_process_restart[evolution_after_intent_prepared]",
    "test_prepared_evolution_recovers_after_process_restart[evolution_before_commit]",
    "test_prepared_restore_recovers_after_process_restart[evolution_after_intent_prepared]",
    "test_prepared_restore_recovers_after_process_restart[evolution_before_commit]",
)


def verify_release_bundle(bundle_dir: Path) -> dict[str, object]:
    root = bundle_dir.resolve()
    inventory = _dict(read_json(root / "inventory.json"), "inventory")
    files = _dict(inventory.get("files"), "inventory files")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.name != "inventory.json"}
    if actual != set(files):
        raise ValueError("Release evidence inventory does not match the filesystem")
    for relative, digest in files.items():
        if not isinstance(digest, str) or file_sha256(_path(root, relative)) != digest:
            raise ValueError(f"Release evidence hash mismatch: {relative}")

    manifest = _dict(read_json(root / "bundle-manifest.json"), "release manifest")
    if manifest.get("schema_version") != 1 or manifest.get("contract") != CONTRACT:
        raise ValueError("Release evidence contract is unsupported")
    implementation_sha = _string(manifest, "implementation_sha")
    if _SHA.fullmatch(implementation_sha) is None:
        raise ValueError("Release implementation SHA is invalid")
    metrics = _aggregate(root, manifest, implementation_sha)
    if read_json(root / "metrics.json") != metrics:
        raise ValueError("Release metrics do not match raw evidence")
    binding = _git_binding(root, implementation_sha)
    return {
        "schema_version": 1,
        "bundle_id": _string(manifest, "bundle_id"),
        "contract": CONTRACT,
        "implementation_sha": implementation_sha,
        "verified": True,
        "verified_file_count": len(files),
        "evidence_binding": binding,
    }


def _aggregate(root: Path, manifest: dict[str, Any], implementation_sha: str) -> dict[str, object]:
    runs = _dict(manifest.get("runs"), "release runs")
    smoke = _offline_run(root, runs, "smoke", implementation_sha)
    scale = _offline_run(root, runs, "scale", implementation_sha)
    retrieval = _offline_run(root, runs, "retrieval", implementation_sha)
    diagnostic = _locomo_run(root, runs, "locomo_200", implementation_sha, expected=200)
    full = _locomo_run(root, runs, "locomo_full", implementation_sha, expected=1_540)
    coding_dir = _run_path(root, runs, "coding")
    coding_manifest = _dict(read_json(coding_dir / "experiment.json"), "coding experiment")
    if coding_manifest.get("repository_commit") != implementation_sha:
        raise ValueError("Coding run is not bound to the implementation SHA")
    coding = report_coding(coding_dir)
    results = []
    for path in sorted(coding_dir.glob("*/result.json")):
        result = _dict(read_json(path), "coding result")
        if _string(result, "run_id") != path.parent.name:
            raise ValueError("Coding run identity does not match its evidence directory")
        metrics = _coding_trace_metrics(path.parent / "trace.json")
        if any(result.get(field) != value for field, value in metrics.items()):
            raise ValueError("Coding trace metrics do not match the reported outcome")
        results.append(result)
    by_arm = {
        arm: {(item["task_id"], item["repeat"]): item for item in results if item.get("arm") == arm}
        for arm in ("memory-off", "memory-on")
    }
    pairs = set(by_arm["memory-off"]) & set(by_arm["memory-on"])
    coding["memory_induced_regression_count"] = sum(
        by_arm["memory-off"][key].get("outcome") == "passed" and by_arm["memory-on"][key].get("outcome") == "failed" for key in pairs
    )
    coding["memory_induced_improvement_count"] = sum(
        by_arm["memory-off"][key].get("outcome") == "failed" and by_arm["memory-on"][key].get("outcome") == "passed" for key in pairs
    )
    if read_json(coding_dir / "summary.json") != coding:
        raise ValueError("Coding aggregate does not match raw outcomes")
    task_count = len({item.get("task_id") for item in results})
    if (
        task_count != 20
        or coding["completed_run_count"] != coding["planned_run_count"]
        or coding["infrastructure_failure_count"] != 0
        or coding["memory_induced_regression_count"] != 0
    ):
        raise ValueError("Coding A/B release threshold failed")

    reports = _dict(manifest.get("reports"), "release reports")
    hooks = _report(root, reports, "real_clients")
    installed = _report(root, reports, "installed_smoke")
    artifacts = _report(root, reports, "artifact_repro")
    source = _report(root, reports, "source_budget")
    quality = _report(root, reports, "quality")
    recovery = _recovery(root, reports, implementation_sha)
    _verify_smoke(smoke)
    _verify_scale(scale)
    _verify_retrieval(retrieval)
    _verify_clients(hooks, implementation_sha)
    _verify_package(installed, artifacts, source, quality, hooks, implementation_sha)
    if full["accuracy"] is None or cast(float, full["accuracy"]) < 0.82:
        raise ValueError("LoCoMo full release threshold failed")
    return {
        "schema_version": 1,
        "implementation_sha": implementation_sha,
        "smoke": smoke,
        "scale": scale,
        "retrieval": retrieval,
        "write_intent_recovery": recovery,
        "locomo_200": diagnostic,
        "locomo_full": full,
        "coding": {**coding, "task_count": task_count},
        "real_clients": hooks,
        "installed_smoke": installed,
        "artifact_repro": artifacts,
        "source_budget": source,
        "quality": quality,
    }


def _offline_run(root: Path, runs: dict[str, Any], name: str, implementation_sha: str) -> dict[str, Any]:
    run = _run_path(root, runs, name)
    manifest = _dict(read_json(run / "manifest.json"), f"{name} manifest")
    aggregate = _dict(read_json(run / "aggregate.json"), f"{name} aggregate")
    outcomes = _list(read_json(run / "outcomes.json"), f"{name} outcomes")
    if (
        manifest.get("implementation_sha") != implementation_sha
        or manifest.get("aggregate_sha256") != canonical_sha256(aggregate)
        or manifest.get("outcome_count") != len(outcomes)
    ):
        raise ValueError(f"{name} run binding is invalid")
    return aggregate


def _locomo_run(root: Path, runs: dict[str, Any], name: str, implementation_sha: str, *, expected: int) -> dict[str, object]:
    run = _run_path(root, runs, name)
    manifest = _dict(read_json(run / "manifest.json"), f"{name} manifest")
    if manifest.get("implementation_sha") != implementation_sha:
        raise ValueError(f"{name} run is not bound to the implementation SHA")
    aggregate = _report_locomo(run)
    if (
        read_json(run / "aggregate.json") != aggregate
        or aggregate["selected_question_count"] != expected
        or aggregate["scored_question_count"] != expected
        or aggregate["infrastructure_failed_count"] != 0
    ):
        raise ValueError(f"{name} run is incomplete")
    return aggregate


def _report_locomo(run: Path) -> dict[str, object]:
    manifest = _dict(read_json(run / "manifest.json"), "LoCoMo manifest")
    results = [_dict(read_json(path), "LoCoMo result") for path in sorted((run / "questions").glob("*.json"))]
    expected = _dict(manifest.get("question_set"), "question set").get("question_count")
    if len(results) != expected or len({item.get("question_id") for item in results}) != len(results):
        raise ValueError("LoCoMo outcome inventory is incomplete")
    scored = [item for item in results if item.get("outcome") in {"correct", "wrong"}]
    categories = {}
    for category in range(1, 5):
        items = [item for item in scored if item.get("category") == category]
        correct = sum(item.get("outcome") == "correct" for item in items)
        categories[str(category)] = {"scored": len(items), "correct": correct, "accuracy": None if not items else correct / len(items)}
    latencies = sorted(float(item["retrieval_latency_ms"]) for item in results if "retrieval_latency_ms" in item)
    correct_count = sum(item.get("outcome") == "correct" for item in scored)
    return {
        "schema_version": 1,
        "protocol": manifest["protocol"],
        "selected_question_count": expected,
        "scored_question_count": len(scored),
        "correct_count": correct_count,
        "wrong_count": sum(item.get("outcome") == "wrong" for item in scored),
        "infrastructure_failed_count": sum(item.get("outcome") == "infrastructure_failure" for item in results),
        "accuracy": None if not scored else correct_count / len(scored),
        "retrieval_p95_ms": None if not latencies else latencies[max(0, ceil(len(latencies) * 0.95) - 1)],
        "categories": categories,
        "manifest_sha256": canonical_sha256(manifest),
        "result_inventory_sha256": canonical_sha256(results),
    }


def _recovery(root: Path, reports: dict[str, Any], implementation_sha: str) -> dict[str, object]:
    report = _report(root, reports, "recovery_junit")
    if report.get("implementation_sha") != implementation_sha:
        raise ValueError("Recovery report is not bound to the implementation SHA")
    xml_path = _path(root, _string(report, "junit_path"))
    cases = tuple(element.attrib.get("name", "") for element in ET.parse(xml_path).iter("testcase"))
    failed = any(element.tag in {"failure", "error"} for element in ET.parse(xml_path).iter())
    missing = [expected for expected in _RECOVERY_CASES if not any(expected in case for case in cases)]
    if failed or missing:
        raise ValueError(f"Write Intent recovery evidence is incomplete: {missing}")
    return {"case_count": len(_RECOVERY_CASES), "all_passed": True, "cases": list(_RECOVERY_CASES)}


def _verify_smoke(value: dict[str, Any]) -> None:
    if (
        value.get("client_family_count") != 2
        or value.get("read_your_writes_rate") != 1
        or value.get("duplicate_memory_count") != 0
        or value.get("continuation_success_rate") != 1
    ):
        raise ValueError("Lifecycle smoke release threshold failed")


def _verify_scale(value: dict[str, Any]) -> None:
    required = {
        "session_count": 1_000,
        "raw_event_count": 100_000,
        "episode_count": 1_000,
        "memory_count": 1_000,
        "repeat_created_count": 0,
        "duplicate_episode_count": 0,
    }
    if any(value.get(field) != expected for field, expected in required.items()):
        raise ValueError("Scale release threshold failed")


def _verify_retrieval(value: dict[str, Any]) -> None:
    if (
        value.get("query_count") != 100
        or not isinstance(value.get("recall_at_5"), int | float)
        or value["recall_at_5"] < 0.9
        or value.get("provenance_coverage") != 1
        or value.get("stale_predecessor_leakage") != 0
        or not isinstance(value.get("p95_latency_ms"), int | float)
        or value["p95_latency_ms"] > 4_000
    ):
        raise ValueError("Retrieval release threshold failed")


def _verify_clients(value: dict[str, Any], implementation_sha: str) -> None:
    if value.get("implementation_sha") != implementation_sha:
        raise ValueError("Real-client report is not bound to the implementation SHA")
    clients = _dict(value.get("clients"), "real clients")
    if set(clients) != {"codex", "claude"}:
        raise ValueError("Both real clients are required")
    required = ("hook_installed", "receipt_verified", "recall_verified", "hook_removed", "config_readback_verified")
    for name, client in clients.items():
        item = _dict(client, "real client")
        if (
            any(item.get(field) is not True for field in required)
            or not isinstance(item.get("native_created_memory_count"), int)
            or item["native_created_memory_count"] < 1
            or item.get("repeat_created_memory_count") != 0
            or (name == "claude" and item.get("transcript_removed") is not True)
        ):
            raise ValueError("Real-client hook release threshold failed")


def _coding_trace_metrics(path: Path) -> dict[str, int | None]:
    trace = _dict(read_json(path), "coding trace")
    events = _list(trace.get("events"), "coding trace events")
    if trace.get("schema_version") != 1:
        raise ValueError("Coding trace schema is unsupported")
    normalized = []
    prior_step = 0
    for value in events:
        event = _dict(value, "coding trace event")
        allowed = {"step", "kind", "exit_code", "command_sha256", "path_sha256"}
        step = event.get("step")
        kind = event.get("kind")
        if (
            set(event) - allowed
            or not isinstance(step, int)
            or step <= prior_step
            or kind not in {"command", "file_read", "file_change", "message"}
            or (kind == "command" and _DIGEST.fullmatch(str(event.get("command_sha256"))) is None)
            or (kind == "file_read" and _DIGEST.fullmatch(str(event.get("path_sha256"))) is None)
            or ("command_sha256" in event and _DIGEST.fullmatch(str(event["command_sha256"])) is None)
            or ("path_sha256" in event and _DIGEST.fullmatch(str(event["path_sha256"])) is None)
            or ("exit_code" in event and not isinstance(event["exit_code"], int | None))
        ):
            raise ValueError("Coding trace contains non-public fields")
        prior_step = step
        normalized.append(event)
    reads = Counter(
        item["path_sha256"] for item in normalized if item.get("kind") == "file_read" and isinstance(item.get("path_sha256"), str)
    )
    failed = Counter(
        item["command_sha256"]
        for item in normalized
        if item.get("kind") == "command" and isinstance(item.get("command_sha256"), str) and item.get("exit_code") not in {None, 0}
    )
    first_change = next((item["step"] for item in normalized if item.get("kind") == "file_change"), None)
    return {
        "repeated_file_reads": sum(max(count - 1, 0) for count in reads.values()),
        "repeated_failed_commands": sum(max(count - 1, 0) for count in failed.values()),
        "steps_to_first_useful_action": first_change,
    }


def _verify_package(
    installed: dict[str, Any],
    artifacts: dict[str, Any],
    source: dict[str, Any],
    quality: dict[str, Any],
    clients: dict[str, Any],
    implementation_sha: str,
) -> None:
    if any(stage.get("status") != "pass" for stage in _list(installed.get("stages"), "installed stages")):
        raise ValueError("Installed artifact smoke failed")
    comparisons = _dict(artifacts.get("comparisons"), "artifact comparisons")
    if (
        artifacts.get("implementation_sha") != implementation_sha
        or artifacts.get("source_worktree_clean") is not True
        or artifacts.get("clean_checkout_count") != 2
        or not comparisons
        or any(_dict(item, "artifact comparison").get("raw_equal") is not True for item in comparisons.values())
    ):
        raise ValueError("Artifact reproducibility threshold failed")
    builds = _list(artifacts.get("builds"), "artifact builds")
    wheel_hashes = {
        _string(
            _dict(_dict(_dict(build, "artifact build").get("artifacts"), "build artifacts").get("wheel"), "wheel artifact"), "sha256"
        )
        for build in builds
    }
    if len(wheel_hashes) != 1 or installed.get("wheel_sha256") not in wheel_hashes or clients.get("wheel_sha256") not in wheel_hashes:
        raise ValueError("Installed and real-client smoke are not bound to the reproducible wheel")
    if (
        source.get("commit") != implementation_sha
        or source.get("dirty") is not False
        or source.get("passed") is not True
        or cast(int, source.get("core", 10_001)) > 10_000
        or cast(int, source.get("total", 15_001)) > 15_000
    ):
        raise ValueError("Source budget threshold failed")
    if quality.get("implementation_sha") != implementation_sha or any(
        quality.get(field) is not True for field in ("format", "check", "docs", "artifact_check")
    ):
        raise ValueError("Quality release threshold failed")


def _git_binding(root: Path, implementation_sha: str) -> dict[str, object]:
    result = subprocess.run(("git", "-C", str(root), "rev-parse", "--show-toplevel"), capture_output=True, text=True, check=False)
    if result.returncode:
        return {"status": "not_available", "direct_descendant": None}
    repository = Path(result.stdout.strip())
    head = _git(repository, "rev-parse", "HEAD")
    parent = _git(repository, "rev-parse", "HEAD^") if head != implementation_sha else None
    return {
        "status": "bound" if parent == implementation_sha else "implementation_worktree" if head == implementation_sha else "mismatch",
        "evidence_sha": None if head == implementation_sha else head,
        "direct_descendant": parent == implementation_sha,
    }


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(("git", "-C", str(root), *arguments), check=True, capture_output=True, text=True).stdout.strip()


def _run_path(root: Path, runs: dict[str, Any], name: str) -> Path:
    return _path(root, _string(runs, name))


def _report(root: Path, reports: dict[str, Any], name: str) -> dict[str, Any]:
    return _dict(read_json(_path(root, _string(reports, name))), name)


def _path(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError("Release evidence path escapes its bundle")
    return candidate


def _dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _string(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item
