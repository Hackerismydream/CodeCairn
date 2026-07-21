import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecairn.bootstrap import app, create_cascade, create_runtime
from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.worker_process import WorkerProcessLimits, WorkerProcessResult

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


def test_cli_builds_reusable_locomo_corpus_and_query_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256 = (
        _build_synthetic_locomo_inputs(tmp_path)
    )
    corpus_manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshots = corpus_manifest["content"]["corpus_snapshots"]
    assert all(snapshot["vector_sha256"] for snapshot in snapshots.values())
    corpus_tree_before = _stable_corpus_tree(corpus_dir)
    secret = "sk-test-worker-secret-must-not-persist"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)

    run = runner.invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "cli-worker-run",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(tmp_path / "runs"),
            "--root",
            str(runtime_root),
            "--mode",
            "retrieval",
            "--corpus",
            str(corpus_dir),
            "--query-vectors",
            str(query_vectors_dir),
            "--expected-dataset-sha256",
            dataset_sha256,
        ],
    )
    assert run.exit_code == 0, run.output
    run_payload = json.loads(run.stdout)
    assert run_payload["completed_question_count"] == 4
    run_dir = tmp_path / "runs" / "locomo" / "cli-worker-run"
    resource_usage = json.loads((run_dir / "resource-usage.json").read_text(encoding="utf-8"))
    assert resource_usage["worker_contract"] == "verified-shared-corpus-exec-per-conversation-v2"
    assert resource_usage["worker_count"] == 2
    assert 0 < resource_usage["max_worker_rss_bytes"] <= 1024 * 1024 * 1024
    workers = resource_usage["accepted_workers"]
    assert len({worker["worker_pid"] for worker in workers}) == 2
    assert all(worker["worker_pid"] != worker["parent_pid"] for worker in workers)
    assert _stable_corpus_tree(corpus_dir) == corpus_tree_before
    assert all(
        secret.encode() not in path.read_bytes() for path in run_dir.rglob("*") if path.is_file()
    )

    def report_unobserved_peak(
        command: tuple[str, ...],
        *,
        progress_root: Path,
        limits: WorkerProcessLimits,
        on_started: Callable[[int], None] | None = None,
    ) -> WorkerProcessResult:
        del progress_root
        if on_started is not None:
            on_started(4242)
        spec = read_json(Path(command[-1]))
        assert isinstance(spec, dict)
        rss_limit = limits.max_rss_bytes
        write_json_exclusive(
            Path(str(spec["resource_path"])),
            {
                "schema_version": 1,
                "status": "completed",
                "conversation_id": spec["conversation_id"],
                "pid": 4242,
                "wall_time_seconds": 0.01,
                "max_rss_bytes": rss_limit + 1,
                "error_type": None,
            },
        )
        return WorkerProcessResult(
            pid=4242,
            returncode=0,
            max_rss_bytes=1,
            wall_time_seconds=0.01,
            termination_reason=None,
        )

    monkeypatch.setattr("codecairn.bootstrap.run_monitored_worker", report_unobserved_peak)
    rejected = runner.invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "cli-worker-reported-rss-rejection",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(tmp_path / "runs"),
            "--root",
            str(runtime_root),
            "--mode",
            "retrieval",
            "--corpus",
            str(corpus_dir),
            "--query-vectors",
            str(query_vectors_dir),
            "--expected-dataset-sha256",
            dataset_sha256,
        ],
    )
    assert rejected.exit_code == 1
    assert isinstance(rejected.exception, MemoryError)
    rejected_run_dir = tmp_path / "runs" / "locomo" / "cli-worker-reported-rss-rejection"
    assert not (rejected_run_dir / "checkpoints" / "questions").exists()
    failed_receipt = read_json(
        next((rejected_run_dir / "resources" / "conversations").glob("*.json"))
    )
    assert isinstance(failed_receipt, dict)
    assert failed_receipt["accepted"] is False


def test_cli_rejects_locomo_run_id_before_creating_an_escaped_lock(tmp_path: Path) -> None:
    output_root = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "../../escaped",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(output_root),
            "--root",
            str(tmp_path / "runtime"),
            "--mode",
            "retrieval",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert "safe path segment" in str(result.exception)
    assert not (output_root / "escaped.lock").exists()
    assert not (output_root / "locomo" / ".locks").exists()


def test_locomo_report_and_resume_reject_a_tampered_accepted_worker_receipt(
    tmp_path: Path,
) -> None:
    runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256 = (
        _build_synthetic_locomo_inputs(tmp_path)
    )
    run_id = "tampered-worker-receipt"
    run_args = _shared_locomo_retrieval_args(
        runtime_root=runtime_root,
        output_root=tmp_path / "runs",
        corpus_dir=corpus_dir,
        query_vectors_dir=query_vectors_dir,
        dataset_sha256=dataset_sha256,
        run_id=run_id,
    )
    completed = runner.invoke(app, run_args)
    assert completed.exit_code == 0, completed.output
    run_dir = tmp_path / "runs" / "locomo" / run_id
    receipt_path = next(
        path
        for path in sorted((run_dir / "resources" / "conversations").glob("*.json"))
        if isinstance(receipt := read_json(path), dict) and receipt.get("accepted") is True
    )
    receipt = read_json(receipt_path)
    assert isinstance(receipt, dict)
    receipt["run_manifest_sha256"] = "0" * 64
    receipt_path.unlink()
    write_json_exclusive(receipt_path, receipt)

    reported = runner.invoke(
        app,
        ["eval", "report", "locomo", str(run_dir), "--root", str(runtime_root)],
    )
    assert reported.exit_code == 1
    assert isinstance(reported.exception, ValueError)

    (run_dir / "summary.json").unlink()
    resumed = runner.invoke(app, [*run_args, "--resume"])
    assert resumed.exit_code == 1
    assert isinstance(resumed.exception, ValueError)


def test_locomo_resume_rejects_a_worker_directory_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256 = (
        _build_synthetic_locomo_inputs(tmp_path)
    )
    run_id = "worker-symlink-escape"
    run_args = _shared_locomo_retrieval_args(
        runtime_root=runtime_root,
        output_root=tmp_path / "runs",
        corpus_dir=corpus_dir,
        query_vectors_dir=query_vectors_dir,
        dataset_sha256=dataset_sha256,
        run_id=run_id,
    )

    def fail_after_worker_staging(
        command: tuple[str, ...],
        *,
        progress_root: Path,
        limits: WorkerProcessLimits,
        on_started: Callable[[int], None] | None = None,
    ) -> WorkerProcessResult:
        del command, progress_root, limits
        if on_started is not None:
            on_started(4242)
        raise RuntimeError("synthetic monitor failure")

    monkeypatch.setattr("codecairn.bootstrap.run_monitored_worker", fail_after_worker_staging)
    interrupted = runner.invoke(app, run_args)
    assert interrupted.exit_code == 1
    assert isinstance(interrupted.exception, RuntimeError)

    run_dir = tmp_path / "runs" / "locomo" / run_id
    worker_root = run_dir / "workers" / "conv-test-1"
    assert worker_root.is_dir()
    shutil.rmtree(worker_root)
    outside = tmp_path / "outside-workers"
    outside.mkdir()
    worker_root.symlink_to(outside, target_is_directory=True)

    resumed = runner.invoke(app, [*run_args, "--resume"])
    assert resumed.exit_code == 1
    assert isinstance(resumed.exception, ValueError)
    assert "symlink" in str(resumed.exception)
    assert not any(outside.iterdir())


def test_locomo_resume_publishes_completed_staging_without_restarting_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _runtime_root, run_args, run_dir = _completed_synthetic_locomo_run(
        tmp_path,
        run_id="recover-before-publish",
    )
    conversation_id = "conv-test-1"
    receipt_path, attempt_dir, original_receipt = _accepted_worker_attempt(
        run_dir,
        conversation_id=conversation_id,
    )
    canonical_question_dir = run_dir / "checkpoints" / "questions" / conversation_id
    original_question_files = _file_tree_sha256(canonical_question_dir)
    staged_question_dir = attempt_dir / "run" / "checkpoints" / "questions" / conversation_id
    staged_question_dir.parent.mkdir(parents=True, exist_ok=True)
    canonical_question_dir.rename(staged_question_dir)
    receipt_path.unlink()
    (attempt_dir / "publish.json").unlink()
    _remove_completed_locomo_outputs(run_dir)

    worker_starts: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def forbid_worker_start(*args: object, **kwargs: object) -> WorkerProcessResult:
        worker_starts.append((args, kwargs))
        raise AssertionError("resume must publish completed staging without a new worker")

    monkeypatch.setattr("codecairn.bootstrap.run_monitored_worker", forbid_worker_start)
    resumed = runner.invoke(app, [*run_args, "--resume"])

    assert resumed.exit_code == 0, resumed.output
    assert worker_starts == []
    assert not staged_question_dir.exists()
    assert _file_tree_sha256(canonical_question_dir) == original_question_files
    recovered = read_json(receipt_path)
    assert isinstance(recovered, dict)
    assert recovered["attempt"] == original_receipt["attempt"]
    assert recovered["recovered_before_publish"] is True
    assert recovered["question_checkpoint_sha256"] == original_receipt["question_checkpoint_sha256"]
    assert recovered["spec_sha256"] == file_sha256(attempt_dir / "spec.json")
    assert recovered["publish_marker_sha256"] == file_sha256(attempt_dir / "publish.json")


def test_locomo_resume_recovers_receipt_after_canonical_rename_without_restarting_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _runtime_root, run_args, run_dir = _completed_synthetic_locomo_run(
        tmp_path,
        run_id="recover-after-publish",
    )
    conversation_id = "conv-test-1"
    receipt_path, attempt_dir, original_receipt = _accepted_worker_attempt(
        run_dir,
        conversation_id=conversation_id,
    )
    canonical_question_dir = run_dir / "checkpoints" / "questions" / conversation_id
    original_question_files = _file_tree_sha256(canonical_question_dir)
    publish_marker = read_json(attempt_dir / "publish.json")
    assert isinstance(publish_marker, dict)
    receipt_path.unlink()
    _remove_completed_locomo_outputs(run_dir)

    worker_starts: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def forbid_worker_start(*args: object, **kwargs: object) -> WorkerProcessResult:
        worker_starts.append((args, kwargs))
        raise AssertionError("resume must recover the accepted receipt without a new worker")

    monkeypatch.setattr("codecairn.bootstrap.run_monitored_worker", forbid_worker_start)
    resumed = runner.invoke(app, [*run_args, "--resume"])

    assert resumed.exit_code == 0, resumed.output
    assert worker_starts == []
    assert _file_tree_sha256(canonical_question_dir) == original_question_files
    recovered = read_json(receipt_path)
    assert isinstance(recovered, dict)
    assert recovered["attempt"] == original_receipt["attempt"]
    assert recovered["recovered_after_publish"] is True
    assert recovered["question_checkpoint_sha256"] == publish_marker["question_checkpoint_sha256"]
    assert recovered["question_checkpoint_sha256"] == original_receipt["question_checkpoint_sha256"]
    assert recovered["spec_sha256"] == file_sha256(attempt_dir / "spec.json")
    assert recovered["publish_marker_sha256"] == file_sha256(attempt_dir / "publish.json")


def test_locomo_resume_rejects_any_historical_worker_hard_breach_before_orchestration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _runtime_root, run_args, run_dir = _completed_synthetic_locomo_run(
        tmp_path,
        run_id="run-wide-hard-breach",
    )
    _receipt_path, _attempt_dir, accepted_receipt = _accepted_worker_attempt(
        run_dir,
        conversation_id="conv-test-2",
    )
    hard_limit = int(accepted_receipt["rss_limit_bytes"])
    breached_receipt = {
        **accepted_receipt,
        "attempt": 99,
        "accepted": False,
        "status": "failed",
        "termination_reason": "rss_limit",
        "observed_max_rss_bytes": hard_limit + 1,
        "reported_max_rss_bytes": hard_limit + 1,
        "max_rss_bytes": hard_limit + 1,
    }
    write_json_exclusive(
        run_dir / "resources" / "conversations" / "conv-test-2.attempt-99.json",
        breached_receipt,
    )

    orchestration_starts: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def forbid_orchestration(*args: object, **kwargs: object) -> object:
        orchestration_starts.append((args, kwargs))
        raise AssertionError("resume preflight must reject the run before orchestration")

    monkeypatch.setattr("codecairn.evaluation.locomo.run_locomo", forbid_orchestration)
    resumed = runner.invoke(app, [*run_args, "--resume"])

    assert resumed.exit_code == 1
    assert isinstance(resumed.exception, MemoryError)
    assert "prior LoCoMo worker attempt exceeded" in str(resumed.exception)
    assert orchestration_starts == []


def test_locomo_coordinator_sigterm_is_failed_and_resume_keeps_checkpoints(
    tmp_path: Path,
) -> None:
    _runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256 = (
        _build_synthetic_locomo_inputs(tmp_path)
    )
    run_id = "coordinator-sigterm-resume"
    run_args = _shared_locomo_retrieval_args(
        runtime_root=runtime_root,
        output_root=tmp_path / "runs",
        corpus_dir=corpus_dir,
        query_vectors_dir=query_vectors_dir,
        dataset_sha256=dataset_sha256,
        run_id=run_id,
    )
    run_dir = tmp_path / "runs" / "locomo" / run_id
    pause_ready = tmp_path / "coordinator-question-phase.ready"
    environment = {
        **os.environ,
        "CODECAIRN_RETRIEVAL_PROFILE": "hashing-test",
        "CODECAIRN_TEST_COORDINATOR_PAUSE_READY": str(pause_ready),
    }
    controlled_launcher = """
import os
import time
from pathlib import Path

import codecairn.bootstrap as bootstrap

original = bootstrap._run_locomo_question_worker

def controlled(work, **kwargs):
    if work.conversation.sample_id == "conv-test-2":
        Path(os.environ["CODECAIRN_TEST_COORDINATOR_PAUSE_READY"]).touch()
        while True:
            time.sleep(0.01)
    return original(work, **kwargs)

bootstrap._run_locomo_question_worker = controlled
bootstrap.main()
"""
    coordinator = subprocess.Popen(
        [sys.executable, "-c", controlled_launcher, *run_args],
        cwd=Path(__file__).parents[1],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 20.0
    while not pause_ready.is_file() and coordinator.poll() is None:
        if time.monotonic() >= deadline:
            coordinator.kill()
            stdout, stderr = coordinator.communicate(timeout=5)
            pytest.fail(f"coordinator did not reach question pause: {stdout}\n{stderr}")
        time.sleep(0.01)
    if not pause_ready.is_file():
        stdout, stderr = coordinator.communicate(timeout=5)
        pytest.fail(f"coordinator exited before question pause: {stdout}\n{stderr}")

    persisted_dir = run_dir / "checkpoints" / "questions" / "conv-test-1"
    persisted_before = _file_tree_sha256(persisted_dir)
    persisted_mtime_before = {
        path.name: path.stat().st_mtime_ns for path in sorted(persisted_dir.glob("*.json"))
    }
    coordinator_started = (run_dir / "resources" / "coordinator-starts" / "start-1.json").is_file()

    coordinator.send_signal(signal.SIGTERM)
    try:
        interrupted_stdout, interrupted_stderr = coordinator.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        coordinator.kill()
        interrupted_stdout, interrupted_stderr = coordinator.communicate(timeout=5)
        pytest.fail(
            f"coordinator did not exit after SIGTERM: {interrupted_stdout}\n{interrupted_stderr}"
        )
    assert len(persisted_before) == 2
    assert coordinator_started is True
    assert coordinator.returncode != 0, interrupted_stdout
    failed_completion_path = run_dir / "resources" / "coordinators" / "attempt-1.json"
    assert failed_completion_path.is_file(), interrupted_stderr
    failed_completion = read_json(failed_completion_path)
    assert isinstance(failed_completion, dict)
    assert failed_completion["attempt"] == 1
    assert failed_completion["pid"] == coordinator.pid
    assert failed_completion["status"] == "failed"

    resumed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from codecairn.bootstrap import main; main()",
            *run_args,
            "--resume",
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    summary = json.loads(resumed.stdout)
    assert summary["completed_question_count"] == 4
    assert summary["worker_resources"]["failed_coordinator_attempt_count"] == 1
    assert [
        read_json(path)["status"]
        for path in sorted((run_dir / "resources" / "coordinators").glob("attempt-*.json"))
    ] == ["failed", "completed"]
    assert _file_tree_sha256(persisted_dir) == persisted_before
    assert {
        path.name: path.stat().st_mtime_ns for path in sorted(persisted_dir.glob("*.json"))
    } == persisted_mtime_before
    question_paths = sorted((run_dir / "checkpoints" / "questions").glob("*/*.json"))
    question_identities = {(path.parent.name, path.stem) for path in question_paths}
    assert len(question_paths) == len(question_identities) == 4


def _build_synthetic_locomo_inputs(
    tmp_path: Path,
) -> tuple[CliRunner, Path, Path, Path, str]:
    runner = CliRunner()
    runtime_root = tmp_path / "runtime"
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
            str(tmp_path / "corpora"),
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

    vectors = runner.invoke(
        app,
        [
            "eval",
            "build-locomo-query-vectors",
            str(LOCOMO_FIXTURE),
            "--vector-set-id",
            "cli-queries",
            "--output-root",
            str(tmp_path / "query-vectors"),
            "--root",
            str(runtime_root),
            "--expected-dataset-sha256",
            dataset_sha256,
        ],
    )
    assert vectors.exit_code == 0, vectors.output
    vector_payload = json.loads(vectors.stdout)
    query_vectors_dir = Path(vector_payload["query_vectors_dir"])
    assert query_vectors_dir.is_dir()
    assert vector_payload["question_count"] == 4
    return runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256


def _shared_locomo_retrieval_args(
    *,
    runtime_root: Path,
    output_root: Path,
    corpus_dir: Path,
    query_vectors_dir: Path,
    dataset_sha256: str,
    run_id: str,
) -> list[str]:
    return [
        "eval",
        "run",
        "locomo",
        str(LOCOMO_FIXTURE),
        "--run-id",
        run_id,
        "--repository-commit",
        "abc123",
        "--output-root",
        str(output_root),
        "--root",
        str(runtime_root),
        "--mode",
        "retrieval",
        "--corpus",
        str(corpus_dir),
        "--query-vectors",
        str(query_vectors_dir),
        "--expected-dataset-sha256",
        dataset_sha256,
    ]


def _completed_synthetic_locomo_run(
    tmp_path: Path,
    *,
    run_id: str,
) -> tuple[CliRunner, Path, list[str], Path]:
    runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256 = (
        _build_synthetic_locomo_inputs(tmp_path)
    )
    output_root = tmp_path / "runs"
    run_args = _shared_locomo_retrieval_args(
        runtime_root=runtime_root,
        output_root=output_root,
        corpus_dir=corpus_dir,
        query_vectors_dir=query_vectors_dir,
        dataset_sha256=dataset_sha256,
        run_id=run_id,
    )
    completed = runner.invoke(app, run_args)
    assert completed.exit_code == 0, completed.output
    return runner, runtime_root, run_args, output_root / "locomo" / run_id


def _accepted_worker_attempt(
    run_dir: Path,
    *,
    conversation_id: str,
) -> tuple[Path, Path, dict[str, object]]:
    resource_root = run_dir / "resources" / "conversations"
    receipt_path = next(
        path
        for path in sorted(resource_root.glob(f"{conversation_id}.attempt-*.json"))
        if isinstance(receipt := read_json(path), dict) and receipt.get("accepted") is True
    )
    receipt = read_json(receipt_path)
    assert isinstance(receipt, dict)
    attempt = receipt["attempt"]
    assert type(attempt) is int
    attempt_dir = run_dir / "workers" / conversation_id / f"attempt-{attempt}"
    assert attempt_dir.is_dir()
    return receipt_path, attempt_dir, receipt


def _remove_completed_locomo_outputs(run_dir: Path) -> None:
    (run_dir / "summary.json").unlink()
    (run_dir / "resource-usage.json").unlink()


def _file_tree_sha256(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stable_corpus_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != ".index.lancedb.lock"
        and not path.name.endswith(("-shm", "-wal"))
    }


def test_cli_help_lists_complete_public_surface() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for command in ("import", "list", "recall", "eval", "evidence", "doctor"):
        assert command in result.stdout
