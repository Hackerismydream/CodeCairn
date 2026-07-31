from __future__ import annotations

import sys
from pathlib import Path

import pytest
from codecairn_v03_acceptance.scenario import (
    SCENARIO_ID,
    STAGE_CONTRACT,
    VERIFICATION_CONTRACT,
    stage_retry_policy_scenario,
    verify_retry_policy_scenario,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "scenarios" / "retry-policy"
_HIDDEN_DECISION_MARKER = "codecairn-v03:retry-policy:hidden-decision:expected-4"


def test_stages_pinned_fixture_only_into_an_empty_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    receipt = stage_retry_policy_scenario(fixture_dir=_FIXTURE_DIR, workspace=workspace)

    assert receipt == {
        "schema_version": 1,
        "contract": STAGE_CONTRACT,
        "scenario_id": SCENARIO_ID,
        "tracked_files": [
            {"path": "retry_policy.py", "bytes": 20, "sha256": "60fe858d0f162797969b14a7dc70e57ece896e52d9bc1f71d6ab0d1a62352224"},
            {
                "path": "test_retry_policy.py",
                "bytes": 264,
                "sha256": "00b5e8002dc4c2c016e5719c319d8806b9e39a8d7f779d4c6a317753200ce5ed",
            },
        ],
        "allowed_changed_paths": ["retry_policy.py"],
        "frozen_paths": ["test_retry_policy.py"],
    }
    assert (workspace / "retry_policy.py").read_text(encoding="utf-8") == "DEFAULT_RETRIES = 2\n"
    assert sorted(path.name for path in workspace.iterdir()) == ["retry_policy.py", "test_retry_policy.py"]

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("owned by the caller", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        stage_retry_policy_scenario(fixture_dir=_FIXTURE_DIR, workspace=occupied)


def test_external_verifier_accepts_only_the_exact_retry_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    receipt = stage_retry_policy_scenario(fixture_dir=_FIXTURE_DIR, workspace=workspace)
    (workspace / "retry_policy.py").write_text("DEFAULT_RETRIES = 4\n", encoding="utf-8")

    result = verify_retry_policy_scenario(workspace=workspace, stage_receipt=receipt, python_executable=Path(sys.executable))

    assert result["contract"] == VERIFICATION_CONTRACT
    assert result["changed_paths"] == ["retry_policy.py"]
    assert result["unexpected_paths"] == []
    assert result["symlink_paths"] == []
    assert result["violations"] == []
    assert result["task_verified"] is True
    assert result["checks"] == {
        "workspace_has_exact_tracked_files": True,
        "no_symlinks": True,
        "frozen_test_unchanged": True,
        "only_retry_policy_changed": True,
        "hidden_decision_marker_absent": True,
        "retry_policy_ast_exact_integer_4": True,
        "workspace_stable_after_test": True,
        "fixed_unittest_passed": True,
    }
    test_receipt = result["test"]
    assert isinstance(test_receipt, dict)
    assert test_receipt["terminal_class"] == "completed"
    assert test_receipt["exit_code"] == 0
    assert test_receipt["output_limit_bytes_per_stream"] == 65_536
    assert test_receipt["stdout"] == {"bytes": 0, "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}
    assert isinstance(test_receipt["stderr"], dict)
    assert len(test_receipt["stderr"]["sha256"]) == 64
    assert not (workspace / "__pycache__").exists()


def test_verifier_rejects_frozen_test_changes_without_executing_them(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    receipt = stage_retry_policy_scenario(fixture_dir=_FIXTURE_DIR, workspace=workspace)
    (workspace / "retry_policy.py").write_text("DEFAULT_RETRIES = 4\n", encoding="utf-8")
    with (workspace / "test_retry_policy.py").open("a", encoding="utf-8") as stream:
        stream.write("\nraise RuntimeError('must not execute')\n")

    result = verify_retry_policy_scenario(workspace=workspace, stage_receipt=receipt, python_executable=Path(sys.executable))

    assert result["task_verified"] is False
    assert "frozen_test_unchanged" in result["violations"]
    assert "only_retry_policy_changed" in result["violations"]
    assert result["test"]["terminal_class"] == "not_run"


@pytest.mark.parametrize(
    ("mutation", "expected_violation"),
    [
        ("hidden_marker", "hidden_decision_marker_absent"),
        ("unexpected_file", "workspace_has_exact_tracked_files"),
        ("non_literal", "retry_policy_ast_exact_integer_4"),
    ],
)
def test_verifier_rejects_bounded_workspace_violations(tmp_path: Path, mutation: str, expected_violation: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    receipt = stage_retry_policy_scenario(fixture_dir=_FIXTURE_DIR, workspace=workspace)
    retry_policy = workspace / "retry_policy.py"
    if mutation == "hidden_marker":
        retry_policy.write_text(f"# {_HIDDEN_DECISION_MARKER}\nDEFAULT_RETRIES = 4\n", encoding="utf-8")
    elif mutation == "unexpected_file":
        retry_policy.write_text("DEFAULT_RETRIES = 4\n", encoding="utf-8")
        (workspace / "agent-notes.txt").write_text("extra output", encoding="utf-8")
    else:
        retry_policy.write_text("DEFAULT_RETRIES = 2 + 2\n", encoding="utf-8")

    result = verify_retry_policy_scenario(workspace=workspace, stage_receipt=receipt, python_executable=Path(sys.executable))

    assert result["task_verified"] is False
    assert expected_violation in result["violations"]
    assert result["test"]["terminal_class"] == "not_run"


def test_verifier_rejects_symlinks_and_tampered_stage_receipts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    receipt = stage_retry_policy_scenario(fixture_dir=_FIXTURE_DIR, workspace=workspace)
    retry_policy = workspace / "retry_policy.py"
    retry_policy.unlink()
    retry_policy.symlink_to(tmp_path / "outside.py")

    result = verify_retry_policy_scenario(workspace=workspace, stage_receipt=receipt, python_executable=Path(sys.executable))

    assert result["task_verified"] is False
    assert result["symlink_paths"] == ["retry_policy.py"]
    assert "no_symlinks" in result["violations"]
    assert result["test"]["terminal_class"] == "not_run"

    tampered_receipt = {**receipt, "allowed_changed_paths": ["test_retry_policy.py"]}
    with pytest.raises(ValueError, match="not the pinned immutable receipt"):
        verify_retry_policy_scenario(workspace=workspace, stage_receipt=tampered_receipt, python_executable=Path(sys.executable))
