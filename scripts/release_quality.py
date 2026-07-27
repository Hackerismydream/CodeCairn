#!/usr/bin/env python3
"""Run the immutable implementation quality gates and write a release report."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = (("format", ("make", "format")), ("check", ("make", "check")), ("docs", ("make", "docs-check")))


def run(*, repository: Path, implementation_sha: str, dist: Path, output: Path) -> dict[str, object]:
    repository = repository.resolve()
    resolved = _git(repository, "rev-parse", f"{implementation_sha}^{{commit}}")
    head = _git(repository, "rev-parse", "HEAD")
    if resolved != implementation_sha or head != implementation_sha:
        raise ValueError("quality gates must run at the exact implementation SHA")
    if _status(repository):
        raise ValueError("quality gates require a clean implementation worktree")

    stages: list[dict[str, object]] = []
    fields: dict[str, bool] = {}
    for name, command in (*GATES, ("artifact_check", ("make", "artifact-check", f"DIST_DIR={dist.resolve()}"))):
        result = _stage(repository, name, command)
        stages.append(result)
        fields[name] = result["status"] == "pass"
        if not fields[name]:
            break
        if _status(repository):
            fields[name] = False
            result["status"] = "failed"
            result["failure"] = "gate modified the implementation worktree"
            break

    for name in ("format", "check", "docs", "artifact_check"):
        fields.setdefault(name, False)
    report = {
        "schema_version": 1,
        "implementation_sha": implementation_sha,
        **fields,
        "stages": stages,
        "worktree_clean_after_gates": not _status(repository),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _stage(repository: Path, name: str, command: tuple[str, ...]) -> dict[str, object]:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
    stdout = result.stdout.encode()
    stderr = result.stderr.encode()
    report: dict[str, object] = {
        "name": name,
        "status": "pass" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "duration_seconds": time.perf_counter() - started,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }
    if result.returncode:
        report["failure"] = (result.stderr or result.stdout)[-2_000:]
    return report


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True).stdout.strip()


def _status(repository: Path) -> str:
    return subprocess.run(("git", "-C", str(repository), "status", "--porcelain"), check=True, capture_output=True, text=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = run(
        repository=arguments.repo, implementation_sha=arguments.implementation_sha, dist=arguments.dist, output=arguments.output
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all(report[field] is True for field in ("format", "check", "docs", "artifact_check")) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"release-quality: {error}", file=sys.stderr)
        raise SystemExit(1) from error
