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
    "v03-acceptance": {"core": 16_200, "total": 25_000},
    "release": {"core": 9_700, "total": 14_100},
}

INTERNAL_TARGETS = {"core": 9_700, "total": 14_100}
POST_V01_INTERNAL_TARGETS = {
    "v02-001": STAGE_LIMITS["v02-001"],
    "v02-002": STAGE_LIMITS["v02-002"],
    "v03-acceptance": STAGE_LIMITS["v03-acceptance"],
}


@dataclass(frozen=True, slots=True)
class SourceBudgetReport:
    schema_version: int
    commit: str
    dirty: bool | None
    stage: str
    root: str
    included_roots: tuple[str, ...]
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
    """Count newline-delimited physical source lines in the selected maintained scope."""
    repository_root = root.resolve()
    source_root = repository_root / "src" / "codecairn"
    if not source_root.is_dir():
        raise ValueError(f"CodeCairn source root is missing: {source_root}")
    limits = STAGE_LIMITS.get(stage)
    if limits is None:
        raise ValueError(f"Unknown source-budget stage: {stage}")

    included_roots: tuple[Path, ...] = (source_root,)
    evaluation_roots: tuple[Path, ...] = (source_root / "evaluation",)
    included_suffixes = {".py"}
    if stage == "v03-acceptance":
        hub_api_root = repository_root / "apps" / "hub-api" / "src" / "codecairn_hub_api"
        hub_web_roots = (
            repository_root / "apps" / "hub-web" / "app",
            repository_root / "apps" / "hub-web" / "lib",
            repository_root / "apps" / "hub-web" / "worker",
            repository_root / "apps" / "hub-web" / "next.config.ts",
            repository_root / "apps" / "hub-web" / "vite.config.ts",
        )
        acceptance_root = repository_root / "tools" / "v03-acceptance" / "src" / "codecairn_v03_acceptance"
        hub_launcher = repository_root / "scripts" / "run_hub.py"
        if (
            not hub_api_root.is_dir()
            or any(not root.exists() for root in hub_web_roots)
            or not acceptance_root.is_dir()
            or not hub_launcher.is_file()
        ):
            raise ValueError("Version 0.3 maintained source roots are incomplete")
        included_roots = (source_root, hub_api_root, *hub_web_roots, acceptance_root, hub_launcher)
        evaluation_roots = (source_root / "evaluation", acceptance_root)
        included_suffixes = {".py", ".ts", ".tsx", ".css"}
    paths = tuple(
        sorted(
            {
                path
                for included_root in included_roots
                for path in ((included_root,) if included_root.is_file() else tuple(included_root.rglob("*")))
                if path.is_file() and path.suffix in included_suffixes
            }
        )
    )
    if not paths:
        raise ValueError(f"No Python source files found under: {source_root}")
    if any(path.is_symlink() for path in paths):
        raise ValueError("Source-budget inputs must not contain symbolic links")

    evaluation_paths = tuple(path for path in paths if any(path.is_relative_to(root) for root in evaluation_roots))
    core_paths = tuple(path for path in paths if path not in evaluation_paths)
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
        included_roots=tuple(relative(path) for path in included_roots),
        included_paths=tuple(relative(path) for path in paths),
        core_paths=tuple(relative(path) for path in core_paths),
        evaluation_paths=tuple(relative(path) for path in evaluation_paths),
        core=core,
        evaluation=evaluation,
        total=total,
        limits=dict(limits),
        internal_targets=dict(POST_V01_INTERNAL_TARGETS.get(stage, INTERNAL_TARGETS)),
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
        f"included-roots={','.join(report.included_roots)}",
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
