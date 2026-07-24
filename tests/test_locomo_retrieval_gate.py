from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import canonical_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo import load_locomo_dataset
from codecairn.evaluation.locomo_retrieval_gate import (
    LOCOMO_PAID_SCORING_GATE_CONTRACT,
    LoCoMoRetrievalGateConfig,
    verify_locomo_retrieval_gate,
)
from codecairn.memory.recall_planner import RecallPlannerConfig

FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"


class StaticGateReporter:
    def report(self, run_dir: Path) -> dict[str, object]:
        value = read_json(run_dir / "summary.json")
        assert isinstance(value, dict)
        return value

    def evidence(self, run_dir: Path, *, dataset_path: Path) -> dict[str, object]:
        assert dataset_path == FIXTURE
        value = read_json(run_dir / "computed-evidence.json")
        assert isinstance(value, dict)
        return value


class RecomputedGateReporter(StaticGateReporter):
    def __init__(self, summaries: dict[Path, dict[str, object]]) -> None:
        self._summaries = summaries

    def report(self, run_dir: Path) -> dict[str, object]:
        return self._summaries[run_dir]


def test_retrieval_gate_accepts_disjoint_verified_runs_and_returns_a_receipt(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)

    receipt = verify_locomo_retrieval_gate(
        config,
        reporter=StaticGateReporter(),
    )

    assert receipt["contract"] == LOCOMO_PAID_SCORING_GATE_CONTRACT
    assert receipt["repository_commit"] == "abc123"
    assert receipt["target_question_count"] == 4
    assert receipt["scored_question_count"] == 4
    assert receipt["minimum_context_all_coverage"] == 0.70
    assert [source["question_count"] for source in receipt["sources"]] == [2, 2]


def test_retrieval_gate_rejects_an_unfrozen_scored_subset(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    dataset = load_locomo_dataset(FIXTURE)
    question_ids = [
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 2}
    ]
    scored_path = tmp_path / "diagnostic-2-scored-v18.json"
    target_definition = read_json(config.target_question_set_path)
    assert isinstance(target_definition, dict)
    write_json_exclusive(
        scored_path,
        {
            "schema_version": 1,
            "selection_id": "diagnostic-2-scored-v18",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "diagnostic-4",
            "category_targets": {"1": 1, "2": 1},
            "selection_sha256": hashlib.sha256(
                json.dumps(
                    sorted(question_ids),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "protocol": target_definition["protocol"],
        },
    )

    with pytest.raises(ValueError, match="frozen canary or target"):
        verify_locomo_retrieval_gate(
            replace(config, scored_question_set_path=scored_path),
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_rejects_context_coverage_below_the_frozen_threshold(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    evidence_path = config.holdout_run_dir / "computed-evidence.json"
    evidence = read_json(evidence_path)
    assert isinstance(evidence, dict)
    evidence["overall"]["context_all_coverage"] = 0.69
    evidence_path.unlink()
    write_json_exclusive(evidence_path, evidence)

    with pytest.raises(ValueError, match="context coverage"):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_rejects_overlapping_canary_and_holdout_questions(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    canary_manifest = read_json(config.canary_run_dir / "manifest.json")
    holdout_manifest_path = config.holdout_run_dir / "manifest.json"
    holdout_manifest = read_json(holdout_manifest_path)
    assert isinstance(canary_manifest, dict)
    assert isinstance(holdout_manifest, dict)
    canary_ids = canary_manifest["selection"]["question_set"]["question_ids"]
    holdout_manifest["selection"]["question_set"]["question_ids"][0] = canary_ids[0]
    holdout_manifest["selection"]["question_set"]["selection_sha256"] = _selection_sha256(
        holdout_manifest["selection"]["question_set"]["question_ids"]
    )
    holdout_evidence_path = config.holdout_run_dir / "computed-evidence.json"
    holdout_evidence = read_json(holdout_evidence_path)
    assert isinstance(holdout_evidence, dict)
    holdout_evidence["questions"][0]["question_id"] = canary_ids[0]
    holdout_manifest_path.unlink()
    holdout_evidence_path.unlink()
    write_json_exclusive(holdout_manifest_path, holdout_manifest)
    write_json_exclusive(holdout_evidence_path, holdout_evidence)

    with pytest.raises(ValueError, match="disjoint"):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_recomputes_each_source_selection_from_question_ids(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    canary_path = config.canary_run_dir / "manifest.json"
    holdout_path = config.holdout_run_dir / "manifest.json"
    canary = read_json(canary_path)
    holdout = read_json(holdout_path)
    assert isinstance(canary, dict)
    assert isinstance(holdout, dict)
    canary_ids = canary["selection"]["question_set"]["question_ids"]
    holdout_ids = holdout["selection"]["question_set"]["question_ids"]
    canary_ids[0], holdout_ids[0] = holdout_ids[0], canary_ids[0]
    canary_path.unlink()
    holdout_path.unlink()
    write_json_exclusive(canary_path, canary)
    write_json_exclusive(holdout_path, holdout)

    with pytest.raises(ValueError, match="question inventory"):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_rejects_a_canary_other_than_the_frozen_selection(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    manifest_path = config.canary_run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    assert isinstance(manifest, dict)
    manifest["selection"]["question_set"]["selection_id"] = "other-canary"
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="frozen selection"):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_rejects_a_saved_summary_that_differs_from_replay(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    summaries = {
        run_dir: read_json(run_dir / "summary.json")
        for run_dir in (config.canary_run_dir, config.holdout_run_dir)
    }
    assert all(isinstance(summary, dict) for summary in summaries.values())
    summary_path = config.holdout_run_dir / "summary.json"
    tampered = read_json(summary_path)
    assert isinstance(tampered, dict)
    tampered["completed_question_count"] = 1
    summary_path.unlink()
    write_json_exclusive(summary_path, tampered)

    with pytest.raises(ValueError, match="saved summary"):
        verify_locomo_retrieval_gate(
            config,
            reporter=RecomputedGateReporter(summaries),
        )


@pytest.mark.parametrize(
    ("path", "drifted_value", "protocol_field"),
    [
        (("retrieval", "top_k"), 21, "top_k"),
        (("retrieval", "inference_threads"), 3, "inference_threads"),
        (
            ("retrieval", "embedding", "model"),
            "test/drifted-embedding",
            "embedding_model",
        ),
        (
            ("retrieval", "reranker", "model"),
            "test/drifted-reranker",
            "reranker_model",
        ),
        (
            ("retrieval", "planner", "maximum_rerank_candidates"),
            95,
            "maximum_rerank_candidates",
        ),
        (
            ("retrieval", "planner", "query_sketcher"),
            "test/drifted-query-sketcher",
            "query_sketcher",
        ),
        (
            ("retrieval", "planner", "fact_selector"),
            "test/drifted-fact-selector",
            "fact_selector",
        ),
        (
            ("retrieval", "planner", "context_renderer"),
            "test/drifted-context-renderer",
            "context_renderer",
        ),
        (
            ("retrieval", "planner", "context_max_tokens"),
            3_999,
            "context_max_tokens",
        ),
        (
            ("retrieval", "planner", "neighbor_window"),
            0,
            "neighbor_window",
        ),
        (("max_workers",), 9, "max_workers"),
        (
            ("execution_phase_contract",),
            "test/drifted-execution-contract",
            "execution_phase_contract",
        ),
        (
            ("paid_scoring_gate",),
            "test/drifted-paid-scoring-gate",
            "paid_scoring_gate",
        ),
        (
            ("question_worker", "max_rss_bytes"),
            1_073_741_824,
            "worker_max_rss_bytes",
        ),
        (
            ("question_worker", "reranker_warmup"),
            "test/drifted-reranker-warmup",
            "worker_reranker_warmup",
        ),
    ],
)
def test_retrieval_gate_rejects_retrieval_protocol_drift(
    tmp_path: Path,
    path: tuple[str, ...],
    drifted_value: object,
    protocol_field: str,
) -> None:
    config = _gate_fixture(tmp_path)
    manifest_path = config.canary_run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    assert isinstance(manifest, dict)
    target = manifest
    for field in path[:-1]:
        nested = target[field]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = drifted_value
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match=protocol_field):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_rejects_an_unknown_frozen_protocol_field(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    question_set_path = config.target_question_set_path
    definition = read_json(question_set_path)
    assert isinstance(definition, dict)
    protocol = definition["protocol"]
    promotion = definition["promotion"]
    assert isinstance(protocol, dict)
    assert isinstance(promotion, dict)
    protocol["unimplemented_retrieval_contract"] = "must-fail-closed"
    protocol_sha256 = canonical_sha256(protocol)
    for key in ("source_selection", "holdout_selection"):
        selection = promotion[key]
        assert isinstance(selection, dict)
        selection["protocol_sha256"] = protocol_sha256
    question_set_path.unlink()
    write_json_exclusive(question_set_path, definition)
    for run_dir in (config.canary_run_dir, config.holdout_run_dir):
        manifest_path = run_dir / "manifest.json"
        manifest = read_json(manifest_path)
        assert isinstance(manifest, dict)
        question_set = manifest["selection"]["question_set"]
        assert isinstance(question_set, dict)
        question_set["protocol_sha256"] = protocol_sha256
        manifest_path.unlink()
        write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="unknown frozen protocol fields"):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def test_retrieval_gate_requires_hierarchy_after_protocol_validation(
    tmp_path: Path,
) -> None:
    config = _gate_fixture(tmp_path)
    manifest_path = config.canary_run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    assert isinstance(manifest, dict)
    planner = manifest["retrieval"]["planner"]
    assert isinstance(planner, dict)
    planner.update(
        {
            "mode": "episode-only",
            "neighbor_window": 0,
            "temporal_neighbor_window": 0,
        }
    )
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="requires hierarchy mode"):
        verify_locomo_retrieval_gate(
            config,
            reporter=StaticGateReporter(),
        )


def _gate_fixture(tmp_path: Path) -> LoCoMoRetrievalGateConfig:
    dataset = load_locomo_dataset(FIXTURE)
    question_ids = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 2, 3, 4}
    )
    assert len(question_ids) == 4
    canary_selection_sha256 = _selection_sha256(question_ids[:2])
    holdout_selection_sha256 = _selection_sha256(question_ids[2:])
    protocol, _, _ = _retrieval_gate_contract()
    protocol_sha256 = canonical_sha256(protocol)
    question_set_path = tmp_path / "diagnostic-4-v18.json"
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "diagnostic-4-v18",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "diagnostic-4",
            "category_targets": {str(category): 1 for category in range(1, 5)},
            "selection_sha256": _selection_sha256(question_ids),
            "protocol": protocol,
            "promotion": {
                "source_selection": {
                    "selection_id": "canary",
                    "question_set_sha256": "a" * 64,
                    "selection_sha256": canary_selection_sha256,
                    "protocol_sha256": protocol_sha256,
                },
                "holdout_selection": {
                    "selection_id": "holdout",
                    "question_set_sha256": "b" * 64,
                    "selection_sha256": holdout_selection_sha256,
                    "protocol_sha256": protocol_sha256,
                },
            },
        },
    )
    corpus_dir = tmp_path / "corpus"
    query_vectors_dir = tmp_path / "query-vectors"
    corpus_dir.mkdir()
    query_vectors_dir.mkdir()
    write_json_exclusive(
        corpus_dir / "manifest.json",
        {
            "content_sha256": "c" * 64,
            "build_contract": {
                "repository_commit": "abc123",
            },
        },
    )
    write_json_exclusive(
        query_vectors_dir / "manifest.json",
        {
            "content_sha256": "f" * 64,
        },
    )
    canary_run_dir = tmp_path / "canary"
    holdout_run_dir = tmp_path / "holdout"
    _write_gate_run(
        canary_run_dir,
        run_id="canary",
        question_ids=question_ids[:2],
        protocol_sha256=protocol_sha256,
        selection_id="canary",
        question_set_sha256="a" * 64,
        selection_sha256=canary_selection_sha256,
    )
    _write_gate_run(
        holdout_run_dir,
        run_id="holdout",
        question_ids=question_ids[2:],
        protocol_sha256=protocol_sha256,
        selection_id="holdout",
        question_set_sha256="b" * 64,
        selection_sha256=holdout_selection_sha256,
    )
    return LoCoMoRetrievalGateConfig(
        target_question_set_path=question_set_path,
        scored_question_set_path=question_set_path,
        dataset_path=FIXTURE,
        canary_run_dir=canary_run_dir,
        holdout_run_dir=holdout_run_dir,
        repository_commit="abc123",
        corpus_path=corpus_dir,
        query_vectors_path=query_vectors_dir,
        expected_canary_questions=2,
        expected_holdout_questions=2,
    )


def _write_gate_run(
    run_dir: Path,
    *,
    run_id: str,
    question_ids: tuple[str, ...],
    protocol_sha256: str,
    selection_id: str,
    question_set_sha256: str,
    selection_sha256: str,
) -> None:
    run_dir.mkdir()
    _, retrieval, question_worker = _retrieval_gate_contract()
    worker_resources = {
        "max_process_rss_bytes": 1_000_000_000,
    }
    write_json_exclusive(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "mode": "retrieval",
            "scored": False,
            "repository_commit": "abc123",
            "paid_scoring_gate": LOCOMO_PAID_SCORING_GATE_CONTRACT,
            "answer_model": None,
            "judge_model": None,
            "corpus": {"content_sha256": "c" * 64},
            "query_vectors": {"content_sha256": "f" * 64},
            "retrieval": retrieval,
            "seed": 17,
            "max_workers": 10,
            "ingest_max_workers": 1,
            "retrieval_max_workers": 1,
            "retrieval_thread_count": 1,
            "execution_phase_contract": question_worker["name"],
            "question_worker": question_worker,
            "selection": {
                "question_set": {
                    "selection_id": selection_id,
                    "definition_sha256": question_set_sha256,
                    "selection_sha256": selection_sha256,
                    "protocol_sha256": protocol_sha256,
                    "question_ids": list(question_ids),
                    "question_count": len(question_ids),
                }
            },
        },
    )
    write_json_exclusive(
        run_dir / "summary.json",
        {
            "run_id": run_id,
            "mode": "retrieval",
            "scored": False,
            "completed_question_count": len(question_ids),
            "question_artifact_count": len(question_ids),
            "infrastructure_failed_count": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "known_cost_count": 0,
                "cost_usd": None,
            },
            "retrieval_diagnostics": {
                "latency_ms": {
                    "p95": 2_000.0,
                }
            },
            "worker_resources": worker_resources,
        },
    )
    write_json_exclusive(run_dir / "resource-usage.json", worker_resources)
    write_json_exclusive(
        run_dir / "computed-evidence.json",
        {
            "overall": {
                "context_all_coverage": 0.85,
            },
            "questions": [
                {
                    "question_id": question_id,
                    "context_token_count": 4_000,
                }
                for question_id in question_ids
            ],
        },
    )


def _retrieval_gate_contract() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    planner = RecallPlannerConfig().public_config
    embedding = {
        "adapter": "hashing-test",
        "model": "test/embedding",
        "dimension": 3,
    }
    reranker = {
        "model": "test/reranker",
        "batch_size": 8,
    }
    retrieval = {
        "method": "hybrid-rrf-cross-encoder",
        "inference_threads": 2,
        "tokenizer_parallelism": False,
        "tokenizer_threads": 1,
        "embedding": embedding,
        "reranker": reranker,
        "planner": planner,
        "top_k": 20,
    }
    question_worker = {
        "name": "verified-shared-corpus-exec-per-conversation-v3",
        "max_rss_bytes": 2 * 1024 * 1024 * 1024,
        "stall_timeout_seconds": 600,
        "poll_interval_seconds": 0.25,
        "rss_poll_interval_seconds": 1,
        "progress_signal": "heartbeat-evidence-and-durable-question-checkpoint-deadline-v2",
        "publish_policy": "conversation-directory-atomic-rename-v1",
        "reranker_warmup": "one-local-document-before-question-timing-v1",
    }
    protocol = {
        # Retrieval-only evidence must not require paid answer or judge providers.
        "answer_model": "test/paid-answer",
        "judge_model": "test/paid-judge",
        "paid_scoring_gate": LOCOMO_PAID_SCORING_GATE_CONTRACT,
        "seed": 17,
        "top_k": retrieval["top_k"],
        "inference_threads": retrieval["inference_threads"],
        "tokenizer_parallelism": retrieval["tokenizer_parallelism"],
        "tokenizer_threads": retrieval["tokenizer_threads"],
        "max_workers": 10,
        "ingest_max_workers": 1,
        "retrieval_max_workers": 1,
        "retrieval_thread_count": 1,
        "execution_phase_contract": question_worker["name"],
        "worker_contract": question_worker["name"],
        "worker_max_rss_bytes": question_worker["max_rss_bytes"],
        "worker_stall_timeout_seconds": question_worker["stall_timeout_seconds"],
        "worker_poll_interval_seconds": question_worker["poll_interval_seconds"],
        "worker_rss_poll_interval_seconds": question_worker["rss_poll_interval_seconds"],
        "worker_progress_signal": question_worker["progress_signal"],
        "worker_publish_policy": question_worker["publish_policy"],
        "worker_reranker_warmup": question_worker["reranker_warmup"],
        "embedding_adapter": embedding["adapter"],
        "embedding_model": embedding["model"],
        "embedding_dimension": embedding["dimension"],
        "reranker_model": reranker["model"],
        "reranker_batch_size": reranker["batch_size"],
        "neighbor_windows": {
            "episode-only": {
                "neighbor_window": 0,
                "temporal_neighbor_window": 0,
            },
            "hierarchy-no-neighbors": {
                "neighbor_window": 0,
                "temporal_neighbor_window": 0,
            },
            "hierarchy": {
                "neighbor_window": planner["neighbor_window"],
                "temporal_neighbor_window": planner["temporal_neighbor_window"],
            },
        },
        **{
            key: value
            for key, value in planner.items()
            if key not in {"mode", "neighbor_window", "temporal_neighbor_window"}
        },
    }
    return protocol, retrieval, question_worker


def _selection_sha256(question_ids: object) -> str:
    assert isinstance(question_ids, (list, tuple))
    assert all(isinstance(question_id, str) for question_id in question_ids)
    return hashlib.sha256(
        json.dumps(
            sorted(question_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
