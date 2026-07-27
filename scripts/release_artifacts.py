#!/usr/bin/env python3
"""Verify curated artifacts and compare builds from two clean checkouts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SDIST_ROOT_FILES = {
    ".gitignore",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "PKG-INFO",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
}
FORBIDDEN_PARTS = {
    ".codecairn",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "benchmark_results",
    "benchmarks",
    "dist",
    "docs",
    "evidence",
    "htmlcov",
    "tests",
}
REQUIRED_PACKAGE_MEMBERS = {
    "codecairn/__init__.py",
    "codecairn/bootstrap.py",
    "codecairn/entrypoints/cli.py",
    "codecairn/entrypoints/mcp.py",
    "codecairn/evaluation/templates/resume.zh-CN.md",
    "codecairn/py.typed",
}


def verify_directory(directory: Path) -> dict[str, object]:
    wheels = sorted(directory.glob("codecairn-*.whl"))
    sdists = sorted(directory.glob("codecairn-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("artifact directory must contain exactly one CodeCairn wheel and one sdist")
    wheel_members = _wheel_members(wheels[0])
    sdist_members = _sdist_members(sdists[0])
    _verify_wheel(wheel_members)
    _verify_sdist(sdist_members)
    return {
        "schema_version": 1,
        "artifacts": {"wheel": _artifact_report(wheels[0], wheel_members), "sdist": _artifact_report(sdists[0], sdist_members)},
    }


def compare_builds(*, repository: Path, commit: str, output: Path | None) -> dict[str, object]:
    repository = repository.resolve()
    source_worktree_clean = not _run(("git", "-C", str(repository), "status", "--porcelain")).stdout
    resolved_commit = _run(("git", "-C", str(repository), "rev-parse", f"{commit}^{{commit}}")).stdout.strip()
    epoch = _run(("git", "-C", str(repository), "show", "-s", "--format=%ct", resolved_commit)).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="codecairn-build-compare-") as directory:
        temporary = Path(directory)
        reports: list[dict[str, object]] = []
        artifact_sets: list[dict[str, Path]] = []
        for ordinal in (1, 2):
            checkout = temporary / f"checkout-{ordinal}"
            _run(("git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repository), str(checkout)))
            _run(("git", "-C", str(checkout), "checkout", "--quiet", "--detach", resolved_commit))
            if _run(("git", "-C", str(checkout), "status", "--porcelain")).stdout:
                raise ValueError("build checkout is not clean")
            dist = temporary / f"dist-{ordinal}"
            environment = {**os.environ, "SOURCE_DATE_EPOCH": epoch, "PYTHONHASHSEED": "0"}
            _run(("uv", "build", "--clear", "--out-dir", str(dist)), cwd=checkout, environment=environment)
            reports.append(verify_directory(dist))
            artifact_sets.append({path.name: path for path in dist.iterdir() if path.is_file() and not path.name.startswith(".")})
        names = set(artifact_sets[0])
        if names != set(artifact_sets[1]):
            raise ValueError("build artifact filenames differ")
        comparisons = {}
        for name in sorted(names):
            left, right = artifact_sets[0][name], artifact_sets[1][name]
            left_members = _members(left)
            right_members = _members(right)
            if left_members != right_members:
                raise ValueError(f"unpacked artifact content differs: {name}")
            comparisons[name] = {
                "raw_sha256_1": _sha256(left.read_bytes()),
                "raw_sha256_2": _sha256(right.read_bytes()),
                "raw_equal": left.read_bytes() == right.read_bytes(),
                "unpacked_content_sha256": _inventory_sha256(left_members),
                "archive_metadata_variance_only": left.read_bytes() != right.read_bytes(),
            }
        report = {
            "schema_version": 1,
            "implementation_sha": resolved_commit,
            "source_worktree_clean": source_worktree_clean,
            "source_date_epoch": int(epoch),
            "clean_checkout_count": 2,
            "builds": reports,
            "comparisons": comparisons,
        }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _verify_wheel(members: dict[str, bytes]) -> None:
    names = set(members)
    _verify_paths(names)
    if not names >= REQUIRED_PACKAGE_MEMBERS:
        raise ValueError(f"wheel is missing runtime assets: {sorted(REQUIRED_PACKAGE_MEMBERS - names)}")
    if any(path.parts[0] != "codecairn" and not path.parts[0].endswith(".dist-info") for path in map(PurePosixPath, names)):
        raise ValueError("wheel contains a non-package, non-metadata top-level path")
    entry_points = [body.decode() for name, body in members.items() if name.endswith(".dist-info/entry_points.txt")]
    if len(entry_points) != 1 or "codecairn =" not in entry_points[0] or "codecairn-mcp =" not in entry_points[0]:
        raise ValueError("wheel entry points are incomplete")
    metadata = [body.decode() for name, body in members.items() if name.endswith(".dist-info/METADATA")]
    required_metadata = (
        "License-Expression: MIT",
        "Project-URL: Documentation,",
        "Project-URL: Issues,",
        "Requires-Python: <3.13,>=3.12",
    )
    if len(metadata) != 1 or any(value not in metadata[0] for value in required_metadata):
        raise ValueError("wheel publication metadata is incomplete")
    if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
        raise ValueError("wheel does not carry the MIT license")


def _verify_sdist(members: dict[str, bytes]) -> None:
    names = set(members)
    _verify_paths(names)
    roots = {PurePosixPath(name).parts[0] for name in names}
    if not roots <= SDIST_ROOT_FILES | {"src"}:
        raise ValueError(f"sdist contains non-allowlisted roots: {sorted(roots - SDIST_ROOT_FILES - {'src'})}")
    required = {f"src/{name}" for name in REQUIRED_PACKAGE_MEMBERS}
    if not required <= names or not {"LICENSE", "README.md", "pyproject.toml"} <= names:
        raise ValueError("sdist is missing build or runtime files")


def _verify_paths(names: set[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or FORBIDDEN_PARTS & set(path.parts) or name.endswith((".pyc", ".pyo")):
            raise ValueError(f"artifact contains forbidden path: {name}")


def _artifact_report(path: Path, members: dict[str, bytes]) -> dict[str, object]:
    return {
        "filename": path.name,
        "sha256": _sha256(path.read_bytes()),
        "size_bytes": path.stat().st_size,
        "member_count": len(members),
        "inventory_sha256": _inventory_sha256(members),
        "members": [{"path": name, "sha256": _sha256(body), "size_bytes": len(body)} for name, body in sorted(members.items())],
    }


def _members(path: Path) -> dict[str, bytes]:
    return _wheel_members(path) if path.suffix == ".whl" else _sdist_members(path)


def _wheel_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}


def _sdist_members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        files = [member for member in archive.getmembers() if member.isfile()]
        if not files:
            raise ValueError("sdist is empty")
        prefixes = {PurePosixPath(member.name).parts[0] for member in files}
        if len(prefixes) != 1:
            raise ValueError("sdist must have one archive root")
        prefix = next(iter(prefixes))
        result = {}
        for member in files:
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read sdist member: {member.name}")
            result[str(PurePosixPath(member.name).relative_to(prefix))] = source.read()
        return result


def _inventory_sha256(members: dict[str, bytes]) -> str:
    inventory = [{"path": name, "sha256": _sha256(body), "size_bytes": len(body)} for name, body in sorted(members.items())]
    return _sha256(json.dumps(inventory, separators=(",", ":"), sort_keys=True).encode())


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run(
    command: tuple[str, ...], *, cwd: Path | None = None, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=environment, check=True, capture_output=True, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("directory", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("--repo", type=Path, default=ROOT)
    compare.add_argument("--commit", default="HEAD")
    compare.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report: dict[str, Any] = (
        verify_directory(arguments.directory)
        if arguments.command == "verify"
        else compare_builds(repository=arguments.repo, commit=arguments.commit, output=arguments.output)
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
