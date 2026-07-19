import json
from pathlib import Path

from typer.testing import CliRunner

from codecairn.bootstrap import app, create_cascade

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"
CLAUDE_FIXTURE = Path(__file__).parent / "fixtures" / "claude" / "failed_command.jsonl"


def test_cli_import_and_list_share_the_runtime_contract(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runner = CliRunner()

    imported = runner.invoke(
        app,
        [
            "import",
            str(FIXTURE),
            "--repo-key",
            "acme/widgets",
            "--root",
            str(root),
        ],
    )

    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["created_memory_count"] == 1

    listed = runner.invoke(
        app,
        ["list", "--repo-key", "acme/widgets", "--root", str(root)],
    )
    assert listed.exit_code == 0, listed.output
    memories = json.loads(listed.stdout)
    assert len(memories) == 1
    assert memories[0]["command"] == "uv run pytest"
    assert [item["raw_event_index"] for item in memories[0]["evidence"]] == [2, 3]


def test_cli_import_auto_detects_claude_code(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runner = CliRunner()

    imported = runner.invoke(
        app,
        [
            "import",
            str(CLAUDE_FIXTURE),
            "--repo-key",
            "acme/widgets",
            "--root",
            str(root),
        ],
    )

    assert imported.exit_code == 0, imported.output
    result = json.loads(imported.stdout)
    assert result["provider"] == "claude"
    assert result["created_memory_count"] == 1


def test_cli_recall_emits_markdown_and_a_structured_sidecar(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runner = CliRunner()
    imported = runner.invoke(
        app,
        ["import", str(FIXTURE), "--repo-key", "acme/widgets", "--root", str(root)],
    )
    assert imported.exit_code == 0, imported.output
    create_cascade(root).run_until_idle(worker_id="test")

    recalled = runner.invoke(
        app,
        [
            "recall",
            "pytest command failed",
            "--repo-key",
            "acme/widgets",
            "--root",
            str(root),
        ],
    )

    assert recalled.exit_code == 0, recalled.output
    result = json.loads(recalled.stdout)
    assert result["markdown"].startswith("# Recall Context")
    assert result["sidecar"]["ranked"][0]["memory_id"].startswith("memory_")
    assert result["sidecar"]["ranked"][0]["candidate_sources"] == ["lexical", "vector"]

    markdown = runner.invoke(
        app,
        [
            "recall",
            "pytest command failed",
            "--repo-key",
            "acme/widgets",
            "--root",
            str(root),
            "--format",
            "markdown",
        ],
    )
    assert markdown.exit_code == 0, markdown.output
    assert "codecairn://memory/" in markdown.stdout
