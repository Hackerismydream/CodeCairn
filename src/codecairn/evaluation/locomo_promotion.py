from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo import report_locomo
from codecairn.evaluation.locomo_ablation import (
    validate_locomo_ablation_sources,
    validate_locomo_manifest_protocol,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CANONICAL_VARIANTS = frozenset({"episode-only", "hierarchy-no-neighbors", "hierarchy"})


@dataclass(frozen=True, slots=True)
class LoCoMoPromotionConfig:
    question_set_path: Path
    selection_report_path: Path
    episode_only_run: Path
    hierarchy_no_neighbors_run: Path
    hierarchy_run: Path
    run_dir: Path
    output_path: Path


def build_locomo_promotion_report(config: LoCoMoPromotionConfig) -> dict[str, object]:
    """Evaluate one selected 200-question run against its frozen promotion contract."""
    definition = _dict(read_json(config.question_set_path), field="promotion question set")
    if definition.get("schema_version") != 1:
        raise ValueError("LoCoMo promotion question-set schema version must be 1")
    definition_sha256 = file_sha256(config.question_set_path)
    selection_sha256 = _sha256(definition, "selection_sha256")
    promotion = _dict(definition.get("promotion"), field="promotion contract")
    if promotion.get("schema_version") != 1:
        raise ValueError("LoCoMo promotion schema version must be 1")
    source_selection = _dict(
        promotion.get("source_selection"),
        field="promotion source selection",
    )
    required_questions = _positive_int(promotion, "required_scored_questions")
    baseline = _validate_frozen_baseline(
        _dict(promotion.get("frozen_baseline"), field="frozen promotion baseline"),
        selection_sha256=selection_sha256,
        required_questions=required_questions,
    )
    gates = _validate_gates(_dict(promotion.get("gates"), field="promotion gates"))

    selection_report = _dict(
        read_json(config.selection_report_path),
        field="40-question selection report",
    )
    selected_variant, selected_contract = _validate_selection_report(
        selection_report,
        source_selection=source_selection,
        protocol=_dict(definition.get("protocol"), field="200-question protocol"),
        run_paths={
            "episode-only": config.episode_only_run,
            "hierarchy-no-neighbors": config.hierarchy_no_neighbors_run,
            "hierarchy": config.hierarchy_run,
        },
    )

    manifest_path = config.run_dir / "manifest.json"
    manifest = _dict(read_json(manifest_path), field="200-question run manifest")
    _validate_run_manifest(
        manifest,
        definition=definition,
        definition_sha256=definition_sha256,
        selection_sha256=selection_sha256,
        selected_variant=selected_variant,
        selected_contract=selected_contract,
        required_questions=required_questions,
        maximum_rss_bytes_exclusive=cast(int, gates["maximum_process_rss_bytes_exclusive"]),
    )
    run_report = report_locomo(config.run_dir)
    _validate_run_report_identity(run_report, manifest=manifest)

    scored_questions = _int(run_report, "scored_question_count")
    infrastructure_failures = _int(run_report, "infrastructure_failed_count")
    overall_accuracy = _accuracy(run_report, field="accuracy")
    multi_hop_accuracy = _category_accuracy(run_report, category=1)
    open_domain_accuracy = _category_accuracy(run_report, category=3)
    single_hop_accuracy = _category_accuracy(run_report, category=4)
    baseline_single_hop_accuracy = _accuracy(baseline, field="single_hop_accuracy")
    single_hop_regression_points = round(
        (baseline_single_hop_accuracy - single_hop_accuracy) * 100,
        3,
    )
    retrieval_p95_ms = _retrieval_p95(run_report)
    max_process_rss_bytes = _max_process_rss(run_report)

    checks = [
        _check(
            "scored_questions",
            observed=scored_questions,
            threshold=required_questions,
            comparison="equal",
            passed=scored_questions == required_questions,
        ),
        _check(
            "infrastructure_failures",
            observed=infrastructure_failures,
            threshold=cast(int, gates["maximum_infrastructure_failures"]),
            comparison="at_most",
            passed=infrastructure_failures <= cast(int, gates["maximum_infrastructure_failures"]),
        ),
        _check(
            "overall_accuracy",
            observed=overall_accuracy,
            threshold=cast(float, gates["minimum_overall_accuracy"]),
            comparison="at_least",
            passed=overall_accuracy >= cast(float, gates["minimum_overall_accuracy"]),
        ),
        _check(
            "multi_hop_accuracy",
            observed=multi_hop_accuracy,
            threshold=cast(float, gates["minimum_multi_hop_accuracy"]),
            comparison="at_least",
            passed=multi_hop_accuracy >= cast(float, gates["minimum_multi_hop_accuracy"]),
        ),
        _check(
            "open_domain_accuracy",
            observed=open_domain_accuracy,
            threshold=cast(float, gates["minimum_open_domain_accuracy"]),
            comparison="at_least",
            passed=open_domain_accuracy >= cast(float, gates["minimum_open_domain_accuracy"]),
        ),
        _check(
            "single_hop_regression_points",
            observed=single_hop_regression_points,
            threshold=cast(float, gates["maximum_single_hop_regression_points"]),
            comparison="at_most",
            passed=single_hop_regression_points
            <= cast(float, gates["maximum_single_hop_regression_points"]),
        ),
        _check(
            "retrieval_p95_ms",
            observed=retrieval_p95_ms,
            threshold=cast(float, gates["maximum_retrieval_p95_ms"]),
            comparison="at_most",
            passed=retrieval_p95_ms <= cast(float, gates["maximum_retrieval_p95_ms"]),
        ),
        _check(
            "max_process_rss_bytes",
            observed=max_process_rss_bytes,
            threshold=cast(int, gates["maximum_process_rss_bytes_exclusive"]),
            comparison="less_than",
            passed=max_process_rss_bytes < cast(int, gates["maximum_process_rss_bytes_exclusive"]),
        ),
    ]
    report: dict[str, object] = {
        "schema_version": 1,
        "suite": "locomo-promotion",
        "selection_id": _str(definition, "selection_id"),
        "question_set_sha256": definition_sha256,
        "selection_sha256": selection_sha256,
        "selection_report_sha256": file_sha256(config.selection_report_path),
        "source_selection_id": _str(source_selection, "selection_id"),
        "selected_variant": selected_variant,
        "selected_run_id": _str(selection_report, "selected_run_id"),
        "selected_run_contract": selected_contract,
        "run_id": _str(manifest, "run_id"),
        "run_manifest_sha256": file_sha256(manifest_path),
        "baseline": baseline,
        "metrics": {
            "scored_question_count": scored_questions,
            "infrastructure_failed_count": infrastructure_failures,
            "overall_accuracy": overall_accuracy,
            "multi_hop_accuracy": multi_hop_accuracy,
            "open_domain_accuracy": open_domain_accuracy,
            "single_hop_accuracy": single_hop_accuracy,
            "single_hop_regression_points": single_hop_regression_points,
            "retrieval_p95_ms": retrieval_p95_ms,
            "max_process_rss_bytes": max_process_rss_bytes,
        },
        "checks": checks,
        "gate_passed": all(cast(bool, check["passed"]) for check in checks),
    }
    write_json_exclusive(config.output_path, report)
    return report


def _validate_selection_report(
    report: dict[str, object],
    *,
    source_selection: dict[str, object],
    protocol: dict[str, object],
    run_paths: dict[str, Path],
) -> tuple[str, dict[str, object]]:
    selected_variant, selected_contract = validate_locomo_ablation_sources(
        report,
        run_paths=run_paths,
        selection_id=_str(source_selection, "selection_id"),
        question_set_sha256=_sha256(source_selection, "question_set_sha256"),
        selection_sha256=_sha256(source_selection, "selection_sha256"),
        protocol_sha256=_sha256(source_selection, "protocol_sha256"),
        gates_sha256=_sha256(source_selection, "gates_sha256"),
        protocol=protocol,
        reporter=report_locomo,
    )
    if selected_variant not in _CANONICAL_VARIANTS:
        raise ValueError("LoCoMo promotion selection report has an unknown variant")
    return selected_variant, selected_contract


def _validate_run_manifest(
    manifest: dict[str, object],
    *,
    definition: dict[str, object],
    definition_sha256: str,
    selection_sha256: str,
    selected_variant: str,
    selected_contract: dict[str, object],
    required_questions: int,
    maximum_rss_bytes_exclusive: int,
) -> None:
    if (
        manifest.get("suite") != "locomo"
        or manifest.get("mode") != "full"
        or manifest.get("scored") is not True
    ):
        raise ValueError("LoCoMo promotion requires one scored full run")
    selection = _dict(manifest.get("selection"), field="200-question run selection")
    question_set = _dict(selection.get("question_set"), field="200-question run question set")
    _validate_run_question_set(
        selection,
        question_set=question_set,
        definition=definition,
        definition_sha256=definition_sha256,
        selection_sha256=selection_sha256,
        required_questions=required_questions,
    )
    observed_contract = _run_contract(manifest)
    for field, expected in selected_contract.items():
        if observed_contract.get(field) != expected:
            raise ValueError(f"LoCoMo promotion run changes the selected contract field: {field}")
    if observed_contract.keys() != selected_contract.keys():
        raise ValueError("LoCoMo promotion selected contract has unsupported fields")
    if observed_contract.get("recall_mode") != selected_variant:
        raise ValueError("LoCoMo promotion run changes the selected recall mode")
    protocol = _dict(definition.get("protocol"), field="200-question protocol")
    validate_locomo_manifest_protocol(manifest, protocol=protocol)
    windows = _dict(protocol.get("neighbor_windows"), field="200-question neighbor windows")
    expected_windows = _dict(
        windows.get(selected_variant),
        field="selected variant neighbor windows",
    )
    planner = _dict(
        _dict(manifest.get("retrieval"), field="200-question retrieval").get("planner"),
        field="200-question planner",
    )
    for field in ("neighbor_window", "temporal_neighbor_window"):
        if planner.get(field) != expected_windows.get(field):
            raise ValueError(f"LoCoMo promotion run changes the selected planner field: {field}")
    query_vectors = _dict(manifest.get("query_vectors"), field="200-question query vectors")
    if (
        query_vectors.get("run_selection_sha256") != selection_sha256
        or query_vectors.get("run_question_count") != required_questions
    ):
        raise ValueError("LoCoMo promotion query vectors do not cover the 200-question run")
    worker = _dict(manifest.get("question_worker"), field="200-question worker contract")
    if worker.get("max_rss_bytes") != maximum_rss_bytes_exclusive:
        raise ValueError("LoCoMo promotion worker changes the frozen RSS limit")


def _validate_run_question_set(
    selection: dict[str, object],
    *,
    question_set: dict[str, object],
    definition: dict[str, object],
    definition_sha256: str,
    selection_sha256: str,
    required_questions: int,
) -> None:
    protocol = _dict(definition.get("protocol"), field="200-question protocol")
    category_targets = _dict(definition.get("category_targets"), field="200-question targets")
    expected_question_set = {
        "selection_id": _str(definition, "selection_id"),
        "definition_sha256": definition_sha256,
        "dataset_sha256": _sha256(definition, "dataset_sha256"),
        "algorithm": _str(definition, "algorithm"),
        "seed": _str(definition, "seed"),
        "category_targets": category_targets,
        "question_count": required_questions,
        "selection_sha256": selection_sha256,
        "protocol_sha256": _canonical_sha256(protocol),
    }
    required_fields = {*expected_question_set, "question_ids"}
    if set(question_set) != required_fields:
        raise ValueError("LoCoMo promotion run has an incomplete frozen question-set manifest")
    for field, expected in expected_question_set.items():
        if question_set.get(field) != expected:
            raise ValueError(f"LoCoMo promotion run changes the frozen question-set field: {field}")

    raw_question_ids = question_set.get("question_ids")
    if not isinstance(raw_question_ids, list) or not all(
        isinstance(question_id, str) and question_id for question_id in raw_question_ids
    ):
        raise ValueError("LoCoMo promotion question identities are invalid")
    question_ids = cast(list[str], raw_question_ids)
    if len(question_ids) != required_questions or len(set(question_ids)) != required_questions:
        raise ValueError("LoCoMo promotion question inventory is incomplete or duplicated")
    observed_selection_sha256 = hashlib.sha256(
        json.dumps(sorted(question_ids), ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    if observed_selection_sha256 != selection_sha256:
        raise ValueError("LoCoMo promotion question identities change the frozen selection")

    expected_counts: dict[str, int] = {}
    for raw_category, raw_count in category_targets.items():
        if not raw_category.isdigit() or type(raw_count) is not int or raw_count < 1:
            raise ValueError("LoCoMo promotion category targets are invalid")
        expected_counts[raw_category] = raw_count
    if sum(expected_counts.values()) != required_questions:
        raise ValueError("LoCoMo promotion category targets do not cover the required questions")
    if selection.get("question_counts") != expected_counts:
        raise ValueError("LoCoMo promotion run changes the frozen category counts")
    expected_categories = sorted(int(category) for category in expected_counts)
    if selection.get("categories") != expected_categories:
        raise ValueError("LoCoMo promotion run changes the frozen scored categories")

    raw_conversation_ids = selection.get("conversation_ids")
    raw_by_conversation = selection.get("question_ids_by_conversation")
    if (
        not isinstance(raw_conversation_ids, list)
        or not all(isinstance(value, str) and value for value in raw_conversation_ids)
        or len(raw_conversation_ids) != len(set(raw_conversation_ids))
        or not isinstance(raw_by_conversation, dict)
        or set(raw_by_conversation) != set(raw_conversation_ids)
    ):
        raise ValueError("LoCoMo promotion conversation selection is invalid")
    flattened: list[str] = []
    for conversation_id in cast(list[str], raw_conversation_ids):
        values = raw_by_conversation[conversation_id]
        if not isinstance(values, list) or not all(
            isinstance(question_id, str) and question_id for question_id in values
        ):
            raise ValueError("LoCoMo promotion conversation question inventory is invalid")
        flattened.extend(cast(list[str], values))
    if sorted(flattened) != sorted(question_ids):
        raise ValueError("LoCoMo promotion run question inventory differs from its frozen set")


def _run_contract(manifest: dict[str, object]) -> dict[str, object]:
    retrieval = _dict(manifest.get("retrieval"), field="run retrieval")
    planner = _dict(retrieval.get("planner"), field="run planner")
    return {
        "repository_commit": _str(manifest, "repository_commit"),
        "recall_mode": _str(planner, "mode"),
        "corpus": _artifact_identity(
            manifest.get("corpus"),
            field="run corpus",
            identity_fields=(
                "artifact_id",
                "repository_commit",
                "content_sha256",
                "build_contract_sha256",
                "tree_sha256",
            ),
        ),
        "query_vectors": _artifact_identity(
            manifest.get("query_vectors"),
            field="run query vectors",
            identity_fields=("artifact_id", "content_sha256", "selection_sha256"),
        ),
        "answer_model": dict(_dict(manifest.get("answer_model"), field="run answer model")),
        "judge_model": dict(_dict(manifest.get("judge_model"), field="run judge model")),
    }


def _validate_frozen_baseline(
    baseline: dict[str, object],
    *,
    selection_sha256: str,
    required_questions: int,
) -> dict[str, object]:
    normalized: dict[str, object] = {
        "run_id": _str(baseline, "run_id"),
        "repository_commit": _str(baseline, "repository_commit"),
        "summary_sha256": _sha256(baseline, "summary_sha256"),
        "selection_sha256": _sha256(baseline, "selection_sha256"),
        "scored_question_count": _positive_int(baseline, "scored_question_count"),
        "infrastructure_failed_count": _int(baseline, "infrastructure_failed_count"),
        "single_hop_accuracy": _accuracy(baseline, field="single_hop_accuracy"),
    }
    if normalized["selection_sha256"] != selection_sha256:
        raise ValueError("Frozen LoCoMo baseline targets a different 200-question selection")
    if normalized["scored_question_count"] != required_questions:
        raise ValueError("Frozen LoCoMo baseline has an incomplete question inventory")
    if normalized["infrastructure_failed_count"] != 0:
        raise ValueError("Frozen LoCoMo baseline contains infrastructure failures")
    return normalized


def _validate_gates(gates: dict[str, object]) -> dict[str, int | float]:
    normalized: dict[str, int | float] = {
        "minimum_overall_accuracy": _accuracy(gates, field="minimum_overall_accuracy"),
        "minimum_multi_hop_accuracy": _accuracy(gates, field="minimum_multi_hop_accuracy"),
        "minimum_open_domain_accuracy": _accuracy(
            gates,
            field="minimum_open_domain_accuracy",
        ),
        "maximum_single_hop_regression_points": _nonnegative_number(
            gates,
            "maximum_single_hop_regression_points",
        ),
        "maximum_infrastructure_failures": _nonnegative_int(
            gates,
            "maximum_infrastructure_failures",
        ),
        "maximum_retrieval_p95_ms": _positive_number(gates, "maximum_retrieval_p95_ms"),
        "maximum_process_rss_bytes_exclusive": _positive_int(
            gates,
            "maximum_process_rss_bytes_exclusive",
        ),
    }
    return normalized


def _validate_run_report_identity(
    report: dict[str, object],
    *,
    manifest: dict[str, object],
) -> None:
    if (
        report.get("suite") != "locomo"
        or report.get("run_id") != manifest.get("run_id")
        or report.get("mode") != "full"
        or report.get("scored") is not True
    ):
        raise ValueError("LoCoMo promotion report does not match its run manifest")


def _category_accuracy(report: dict[str, object], *, category: int) -> float:
    by_category = _dict(report.get("by_category"), field="LoCoMo category report")
    category_report = _dict(
        by_category.get(str(category)),
        field=f"LoCoMo category {category} report",
    )
    return _accuracy(category_report, field="accuracy")


def _retrieval_p95(report: dict[str, object]) -> float:
    diagnostics = _dict(report.get("retrieval_diagnostics"), field="retrieval diagnostics")
    latency = _dict(diagnostics.get("latency_ms"), field="retrieval latency")
    return _nonnegative_number(latency, "p95")


def _max_process_rss(report: dict[str, object]) -> int:
    resources = _dict(report.get("worker_resources"), field="worker resources")
    return _positive_int(resources, "max_process_rss_bytes")


def _artifact_identity(
    value: object,
    *,
    field: str,
    identity_fields: tuple[str, ...],
) -> dict[str, object]:
    artifact = _dict(value, field=field)
    return {identity_field: _str(artifact, identity_field) for identity_field in identity_fields}


def _check(
    check_id: str,
    *,
    observed: int | float,
    threshold: int | float,
    comparison: str,
    passed: bool,
) -> dict[str, object]:
    return {
        "id": check_id,
        "observed": observed,
        "threshold": threshold,
        "comparison": comparison,
        "passed": passed,
    }


def _dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _str(value: dict[str, object], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{field} must be a non-empty string")
    return raw


def _sha256(value: dict[str, object], field: str) -> str:
    raw = _str(value, field)
    if _SHA256.fullmatch(raw) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return raw


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _int(value: dict[str, object], field: str) -> int:
    raw = value.get(field)
    if type(raw) is not int:
        raise ValueError(f"{field} must be an integer")
    return raw


def _positive_int(value: dict[str, object], field: str) -> int:
    raw = _int(value, field)
    if raw < 1:
        raise ValueError(f"{field} must be positive")
    return raw


def _nonnegative_int(value: dict[str, object], field: str) -> int:
    raw = _int(value, field)
    if raw < 0:
        raise ValueError(f"{field} must be non-negative")
    return raw


def _number(value: dict[str, object], field: str) -> float:
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{field} must be finite numeric")
    return float(raw)


def _positive_number(value: dict[str, object], field: str) -> float:
    raw = _number(value, field)
    if raw <= 0:
        raise ValueError(f"{field} must be positive")
    return raw


def _nonnegative_number(value: dict[str, object], field: str) -> float:
    raw = _number(value, field)
    if raw < 0:
        raise ValueError(f"{field} must be non-negative")
    return raw


def _accuracy(value: dict[str, object], *, field: str) -> float:
    raw = _number(value, field)
    if not 0 <= raw <= 1:
        raise ValueError(f"{field} must be an accuracy between zero and one")
    return raw
