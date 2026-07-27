from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codecairn.bootstrap import app, create_application
from codecairn.entrypoints.cli import build_app

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "codex" / "failed_command.jsonl"


def test_cli_import_list_recall_and_doctor(tmp_path: Path) -> None:
    runner = CliRunner()
    root = tmp_path / "runtime"
    test_app = build_app(lambda path: create_application(path, test_retrieval=True))
    common = ["--repo-key", "acme/widgets", "--root", str(root)]

    imported = runner.invoke(
        test_app,
        ["import", str(FIXTURE), *common, "--no-index", "--finalize"],
    )
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["created_memory_count"] == 1

    listed = runner.invoke(test_app, ["list", *common])
    assert listed.exit_code == 0, listed.output
    memories = json.loads(listed.output)
    assert memories[0]["memory_type"] == "task_experience"

    processed = runner.invoke(
        test_app,
        ["process", "--root", str(root), "--worker-id", "test"],
    )
    assert processed.exit_code == 0, processed.output
    assert json.loads(processed.output)["pending"] == 1

    recalled = runner.invoke(test_app, ["recall", "pytest failure", *common])
    assert recalled.exit_code == 0, recalled.output
    assert json.loads(recalled.output)["sidecar"]["ranked"]

    doctor = runner.invoke(test_app, ["doctor", "--root", str(root)])
    assert doctor.exit_code == 0, doctor.output
    assert json.loads(doctor.output)["status"] == "ok"


def test_cli_verifies_historical_bundle_without_runtime_provider() -> None:
    result = CliRunner().invoke(
        app,
        ["evidence", "verify", str(ROOT / "evidence" / "benchmark-v3")],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verified"] is True
