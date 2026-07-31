"""Bounded fixture staging and external verification for the v0.3 Agent task."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256
from codecairn_v03_acceptance.bounded_process import run_bounded_process

SCENARIO_ID = "retry-policy-v1"
STAGE_CONTRACT = "codecairn.v03-acceptance.retry-policy-stage.v1"
VERIFICATION_CONTRACT = "codecairn.v03-acceptance.retry-policy-verification.v1"

_RETRY_POLICY_PATH = "retry_policy.py"
_FROZEN_TEST_PATH = "test_retry_policy.py"
_TRACKED_PATHS = (_RETRY_POLICY_PATH, _FROZEN_TEST_PATH)
_EXPECTED_FIXTURE = {
    _RETRY_POLICY_PATH: {"bytes": 20, "sha256": "60fe858d0f162797969b14a7dc70e57ece896e52d9bc1f71d6ab0d1a62352224"},
    _FROZEN_TEST_PATH: {"bytes": 264, "sha256": "00b5e8002dc4c2c016e5719c319d8806b9e39a8d7f779d4c6a317753200ce5ed"},
}
_HIDDEN_DECISION_MARKER = b"codecairn-v03:retry-policy:hidden-decision:expected-4"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def stage_retry_policy_scenario(*, fixture_dir: Path, workspace: Path) -> dict[str, object]:
    """Copy the pinned retry-policy fixture into an existing empty workspace."""
    fixture_root = _existing_plain_directory(fixture_dir, field="fixture directory")
    workspace_root = _existing_plain_directory(workspace, field="workspace")
    if any(workspace_root.iterdir()):
        raise ValueError("retry-policy workspace must be empty before staging")

    fixture_entries = {entry.name: entry for entry in fixture_root.iterdir()}
    if set(fixture_entries) != set(_TRACKED_PATHS):
        raise ValueError("retry-policy fixture must contain exactly the pinned tracked files")
    for relative_path in _TRACKED_PATHS:
        source = fixture_entries[relative_path]
        if source.is_symlink() or not source.is_file():
            raise ValueError("retry-policy fixture files must be regular files, not symlinks")
        metadata = _file_metadata(source)
        if metadata != {"path": relative_path, **_EXPECTED_FIXTURE[relative_path]}:
            raise ValueError(f"retry-policy fixture digest mismatch: {relative_path}")
    if not _module_is_exact_integer_assignment(fixture_entries[_RETRY_POLICY_PATH], expected=2):
        raise ValueError("retry-policy fixture must start with DEFAULT_RETRIES = 2")

    for relative_path in _TRACKED_PATHS:
        destination = workspace_root / relative_path
        with destination.open("xb") as stream:
            stream.write(fixture_entries[relative_path].read_bytes())
        destination.chmod(0o600)

    staged_files = [_file_metadata(workspace_root / relative_path) for relative_path in _TRACKED_PATHS]
    receipt = _stage_receipt(staged_files)
    if receipt != _expected_stage_receipt():
        raise ValueError("staged retry-policy fixture does not match the pinned receipt")
    return receipt


def verify_retry_policy_scenario(
    *, workspace: Path, stage_receipt: object, python_executable: Path, timeout_seconds: int = 10, max_output_bytes: int = 65_536
) -> dict[str, object]:
    """Verify the Agent result without trusting the Agent's own success claim."""
    expected_receipt = _expected_stage_receipt()
    if stage_receipt != expected_receipt:
        raise ValueError("retry-policy stage receipt is not the pinned immutable receipt")
    if timeout_seconds < 1 or timeout_seconds > 60:
        raise ValueError("retry-policy test timeout must be between 1 and 60 seconds")
    if max_output_bytes < 1_024 or max_output_bytes > 1_048_576:
        raise ValueError("retry-policy output cap must be between 1024 and 1048576 bytes")

    workspace_root = _existing_plain_directory(workspace, field="workspace")
    python = _installed_python(python_executable, workspace=workspace_root)
    baseline = {
        cast(str, entry["path"]): {"bytes": cast(int, entry["bytes"]), "sha256": cast(str, entry["sha256"])}
        for entry in cast(list[dict[str, object]], expected_receipt["tracked_files"])
    }
    inventory = _workspace_inventory(workspace_root)
    actual_files = cast(dict[str, dict[str, object]], inventory["files"])
    actual_paths = set(actual_files)
    tracked_paths = set(_TRACKED_PATHS)
    changed_paths = sorted(
        relative_path
        for relative_path in tracked_paths & actual_paths
        if {"bytes": actual_files[relative_path]["bytes"], "sha256": actual_files[relative_path]["sha256"]} != baseline[relative_path]
    )
    unexpected_paths = sorted(
        (actual_paths - tracked_paths)
        | set(cast(list[str], inventory["directories"]))
        | set(cast(list[str], inventory["special_files"]))
    )
    symlink_paths = cast(list[str], inventory["symlinks"])

    retry_policy = workspace_root / _RETRY_POLICY_PATH
    exact_tracked_files = actual_paths == tracked_paths and not unexpected_paths
    no_symlinks = not symlink_paths
    frozen_test_unchanged = (
        _FROZEN_TEST_PATH in actual_files
        and {"bytes": actual_files[_FROZEN_TEST_PATH]["bytes"], "sha256": actual_files[_FROZEN_TEST_PATH]["sha256"]}
        == baseline[_FROZEN_TEST_PATH]
    )
    only_retry_policy_changed = changed_paths == [_RETRY_POLICY_PATH]
    hidden_marker_absent = (
        exact_tracked_files
        and no_symlinks
        and all(_HIDDEN_DECISION_MARKER not in (workspace_root / relative_path).read_bytes() for relative_path in _TRACKED_PATHS)
    )
    ast_exact_integer_four = (
        _RETRY_POLICY_PATH in actual_files and retry_policy.is_file() and _module_is_exact_integer_assignment(retry_policy, expected=4)
    )
    preflight_checks = {
        "workspace_has_exact_tracked_files": exact_tracked_files,
        "no_symlinks": no_symlinks,
        "frozen_test_unchanged": frozen_test_unchanged,
        "only_retry_policy_changed": only_retry_policy_changed,
        "hidden_decision_marker_absent": hidden_marker_absent,
        "retry_policy_ast_exact_integer_4": ast_exact_integer_four,
    }
    command = _fixed_unittest_command(python, workspace_root)
    if all(preflight_checks.values()):
        test_receipt = _run_fixed_unittest(
            command=command, workspace=workspace_root, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes
        )
    else:
        test_receipt = _not_run_receipt(command=command, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)

    post_inventory = _workspace_inventory(workspace_root)
    workspace_stable_after_test = post_inventory == inventory
    fixed_unittest_passed = (
        test_receipt["terminal_class"] == "completed" and test_receipt["exit_code"] == 0 and workspace_stable_after_test
    )
    checks = {
        **preflight_checks,
        "workspace_stable_after_test": workspace_stable_after_test,
        "fixed_unittest_passed": fixed_unittest_passed,
    }
    violations = [check for check, passed in checks.items() if not passed]
    return {
        "schema_version": 1,
        "contract": VERIFICATION_CONTRACT,
        "scenario_id": SCENARIO_ID,
        "python": {"path": str(python), "bytes": python.stat().st_size, "sha256": file_sha256(python)},
        "changed_paths": changed_paths,
        "unexpected_paths": unexpected_paths,
        "symlink_paths": symlink_paths,
        "checks": checks,
        "test": test_receipt,
        "violations": violations,
        "task_verified": not violations,
    }


def _stage_receipt(staged_files: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": STAGE_CONTRACT,
        "scenario_id": SCENARIO_ID,
        "tracked_files": staged_files,
        "allowed_changed_paths": [_RETRY_POLICY_PATH],
        "frozen_paths": [_FROZEN_TEST_PATH],
    }


def _expected_stage_receipt() -> dict[str, object]:
    return _stage_receipt([{"path": relative_path, **_EXPECTED_FIXTURE[relative_path]} for relative_path in _TRACKED_PATHS])


def _existing_plain_directory(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{field} must be an existing regular directory")
    return path.resolve(strict=True)


def _installed_python(path: Path, *, workspace: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("python_executable must be an explicit absolute path")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("python_executable does not exist") from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("python_executable must resolve to an executable regular file")
    if resolved == workspace or resolved.is_relative_to(workspace):
        raise ValueError("python_executable must be installed outside the Agent workspace")
    return resolved


def _file_metadata(path: Path) -> dict[str, object]:
    digest = file_sha256(path)
    if not _SHA256.fullmatch(digest):
        raise ValueError("file SHA-256 helper returned an invalid digest")
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": digest}


def _workspace_inventory(workspace: Path) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    directories: list[str] = []
    symlinks: list[str] = []
    special_files: list[str] = []
    for root, directory_names, file_names in os.walk(workspace, topdown=True, followlinks=False):
        root_path = Path(root)
        for name in list(directory_names):
            path = root_path / name
            relative_path = path.relative_to(workspace).as_posix()
            if path.is_symlink():
                symlinks.append(relative_path)
                directory_names.remove(name)
            else:
                directories.append(relative_path)
        for name in file_names:
            path = root_path / name
            relative_path = path.relative_to(workspace).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                symlinks.append(relative_path)
            elif stat.S_ISREG(mode):
                metadata = _file_metadata(path)
                files[relative_path] = {"bytes": metadata["bytes"], "sha256": metadata["sha256"]}
            else:
                special_files.append(relative_path)
    return {
        "files": dict(sorted(files.items())),
        "directories": sorted(directories),
        "symlinks": sorted(symlinks),
        "special_files": sorted(special_files),
    }


def _module_is_exact_integer_assignment(path: Path, *, expected: int) -> bool:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    if len(module.body) != 1:
        return False
    statement = module.body[0]
    return (
        isinstance(statement, ast.Assign)
        and statement.type_comment is None
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "DEFAULT_RETRIES"
        and isinstance(statement.value, ast.Constant)
        and type(statement.value.value) is int
        and statement.value.value == expected
    )


def _fixed_unittest_command(python: Path, workspace: Path) -> tuple[str, ...]:
    return (str(python), "-I", "-B", "-m", "unittest", "discover", "-s", str(workspace), "-p", _FROZEN_TEST_PATH, "-t", str(workspace))


def _run_fixed_unittest(*, command: tuple[str, ...], workspace: Path, timeout_seconds: int, max_output_bytes: int) -> dict[str, object]:
    result = run_bounded_process(
        command,
        cwd=workspace,
        environment={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        timeout_seconds=timeout_seconds,
        stdout_limit=max_output_bytes,
        stderr_limit=max_output_bytes,
    )
    terminal_class: str = result.terminal
    if terminal_class in {"stdout_limit", "stderr_limit"}:
        terminal_class = "output_limit"
    elif terminal_class == "exited":
        terminal_class = "completed" if result.exit_code == 0 else "test_failure"
    return {
        "terminal_class": terminal_class,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "timeout_seconds": timeout_seconds,
        "output_limit_bytes_per_stream": max_output_bytes,
        "command_sha256": canonical_sha256(list(command)),
        "stdout": _output_receipt(result.stdout),
        "stderr": _output_receipt(result.stderr),
    }


def _not_run_receipt(*, command: tuple[str, ...], timeout_seconds: int, max_output_bytes: int) -> dict[str, object]:
    empty = b""
    return {
        "terminal_class": "not_run",
        "exit_code": None,
        "duration_ms": 0,
        "timeout_seconds": timeout_seconds,
        "output_limit_bytes_per_stream": max_output_bytes,
        "command_sha256": canonical_sha256(list(command)),
        "stdout": _output_receipt(empty),
        "stderr": _output_receipt(empty),
    }


def _output_receipt(output: bytes) -> dict[str, object]:
    return {"bytes": len(output), "sha256": hashlib.sha256(output).hexdigest()}
