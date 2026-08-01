from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from click import unstyle
from typer.testing import CliRunner

from codecairn.bootstrap import app, create_application, create_runtime
from codecairn.entrypoints.cli import build_app
from codecairn.memory.schema import CodingMemory, RepositoryKnowledgePayload
from tests.retrieval_fakes import TEST_RETRIEVAL

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "codex" / "failed_command.jsonl"


def test_cli_import_list_recall_and_doctor(tmp_path: Path, monkeypatch: Any) -> None:
    runner = CliRunner()
    root = tmp_path / "runtime"
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    monkeypatch.chdir(repository)

    def factory(path: Path, **kwargs: Any) -> Any:
        return create_application(path, retrieval_adapters=TEST_RETRIEVAL, **kwargs)

    test_app = build_app(factory)
    initialized = runner.invoke(test_app, ["init", "--root", str(root), "--repo-key", "acme/widgets"])
    assert initialized.exit_code == 0, initialized.output

    imported = runner.invoke(test_app, ["import", str(FIXTURE), "--no-index", "--finalize"])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["created_memory_count"] == 1

    listed = runner.invoke(test_app, ["list"])
    assert listed.exit_code == 0, listed.output
    memories = json.loads(listed.output)
    assert memories[0]["memory_type"] == "task_experience"

    processed = runner.invoke(test_app, ["process", "--worker-id", "test"])
    assert processed.exit_code == 0, processed.output
    assert json.loads(processed.output)["semantic"]["pending"] == 1

    recalled = runner.invoke(test_app, ["recall", "pytest failure"])
    assert recalled.exit_code == 0, recalled.output
    assert json.loads(recalled.output)["sidecar"]["ranked"]

    doctor = runner.invoke(test_app, ["doctor", "--format", "json"])
    assert doctor.exit_code == 0, doctor.output
    assert json.loads(doctor.output)["status"] == "ok"

    remembered = runner.invoke(
        test_app,
        [
            "remember",
            "repository_knowledge",
            "Use pytest for repository checks.",
            "--title",
            "Repository checks",
            "--subject-key",
            "repository checks",
        ],
    )
    assert remembered.exit_code == 0, remembered.output
    memory_id = json.loads(remembered.output)["memory_id"]
    shown = runner.invoke(test_app, ["memory", "show", memory_id])
    assert shown.exit_code == 0
    assert json.loads(shown.output)["memory_id"] == memory_id

    exported = runner.invoke(test_app, ["namespace", "export", "--output", str(tmp_path / "export")])
    assert exported.exit_code == 0, exported.output
    assert (tmp_path / "export" / "manifest.json").is_file()

    preview = runner.invoke(test_app, ["namespace", "reset", "--dry-run"])
    assert preview.exit_code == 0
    assert json.loads(preview.output)["memory_count"] == 2
    other = CodingMemory.create(
        repo_key="other/repository",
        memory_type="repository_knowledge",
        title="Other namespace",
        content="Must remain.",
        category="other",
        tags=(),
        created_at_ms=0,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=RepositoryKnowledgePayload(subject_key="other", claim="Must remain."),
    )
    create_runtime(root, retrieval_adapters=TEST_RETRIEVAL).store_memory(other)

    reset = runner.invoke(test_app, ["namespace", "reset", "--confirm", "acme/widgets"])
    assert reset.exit_code == 0, reset.output
    assert json.loads(reset.output)["backup"]
    assert json.loads(runner.invoke(test_app, ["list"]).output) == []
    assert create_runtime(root, retrieval_adapters=TEST_RETRIEVAL).list_memories(repo_key="other/repository") == (other,)


def test_cli_verifies_historical_bundle_without_runtime_provider() -> None:
    result = CliRunner().invoke(app, ["evidence", "verify", str(ROOT / "evidence" / "benchmark-v3")])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verified"] is True


def test_cli_help_exposes_only_user_facing_operations() -> None:
    runner = CliRunner()
    root_help = runner.invoke(app, ["--help"])
    hook_help = runner.invoke(app, ["hook", "--help"])
    process_help = runner.invoke(app, ["process", "--help"])

    assert root_help.exit_code == hook_help.exit_code == process_help.exit_code == 0
    root_output = unstyle(root_help.output)
    hook_output = unstyle(hook_help.output)
    process_output = unstyle(process_help.output)
    assert "--install-completion" not in root_output
    assert "--show-completion" not in root_output
    assert "evidence " not in root_output
    assert "run " not in hook_output
    assert "install " in hook_output
    assert "--worker-id" not in process_output
    assert "--retry-failed" not in process_output
