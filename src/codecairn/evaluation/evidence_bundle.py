from __future__ import annotations

import platform
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.coding import report_coding_runs
from codecairn.evaluation.locomo import report_locomo
from codecairn.evaluation.retrieval import report_recovery, report_retrieval

AGGREGATION_COMMAND = "uv run codecairn evidence verify {bundle_dir}"


@dataclass(frozen=True)
class EvidenceBundleConfig:
    bundle_id: str
    output_root: Path
    locomo_run_dir: Path
    retrieval_run_dir: Path
    recovery_run_dir: Path
    coding_run_dir: Path
    quality_junit_path: Path
    quality_coverage_path: Path
    repository_root: Path
    generator_commit: str


@dataclass(frozen=True)
class EvidenceBundleArtifact:
    bundle_dir: Path
    metrics: dict[str, object]


def build_evidence_bundle(config: EvidenceBundleConfig) -> EvidenceBundleArtifact:
    """Build one immutable, public evidence bundle from completed run artifacts."""
    _safe_id(config.bundle_id, field="bundle_id")
    if not config.generator_commit.strip():
        raise ValueError("generator_commit must not be empty")
    bundle_dir = (config.output_root / config.bundle_id).resolve()
    bundle_dir.mkdir(parents=True, exist_ok=False)
    try:
        _copy_evaluation_artifacts(config, bundle_dir)
        metrics, manifest = _aggregate_bundle(
            bundle_dir,
            repository_root=config.repository_root.resolve(),
            generator_commit=config.generator_commit,
        )
        write_json_exclusive(bundle_dir / "metrics.json", metrics)
        write_json_exclusive(bundle_dir / "bundle-manifest.json", manifest)
        (bundle_dir / "README.md").write_text(_render_readme(metrics, manifest), encoding="utf-8")
        (bundle_dir / "resume.md").write_text(_render_resume(metrics), encoding="utf-8")
        (bundle_dir / "resume.zh-CN.md").write_text(_render_resume_zh(metrics), encoding="utf-8")
        write_json_exclusive(bundle_dir / "inventory.json", _build_inventory(bundle_dir))
        verify_evidence_bundle(bundle_dir)
    except Exception:
        shutil.rmtree(bundle_dir)
        raise
    return EvidenceBundleArtifact(bundle_dir=bundle_dir, metrics=metrics)


def verify_evidence_bundle(bundle_dir: Path) -> dict[str, object]:
    """Verify hashes, saved reports, aggregate counts, and generated documents."""
    root = bundle_dir.resolve()
    inventory = _required_dict(read_json(root / "inventory.json"), field="inventory")
    files = _required_dict(inventory.get("files"), field="inventory files")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "inventory.json"
    }
    if actual_paths != set(files):
        raise ValueError("Evidence bundle file inventory does not match the filesystem")
    for relative, expected in files.items():
        if not isinstance(expected, str) or file_sha256(root / relative) != expected:
            raise ValueError(f"Evidence bundle hash mismatch: {relative}")

    manifest = _required_dict(read_json(root / "bundle-manifest.json"), field="bundle manifest")
    metrics, regenerated_manifest = _aggregate_bundle(
        root,
        repository_root=None,
        generator_commit=_required_str(manifest, "generator_commit"),
    )
    _assert_equal(read_json(root / "metrics.json"), metrics, field="metrics")
    _assert_equal(manifest, regenerated_manifest, field="bundle manifest")
    if (root / "README.md").read_text(encoding="utf-8") != _render_readme(metrics, manifest):
        raise ValueError("Generated README does not match aggregate metrics")
    if (root / "resume.md").read_text(encoding="utf-8") != _render_resume(metrics):
        raise ValueError("Generated English resume does not match aggregate metrics")
    if (root / "resume.zh-CN.md").read_text(encoding="utf-8") != _render_resume_zh(metrics):
        raise ValueError("Generated Chinese resume does not match aggregate metrics")
    return {
        "schema_version": 1,
        "bundle_id": _required_str(manifest, "bundle_id"),
        "verified": True,
        "verified_file_count": len(files),
    }


def _copy_evaluation_artifacts(config: EvidenceBundleConfig, target: Path) -> None:
    _copy_named_files(
        config.locomo_run_dir,
        target / "raw" / "locomo",
        ("manifest.json", "summary.json"),
    )
    _copy_public_locomo_ingests(config.locomo_run_dir, target / "raw" / "locomo")
    _copy_public_locomo_questions(
        config.locomo_run_dir,
        target / "raw" / "locomo",
    )
    _copy_named_files(
        config.retrieval_run_dir,
        target / "raw" / "retrieval",
        ("manifest.json", "summary.json", "corpus.json"),
    )
    _copy_glob(config.retrieval_run_dir, target / "raw" / "retrieval", "queries/*.json")
    _copy_named_files(
        config.recovery_run_dir,
        target / "raw" / "recovery",
        ("manifest.json", "summary.json", "checks.json"),
    )
    _copy_named_files(
        config.coding_run_dir,
        target / "raw" / "coding",
        ("experiment.json", "summary.json"),
    )
    for pattern in ("*/manifest.json", "*/result.json", "*/trace.json"):
        _copy_glob(config.coding_run_dir, target / "raw" / "coding", pattern)
    _copy_public_verifiers(config.coding_run_dir, target / "raw" / "coding")
    quality = target / "raw" / "quality"
    quality.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(config.quality_junit_path, quality / "junit.xml")
    shutil.copyfile(config.quality_coverage_path, quality / "coverage.json")


def _copy_named_files(source: Path, target: Path, names: tuple[str, ...]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        if not path.is_file():
            raise ValueError(f"Required evidence artifact is missing: {path}")
        shutil.copyfile(path, target / name)


def _copy_glob(source: Path, target: Path, pattern: str) -> None:
    paths = sorted(source.glob(pattern))
    if not paths:
        raise ValueError(f"Required evidence artifacts are missing: {source / pattern}")
    for path in paths:
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def _copy_public_verifiers(source: Path, target: Path) -> None:
    paths = sorted(source.glob("*/verifier.json"))
    if not paths:
        raise ValueError(f"Required verifier artifacts are missing: {source}")
    for path in paths:
        raw = _required_dict(read_json(path), field="verifier artifact")
        exit_code = raw.get("exit_code")
        if not isinstance(exit_code, int):
            passed = raw.get("passed")
            if not isinstance(passed, bool):
                raise ValueError("Verifier artifact must contain exit_code or passed")
            exit_code = 0 if passed else 1
        public = {
            "schema_version": raw.get("schema_version", 1),
            "status": raw.get("status", "completed"),
            "passed": raw.get("status", "completed") == "completed" and exit_code == 0,
            "exit_code": exit_code,
            "duration_ms": raw.get("duration_ms"),
            "executed_in_workspace": raw.get("executed_in_workspace"),
            "output_sha256": raw.get("output_sha256"),
            "verifier_source_sha256": raw.get("verifier_source_sha256"),
            "source_artifact_sha256": file_sha256(path),
        }
        destination = target / path.relative_to(source)
        write_json_exclusive(destination, public)


def _copy_public_locomo_questions(source: Path, target: Path) -> None:
    paths = sorted(source.glob("checkpoints/questions/*/*.json"))
    if not paths:
        raise ValueError(f"Required LoCoMo question artifacts are missing: {source}")
    allowed_fields = (
        "schema_version",
        "sample_id",
        "question_id",
        "category",
        "category_name",
        "status",
        "phase",
        "error_type",
        "answer",
        "judge_votes",
    )
    for path in paths:
        raw = _required_dict(read_json(path), field="LoCoMo question artifact")
        public = {key: raw[key] for key in allowed_fields if key in raw}
        public["source_artifact_sha256"] = file_sha256(path)
        destination = target / path.relative_to(source)
        write_json_exclusive(destination, public)


def _copy_public_locomo_ingests(source: Path, target: Path) -> None:
    paths = sorted(source.glob("checkpoints/ingest/*.json"))
    if not paths:
        raise ValueError(f"Required LoCoMo ingest artifacts are missing: {source}")
    allowed_fields = (
        "sample_id",
        "session_count",
        "turn_count",
        "accepted_memory_count",
        "rejected_memory_count",
    )
    for path in paths:
        raw = _required_dict(read_json(path), field="LoCoMo ingest checkpoint")
        public = {key: raw[key] for key in allowed_fields if key in raw}
        public["source_artifact_sha256"] = file_sha256(path)
        destination = target / path.relative_to(source)
        write_json_exclusive(destination, public)


def _aggregate_bundle(
    bundle_dir: Path,
    *,
    repository_root: Path | None,
    generator_commit: str,
) -> tuple[dict[str, object], dict[str, object]]:
    raw = bundle_dir / "raw"
    locomo_dir = raw / "locomo"
    retrieval_dir = raw / "retrieval"
    recovery_dir = raw / "recovery"
    coding_dir = raw / "coding"

    locomo = report_locomo(locomo_dir)
    retrieval = report_retrieval(retrieval_dir)
    recovery = report_recovery(recovery_dir)
    coding = report_coding_runs(coding_dir)
    for directory, report, name in (
        (locomo_dir, locomo, "LoCoMo"),
        (retrieval_dir, retrieval, "retrieval"),
        (recovery_dir, recovery, "recovery"),
        (coding_dir, coding, "coding"),
    ):
        _assert_equal(read_json(directory / "summary.json"), report, field=f"{name} report")

    locomo_manifest = _required_dict(
        read_json(locomo_dir / "manifest.json"), field="LoCoMo manifest"
    )
    retrieval_manifest = _required_dict(
        read_json(retrieval_dir / "manifest.json"), field="retrieval manifest"
    )
    recovery_manifest = _required_dict(
        read_json(recovery_dir / "manifest.json"), field="recovery manifest"
    )
    coding_manifest = _required_dict(
        read_json(coding_dir / "experiment.json"), field="coding experiment"
    )
    quality = _quality_metrics(raw / "quality")
    counts = _inventory_counts(
        locomo_dir=locomo_dir,
        retrieval_dir=retrieval_dir,
        coding_dir=coding_dir,
    )
    _validate_completed_runs(
        locomo=locomo,
        retrieval=retrieval,
        recovery=recovery,
        coding=coding,
        manifests=(locomo_manifest, retrieval_manifest, coding_manifest),
        counts=counts,
    )

    command = AGGREGATION_COMMAND.format(bundle_dir=f"evidence/{bundle_dir.name}")
    metrics: dict[str, object] = {
        "schema_version": 1,
        "claims": _claims(
            locomo=locomo,
            retrieval=retrieval,
            recovery=recovery,
            coding=coding,
            quality=quality,
            counts=counts,
            command=command,
        ),
        "counts": counts,
        "locomo": locomo,
        "retrieval": retrieval,
        "recovery": recovery,
        "coding": coding,
        "quality": quality,
        "pending": _pending_measurements(locomo=locomo, coding=coding),
    }
    existing_manifest_path = bundle_dir / "bundle-manifest.json"
    if repository_root is None:
        existing = _required_dict(read_json(existing_manifest_path), field="bundle manifest")
        generated_at = _required_str(existing, "generated_at_utc")
        lock_sha256 = _required_str(existing, "dependency_lock_sha256")
        environment = _required_dict(existing.get("environment"), field="environment")
    else:
        generated_at = datetime.now(UTC).isoformat()
        lock_path = repository_root / "uv.lock"
        if not lock_path.is_file():
            raise ValueError(f"Dependency lock is missing: {lock_path}")
        lock_sha256 = file_sha256(lock_path)
        environment = {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "bundle_id": bundle_dir.name,
        "generated_at_utc": generated_at,
        "generator_commit": generator_commit,
        "dependency_lock": "uv.lock",
        "dependency_lock_sha256": lock_sha256,
        "environment": environment,
        "source_runs": {
            "locomo": _run_provenance(locomo_manifest, manifest_name="manifest.json"),
            "retrieval": _run_provenance(retrieval_manifest, manifest_name="manifest.json"),
            "recovery": _run_provenance(recovery_manifest, manifest_name="manifest.json"),
            "coding": _run_provenance(coding_manifest, manifest_name="experiment.json"),
        },
        "models": {
            "locomo_answer": locomo_manifest.get("answer_model"),
            "locomo_judge": locomo_manifest.get("judge_model"),
            "coding_agent": coding_manifest.get("agent"),
        },
        "costs": {
            "locomo": _locomo_cost(locomo),
            "coding_memory_off": _arm(coding, "memory-off").get("total_cost_usd"),
            "coding_memory_on": _arm(coding, "memory-on").get("total_cost_usd"),
        },
        "aggregation_command": command,
        "known_limitations": _known_limitations(locomo),
        "licensing": {
            "locomo": "CC BY-NC 4.0; the dataset itself is not redistributed in this bundle.",
            "source": "https://github.com/snap-research/locomo",
        },
    }
    return metrics, manifest


def _inventory_counts(*, locomo_dir: Path, retrieval_dir: Path, coding_dir: Path) -> dict[str, int]:
    ingest_records = [
        _required_dict(read_json(path), field="LoCoMo ingest checkpoint")
        for path in sorted((locomo_dir / "checkpoints" / "ingest").glob("*.json"))
    ]
    coding_traces = [
        _required_dict(read_json(path), field="coding trace")
        for path in sorted(coding_dir.glob("*/trace.json"))
    ]
    events = [
        event
        for trace in coding_traces
        for event in _required_list(trace.get("events"), field="coding trace events")
        if isinstance(event, dict)
    ]
    results = sorted(coding_dir.glob("*/result.json"))
    verifier_results = sorted(coding_dir.glob("*/verifier.json"))
    for result_path in results:
        result = _required_dict(read_json(result_path), field="coding result")
        verifier = _required_dict(
            read_json(result_path.with_name("verifier.json")), field="public verifier"
        )
        if verifier.get("passed") is not (result.get("outcome") == "passed"):
            raise ValueError(f"Verifier outcome does not match coding result: {result_path.parent}")
        source_sha256 = verifier.get("source_artifact_sha256")
        if not isinstance(source_sha256, str) or len(source_sha256) != 64:
            raise ValueError("Public verifier must retain its source artifact SHA-256")
    return {
        "locomo_conversation_count": len(ingest_records),
        "locomo_session_count": sum(
            _required_int(item, "session_count") for item in ingest_records
        ),
        "locomo_turn_count": sum(_required_int(item, "turn_count") for item in ingest_records),
        "accepted_memory_count": sum(
            _required_int(item, "accepted_memory_count") for item in ingest_records
        ),
        "rejected_memory_count": sum(
            _required_int(item, "rejected_memory_count") for item in ingest_records
        ),
        "locomo_question_run_count": len(
            list((locomo_dir / "checkpoints" / "questions").glob("*/*.json"))
        ),
        "retrieval_query_count": len(list((retrieval_dir / "queries").glob("*.json"))),
        "coding_run_count": len(results),
        "coding_trace_count": len(coding_traces),
        "coding_event_count": len(events),
        "coding_tool_call_count": sum(event.get("kind") == "command" for event in events),
        "coding_file_change_count": sum(event.get("kind") == "file_change" for event in events),
        "coding_verifier_count": len(verifier_results),
    }


def _validate_completed_runs(
    *,
    locomo: dict[str, object],
    retrieval: dict[str, object],
    recovery: dict[str, object],
    coding: dict[str, object],
    manifests: tuple[dict[str, object], dict[str, object], dict[str, object]],
    counts: dict[str, int],
) -> None:
    locomo_manifest, retrieval_manifest, coding_manifest = manifests
    dataset = _required_dict(locomo_manifest.get("dataset"), field="LoCoMo dataset")
    if counts["locomo_conversation_count"] != _required_int(dataset, "conversation_count"):
        raise ValueError("LoCoMo ingest checkpoints do not cover every conversation")
    if counts["locomo_session_count"] != _required_int(dataset, "session_count"):
        raise ValueError("LoCoMo session count does not match the dataset manifest")
    if counts["locomo_turn_count"] != _required_int(dataset, "turn_count"):
        raise ValueError("LoCoMo turn count does not match the dataset manifest")
    if counts["locomo_question_run_count"] != _required_int(
        locomo, "completed_question_count"
    ) or _required_int(locomo, "infrastructure_failed_count"):
        raise ValueError("LoCoMo question artifacts are incomplete")
    if locomo.get("scored") is True:
        selection = _required_dict(locomo_manifest.get("selection"), field="LoCoMo selection")
        if selection.get("categories") != [1, 2, 3, 4]:
            raise ValueError("Publishable full LoCoMo must select categories 1 through 4")
        question_counts = _required_dict(
            selection.get("question_counts"), field="LoCoMo selected question counts"
        )
        if set(question_counts) != {"1", "2", "3", "4"}:
            raise ValueError("Full LoCoMo question counts must cover categories 1 through 4")
        expected_questions = sum(
            _required_int(question_counts, category) for category in question_counts
        )
        if counts["locomo_question_run_count"] != expected_questions:
            raise ValueError("Full LoCoMo checkpoints do not cover the selected questions")
        if _required_int(locomo, "scored_question_count") != expected_questions:
            raise ValueError("Full LoCoMo report did not score every selected question")
    if counts["retrieval_query_count"] != _required_int(retrieval_manifest, "query_count"):
        raise ValueError("Retrieval query artifacts are incomplete")
    if counts["retrieval_query_count"] != _required_int(retrieval, "query_count"):
        raise ValueError("Retrieval report query count is inconsistent")
    if recovery.get("all_passed") is not True:
        raise ValueError("Recovery evidence contains a failed check")
    planned = _required_int(coding_manifest, "planned_run_count")
    if counts["coding_run_count"] != planned or counts["coding_trace_count"] != planned:
        raise ValueError("Coding evaluation artifacts are incomplete")
    if counts["coding_verifier_count"] != planned:
        raise ValueError("Coding verifier artifacts are incomplete")
    if _required_int(coding, "completed_run_count") != planned:
        raise ValueError("Coding evaluation did not complete every planned run")
    if _required_int(coding, "infrastructure_failure_count") != 0:
        raise ValueError("Coding evaluation contains infrastructure failures")


def _quality_metrics(quality_dir: Path) -> dict[str, object]:
    coverage = _required_dict(read_json(quality_dir / "coverage.json"), field="coverage")
    totals = _required_dict(coverage.get("totals"), field="coverage totals")
    percent = totals.get("percent_covered")
    if not isinstance(percent, int | float):
        raise ValueError("Coverage percentage must be numeric")
    junit_root = ET.parse(quality_dir / "junit.xml").getroot()
    if junit_root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("JUnit root must be testsuite or testsuites")
    suites = (
        [junit_root] if junit_root.tag == "testsuite" else list(junit_root.findall("testsuite"))
    )
    if not suites:
        raise ValueError("JUnit testsuites must contain at least one testsuite")
    tests = sum(_xml_int(suite.attrib, "tests") for suite in suites)
    failures = sum(_xml_int(suite.attrib, "failures") for suite in suites)
    errors = sum(_xml_int(suite.attrib, "errors") for suite in suites)
    skipped = sum(_xml_int(suite.attrib, "skipped") for suite in suites)
    if failures or errors:
        raise ValueError("Quality artifact contains failed tests")
    return {
        "test_count": tests,
        "failure_count": failures,
        "error_count": errors,
        "skipped_count": skipped,
        "coverage_percent": round(float(percent), 2),
    }


def _claims(
    *,
    locomo: dict[str, object],
    retrieval: dict[str, object],
    recovery: dict[str, object],
    coding: dict[str, object],
    quality: dict[str, object],
    counts: dict[str, int],
    command: str,
) -> list[dict[str, object]]:
    off = _arm(coding, "memory-off")
    on = _arm(coding, "memory-on")
    off_pass_rate = _required_number(off, "pass_rate")
    on_pass_rate = _required_number(on, "pass_rate")
    off_tokens = _required_number(off, "total_tokens")
    on_tokens = _required_number(on, "total_tokens")
    off_steps = _required_number(off, "mean_steps_to_first_useful_action")
    on_steps = _required_number(on, "mean_steps_to_first_useful_action")
    shared = {"aggregation_command": command}
    return [
        _claim(
            "retrieval_recall_at_5",
            "Retrieval Recall@5",
            100 * _required_number(retrieval, "recall_at_5"),
            "%",
            "raw/retrieval/manifest.json",
            "raw/retrieval/queries/*.json",
            shared,
        ),
        _claim(
            "retrieval_mrr",
            "Retrieval MRR",
            _required_number(retrieval, "mrr"),
            "ratio",
            "raw/retrieval/manifest.json",
            "raw/retrieval/queries/*.json",
            shared,
        ),
        _claim(
            "retrieval_p95_latency_ms",
            "Retrieval P95 latency",
            _required_number(retrieval, "p95_latency_ms"),
            "ms",
            "raw/retrieval/manifest.json",
            "raw/retrieval/queries/*.json",
            shared,
        ),
        _claim(
            "rebuild_consistency",
            "Index rebuild consistency",
            100 * _required_number(recovery, "index_rebuild_consistency"),
            "%",
            "raw/recovery/manifest.json",
            "raw/recovery/checks.json",
            shared,
        ),
        _claim(
            "coding_pass_rate_off",
            "Coding task pass rate, memory off",
            100 * off_pass_rate,
            "%",
            "raw/coding/experiment.json",
            "raw/coding/*/result.json",
            shared,
        ),
        _claim(
            "coding_pass_rate_on",
            "Coding task pass rate, memory on",
            100 * on_pass_rate,
            "%",
            "raw/coding/experiment.json",
            "raw/coding/*/result.json",
            shared,
        ),
        _claim(
            "coding_pass_rate_delta_pp",
            "Coding task pass-rate change",
            100 * (on_pass_rate - off_pass_rate),
            "percentage points",
            "raw/coding/experiment.json",
            "raw/coding/*/result.json",
            shared,
        ),
        _claim(
            "coding_token_reduction",
            "Coding total-token reduction",
            100 * (off_tokens - on_tokens) / off_tokens,
            "%",
            "raw/coding/experiment.json",
            "raw/coding/*/result.json",
            shared,
        ),
        _claim(
            "coding_first_action_reduction",
            "Steps-to-first-useful-action reduction",
            100 * (off_steps - on_steps) / off_steps,
            "%",
            "raw/coding/experiment.json",
            "raw/coding/*/result.json",
            shared,
        ),
        _claim(
            "locomo_sessions_ingested",
            "Official LoCoMo sessions ingested",
            counts["locomo_session_count"],
            "sessions",
            "raw/locomo/manifest.json",
            "raw/locomo/checkpoints/ingest/*.json",
            shared,
        ),
        *_locomo_claims(locomo, shared=shared),
        _claim(
            "test_count",
            "Automated tests",
            _required_number(quality, "test_count"),
            "tests",
            "bundle-manifest.json",
            "raw/quality/junit.xml",
            shared,
        ),
        _claim(
            "coverage_percent",
            "Statement coverage",
            _required_number(quality, "coverage_percent"),
            "%",
            "bundle-manifest.json",
            "raw/quality/coverage.json",
            shared,
        ),
    ]


def _claim(
    claim_id: str,
    title: str,
    value: int | float,
    unit: str,
    manifest: str,
    raw_inputs: str,
    shared: dict[str, str],
) -> dict[str, object]:
    return {
        "id": claim_id,
        "title": title,
        "value": round(float(value), 4),
        "unit": unit,
        "manifest": manifest,
        "raw_inputs": raw_inputs,
        **shared,
    }


def _pending_measurements(
    *, locomo: dict[str, object], coding: dict[str, object]
) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    if locomo.get("scored") is not True or locomo.get("accuracy") is None:
        pending.append(
            {
                "measurement": "LoCoMo accuracy",
                "status": "pending",
                "reason": "Only the explicitly unscored smoke run is complete.",
            }
        )
    if any(_arm(coding, arm).get("total_cost_usd") is None for arm in ("memory-off", "memory-on")):
        pending.append(
            {
                "measurement": "CodingMemoryBench provider cost",
                "status": "pending",
                "reason": "The provider trace contains no cost observations.",
            }
        )
    usage = _required_dict(locomo.get("usage"), field="LoCoMo usage")
    if usage.get("cost_usd") is None and usage.get("cost_cny") is None:
        pending.append(
            {
                "measurement": "LoCoMo provider cost",
                "status": "pending",
                "reason": "The provider response contains no cost observations.",
            }
        )
    return pending


def _locomo_claims(locomo: dict[str, object], *, shared: dict[str, str]) -> list[dict[str, object]]:
    completion = _claim(
        "locomo_full_completion" if locomo.get("scored") is True else "locomo_smoke_completion",
        "LoCoMo full completion" if locomo.get("scored") is True else "LoCoMo smoke completion",
        100
        * _required_number(locomo, "completed_question_count")
        / _required_number(locomo, "question_artifact_count"),
        "%",
        "raw/locomo/manifest.json",
        "raw/locomo/checkpoints/questions/*/*.json",
        shared,
    )
    if locomo.get("scored") is not True:
        return [completion]
    return [
        completion,
        _claim(
            "locomo_accuracy",
            "LoCoMo answer accuracy",
            100 * _required_number(locomo, "accuracy"),
            "%",
            "raw/locomo/manifest.json",
            "raw/locomo/checkpoints/questions/*/*.json",
            shared,
        ),
    ]


def _locomo_cost(locomo: dict[str, object]) -> dict[str, object] | float | None:
    usage = _required_dict(locomo.get("usage"), field="LoCoMo usage")
    cost_cny = usage.get("cost_cny")
    if isinstance(cost_cny, int | float) and not isinstance(cost_cny, bool):
        return {"amount": float(cost_cny), "currency": "CNY"}
    cost_usd = usage.get("cost_usd")
    return float(cost_usd) if isinstance(cost_usd, int | float) else None


def _known_limitations(locomo: dict[str, object]) -> list[str]:
    limitations: list[str] = []
    if locomo.get("scored") is not True:
        limitations.append(
            "The LoCoMo run is an unscored smoke run; full benchmark accuracy is pending."
        )
    limitations.extend(
        [
            "LoCoMo category 5 is adversarial and excluded from the official scored subset.",
            "Provider cost is pending where upstream artifacts expose no cost observation.",
            "Coding tasks and public fixtures are controlled evaluations, "
            "not private production traces.",
            "Latency was measured on one local machine and is not a cross-machine guarantee.",
            "An earlier CodingMemoryBench v1 run was invalidated and excluded after "
            "a verifier defect was found.",
        ]
    )
    return limitations


def _render_readme(metrics: dict[str, object], manifest: dict[str, object]) -> str:
    claims = _required_list(metrics.get("claims"), field="claims")
    counts = _required_dict(metrics.get("counts"), field="counts")
    pending = _required_list(metrics.get("pending"), field="pending")
    lines = [
        f"# Evidence bundle: {_required_str(manifest, 'bundle_id')}",
        "",
        "This directory is generated from immutable evaluation artifacts. Do not edit its",
        "metrics or recruiting copy by hand; rebuild it with the command in the manifest.",
        "",
        "## Headline measurements",
        "",
        "| Measurement | Value | Manifest | Raw inputs | Aggregation |",
        "|---|---:|---|---|---|",
    ]
    for item in claims:
        claim = _required_dict(item, field="claim")
        value = _format_value(_required_number(claim, "value"), _required_str(claim, "unit"))
        lines.append(
            "| {title} | {value} | [{manifest}]({manifest}) | "
            "[`{raw}`]({raw_link}) | `{command}` |".format(
                title=_required_str(claim, "title"),
                value=value,
                manifest=_required_str(claim, "manifest"),
                raw=_required_str(claim, "raw_inputs"),
                raw_link=_glob_link(_required_str(claim, "raw_inputs")),
                command=_required_str(claim, "aggregation_command"),
            )
        )
    locomo_scale = (
        f"- LoCoMo: {counts['locomo_conversation_count']} conversations, "
        f"{counts['locomo_session_count']} sessions, {counts['locomo_turn_count']} turns, "
        f"{counts['accepted_memory_count']} accepted memories, and "
        f"{counts['rejected_memory_count']} rejected memories."
    )
    coding_scale = (
        f"- Coding A/B: {counts['coding_run_count']} runs, "
        f"{counts['coding_event_count']} normalized events, "
        f"{counts['coding_tool_call_count']} command/tool calls, "
        f"{counts['coding_file_change_count']} file changes, and "
        f"{counts['coding_verifier_count']} hidden-verifier results."
    )
    lines.extend(
        [
            "",
            "## Artifact-derived scale",
            "",
            locomo_scale,
            f"- Retrieval: {counts['retrieval_query_count']} isolated queries.",
            coding_scale,
            "",
            "## Pending measurements",
            "",
        ]
    )
    for item in pending:
        record = _required_dict(item, field="pending measurement")
        measurement = _required_str(record, "measurement")
        reason = _required_str(record, "reason")
        lines.append(f"- **{measurement}** — pending: {reason}")
    limitations = _required_list(manifest.get("known_limitations"), field="limitations")
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {item}" for item in limitations if isinstance(item, str))
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            _required_str(manifest, "aggregation_command"),
            "```",
            "",
            "The verifier recomputes all four suite reports, aggregate counts, recruiting",
            "copy, and the SHA-256 inventory. It requires no private trace or provider key.",
            "",
            "LoCoMo is attributed to the [official repository](https://github.com/snap-research/locomo)",
            "and is licensed CC BY-NC 4.0. The dataset file is not redistributed here.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_resume(metrics: dict[str, object]) -> str:
    claims = _claim_map(metrics)
    counts = _required_dict(metrics.get("counts"), field="counts")
    pending = _required_list(metrics.get("pending"), field="pending")
    architecture = (
        "- Built an auditable long-term memory runtime for coding agents with Markdown truth, "
        "SQLite state, LanceDB hybrid retrieval, evidence gates, resumable import, and "
        f"{_claim_value(claims, 'test_count', 0)} automated tests at "
        f"{_claim_value(claims, 'coverage_percent', 2)}% coverage."
    )
    retrieval = (
        f"- Evaluated {_claim_value(claims, 'retrieval_recall_at_5', 0)}% Recall@5, "
        f"{_claim_value(claims, 'retrieval_mrr', 3)} MRR, and "
        f"{_claim_value(claims, 'retrieval_p95_latency_ms', 2)} ms P95 latency over "
        f"{counts['retrieval_query_count']} isolated queries; reproduced the index from "
        f"Markdown truth with {_claim_value(claims, 'rebuild_consistency', 0)}% consistency."
    )
    coding = (
        f"- Ran {counts['coding_run_count']} isolated hidden-verifier CodingMemoryBench trials; "
        "memory-on raised pass rate from "
        f"{_claim_value(claims, 'coding_pass_rate_off', 0)}% to "
        f"{_claim_value(claims, 'coding_pass_rate_on', 0)}% "
        f"(+{_claim_value(claims, 'coding_pass_rate_delta_pp', 0)} pp), reduced total tokens by "
        f"{_claim_value(claims, 'coding_token_reduction', 2)}%, and shortened steps to first "
        f"useful action by {_claim_value(claims, 'coding_first_action_reduction', 2)}%."
    )
    locomo_report = _required_dict(metrics.get("locomo"), field="LoCoMo report")
    if locomo_report.get("scored") is True:
        locomo = (
            f"- Evaluated {counts['locomo_question_run_count']} official LoCoMo category 1-4 "
            f"questions with {_required_int(locomo_report, 'judge_votes')} judge votes each and "
            "zero infrastructure failures; achieved "
            f"LoCoMo accuracy of {_claim_value(claims, 'locomo_accuracy', 2)}% after ingesting "
            f"{counts['locomo_session_count']} sessions and {counts['locomo_turn_count']} turns."
        )
    else:
        locomo = (
            f"- Ingested all {counts['locomo_conversation_count']} official LoCoMo conversations "
            f"({counts['locomo_session_count']} sessions, "
            f"{counts['locomo_turn_count']} turns) into "
            f"{counts['accepted_memory_count']} evidence-backed memories with "
            f"{counts['rejected_memory_count']} gate rejections; completed an explicitly unscored "
            f"{counts['locomo_question_run_count']}-question end-to-end smoke run with zero "
            "infrastructure failures."
        )
    return "\n".join(
        [
            "# Resume evidence — CodeCairn",
            "",
            architecture,
            retrieval,
            coding,
            locomo,
            "",
            "## Pending — do not publish as measured",
            "",
            *[
                f"- {_required_str(_required_dict(item, field='pending'), 'measurement')}: pending."
                for item in pending
            ],
            "",
        ]
    )


def _render_resume_zh(metrics: dict[str, object]) -> str:
    claims = _claim_map(metrics)
    counts = _required_dict(metrics.get("counts"), field="counts")
    pending = _required_list(metrics.get("pending"), field="pending")
    pending_lines = "\n".join(
        f"- {_required_str(_required_dict(item, field='pending'), 'measurement')}: pending."
        for item in pending
    )
    locomo_report = _required_dict(metrics.get("locomo"), field="LoCoMo report")
    if locomo_report.get("scored") is True:
        locomo_evidence = (
            f"- 在 LoCoMo 官方类别 1-4 的 {counts['locomo_question_run_count']} 问全量评测中, "
            f"每题执行 {_required_int(locomo_report, 'judge_votes')} 次独立评审且"
            "基础设施失败为 0; LoCoMo 准确率 "
            f"{_claim_value(claims, 'locomo_accuracy', 2)}%, 共导入 "
            f"{counts['locomo_session_count']} 个 session 和 {counts['locomo_turn_count']} 条 turn."
        )
    else:
        locomo_evidence = (
            f"- 导入 LoCoMo 官方全部 {counts['locomo_conversation_count']} 个会话样本 "
            f"({counts['locomo_session_count']} 个 session, "
            f"{counts['locomo_turn_count']} 条 turn), "
            f"生成 {counts['accepted_memory_count']} 条证据记忆, 记录 "
            f"{counts['rejected_memory_count']} 条门控拒绝; 完成明确不计分的 "
            f"{counts['locomo_question_run_count']} 问端到端 smoke, 基础设施失败为 0."
        )
    template = Path(__file__).with_name("templates") / "resume.zh-CN.md"
    return template.read_text(encoding="utf-8").format(
        test_count=_claim_value(claims, "test_count", 0),
        coverage_percent=_claim_value(claims, "coverage_percent", 2),
        retrieval_query_count=counts["retrieval_query_count"],
        retrieval_recall_at_5=_claim_value(claims, "retrieval_recall_at_5", 0),
        retrieval_mrr=_claim_value(claims, "retrieval_mrr", 3),
        retrieval_p95_latency_ms=_claim_value(claims, "retrieval_p95_latency_ms", 2),
        rebuild_consistency=_claim_value(claims, "rebuild_consistency", 0),
        coding_run_count=counts["coding_run_count"],
        coding_pass_rate_off=_claim_value(claims, "coding_pass_rate_off", 0),
        coding_pass_rate_on=_claim_value(claims, "coding_pass_rate_on", 0),
        coding_pass_rate_delta_pp=_claim_value(claims, "coding_pass_rate_delta_pp", 0),
        coding_token_reduction=_claim_value(claims, "coding_token_reduction", 2),
        coding_first_action_reduction=_claim_value(claims, "coding_first_action_reduction", 2),
        locomo_evidence=locomo_evidence,
        pending_lines=pending_lines,
    )


def _build_inventory(bundle_dir: Path) -> dict[str, object]:
    files = {
        path.relative_to(bundle_dir).as_posix(): file_sha256(path)
        for path in sorted(bundle_dir.rglob("*"))
        if path.is_file() and path.name != "inventory.json"
    }
    return {"schema_version": 1, "algorithm": "sha256", "files": files}


def _run_provenance(manifest: dict[str, object], *, manifest_name: str) -> dict[str, object]:
    return {
        "run_id": manifest.get("run_id") or manifest.get("experiment_id"),
        "repository_commit": manifest.get("repository_commit"),
        "manifest": manifest_name,
    }


def _claim_map(metrics: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        _required_str(record, "id"): record
        for item in _required_list(metrics.get("claims"), field="claims")
        for record in [_required_dict(item, field="claim")]
    }


def _claim_value(claims: dict[str, dict[str, object]], claim_id: str, digits: int) -> str:
    value = _required_number(claims[claim_id], "value")
    return f"{value:.{digits}f}"


def _arm(coding: dict[str, object], name: str) -> dict[str, object]:
    arms = _required_dict(coding.get("arms"), field="coding arms")
    return _required_dict(arms.get(name), field=f"coding arm {name}")


def _format_value(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "ms":
        return f"{value:.2f} ms"
    if unit == "ratio":
        return f"{value:.4f}"
    if unit == "percentage points":
        return f"{value:.2f} pp"
    return f"{value:g} {unit}"


def _glob_link(value: str) -> str:
    marker = value.find("*")
    return value if marker < 0 else value[:marker].rstrip("/")


def _xml_int(attributes: dict[str, str], key: str) -> int:
    raw = attributes.get(key, "0")
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"JUnit {key} must be an integer") from error


def _assert_equal(actual: object, expected: object, *, field: str) -> None:
    if actual != expected:
        raise ValueError(f"Saved {field} does not match recomputed data")


def _safe_id(value: str, *, field: str) -> str:
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in value
    ):
        raise ValueError(f"{field} must contain only lowercase letters, digits, dash, or dot")
    return value


def _required_dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _required_list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return cast(list[object], value)


def _required_str(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_int(record: dict[str, object], field: str) -> int:
    value = record.get(field)
    if not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _required_number(record: dict[str, object], field: str) -> float:
    value = record.get(field)
    if not isinstance(value, int | float):
        raise ValueError(f"{field} must be numeric")
    return float(value)
