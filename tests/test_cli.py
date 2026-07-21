import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from codecairn.bootstrap import app, create_cascade, create_runtime

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"
CLAUDE_FIXTURE = Path(__file__).parent / "fixtures" / "claude" / "failed_command.jsonl"
LOCOMO_FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"


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


def test_cli_exposes_doctor_and_evaluation_run_report(tmp_path: Path) -> None:
    runner = CliRunner()
    runtime_root = tmp_path / "runtime"
    artifact_root = tmp_path / "artifacts"

    doctor = runner.invoke(app, ["doctor", "--root", str(runtime_root)])

    assert doctor.exit_code == 0, doctor.output
    diagnostics = json.loads(doctor.stdout)
    assert diagnostics["markdown_truth"]["memory_count"] == 0
    assert diagnostics["import_ledger"]["import_count"] == 0
    assert diagnostics["index_queue"]["pending"] == 0
    assert diagnostics["index"]["ready"] is True
    assert "codex_cli" in diagnostics["providers"]
    assert diagnostics["providers"]["retrieval"]["embedding"]["adapter"] == "hashing-test"

    executed = runner.invoke(
        app,
        [
            "eval",
            "run",
            "recovery",
            str(FIXTURE),
            "--run-id",
            "cli-recovery",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(artifact_root),
            "--root",
            str(runtime_root),
        ],
    )
    assert executed.exit_code == 0, executed.output
    assert json.loads(executed.stdout)["all_passed"] is True

    reported = runner.invoke(
        app,
        [
            "eval",
            "report",
            "recovery",
            str(artifact_root / "recovery" / "cli-recovery"),
            "--root",
            str(runtime_root),
        ],
    )
    assert reported.exit_code == 0, reported.output
    assert json.loads(reported.stdout) == json.loads(executed.stdout)


def test_doctor_verifies_the_hierarchical_document_projection(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    create_runtime(root).import_session(FIXTURE, repo_key="acme/widgets")
    create_cascade(root).run_until_idle(worker_id="test")

    doctor = CliRunner().invoke(app, ["doctor", "--root", str(root)])

    assert doctor.exit_code == 0, doctor.output
    diagnostics = json.loads(doctor.stdout)
    assert diagnostics["status"] == "healthy"
    assert diagnostics["index"]["ready"] is True
    assert diagnostics["index"]["document_fingerprint_count"] == 4
    assert diagnostics["index"]["truth_document_fingerprint_count"] == 4


def test_cli_builds_reusable_locomo_corpus_and_query_vectors(tmp_path: Path) -> None:
    runner = CliRunner()
    runtime_root = tmp_path / "runtime"
    corpus_root = tmp_path / "corpora"
    vector_root = tmp_path / "query-vectors"
    dataset_sha256 = hashlib.sha256(LOCOMO_FIXTURE.read_bytes()).hexdigest()

    corpus = runner.invoke(
        app,
        [
            "eval",
            "build-locomo-corpus",
            str(LOCOMO_FIXTURE),
            "--corpus-id",
            "cli-corpus",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(corpus_root),
            "--root",
            str(runtime_root),
            "--expected-dataset-sha256",
            dataset_sha256,
        ],
    )
    assert corpus.exit_code == 0, corpus.output
    corpus_payload = json.loads(corpus.stdout)
    corpus_dir = Path(corpus_payload["corpus_dir"])
    assert corpus_dir.is_dir()
    assert corpus_payload["counts"]["conversation_count"] == 2
    corpus_manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshots = corpus_manifest["content"]["corpus_snapshots"]
    assert all(snapshot["vector_sha256"] for snapshot in snapshots.values())

    vectors = runner.invoke(
        app,
        [
            "eval",
            "build-locomo-query-vectors",
            str(LOCOMO_FIXTURE),
            "--vector-set-id",
            "cli-queries",
            "--output-root",
            str(vector_root),
            "--root",
            str(runtime_root),
            "--expected-dataset-sha256",
            dataset_sha256,
        ],
    )
    assert vectors.exit_code == 0, vectors.output
    vector_payload = json.loads(vectors.stdout)
    assert Path(vector_payload["query_vectors_dir"]).is_dir()
    assert vector_payload["question_count"] == 4


def test_cli_help_lists_complete_public_surface() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for command in ("import", "list", "recall", "eval", "evidence", "doctor"):
        assert command in result.stdout
