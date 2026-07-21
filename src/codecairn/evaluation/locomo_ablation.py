from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo import report_locomo


@dataclass(frozen=True, slots=True)
class LoCoMoAblationConfig:
    question_set_path: Path
    episode_only_run: Path
    hierarchy_no_neighbors_run: Path
    hierarchy_run: Path
    output_path: Path


def build_locomo_ablation_report(config: LoCoMoAblationConfig) -> dict[str, object]:
    """Compare three immutable LoCoMo runs and evaluate the frozen launch gate."""
    definition = _dict(read_json(config.question_set_path), field="question set")
    definition_sha256 = file_sha256(config.question_set_path)
    expected_selection_sha256 = _str(definition, "selection_sha256")
    expected_variants = {
        _str(_dict(item, field="variant"), "id"): _str(_dict(item, field="variant"), "recall_mode")
        for item in _list(definition, "variants")
    }
    if expected_variants != {
        "episode-only": "episode-only",
        "hierarchy-no-neighbors": "hierarchy-no-neighbors",
        "hierarchy": "hierarchy",
    }:
        raise ValueError("LoCoMo ablation requires the three canonical recall variants")

    run_paths = {
        "episode-only": config.episode_only_run,
        "hierarchy-no-neighbors": config.hierarchy_no_neighbors_run,
        "hierarchy": config.hierarchy_run,
    }
    reports: dict[str, dict[str, object]] = {}
    manifests: dict[str, dict[str, object]] = {}
    for variant, run_path in run_paths.items():
        manifest = _dict(read_json(run_path / "manifest.json"), field=f"{variant} manifest")
        _validate_run_manifest(
            manifest,
            variant=variant,
            expected_mode=expected_variants[variant],
            definition_sha256=definition_sha256,
            selection_sha256=expected_selection_sha256,
        )
        manifests[variant] = manifest
        reports[variant] = report_locomo(run_path)
    _validate_constant_protocol(manifests)
    _validate_definition_protocol(
        manifests["hierarchy"],
        protocol=_dict(definition.get("protocol"), field="protocol"),
    )

    gates = _dict(definition.get("gates"), field="gates")
    required_questions = _int(gates, "required_scored_questions_per_variant")
    maximum_failures = _int(gates, "maximum_infrastructure_failures")
    minimum_core_delta = _number(
        gates, "hierarchy_no_neighbors_vs_episode_minimum_accuracy_delta_points"
    )
    minimum_neighbor_delta = _number(
        gates, "temporal_neighbor_minimum_overall_accuracy_delta_points"
    )
    minimum_neighbor_category_delta = _number(
        gates, "temporal_neighbor_minimum_temporal_or_multihop_delta_points"
    )
    maximum_neighbor_p95_increase = _number(gates, "temporal_neighbor_maximum_p95_increase_percent")
    maximum_selected_p95 = _number(gates, "selected_maximum_retrieval_p95_ms")
    episode_accuracy = _accuracy(reports["episode-only"])
    no_neighbor_accuracy = _accuracy(reports["hierarchy-no-neighbors"])
    hierarchy_accuracy = _accuracy(reports["hierarchy"])
    core_delta = round((no_neighbor_accuracy - episode_accuracy) * 100, 3)
    neighbor_delta = round((hierarchy_accuracy - no_neighbor_accuracy) * 100, 3)
    temporal_delta = round(
        (
            _category_accuracy(reports["hierarchy"], category=2)
            - _category_accuracy(reports["hierarchy-no-neighbors"], category=2)
        )
        * 100,
        3,
    )
    multihop_delta = round(
        (
            _category_accuracy(reports["hierarchy"], category=1)
            - _category_accuracy(reports["hierarchy-no-neighbors"], category=1)
        )
        * 100,
        3,
    )
    best_neighbor_category_delta = max(temporal_delta, multihop_delta)
    no_neighbor_p95 = _retrieval_p95(reports["hierarchy-no-neighbors"])
    hierarchy_p95 = _retrieval_p95(reports["hierarchy"])
    neighbor_p95_increase = (
        0.0
        if no_neighbor_p95 == 0 and hierarchy_p95 == 0
        else math.inf
        if no_neighbor_p95 == 0
        else round((hierarchy_p95 - no_neighbor_p95) * 100 / no_neighbor_p95, 3)
    )

    checks: list[dict[str, object]] = []
    for variant, report in reports.items():
        scored = _int(report, "scored_question_count")
        failures = _int(report, "infrastructure_failed_count")
        checks.extend(
            (
                _check(
                    f"{variant}.scored_questions",
                    observed=scored,
                    threshold=required_questions,
                    passed=scored == required_questions,
                ),
                _check(
                    f"{variant}.infrastructure_failures",
                    observed=failures,
                    threshold=maximum_failures,
                    passed=failures <= maximum_failures,
                ),
            )
        )
    checks.append(
        _check(
            "hierarchy-no-neighbors.accuracy_delta_vs_episode_points",
            observed=core_delta,
            threshold=minimum_core_delta,
            passed=core_delta >= minimum_core_delta,
        )
    )
    temporal_neighbor_checks = [
        _check(
            "hierarchy.accuracy_delta_vs_no_neighbors_points",
            observed=neighbor_delta,
            threshold=minimum_neighbor_delta,
            passed=neighbor_delta >= minimum_neighbor_delta,
        ),
        _check(
            "hierarchy.best_temporal_or_multihop_delta_points",
            observed=best_neighbor_category_delta,
            threshold=minimum_neighbor_category_delta,
            passed=best_neighbor_category_delta >= minimum_neighbor_category_delta,
        ),
        _check(
            "hierarchy.retrieval_p95_increase_percent",
            observed=neighbor_p95_increase,
            threshold=maximum_neighbor_p95_increase,
            passed=neighbor_p95_increase <= maximum_neighbor_p95_increase,
        ),
    ]
    temporal_neighbor_promoted = all(
        cast(bool, check["passed"]) for check in temporal_neighbor_checks
    )
    selected_variant = "hierarchy" if temporal_neighbor_promoted else "hierarchy-no-neighbors"
    selected_p95 = hierarchy_p95 if temporal_neighbor_promoted else no_neighbor_p95
    checks.append(
        _check(
            f"{selected_variant}.retrieval_p95_ms",
            observed=selected_p95,
            threshold=maximum_selected_p95,
            passed=selected_p95 <= maximum_selected_p95,
        )
    )
    report = {
        "schema_version": 1,
        "suite": "locomo-ablation",
        "selection_id": _str(definition, "selection_id"),
        "question_set_sha256": definition_sha256,
        "selection_sha256": expected_selection_sha256,
        "repository_commit": _str(manifests["hierarchy"], "repository_commit"),
        "variants": reports,
        "accuracy_delta_points": {
            "hierarchy_no_neighbors_vs_episode_only": core_delta,
            "hierarchy_vs_hierarchy_no_neighbors": neighbor_delta,
            "hierarchy_temporal_category_vs_no_neighbors": temporal_delta,
            "hierarchy_multihop_category_vs_no_neighbors": multihop_delta,
        },
        "checks": checks,
        "temporal_neighbor_checks": temporal_neighbor_checks,
        "temporal_neighbor_promoted": temporal_neighbor_promoted,
        "selected_variant": selected_variant,
        "gate_passed": all(cast(bool, check["passed"]) for check in checks),
    }
    write_json_exclusive(config.output_path, report)
    return report


def _validate_run_manifest(
    manifest: dict[str, object],
    *,
    variant: str,
    expected_mode: str,
    definition_sha256: str,
    selection_sha256: str,
) -> None:
    if manifest.get("mode") != "full" or manifest.get("scored") is not True:
        raise ValueError(f"{variant} must be a scored full LoCoMo run")
    selection = _dict(manifest.get("selection"), field=f"{variant} selection")
    question_set = _dict(selection.get("question_set"), field=f"{variant} question set")
    if (
        question_set.get("definition_sha256") != definition_sha256
        or question_set.get("selection_sha256") != selection_sha256
    ):
        raise ValueError(f"{variant} does not use the frozen diagnostic question set")
    retrieval = _dict(manifest.get("retrieval"), field=f"{variant} retrieval")
    planner = _dict(retrieval.get("planner"), field=f"{variant} planner")
    if planner.get("mode") != expected_mode:
        raise ValueError(f"{variant} retrieval mode does not match the ablation definition")


def _validate_constant_protocol(manifests: dict[str, dict[str, object]]) -> None:
    reference = manifests["hierarchy"]
    fields = (
        "repository_commit",
        "dataset",
        "selection",
        "answer_model",
        "judge_model",
        "judge_votes",
        "judge_response_max_attempts",
        "judge_response_max_chars",
        "seed",
        "max_workers",
        "ingest_max_workers",
        "retrieval_max_workers",
        "retrieval_thread_count",
        "execution_phase_contract",
        "corpus",
        "query_vectors",
    )
    for variant, manifest in manifests.items():
        for field in fields:
            if manifest.get(field) != reference.get(field):
                raise ValueError(f"{variant} changes the frozen LoCoMo protocol field: {field}")
        retrieval = _dict(manifest.get("retrieval"), field=f"{variant} retrieval")
        reference_retrieval = _dict(reference.get("retrieval"), field="hierarchy retrieval")
        for field in (
            "embedding",
            "reranker",
            "inference_threads",
            "tokenizer_parallelism",
            "tokenizer_threads",
            "top_k",
        ):
            if retrieval.get(field) != reference_retrieval.get(field):
                raise ValueError(f"{variant} changes the frozen retrieval field: {field}")
        planner = _dict(retrieval.get("planner"), field=f"{variant} planner")
        reference_planner = _dict(reference_retrieval.get("planner"), field="hierarchy planner")
        for field in (
            "router",
            "hard_route_cutoff",
            "primary_candidate_multiplier",
            "secondary_candidate_multiplier",
            "minimum_primary_candidates",
            "minimum_secondary_candidates",
            "neighbor_snippet_budget",
            "enrichment_order",
            "matched_facts_per_memory",
            "sibling_facts_per_memory",
        ):
            if planner.get(field) != reference_planner.get(field):
                raise ValueError(f"{variant} changes the frozen planner field: {field}")


def _validate_definition_protocol(
    manifest: dict[str, object],
    *,
    protocol: dict[str, object],
) -> None:
    answer = _dict(manifest.get("answer_model"), field="answer model")
    judge = _dict(manifest.get("judge_model"), field="judge model")
    retrieval = _dict(manifest.get("retrieval"), field="retrieval")
    embedding = _dict(retrieval.get("embedding"), field="embedding")
    reranker = _dict(retrieval.get("reranker"), field="reranker")
    planner = _dict(retrieval.get("planner"), field="planner")
    observed = {
        "answer_model": answer.get("model"),
        "judge_model": judge.get("model"),
        "judge_votes": manifest.get("judge_votes"),
        "top_k": retrieval.get("top_k"),
        "inference_threads": retrieval.get("inference_threads"),
        "tokenizer_parallelism": retrieval.get("tokenizer_parallelism"),
        "tokenizer_threads": retrieval.get("tokenizer_threads"),
        "max_workers": manifest.get("max_workers"),
        "ingest_max_workers": manifest.get("ingest_max_workers"),
        "retrieval_max_workers": manifest.get("retrieval_max_workers"),
        "retrieval_thread_count": manifest.get("retrieval_thread_count"),
        "execution_phase_contract": manifest.get("execution_phase_contract"),
        "embedding_adapter": embedding.get("adapter"),
        "embedding_model": embedding.get("model"),
        "embedding_dimension": embedding.get("dimension"),
        "reranker_model": reranker.get("model"),
        "primary_candidate_multiplier": planner.get("primary_candidate_multiplier"),
        "secondary_candidate_multiplier": planner.get("secondary_candidate_multiplier"),
        "minimum_primary_candidates": planner.get("minimum_primary_candidates"),
        "minimum_secondary_candidates": planner.get("minimum_secondary_candidates"),
        "neighbor_snippet_budget": planner.get("neighbor_snippet_budget"),
        "enrichment_order": planner.get("enrichment_order"),
    }
    for field, value in observed.items():
        if value != protocol.get(field):
            raise ValueError(f"LoCoMo run changes the diagnostic protocol field: {field}")


def _accuracy(report: dict[str, object]) -> float:
    value = report.get("accuracy")
    if not isinstance(value, int | float):
        raise ValueError("LoCoMo ablation run has no scored accuracy")
    return float(value)


def _category_accuracy(report: dict[str, object], *, category: int) -> float:
    by_category = _dict(report.get("by_category"), field="LoCoMo category report")
    category_report = _dict(
        by_category.get(str(category)), field=f"LoCoMo category {category} report"
    )
    return _number(category_report, "accuracy")


def _retrieval_p95(report: dict[str, object]) -> float:
    diagnostics = _dict(report.get("retrieval_diagnostics"), field="retrieval diagnostics")
    latency = _dict(diagnostics.get("latency_ms"), field="retrieval latency")
    return _number(latency, "p95")


def _check(
    check_id: str,
    *,
    observed: int | float,
    threshold: int | float,
    passed: bool,
) -> dict[str, object]:
    return {
        "id": check_id,
        "observed": observed,
        "threshold": threshold,
        "passed": passed,
    }


def _dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _list(value: dict[str, object], field: str) -> list[object]:
    raw = value.get(field)
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be an array")
    return raw


def _str(value: dict[str, object], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{field} must be a non-empty string")
    return raw


def _int(value: dict[str, object], field: str) -> int:
    raw = value.get(field)
    if type(raw) is not int:
        raise ValueError(f"{field} must be an integer")
    return raw


def _number(value: dict[str, object], field: str) -> float:
    raw = value.get(field)
    if not isinstance(raw, int | float):
        raise ValueError(f"{field} must be numeric")
    return float(raw)
