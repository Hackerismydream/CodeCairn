#!/usr/bin/env python3
"""Deterministically enforce CodeCairn's physical Python source budget."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

ACCEPTED_BASELINE = {"commit": "954f72842261074ed74c5a1fff664ae25ae4857a", "core": 17_250, "evaluation": 16_841, "total": 34_091}

STAGE_LIMITS = {
    "v01-000a": {"core": 17_250, "total": 34_300},
    "v01-001": {"core": 15_500, "total": 34_091},
    "v01-002": {"core": 15_500, "total": 34_091},
    "v01-003": {"core": 15_500, "total": 34_091},
    "v01-004": {"core": 11_500, "total": 34_091},
    "v01-005": {"core": 12_650, "total": 34_091},
    "v01-006": {"core": 13_200, "total": 34_091},
    "v01-007": {"core": 10_000, "total": 34_091},
    "v01-008": {"core": 9_700, "total": 14_100},
    "v02-001": {"core": 10_625, "total": 14_900},
    "v02-002": {"core": 11_000, "total": 15_300},
    "v02-004": {"core": 11_125, "total": 15_435},
    "release": {"core": 9_700, "total": 14_100},
}

INTERNAL_TARGETS = {"core": 9_700, "total": 14_100}
V02_INTERNAL_TARGETS = {stage: STAGE_LIMITS[stage] for stage in ("v02-001", "v02-002", "v02-004")}


@dataclass(frozen=True, slots=True)
class SourceBudgetReport:
    schema_version: int
    commit: str
    dirty: bool | None
    stage: str
    root: str
    included_paths: tuple[str, ...]
    core_paths: tuple[str, ...]
    evaluation_paths: tuple[str, ...]
    core: int
    evaluation: int
    total: int
    limits: dict[str, int]
    internal_targets: dict[str, int]
    accepted_baseline: dict[str, int | str]
    passed: bool
    violations: tuple[str, ...]


def build_report(root: Path, *, stage: str) -> SourceBudgetReport:
    """Count newline-delimited physical Python lines under ``src/codecairn``."""
    repository_root = root.resolve()
    source_root = repository_root / "src" / "codecairn"
    if not source_root.is_dir():
        raise ValueError(f"CodeCairn source root is missing: {source_root}")
    limits = STAGE_LIMITS.get(stage)
    if limits is None:
        raise ValueError(f"Unknown source-budget stage: {stage}")

    paths = tuple(sorted(path for path in source_root.rglob("*.py") if path.is_file()))
    if not paths:
        raise ValueError(f"No Python source files found under: {source_root}")
    if any(path.is_symlink() for path in paths):
        raise ValueError("Source-budget inputs must not contain symbolic links")

    evaluation_root = source_root / "evaluation"
    evaluation_paths = tuple(path for path in paths if path.is_relative_to(evaluation_root))
    core_paths = tuple(path for path in paths if not path.is_relative_to(evaluation_root))
    core = sum(_physical_lines(path) for path in core_paths)
    evaluation = sum(_physical_lines(path) for path in evaluation_paths)
    total = core + evaluation
    violations = tuple(
        message
        for name, observed in (("core", core), ("total", total))
        if observed > limits[name]
        for message in (f"{name}={observed} exceeds {stage} limit={limits[name]}",)
    )

    def relative(path: Path) -> str:
        return path.relative_to(repository_root).as_posix()

    commit, dirty = _git_state(repository_root)
    return SourceBudgetReport(
        schema_version=1,
        commit=commit,
        dirty=dirty,
        stage=stage,
        root=source_root.relative_to(repository_root).as_posix(),
        included_paths=tuple(relative(path) for path in paths),
        core_paths=tuple(relative(path) for path in core_paths),
        evaluation_paths=tuple(relative(path) for path in evaluation_paths),
        core=core,
        evaluation=evaluation,
        total=total,
        limits=dict(limits),
        internal_targets=dict(V02_INTERNAL_TARGETS.get(stage, INTERNAL_TARGETS)),
        accepted_baseline=dict(ACCEPTED_BASELINE),
        passed=not violations,
        violations=violations,
    )


def render_text(report: SourceBudgetReport) -> str:
    lines = [
        f"source-budget stage={report.stage} commit={report.commit} dirty={report.dirty}",
        (f"counts core={report.core} evaluation={report.evaluation} total={report.total}"),
        f"limits core={report.limits['core']} total={report.limits['total']}",
        (f"targets core={report.internal_targets['core']} total={report.internal_targets['total']}"),
        (
            "accepted-baseline "
            f"commit={report.accepted_baseline['commit']} "
            f"core={report.accepted_baseline['core']} "
            f"evaluation={report.accepted_baseline['evaluation']} "
            f"total={report.accepted_baseline['total']}"
        ),
        "included-paths:",
        *(f"  {path}" for path in report.included_paths),
        f"result={'pass' if report.passed else 'fail'}",
        *(f"violation: {violation}" for violation in report.violations),
    ]
    return "\n".join(lines)


def _physical_lines(path: Path) -> int:
    return path.read_bytes().count(b"\n")


def _git_state(root: Path) -> tuple[str, bool | None]:
    commit_result = subprocess.run(("git", "-C", str(root), "rev-parse", "HEAD"), check=False, capture_output=True, text=True)
    commit = commit_result.stdout.strip()
    if commit_result.returncode != 0 or len(commit) != 40:
        return "unavailable", None
    status_result = subprocess.run(("git", "-C", str(root), "status", "--porcelain"), check=False, capture_output=True, text=True)
    dirty = None if status_result.returncode != 0 else bool(status_result.stdout)
    return commit, dirty


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--stage", choices=tuple(STAGE_LIMITS), default="v01-000a")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    report = build_report(args.root, stage=args.stage)
    if args.format == "json":
        print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
