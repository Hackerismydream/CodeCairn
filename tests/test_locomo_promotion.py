from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecairn.bootstrap import app
from codecairn.evaluation.artifacts import file_sha256, write_json_exclusive
from codecairn.evaluation.locomo import (
    _FROZEN_PLANNER_PROTOCOL_FIELDS,
    LoCoMoCorpusConfig,
    build_locomo_corpus,
)
from codecairn.evaluation.locomo_promotion import (
    LoCoMoPromotionConfig,
    build_locomo_promotion_report,
)

LOCOMO_FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"
TEST_RETRIEVAL_CONFIG: dict[str, object] = {
    "embedding": {
        "adapter": "test",
        "model": "test/embedding",
        "source": "test",
        "revision": "v1",
        "dimension": 3,
    }
}


def test_paid_semantic_corpus_requires_question_set_before_any_side_effect(
    tmp_path: Path,
) -> None:
    memory_factory_calls = 0
    usage_reader_calls = 0

    def memory_factory(_root: Path) -> object:
        nonlocal memory_factory_calls
        memory_factory_calls += 1
        raise AssertionError("memory factory must not run before protocol preflight")

    def usage_reader() -> dict[str, object]:
        nonlocal usage_reader_calls
        usage_reader_calls += 1
        raise AssertionError("provider usage must not be read before protocol preflight")

    output_root = tmp_path / "corpora"
    with pytest.raises(ValueError, match="frozen question set"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=LOCOMO_FIXTURE,
                output_root=output_root,
                corpus_id="paid-without-question-set",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=TEST_RETRIEVAL_CONFIG,
                semantic_projection={
                    "adapter": "openai-compatible-structured-clause",
                    "model": "test/semantic",
                    "revision": "v1",
                },
                semantic_projection_usage=usage_reader,
            ),
            memory_factory=memory_factory,
        )

    assert memory_factory_calls == 0
    assert usage_reader_calls == 0
    assert not output_root.exists()


def test_single_run_promotion_binds_selection_contract_and_all_absolute_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        _fixture_reporter(config, run_report),
    )

    report = build_locomo_promotion_report(config)

    assert report["gate_passed"] is True
    assert report["selected_variant"] == "hierarchy"
    assert report["baseline"] == {
        "run_id": "locomo-v5-diagnostic200-hierarchy-d5fb39c",
        "repository_commit": "d5fb39c31355b66b46a5600d1f4a7116d723dece",
        "summary_sha256": "5" * 64,
        "selection_sha256": json.loads(config.question_set_path.read_text(encoding="utf-8"))[
            "selection_sha256"
        ],
        "scored_question_count": 200,
        "infrastructure_failed_count": 0,
        "single_hop_accuracy": 0.92,
    }
    checks = {item["id"]: item for item in report["checks"]}
    assert set(checks) == {
        "scored_questions",
        "infrastructure_failures",
        "overall_accuracy",
        "multi_hop_accuracy",
        "open_domain_accuracy",
        "single_hop_regression_points",
        "retrieval_p95_ms",
        "max_process_rss_bytes",
    }
    assert checks["single_hop_regression_points"]["observed"] == pytest.approx(1.0)
    assert checks["max_process_rss_bytes"]["comparison"] == "less_than"
    assert report["selection_report_sha256"] == file_sha256(config.selection_report_path)
    assert json.loads(config.output_path.read_text(encoding="utf-8")) == report


def test_single_run_promotion_freezes_every_failed_absolute_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    run_report["scored_question_count"] = 199
    run_report["infrastructure_failed_count"] = 1
    run_report["accuracy"] = 0.77
    by_category = run_report["by_category"]
    assert isinstance(by_category, dict)
    by_category["1"]["accuracy"] = 0.69
    by_category["3"]["accuracy"] = 0.67
    by_category["4"]["accuracy"] = 0.89
    run_report["retrieval_diagnostics"] = {"latency_ms": {"p95": 2500.001}}
    run_report["worker_resources"] = {"max_process_rss_bytes": 2 * 1024 * 1024 * 1024}
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        _fixture_reporter(config, run_report),
    )

    report = build_locomo_promotion_report(config)

    assert report["gate_passed"] is False
    assert {item["id"] for item in report["checks"] if item["passed"] is False} == {
        "scored_questions",
        "infrastructure_failures",
        "overall_accuracy",
        "multi_hop_accuracy",
        "open_domain_accuracy",
        "single_hop_regression_points",
        "retrieval_p95_ms",
        "max_process_rss_bytes",
    }


def test_single_run_promotion_rejects_contract_drift_before_reporting_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    manifest_path = config.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["corpus"]["content_sha256"] = "f" * 64
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)
    report_calls = 0

    source_reporter = _fixture_reporter(config, run_report)

    def forbidden_report(run_dir: Path) -> dict[str, object]:
        nonlocal report_calls
        if run_dir.resolve() == config.run_dir.resolve():
            report_calls += 1
            raise AssertionError("contract drift must fail before reading paid run results")
        return source_reporter(run_dir)

    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        forbidden_report,
    )

    with pytest.raises(ValueError, match="corpus"):
        build_locomo_promotion_report(config)

    assert report_calls == 0
    assert not config.output_path.exists()


def test_promotion_rejects_incomplete_selection_evidence_before_reading_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _run_report = _promotion_fixture(tmp_path)
    selection_report = json.loads(config.selection_report_path.read_text(encoding="utf-8"))
    selection_report.pop("checks")
    _replace_json(config.selection_report_path, selection_report)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        lambda _run_dir: pytest.fail("invalid selection evidence must fail before run reporting"),
    )

    with pytest.raises(ValueError, match=r"derived field.*checks"):
        build_locomo_promotion_report(config)

    assert not config.output_path.exists()


def test_promotion_rejects_invalid_selection_manifest_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _run_report = _promotion_fixture(tmp_path)
    selection_report = json.loads(config.selection_report_path.read_text(encoding="utf-8"))
    selection_report["run_manifests"]["hierarchy"]["manifest_sha256"] = "not-a-digest"
    _replace_json(config.selection_report_path, selection_report)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        lambda _run_dir: pytest.fail("invalid manifest evidence must fail before run reporting"),
    )

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        build_locomo_promotion_report(config)


def test_promotion_rejects_selection_report_not_bound_to_actual_40_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    manifest_path = config.hierarchy_run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at_utc"] = "changed-after-selection-report"
    _replace_json(manifest_path, manifest)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        _fixture_reporter(config, run_report, forbid_200=True),
    )

    with pytest.raises(ValueError, match=r"manifest receipt.*source run"):
        build_locomo_promotion_report(config)


def test_promotion_rejects_selection_metrics_not_reproduced_by_40_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    bound_reporter = _fixture_reporter(config, run_report)

    def drifted_reporter(run_dir: Path) -> dict[str, object]:
        report = json.loads(json.dumps(bound_reporter(run_dir)))
        if run_dir.resolve() == config.hierarchy_run.resolve():
            report["accuracy"] = 0.79
        return report

    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        drifted_reporter,
    )

    with pytest.raises(ValueError, match="checkpoints"):
        build_locomo_promotion_report(config)


def test_promotion_rejects_complete_200_protocol_drift_before_reporting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    manifest_path = config.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["max_workers"] = 1
    _replace_json(manifest_path, manifest)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        _fixture_reporter(config, run_report, forbid_200=True),
    )

    with pytest.raises(ValueError, match="max_workers"):
        build_locomo_promotion_report(config)


def test_promotion_rejects_200_question_inventory_drift_before_reporting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    manifest_path = config.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["selection"]["question_set"]["question_ids"][-1] = "substituted-question"
    manifest["selection"]["question_ids_by_conversation"]["conversation-1"][-1] = (
        "substituted-question"
    )
    _replace_json(manifest_path, manifest)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        _fixture_reporter(config, run_report, forbid_200=True),
    )

    with pytest.raises(ValueError, match="frozen selection"):
        build_locomo_promotion_report(config)


def test_cli_builds_the_single_run_promotion_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, run_report = _promotion_fixture(tmp_path)
    monkeypatch.setattr(
        "codecairn.evaluation.locomo_promotion.report_locomo",
        _fixture_reporter(config, run_report),
    )

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "promote-locomo",
            str(config.question_set_path),
            "--selection-report",
            str(config.selection_report_path),
            "--episode-only-run",
            str(config.episode_only_run),
            "--hierarchy-no-neighbors-run",
            str(config.hierarchy_no_neighbors_run),
            "--hierarchy-run",
            str(config.hierarchy_run),
            "--run",
            str(config.run_dir),
            "--output",
            str(config.output_path),
            "--root",
            str(tmp_path / "runtime"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["gate_passed"] is True


def _promotion_fixture(
    tmp_path: Path,
) -> tuple[LoCoMoPromotionConfig, dict[str, object]]:
    question_set_path = tmp_path / "diagnostic-200.json"
    selection_report_path = tmp_path / "diagnostic-40-report.json"
    run_dir = tmp_path / "diagnostic-200-run"
    run_dir.mkdir()
    protocol = json.loads(
        (Path(__file__).parents[1] / "benchmarks/locomo/diagnostic-200-v17.json").read_text(
            encoding="utf-8"
        )
    )["protocol"]
    source_question_set_sha256 = "1" * 64
    source_selection_sha256 = "6" * 64
    source_gates = {
        "required_scored_questions_per_variant": 40,
        "maximum_infrastructure_failures": 0,
        "hierarchy_no_neighbors_vs_episode_minimum_accuracy_delta_points": 2.0,
        "temporal_neighbor_minimum_overall_accuracy_delta_points": 0.0,
        "temporal_neighbor_minimum_temporal_or_multihop_delta_points": 0.1,
        "temporal_neighbor_maximum_p95_increase_percent": 20.0,
        "selected_maximum_retrieval_p95_ms": 2500.0,
    }
    question_ids = [f"question-{index:03d}" for index in range(200)]
    selection_sha256 = hashlib.sha256(
        json.dumps(sorted(question_ids), separators=(",", ":")).encode()
    ).hexdigest()
    corpus = {
        "artifact_id": "locomo-corpus-v7",
        "repository_commit": "corpus-commit",
        "content_sha256": "a" * 64,
        "build_contract_sha256": "b" * 64,
        "tree_sha256": "c" * 64,
    }
    query_vectors = {
        "artifact_id": "locomo-query-vectors-v13",
        "content_sha256": "d" * 64,
        "selection_sha256": selection_sha256,
    }
    answer_model = {"adapter": "deepseek", "model": "deepseek-v4-flash"}
    judge_model = {"adapter": "deepseek", "model": "deepseek-v4-flash"}
    run_contracts = {
        variant: {
            "repository_commit": "abc123",
            "recall_mode": variant,
            "corpus": corpus,
            "query_vectors": query_vectors,
            "answer_model": answer_model,
            "judge_model": judge_model,
        }
        for variant in ("episode-only", "hierarchy-no-neighbors", "hierarchy")
    }
    selected_contract = run_contracts["hierarchy"]
    variant_reports = {
        "episode-only": _selection_variant_report(
            "episode-only",
            accuracy=0.70,
            multi_hop_accuracy=0.65,
            temporal_accuracy=0.65,
            retrieval_p95_ms=900.0,
        ),
        "hierarchy-no-neighbors": _selection_variant_report(
            "hierarchy-no-neighbors",
            accuracy=0.75,
            multi_hop_accuracy=0.70,
            temporal_accuracy=0.70,
            retrieval_p95_ms=1000.0,
        ),
        "hierarchy": _selection_variant_report(
            "hierarchy",
            accuracy=0.80,
            multi_hop_accuracy=0.80,
            temporal_accuracy=0.75,
            retrieval_p95_ms=1100.0,
        ),
    }
    selection_checks = [
        *[
            check
            for variant in ("episode-only", "hierarchy-no-neighbors", "hierarchy")
            for check in (
                {
                    "id": f"{variant}.scored_questions",
                    "observed": 40,
                    "threshold": 40,
                    "passed": True,
                },
                {
                    "id": f"{variant}.infrastructure_failures",
                    "observed": 0,
                    "threshold": 0,
                    "passed": True,
                },
            )
        ],
        {
            "id": "hierarchy-no-neighbors.accuracy_delta_vs_episode_points",
            "observed": 5.0,
            "threshold": 2.0,
            "passed": True,
        },
        {
            "id": "hierarchy.retrieval_p95_ms",
            "observed": 1100.0,
            "threshold": 2500.0,
            "passed": True,
        },
    ]
    temporal_checks = [
        {
            "id": "hierarchy.accuracy_delta_vs_no_neighbors_points",
            "observed": 5.0,
            "threshold": 0.0,
            "passed": True,
        },
        {
            "id": "hierarchy.best_temporal_or_multihop_delta_points",
            "observed": 10.0,
            "threshold": 0.1,
            "passed": True,
        },
        {
            "id": "hierarchy.retrieval_p95_increase_percent",
            "observed": 10.0,
            "threshold": 20.0,
            "passed": True,
        },
    ]
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "locomo-diagnostic-200-v1",
            "dataset_sha256": "0" * 64,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {str(category): 50 for category in range(1, 5)},
            "selection_sha256": selection_sha256,
            "protocol": protocol,
            "promotion": {
                "schema_version": 1,
                "source_selection": {
                    "selection_id": "locomo-diagnostic-40-v1",
                    "question_set_sha256": source_question_set_sha256,
                    "selection_sha256": source_selection_sha256,
                    "protocol_sha256": _canonical_sha256(protocol),
                    "gates_sha256": _canonical_sha256(source_gates),
                },
                "required_scored_questions": 200,
                "frozen_baseline": {
                    "run_id": "locomo-v5-diagnostic200-hierarchy-d5fb39c",
                    "repository_commit": "d5fb39c31355b66b46a5600d1f4a7116d723dece",
                    "summary_sha256": "5" * 64,
                    "selection_sha256": selection_sha256,
                    "scored_question_count": 200,
                    "infrastructure_failed_count": 0,
                    "single_hop_accuracy": 0.92,
                },
                "gates": {
                    "minimum_overall_accuracy": 0.78,
                    "minimum_multi_hop_accuracy": 0.70,
                    "minimum_open_domain_accuracy": 0.68,
                    "maximum_single_hop_regression_points": 2.0,
                    "maximum_infrastructure_failures": 0,
                    "maximum_retrieval_p95_ms": 2500.0,
                    "maximum_process_rss_bytes_exclusive": 2 * 1024 * 1024 * 1024,
                },
            },
        },
    )
    write_json_exclusive(
        selection_report_path,
        {
            "schema_version": 1,
            "suite": "locomo-ablation",
            "selection_id": "locomo-diagnostic-40-v1",
            "question_set_sha256": source_question_set_sha256,
            "selection_sha256": source_selection_sha256,
            "question_set_protocol_sha256": _canonical_sha256(protocol),
            "question_set_gates": source_gates,
            "question_set_gates_sha256": _canonical_sha256(source_gates),
            "repository_commit": "abc123",
            "variants": variant_reports,
            "run_manifests": {
                variant: {
                    "run_id": f"locomo-diagnostic-40-v15-{variant}",
                    "manifest_sha256": str(index) * 64,
                }
                for index, variant in enumerate(
                    ("episode-only", "hierarchy-no-neighbors", "hierarchy"),
                    start=1,
                )
            },
            "run_contracts": run_contracts,
            "accuracy_delta_points": {
                "hierarchy_no_neighbors_vs_episode_only": 5.0,
                "hierarchy_vs_hierarchy_no_neighbors": 5.0,
                "hierarchy_temporal_category_vs_no_neighbors": 5.0,
                "hierarchy_multihop_category_vs_no_neighbors": 10.0,
            },
            "checks": selection_checks,
            "temporal_neighbor_checks": temporal_checks,
            "temporal_neighbor_promoted": True,
            "selected_variant": "hierarchy",
            "selected_run_id": "locomo-diagnostic-40-v15-hierarchy",
            "selected_run_contract": selected_contract,
            "gate_passed": True,
        },
    )
    write_json_exclusive(
        run_dir / "manifest.json",
        {
            "schema_version": 1,
            "suite": "locomo",
            "run_id": "locomo-diagnostic-200-v15-hierarchy",
            "mode": "full",
            "scored": True,
            "repository_commit": "abc123",
            "selection": {
                "conversation_ids": ["conversation-1"],
                "categories": [1, 2, 3, 4],
                "question_counts": {str(category): 50 for category in range(1, 5)},
                "question_ids_by_conversation": {"conversation-1": question_ids},
                "question_set": {
                    "selection_id": "locomo-diagnostic-200-v1",
                    "definition_sha256": file_sha256(question_set_path),
                    "dataset_sha256": "0" * 64,
                    "algorithm": "stratified-sha256-v1",
                    "seed": "selection-seed",
                    "category_targets": {str(category): 50 for category in range(1, 5)},
                    "question_count": 200,
                    "question_ids": question_ids,
                    "selection_sha256": selection_sha256,
                    "protocol_sha256": _canonical_sha256(protocol),
                },
            },
            "retrieval": _retrieval_manifest(protocol, mode="hierarchy"),
            "corpus": corpus,
            "query_vectors": {
                **query_vectors,
                "coverage": "exact",
                "artifact_question_count": 200,
                "run_question_count": 200,
                "run_selection_sha256": selection_sha256,
            },
            "answer_model": answer_model,
            "answer_evidence_contract": protocol["answer_evidence_contract"],
            "answer_retry_contract": protocol["answer_retry_contract"],
            "answer_response_max_attempts": protocol["answer_response_max_attempts"],
            "judge_model": judge_model,
            "judge_contract": protocol["judge_contract"],
            "judge_votes": protocol["judge_votes"],
            "judge_response_max_attempts": protocol["judge_response_max_attempts"],
            "judge_response_max_chars": protocol["judge_response_max_chars"],
            "seed": protocol["seed"],
            "max_workers": protocol["max_workers"],
            "ingest_max_workers": protocol["ingest_max_workers"],
            "retrieval_max_workers": protocol["retrieval_max_workers"],
            "retrieval_thread_count": protocol["retrieval_thread_count"],
            "execution_phase_contract": protocol["execution_phase_contract"],
            "question_worker": _worker_manifest(protocol),
        },
    )
    source_run_dirs: dict[str, Path] = {}
    source_manifest_receipts: dict[str, dict[str, object]] = {}
    base_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    for variant in ("episode-only", "hierarchy-no-neighbors", "hierarchy"):
        source_run_dir = tmp_path / f"diagnostic-40-{variant}"
        source_run_dir.mkdir()
        source_manifest = json.loads(json.dumps(base_manifest))
        source_manifest["run_id"] = f"locomo-diagnostic-40-v15-{variant}"
        source_manifest["retrieval"] = _retrieval_manifest(protocol, mode=variant)
        source_question_set = source_manifest["selection"]["question_set"]
        source_question_set["definition_sha256"] = source_question_set_sha256
        source_question_set["selection_sha256"] = source_selection_sha256
        source_manifest_path = source_run_dir / "manifest.json"
        write_json_exclusive(source_manifest_path, source_manifest)
        source_run_dirs[variant] = source_run_dir
        source_manifest_receipts[variant] = {
            "run_id": source_manifest["run_id"],
            "manifest_sha256": file_sha256(source_manifest_path),
        }
    selection_report = json.loads(selection_report_path.read_text(encoding="utf-8"))
    selection_report["run_manifests"] = source_manifest_receipts
    _replace_json(selection_report_path, selection_report)
    run_report: dict[str, object] = {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": "locomo-diagnostic-200-v15-hierarchy",
        "mode": "full",
        "scored": True,
        "scored_question_count": 200,
        "infrastructure_failed_count": 0,
        "accuracy": 0.80,
        "by_category": {
            "1": {"accuracy": 0.72, "count": 50},
            "3": {"accuracy": 0.70, "count": 50},
            "4": {"accuracy": 0.91, "count": 50},
        },
        "retrieval_diagnostics": {"latency_ms": {"p95": 2400.0}},
        "worker_resources": {"max_process_rss_bytes": 2 * 1024 * 1024 * 1024 - 1},
    }
    return (
        LoCoMoPromotionConfig(
            question_set_path=question_set_path,
            selection_report_path=selection_report_path,
            episode_only_run=source_run_dirs["episode-only"],
            hierarchy_no_neighbors_run=source_run_dirs["hierarchy-no-neighbors"],
            hierarchy_run=source_run_dirs["hierarchy"],
            run_dir=run_dir,
            output_path=tmp_path / "promotion-report.json",
        ),
        run_report,
    )


def _selection_variant_report(
    variant: str,
    *,
    accuracy: float,
    multi_hop_accuracy: float,
    temporal_accuracy: float,
    retrieval_p95_ms: float,
) -> dict[str, object]:
    return {
        "suite": "locomo",
        "run_id": f"locomo-diagnostic-40-v15-{variant}",
        "mode": "full",
        "scored": True,
        "scored_question_count": 40,
        "infrastructure_failed_count": 0,
        "accuracy": accuracy,
        "by_category": {
            "1": {"accuracy": multi_hop_accuracy},
            "2": {"accuracy": temporal_accuracy},
        },
        "retrieval_diagnostics": {"latency_ms": {"p95": retrieval_p95_ms}},
    }


def _retrieval_manifest(protocol: dict[str, object], *, mode: str) -> dict[str, object]:
    windows = protocol["neighbor_windows"][mode]
    return {
        "top_k": protocol["top_k"],
        "inference_threads": protocol["inference_threads"],
        "tokenizer_parallelism": protocol["tokenizer_parallelism"],
        "tokenizer_threads": protocol["tokenizer_threads"],
        "embedding": {
            "adapter": protocol["embedding_adapter"],
            "model": protocol["embedding_model"],
            "dimension": protocol["embedding_dimension"],
        },
        "reranker": {
            "model": protocol["reranker_model"],
            "batch_size": protocol["reranker_batch_size"],
        },
        "planner": {
            "mode": mode,
            **windows,
            **{field: protocol[field] for field in _FROZEN_PLANNER_PROTOCOL_FIELDS},
        },
    }


def _worker_manifest(protocol: dict[str, object]) -> dict[str, object]:
    return {
        "name": protocol["worker_contract"],
        "max_rss_bytes": protocol["worker_max_rss_bytes"],
        "stall_timeout_seconds": protocol["worker_stall_timeout_seconds"],
        "poll_interval_seconds": protocol["worker_poll_interval_seconds"],
        "rss_poll_interval_seconds": protocol["worker_rss_poll_interval_seconds"],
        "progress_signal": protocol["worker_progress_signal"],
        "publish_policy": protocol["worker_publish_policy"],
    }


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _fixture_reporter(
    config: LoCoMoPromotionConfig,
    run_report: dict[str, object],
    *,
    forbid_200: bool = False,
) -> Callable[[Path], dict[str, object]]:
    selection_report = json.loads(config.selection_report_path.read_text(encoding="utf-8"))
    source_reports = {
        config.episode_only_run.resolve(): selection_report["variants"]["episode-only"],
        config.hierarchy_no_neighbors_run.resolve(): selection_report["variants"][
            "hierarchy-no-neighbors"
        ],
        config.hierarchy_run.resolve(): selection_report["variants"]["hierarchy"],
    }

    def report(run_dir: Path) -> dict[str, object]:
        resolved = run_dir.resolve()
        if resolved == config.run_dir.resolve():
            if forbid_200:
                pytest.fail("invalid 200-question artifact must fail before run reporting")
            return run_report
        return source_reports[resolved]

    return report


def _replace_json(path: Path, value: object) -> None:
    path.unlink()
    write_json_exclusive(path, value)
