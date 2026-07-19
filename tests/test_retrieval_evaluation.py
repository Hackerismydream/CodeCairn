from __future__ import annotations

import json
from pathlib import Path

import pytest

from codecairn.evaluation.retrieval import (
    RecoveryRunConfig,
    RetrievalRunConfig,
    report_recovery,
    report_retrieval,
    run_recovery_suite,
    run_retrieval_evaluation,
)

CODEX_FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"
BENCHMARK_ROOT = Path(__file__).parent.parent / "benchmarks" / "retrieval"


def test_retrieval_evaluation_uses_fixed_labels_and_immutable_per_query_artifacts(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus.json"
    queries = tmp_path / "queries.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "key": "tests",
                    "repo_key": "acme/widgets",
                    "title": "Rule 01",
                    "content": "Run unit tests with uv run pytest -q before opening a PR.",
                },
                {
                    "key": "money",
                    "repo_key": "acme/widgets",
                    "title": "Rule 02",
                    "content": "Represent currency with Decimal and never binary float.",
                },
                {
                    "key": "secrets",
                    "repo_key": "acme/widgets",
                    "title": "Rule 03",
                    "content": "Load credentials from environment variables and never commit them.",
                },
            ]
        ),
        encoding="utf-8",
    )
    queries.write_text(
        json.dumps(
            [
                {
                    "query_id": "q-test-command",
                    "repo_key": "acme/widgets",
                    "text": "uv run pytest unit tests",
                    "relevant_keys": ["tests"],
                },
                {
                    "query_id": "q-currency-type",
                    "repo_key": "acme/widgets",
                    "text": "Decimal currency instead of binary float",
                    "relevant_keys": ["money"],
                },
            ]
        ),
        encoding="utf-8",
    )
    config = RetrievalRunConfig(
        corpus_path=corpus,
        queries_path=queries,
        output_root=tmp_path / "runs",
        run_id="retrieval-test",
        repository_commit="abc123",
    )

    artifact = run_retrieval_evaluation(config)

    assert artifact.summary["query_count"] == 2
    assert artifact.summary["recall_at_1"] == 1.0
    assert artifact.summary["recall_at_5"] == 1.0
    assert artifact.summary["mrr"] == 1.0
    assert artifact.summary["repository_isolation_violation_count"] == 0
    query_files = sorted((artifact.run_dir / "queries").glob("*.json"))
    assert len(query_files) == 2
    query_payload = json.loads(query_files[0].read_text(encoding="utf-8"))
    assert query_payload["relevant_keys"] != ["tests", "money", "secrets"]
    assert query_payload["rankings"][0]["candidate_sources"]
    assert "latency_ms" in query_payload

    before = {path: path.stat().st_mtime_ns for path in artifact.run_dir.rglob("*")}
    assert report_retrieval(artifact.run_dir) == artifact.summary
    after = {path: path.stat().st_mtime_ns for path in artifact.run_dir.rglob("*")}
    assert after == before
    with pytest.raises(FileExistsError):
        run_retrieval_evaluation(config)


def test_retrieval_labels_cannot_mark_repository_membership_as_relevance(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus.json"
    queries = tmp_path / "queries.json"
    corpus.write_text(
        json.dumps(
            [
                {"key": "a", "repo_key": "repo", "title": "A", "content": "alpha"},
                {"key": "b", "repo_key": "repo", "title": "B", "content": "bravo"},
            ]
        ),
        encoding="utf-8",
    )
    queries.write_text(
        json.dumps(
            [
                {
                    "query_id": "q",
                    "repo_key": "repo",
                    "text": "find alpha",
                    "relevant_keys": ["a", "b"],
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="repository membership"):
        run_retrieval_evaluation(
            RetrievalRunConfig(
                corpus_path=corpus,
                queries_path=queries,
                output_root=tmp_path / "runs",
                run_id="bad-labels",
                repository_commit="abc123",
            )
        )


def test_recovery_suite_covers_rebuild_replay_corruption_resume_and_isolation(
    tmp_path: Path,
) -> None:
    artifact = run_recovery_suite(
        RecoveryRunConfig(
            source_fixture=CODEX_FIXTURE,
            output_root=tmp_path / "runs",
            run_id="recovery-test",
            repository_commit="abc123",
        )
    )

    assert artifact.summary["all_passed"] is True
    assert artifact.summary["checks"] == {
        "append_resume": True,
        "corruption_detection": True,
        "cross_repository_import": True,
        "import_idempotency": True,
        "index_rebuild_parity": True,
        "queue_replay": True,
    }
    assert artifact.summary["index_rebuild_consistency"] == 1.0
    assert report_recovery(artifact.run_dir) == artifact.summary


def test_checked_in_retrieval_inputs_define_100_independently_labeled_queries(
    tmp_path: Path,
) -> None:
    artifact = run_retrieval_evaluation(
        RetrievalRunConfig(
            corpus_path=BENCHMARK_ROOT / "corpus.json",
            queries_path=BENCHMARK_ROOT / "queries.json",
            output_root=tmp_path / "runs",
            run_id="checked-inputs",
            repository_commit="abc123",
        )
    )

    assert artifact.summary["query_count"] == 100
    assert artifact.summary["repository_isolation_violation_count"] == 0
    assert len(list((artifact.run_dir / "queries").glob("*.json"))) == 100
