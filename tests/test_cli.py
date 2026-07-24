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

from codecairn.bootstrap import app, create_cascade, create_retrieval_providers, create_runtime
from codecairn.evaluation.artifacts import (
    canonical_json,
    canonical_sha256,
    file_sha256,
    read_json,
    write_json_exclusive,
)
from codecairn.evaluation.locomo import (
    LOCOMO_PAID_SCORING_GATE_CONTRACT,
    load_locomo_dataset,
)
from codecairn.evaluation.locomo_retrieval_gate import (
    LoCoMoRetrievalGateConfig,
    verify_locomo_retrieval_gate,
)
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
    assert "[fact_" in markdown.stdout
    assert result["sidecar"]["ranked"][0]["source_uri"].startswith("codecairn://memory/")


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


def test_cli_rejects_v18_paid_scoring_before_constructing_model_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_roles: list[str] = []

    def record_provider(*, role: str, **_kwargs: object) -> object:
        provider_roles.append(role)
        raise AssertionError("paid provider must not be constructed before the retrieval gate")

    monkeypatch.setattr(
        "codecairn.evaluation.providers.create_locomo_text_model",
        record_provider,
    )
    question_set = Path(__file__).parents[1] / "benchmarks/locomo/diagnostic-200-v18.json"
    output_root = tmp_path / "artifacts"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "v18-missing-gate",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(output_root),
            "--root",
            str(tmp_path / "runtime"),
            "--question-set",
            str(question_set),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert "paid-scoring gate is missing" in str(result.exception)
    assert provider_roles == []
    assert not (output_root / "locomo" / "v18-missing-gate").exists()


def test_cli_rejects_a_gate_for_another_question_set_before_model_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_roles: list[str] = []
    retrieval_p95_gates: list[float] = []

    def record_provider(*, role: str, **_kwargs: object) -> object:
        provider_roles.append(role)
        raise AssertionError("mismatched retrieval evidence must stop before providers")

    monkeypatch.setattr(
        "codecairn.evaluation.providers.create_locomo_text_model",
        record_provider,
    )

    def record_retrieval_gate(
        config: LoCoMoRetrievalGateConfig,
    ) -> dict[str, object]:
        retrieval_p95_gates.append(config.maximum_retrieval_p95_ms)
        return {"scored_question_set_sha256": "0" * 64}

    monkeypatch.setattr(
        "codecairn.evaluation.locomo_retrieval_gate.verify_locomo_retrieval_gate",
        record_retrieval_gate,
    )
    question_set = Path(__file__).parents[1] / "benchmarks/locomo/diagnostic-200-v22.json"
    gate_dirs = [tmp_path / name for name in ("corpus", "queries", "canary", "holdout")]
    for directory in gate_dirs:
        directory.mkdir()

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "v18-wrong-gate-target",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(tmp_path / "artifacts"),
            "--root",
            str(tmp_path / "runtime"),
            "--question-set",
            str(question_set),
            "--corpus",
            str(gate_dirs[0]),
            "--query-vectors",
            str(gate_dirs[1]),
            "--retrieval-gate-question-set",
            str(question_set),
            "--retrieval-canary-run",
            str(gate_dirs[2]),
            "--retrieval-holdout-run",
            str(gate_dirs[3]),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert "does not target the scored question set" in str(result.exception)
    assert retrieval_p95_gates == [4_000.0]
    assert provider_roles == []


def test_cli_preserves_protocol_less_legacy_question_set_before_model_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_roles: list[str] = []

    def record_provider(*, role: str, **_kwargs: object) -> object:
        provider_roles.append(role)
        raise RuntimeError("legacy provider construction reached")

    monkeypatch.setattr(
        "codecairn.evaluation.providers.create_locomo_text_model",
        record_provider,
    )
    question_set = Path(__file__).parents[1] / "benchmarks/locomo/canary-20.json"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "legacy-question-set",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(tmp_path / "artifacts"),
            "--root",
            str(tmp_path / "runtime"),
            "--mode",
            "smoke",
            "--question-set",
            str(question_set),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "legacy provider construction reached"
    assert provider_roles == ["answer"]


def test_cli_rejects_paid_gate_for_protocol_less_question_set_before_model_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_roles: list[str] = []

    def record_provider(*, role: str, **_kwargs: object) -> object:
        provider_roles.append(role)
        raise AssertionError("legacy gate rejection must happen before model providers")

    monkeypatch.setattr(
        "codecairn.evaluation.providers.create_locomo_text_model",
        record_provider,
    )
    question_set = Path(__file__).parents[1] / "benchmarks/locomo/canary-20.json"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "locomo",
            str(LOCOMO_FIXTURE),
            "--run-id",
            "legacy-question-set-with-gate",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(tmp_path / "artifacts"),
            "--root",
            str(tmp_path / "runtime"),
            "--mode",
            "smoke",
            "--question-set",
            str(question_set),
            "--retrieval-gate-question-set",
            str(question_set),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert "does not support paid-scoring gates" in str(result.exception)
    assert provider_roles == []


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
            "run-commit-after-corpus",
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
    run_manifest = read_json(run_dir / "manifest.json")
    assert isinstance(run_manifest, dict)
    assert run_manifest["repository_commit"] == "run-commit-after-corpus"
    assert isinstance(run_manifest["corpus"], dict)
    assert run_manifest["corpus"]["repository_commit"] == "abc123"
    worker_specs = [
        read_json(path) for path in sorted((run_dir / "workers").glob("*/attempt-*/spec.json"))
    ]
    assert worker_specs
    assert all(
        isinstance(spec, dict) and spec["corpus_repository_commit"] == "abc123"
        for spec in worker_specs
    )
    resource_usage = json.loads((run_dir / "resource-usage.json").read_text(encoding="utf-8"))
    assert resource_usage["worker_contract"] == "verified-shared-corpus-exec-per-conversation-v3"
    assert resource_usage["worker_count"] == 2
    assert 0 < resource_usage["max_worker_rss_bytes"] <= 2 * 1024 * 1024 * 1024
    workers = resource_usage["accepted_workers"]
    assert len({worker["worker_pid"] for worker in workers}) == 2
    assert all(worker["worker_pid"] != worker["parent_pid"] for worker in workers)
    assert all(
        isinstance(worker["reranker_warmup_ms"], int | float)
        and not isinstance(worker["reranker_warmup_ms"], bool)
        and worker["reranker_warmup_ms"] >= 0
        for worker in workers
    )
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


def test_production_retrieval_gate_replays_real_cli_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODECAIRN_RETRIEVAL_PROFILE", "hashing-test")
    runner, runtime_root, corpus_dir, query_vectors_dir, dataset_sha256 = (
        _build_synthetic_locomo_inputs(tmp_path)
    )
    target_path, canary_path, holdout_path = _write_synthetic_locomo_gate_question_sets(tmp_path)
    output_root = tmp_path / "runs"
    source_run_dirs: list[Path] = []
    for run_id, question_set_path in (
        ("production-reporter-canary", canary_path),
        ("production-reporter-holdout", holdout_path),
    ):
        result = runner.invoke(
            app,
            [
                *_shared_locomo_retrieval_args(
                    runtime_root=runtime_root,
                    output_root=output_root,
                    corpus_dir=corpus_dir,
                    query_vectors_dir=query_vectors_dir,
                    dataset_sha256=dataset_sha256,
                    run_id=run_id,
                ),
                "--question-set",
                str(question_set_path),
            ],
        )
        assert result.exit_code == 0, result.output
        run_dir = output_root / "locomo" / run_id
        assert not (run_dir / "computed-evidence.json").exists()
        source_run_dirs.append(run_dir)

    receipt = verify_locomo_retrieval_gate(
        LoCoMoRetrievalGateConfig(
            target_question_set_path=target_path,
            scored_question_set_path=target_path,
            dataset_path=LOCOMO_FIXTURE,
            canary_run_dir=source_run_dirs[0],
            holdout_run_dir=source_run_dirs[1],
            repository_commit="abc123",
            corpus_path=corpus_dir,
            query_vectors_path=query_vectors_dir,
            expected_canary_questions=2,
            expected_holdout_questions=2,
        )
    )

    assert receipt["contract"] == LOCOMO_PAID_SCORING_GATE_CONTRACT
    assert receipt["target_question_count"] == 4
    assert receipt["scored_question_count"] == 4
    sources = receipt["sources"]
    assert isinstance(sources, list)
    assert len(sources) == 2
    source_reports = [source for source in sources if isinstance(source, dict)]
    assert len(source_reports) == 2
    assert [source["question_count"] for source in source_reports] == [2, 2]
    assert [source["context_all_coverage"] for source in source_reports] == [1.0, 1.0]
    assert all(
        isinstance(source.get("evidence_report_sha256"), str)
        and len(source["evidence_report_sha256"]) == 64
        for source in source_reports
    )


def test_cli_binds_a_frozen_question_set_to_the_corpus_build_contract(
    tmp_path: Path,
) -> None:
    dataset = load_locomo_dataset(LOCOMO_FIXTURE)
    retrieval = create_retrieval_providers().public_config
    embedding = retrieval["embedding"]
    reranker = retrieval["reranker"]
    planner = retrieval["planner"]
    assert isinstance(embedding, dict)
    assert isinstance(reranker, dict)
    assert isinstance(planner, dict)
    definition = json.loads(
        (Path(__file__).parents[1] / "benchmarks/locomo/diagnostic-200-v15.json").read_text(
            encoding="utf-8"
        )
    )
    protocol = definition["protocol"]
    protocol.update(
        {
            "inference_threads": retrieval.get("inference_threads"),
            "tokenizer_parallelism": retrieval.get("tokenizer_parallelism"),
            "tokenizer_threads": retrieval.get("tokenizer_threads"),
            "embedding_adapter": embedding.get("adapter"),
            "embedding_model": embedding.get("model"),
            "embedding_dimension": embedding.get("dimension"),
            "reranker_model": reranker.get("model"),
            "reranker_batch_size": reranker.get("batch_size"),
            **{
                field: value
                for field, value in planner.items()
                if field not in {"mode", "neighbor_window", "temporal_neighbor_window"}
            },
            "neighbor_windows": {
                "episode-only": {"neighbor_window": 0, "temporal_neighbor_window": 0},
                "hierarchy-no-neighbors": {
                    "neighbor_window": 0,
                    "temporal_neighbor_window": 0,
                },
                "hierarchy": {
                    "neighbor_window": planner["neighbor_window"],
                    "temporal_neighbor_window": planner["temporal_neighbor_window"],
                },
            },
        }
    )
    selected = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 2, 3, 4}
    )
    question_set_path = tmp_path / "corpus-question-set.json"
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "cli-corpus-protocol",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {str(category): 1 for category in range(1, 5)},
            "selection_sha256": hashlib.sha256(
                json.dumps(sorted(selected), separators=(",", ":")).encode()
            ).hexdigest(),
            "protocol": protocol,
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "build-locomo-corpus",
            str(LOCOMO_FIXTURE),
            "--question-set",
            str(question_set_path),
            "--corpus-id",
            "cli-protocol-corpus",
            "--repository-commit",
            "abc123",
            "--output-root",
            str(tmp_path / "corpora"),
            "--root",
            str(tmp_path / "runtime"),
            "--expected-dataset-sha256",
            dataset.sha256,
        ],
    )

    assert result.exit_code == 0, result.output
    corpus_dir = Path(json.loads(result.stdout)["corpus_dir"])
    manifest = read_json(corpus_dir / "manifest.json")
    assert isinstance(manifest, dict)
    build_contract = manifest["build_contract"]
    assert isinstance(build_contract, dict)
    question_set = build_contract["question_set"]
    assert isinstance(question_set, dict)
    assert question_set["definition_sha256"] == file_sha256(question_set_path)
    assert (
        question_set["protocol_sha256"]
        == hashlib.sha256(canonical_json(protocol).encode()).hexdigest()
    )


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


def _write_synthetic_locomo_gate_question_sets(
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    dataset = load_locomo_dataset(LOCOMO_FIXTURE)
    question_ids_by_category: dict[int, list[str]] = {}
    for conversation in dataset.conversations:
        for question in conversation.questions:
            question_ids_by_category.setdefault(question.category, []).append(question.question_id)
    assert {category: len(question_ids_by_category[category]) for category in range(1, 5)} == {
        1: 1,
        2: 1,
        3: 1,
        4: 1,
    }
    protocol = {
        "paid_scoring_gate": LOCOMO_PAID_SCORING_GATE_CONTRACT,
    }

    def selection_sha256(categories: tuple[int, ...]) -> str:
        question_ids = sorted(question_ids_by_category[category][0] for category in categories)
        return hashlib.sha256(
            json.dumps(
                question_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def write_question_set(
        selection_id: str,
        categories: tuple[int, ...],
        *,
        promotion: dict[str, object] | None = None,
    ) -> Path:
        path = tmp_path / f"{selection_id}.json"
        definition: dict[str, object] = {
            "schema_version": 1,
            "selection_id": selection_id,
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "production-reporter-gate",
            "category_targets": {str(category): 1 for category in categories},
            "selection_sha256": selection_sha256(categories),
            "protocol": protocol,
        }
        if promotion is not None:
            definition["promotion"] = promotion
        write_json_exclusive(path, definition)
        return path

    canary_categories = (1, 2)
    holdout_categories = (3, 4)
    canary_path = write_question_set("production-reporter-canary", canary_categories)
    holdout_path = write_question_set("production-reporter-holdout", holdout_categories)
    protocol_sha256 = canonical_sha256(protocol)
    target_path = write_question_set(
        "production-reporter-target",
        (*canary_categories, *holdout_categories),
        promotion={
            "source_selection": {
                "selection_id": "production-reporter-canary",
                "question_set_sha256": file_sha256(canary_path),
                "selection_sha256": selection_sha256(canary_categories),
                "protocol_sha256": protocol_sha256,
            },
            "holdout_selection": {
                "selection_id": "production-reporter-holdout",
                "question_set_sha256": file_sha256(holdout_path),
                "selection_sha256": selection_sha256(holdout_categories),
                "protocol_sha256": protocol_sha256,
            },
        },
    )
    return target_path, canary_path, holdout_path


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


def test_cli_help_exposes_single_run_locomo_promotion() -> None:
    result = CliRunner().invoke(app, ["eval", "--help"])

    assert result.exit_code == 0, result.output
    assert "promote-locomo" in result.stdout
