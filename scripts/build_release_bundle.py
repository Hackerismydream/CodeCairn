#!/usr/bin/env python3
"""Build a redacted, immutable CodeCairn v0.1 release evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, cast

from codecairn.evaluation.artifacts import file_sha256, read_json
from codecairn.evaluation.release_bundle import CONTRACT, _aggregate, verify_release_bundle


def build(arguments: argparse.Namespace) -> dict[str, object]:
    target = (arguments.output_root / arguments.bundle_id).resolve()
    target.mkdir(parents=True, exist_ok=False)
    try:
        runs = {
            "smoke": _copy_offline(arguments.smoke, target / "raw/offline/smoke"),
            "scale": _copy_offline(arguments.scale, target / "raw/offline/scale"),
            "retrieval": _copy_offline(arguments.retrieval, target / "raw/offline/retrieval"),
            "locomo_200": _copy_locomo(arguments.locomo_200, target / "raw/locomo/diagnostic"),
            "locomo_full": _copy_locomo(arguments.locomo_full, target / "raw/locomo/full"),
            "coding": _copy_coding(arguments.coding, target / "raw/coding"),
        }
        report_root = target / "raw/reports"
        reports = {
            "real_clients": _copy(arguments.real_clients, report_root / "real-clients.json", target),
            "installed_smoke": _copy(arguments.installed_smoke, report_root / "installed-smoke.json", target),
            "artifact_repro": _copy(arguments.artifact_repro, report_root / "artifact-repro.json", target),
            "source_budget": _copy(arguments.source_budget, report_root / "source-budget.json", target),
            "quality": _copy(arguments.quality, report_root / "quality.json", target),
        }
        junit = target / "raw/reports/recovery-junit.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(arguments.recovery_junit, junit)
        recovery = {
            "schema_version": 1,
            "implementation_sha": arguments.implementation_sha,
            "junit_path": junit.relative_to(target).as_posix(),
        }
        recovery_path = report_root / "recovery.json"
        _write_json(recovery_path, recovery)
        reports["recovery_junit"] = recovery_path.relative_to(target).as_posix()
        manifest = {
            "schema_version": 1,
            "contract": CONTRACT,
            "bundle_id": arguments.bundle_id,
            "implementation_sha": arguments.implementation_sha,
            "runs": runs,
            "reports": reports,
            "evidence_sha": None,
            "limitations": [
                "Provider-managed answer and judge model aliases are not immutable revisions.",
                "Evidence verification proves integrity and thresholds, not semantic truth by provider replay.",
                "Raven integration is outside version 0.1.",
            ],
        }
        _write_json(target / "bundle-manifest.json", manifest)
        _write_json(target / "metrics.json", _aggregate(target, manifest, arguments.implementation_sha))
        shutil.copyfile(arguments.release_notes, target / "RELEASE_NOTES.md")
        _write_json(target / "inventory.json", _inventory(target))
        return verify_release_bundle(target)
    except Exception:
        shutil.rmtree(target)
        raise


def _copy_offline(source: Path, target: Path) -> str:
    target.mkdir(parents=True)
    for name in ("manifest.json", "outcomes.json", "aggregate.json"):
        _required_copy(source / name, target / name)
    return target.relative_to(target.parents[2]).as_posix()


def _copy_locomo(source: Path, target: Path) -> str:
    target.mkdir(parents=True)
    for name in ("manifest.json", "aggregate.json"):
        _required_copy(source / name, target / name)
    questions = sorted((source / "questions").glob("*.json"))
    if not questions:
        raise ValueError(f"LoCoMo outcomes are missing: {source}")
    for path in questions:
        raw = _dict(read_json(path), "LoCoMo result")
        public = {key: raw[key] for key in ("question_id", "category", "outcome", "retrieval_latency_ms") if key in raw}
        _write_json(target / "questions" / path.name, public)
    return target.relative_to(target.parents[2]).as_posix()


def _copy_coding(source: Path, target: Path) -> str:
    target.mkdir(parents=True)
    for name in ("experiment.json", "summary.json"):
        _required_copy(source / name, target / name)
    runs = sorted(path for path in source.iterdir() if path.is_dir() and (path / "result.json").is_file())
    if not runs:
        raise ValueError(f"Coding outcomes are missing: {source}")
    for run in runs:
        destination = target / run.name
        destination.mkdir()
        _required_copy(run / "manifest.json", destination / "manifest.json")
        raw = _dict(read_json(run / "result.json"), "coding result")
        allowed = (
            "schema_version",
            "run_id",
            "task_id",
            "arm",
            "repeat",
            "outcome",
            "workspace_snapshot_after_sha256",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "cost_usd",
            "repeated_file_reads",
            "repeated_failed_commands",
            "steps_to_first_useful_action",
            "infrastructure_error_type",
        )
        _write_json(destination / "result.json", {key: raw[key] for key in allowed if key in raw})
        _write_json(destination / "trace.json", _public_trace(run / "trace.json"))
        verifier = run / "verifier.json"
        if verifier.is_file():
            value = _dict(read_json(verifier), "coding verifier")
            public = {
                key: value[key]
                for key in (
                    "schema_version",
                    "status",
                    "executed_in_workspace",
                    "exit_code",
                    "duration_ms",
                    "output_sha256",
                    "verifier_source_sha256",
                )
                if key in value
            }
            _write_json(destination / "verifier.json", public)
    return target.relative_to(target.parents[1]).as_posix()


def _copy(source: Path, target: Path, root: Path) -> str:
    _required_copy(source, target)
    return target.relative_to(root).as_posix()


def _public_trace(path: Path) -> dict[str, object]:
    raw = _dict(read_json(path), "coding trace")
    events = raw.get("events")
    if raw.get("schema_version") != 1 or not isinstance(events, list):
        raise ValueError("Coding trace is invalid")
    public = []
    for value in events:
        event = _dict(value, "coding trace event")
        item = {key: event[key] for key in ("step", "kind", "exit_code") if key in event}
        for source, target in (("command", "command_sha256"), ("path", "path_sha256")):
            text = event.get(source)
            if isinstance(text, str):
                item[target] = hashlib.sha256(text.encode()).hexdigest()
        public.append(item)
    return {"schema_version": 1, "events": public}


def _required_copy(source: Path, target: Path) -> None:
    if not source.is_file():
        raise ValueError(f"Required release artifact is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _inventory(root: Path) -> dict[str, object]:
    files = {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "inventory.json"
    }
    return {"schema_version": 1, "algorithm": "sha256", "files": files}


def _dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("evidence"))
    parser.add_argument("--implementation-sha", required=True)
    for name in (
        "smoke",
        "scale",
        "retrieval",
        "locomo-200",
        "locomo-full",
        "coding",
        "recovery-junit",
        "real-clients",
        "installed-smoke",
        "artifact-repro",
        "source-budget",
        "quality",
        "release-notes",
    ):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    print(json.dumps(build(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
