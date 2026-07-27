"""Pure readers for checked-in historical evidence.

This module deliberately imports no product domain, runtime, recall planner, or
provider code. It recomputes public reports from the redacted aggregate inputs
that are already inside an evidence bundle.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import cast

from codecairn.evaluation.artifacts import file_sha256, read_json

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
LOCOMO_PUBLIC_COMPOSITE_CONTRACT = "public-exact-repair-outcomes-v1"


@dataclass(frozen=True, slots=True)
class HistoricalBundleReports:
    locomo: dict[str, object]
    retrieval: dict[str, object]
    recovery: dict[str, object]
    coding: dict[str, object]


def read_historical_bundle(raw_root: Path) -> HistoricalBundleReports:
    """Recompute all benchmark-v3 reports without importing product code."""
    root = raw_root.resolve()
    return HistoricalBundleReports(
        locomo=report_locomo_composite(root / "locomo"),
        retrieval=report_retrieval(root / "retrieval"),
        recovery=report_recovery(root / "recovery"),
        coding=report_coding(root / "coding"),
    )


def report_locomo_composite(source: Path) -> dict[str, object]:
    composite = _dict(read_json(source / "composite.json"), field="LoCoMo composite")
    manifest = _dict(read_json(source / "manifest.json"), field="LoCoMo public manifest")
    if (
        manifest.get("suite") != "locomo-public-composite"
        or manifest.get("contract") != LOCOMO_PUBLIC_COMPOSITE_CONTRACT
        or composite.get("formal_score") is not True
    ):
        raise ValueError("LoCoMo public composite contract is invalid")

    receipts = _dict(composite.get("sources"), field="LoCoMo source receipts")
    source_reports: dict[str, dict[str, object]] = {}
    source_outcomes: dict[str, dict[str, dict[str, object]]] = {}
    for name in ("base", "repair"):
        source_root = source / "sources" / name
        receipt = _dict(receipts.get(name), field=f"LoCoMo {name} receipt")
        public_manifest = _dict(
            read_json(source_root / "manifest.json"),
            field=f"LoCoMo {name} manifest",
        )
        public_report = _dict(
            read_json(source_root / "report.json"),
            field=f"LoCoMo {name} report",
        )
        if public_manifest.get("source_manifest_sha256") != receipt.get("manifest_sha256"):
            raise ValueError(f"LoCoMo {name} manifest receipt does not match")
        if public_report.get("source_report_sha256") != receipt.get("report_sha256"):
            raise ValueError(f"LoCoMo {name} report receipt does not match")
        outcomes = _load_outcomes(source_root / "questions")
        _validate_source_report(public_report, outcomes=outcomes, field=name)
        source_reports[name] = public_report
        source_outcomes[name] = outcomes

    target_definition = _dict(
        read_json(source / "target-question-set.json"),
        field="LoCoMo target question set",
    )
    repair_definition = _dict(
        read_json(source / "repair-question-set.json"),
        field="LoCoMo repair question set",
    )
    target_receipt = _dict(composite.get("target"), field="LoCoMo target")
    repair_receipt = _dict(composite.get("repair_selection"), field="LoCoMo repair selection")
    if file_sha256(source / "target-question-set.json") != target_receipt.get(
        "question_set_sha256"
    ) or file_sha256(source / "repair-question-set.json") != repair_receipt.get(
        "question_set_sha256"
    ):
        raise ValueError("LoCoMo public question-set receipt does not match")
    if target_definition.get("selection_id") != target_receipt.get("selection_id") or (
        repair_definition.get("selection_id") != repair_receipt.get("selection_id")
    ):
        raise ValueError("LoCoMo public question-set identity does not match")

    base_outcomes = source_outcomes["base"]
    repair_outcomes = source_outcomes["repair"]
    failed_base_ids = {
        question_id
        for question_id, outcome in base_outcomes.items()
        if outcome["outcome"] == "infrastructure_failed"
    }
    repair_ids = _string_set(repair_definition.get("question_ids"), field="repair question IDs")
    if failed_base_ids != repair_ids or set(repair_outcomes) != repair_ids:
        raise ValueError("LoCoMo public repair does not exactly replace base failures")

    final_outcomes = _load_outcomes(source / "checkpoints" / "questions")
    if set(final_outcomes) != set(base_outcomes) or any(
        outcome["outcome"] == "infrastructure_failed" for outcome in final_outcomes.values()
    ):
        raise ValueError("LoCoMo public final outcomes do not cover the target")
    for question_id, outcome in final_outcomes.items():
        expected = (
            repair_outcomes[question_id]
            if question_id in repair_ids
            else base_outcomes[question_id]
        )
        if {key: value for key, value in outcome.items() if key != "source"} != expected:
            raise ValueError("LoCoMo public final outcome changes its source")

    aggregate = _aggregate_outcomes(final_outcomes)
    for field in (
        "scored_question_count",
        "infrastructure_failed_count",
        "correct_count",
        "accuracy",
        "by_category",
    ):
        if aggregate[field] != composite.get(field):
            raise ValueError("LoCoMo public outcomes do not reproduce the composite score")
    usage = _merge_usage(source_reports["base"], source_reports["repair"])
    if usage != composite.get("usage"):
        raise ValueError("LoCoMo public source usage does not reproduce the composite")
    question_count = _int(composite, "question_count")
    return {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": _str(manifest, "run_id"),
        "mode": "full",
        "scored": True,
        "question_artifact_count": question_count,
        "completed_question_count": question_count,
        "scored_question_count": question_count,
        "infrastructure_failed_count": 0,
        "correct_count": aggregate["correct_count"],
        "accuracy": aggregate["accuracy"],
        "by_category": aggregate["by_category"],
        "usage": usage,
        "judge_votes": _int(manifest, "judge_votes"),
        "composite_contract": composite.get("contract"),
        "model_output_scoring_contract": composite.get("model_output_scoring_contract"),
    }


def report_retrieval(run_dir: Path) -> dict[str, object]:
    manifest = _dict(read_json(run_dir / "manifest.json"), field="retrieval manifest")
    records = [
        _dict(read_json(path), field="retrieval query")
        for path in sorted((run_dir / "queries").glob("*.json"))
    ]
    contract = _retrieval_contract(manifest)
    recall_at_1: list[float] = []
    recall_at_5: list[float] = []
    reciprocal_ranks: list[float] = []
    irrelevant_rates: list[float] = []
    latencies: list[float] = []
    isolation_violations = 0
    for record in records:
        _validate_retrieval_record(record, contract=contract)
        relevant = _string_set(record.get("relevant_keys"), field="relevant_keys")
        rankings = record.get("rankings")
        if not isinstance(rankings, list):
            raise ValueError("Query rankings must be an array")
        ranked_keys = [
            item.get("key")
            for item in rankings
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        ]
        recall_at_1.append(len(relevant.intersection(ranked_keys[:1])) / len(relevant))
        recall_at_5.append(len(relevant.intersection(ranked_keys[:5])) / len(relevant))
        first = next(
            (rank for rank, key in enumerate(ranked_keys, start=1) if key in relevant),
            None,
        )
        reciprocal_ranks.append(0.0 if first is None else 1.0 / first)
        top_five = ranked_keys[:5]
        irrelevant_rates.append(
            0.0 if not top_five else sum(key not in relevant for key in top_five) / len(top_five)
        )
        latency = record.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, int | float):
            raise ValueError("Query latency must be numeric")
        latencies.append(float(latency))
        isolation_violations += int(record.get("repository_isolation_violation") is True)
    return {
        "schema_version": 1,
        "suite": "retrieval",
        "run_id": _str(manifest, "run_id"),
        "query_count": len(records),
        "recall_at_1": _mean(recall_at_1),
        "recall_at_5": _mean(recall_at_5),
        "mrr": _mean(reciprocal_ranks),
        "irrelevant_at_5_rate": _mean(irrelevant_rates),
        "p95_latency_ms": _percentile_nearest_rank(latencies, percentile=0.95),
        "repository_isolation_violation_count": isolation_violations,
    }


def report_recovery(run_dir: Path) -> dict[str, object]:
    manifest = _dict(read_json(run_dir / "manifest.json"), field="recovery manifest")
    raw = _dict(read_json(run_dir / "checks.json"), field="recovery checks")
    raw_checks = _dict(raw.get("checks"), field="check results")
    if not raw_checks or not all(isinstance(value, bool) for value in raw_checks.values()):
        raise ValueError("Recovery check results must be non-empty booleans")
    checks = {key: cast(bool, value) for key, value in sorted(raw_checks.items())}
    return {
        "schema_version": 1,
        "suite": "storage-recovery",
        "run_id": _str(manifest, "run_id"),
        "checks": checks,
        "all_passed": all(checks.values()),
        "index_rebuild_consistency": 1.0 if checks["index_rebuild_parity"] else 0.0,
        "details": raw.get("details"),
    }


def report_coding(run_dir: Path) -> dict[str, object]:
    experiment = _dict(read_json(run_dir / "experiment.json"), field="coding experiment")
    results = [
        _dict(read_json(path), field="coding result")
        for path in sorted(run_dir.glob("*/result.json"))
    ]
    arms: dict[str, object] = {}
    for arm in ("memory-off", "memory-on"):
        selected = [result for result in results if result.get("arm") == arm]
        completed = [result for result in selected if result.get("outcome") in {"passed", "failed"}]
        passed = [result for result in completed if result.get("outcome") == "passed"]
        failed = [result for result in completed if result.get("outcome") == "failed"]
        infra = [result for result in selected if result.get("outcome") == "infrastructure_failed"]
        token_values: list[int] = []
        input_values: list[int] = []
        cached_values: list[int] = []
        output_values: list[int] = []
        cost_values: list[float] = []
        for result in completed:
            input_tokens = result.get("input_tokens")
            output_tokens = result.get("output_tokens")
            cached_tokens = result.get("cached_input_tokens")
            cost_usd = result.get("cost_usd")
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                token_values.append(input_tokens + output_tokens)
                input_values.append(input_tokens)
                output_values.append(output_tokens)
            if isinstance(cached_tokens, int):
                cached_values.append(cached_tokens)
            if isinstance(cost_usd, int | float):
                cost_values.append(float(cost_usd))
        arms[arm] = {
            "planned_run_count": len(selected),
            "completed_run_count": len(completed),
            "passed_run_count": len(passed),
            "task_failure_count": len(failed),
            "infrastructure_failure_count": len(infra),
            "pass_rate": None if not completed else len(passed) / len(completed),
            "mean_repeated_file_reads": _numeric_mean(completed, "repeated_file_reads"),
            "mean_repeated_failed_commands": _numeric_mean(completed, "repeated_failed_commands"),
            "mean_steps_to_first_useful_action": _numeric_mean(
                completed, "steps_to_first_useful_action"
            ),
            "total_tokens": sum(token_values),
            "total_input_tokens": sum(input_values),
            "total_cached_input_tokens": sum(cached_values),
            "total_output_tokens": sum(output_values),
            "token_observation_count": len(token_values),
            "total_cost_usd": sum(cost_values) if cost_values else None,
            "cost_observation_count": len(cost_values),
        }
    return {
        "schema_version": 1,
        "suite": "coding-memory-ab",
        "experiment_id": _str(experiment, "experiment_id"),
        "planned_run_count": _int(experiment, "planned_run_count"),
        "completed_run_count": sum(
            result.get("outcome") in {"passed", "failed"} for result in results
        ),
        "infrastructure_failure_count": sum(
            result.get("outcome") == "infrastructure_failed" for result in results
        ),
        "arms": arms,
    }


def _retrieval_contract(
    manifest: dict[str, object],
) -> tuple[dict[str, object], int, str] | None:
    raw = manifest.get("retrieval")
    if not isinstance(raw, dict) or not all(
        isinstance(raw.get(name), dict) for name in ("embedding", "reranker")
    ):
        return None
    top_k = _int(manifest, "top_k")
    canonical = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return raw, top_k, hashlib.sha256(canonical.encode()).hexdigest()


def _validate_retrieval_record(
    record: dict[str, object],
    *,
    contract: tuple[dict[str, object], int, str] | None,
) -> None:
    if contract is None:
        return
    config, top_k, digest = contract
    if record.get("limit") != top_k:
        raise ValueError("Retrieval query limit does not match its manifest")
    if record.get("retrieval_config_sha256") != digest:
        raise ValueError("Retrieval query configuration hash does not match its manifest")
    for provider in ("embedding", "reranker"):
        expected = _dict(config.get(provider), field=f"retrieval {provider}")
        for identity_field in ("model", "source", "revision"):
            if record.get(f"{provider}_{identity_field}") != expected.get(identity_field):
                raise ValueError(f"Retrieval {provider} identity does not match its manifest")


def _validate_source_report(
    report: dict[str, object],
    *,
    outcomes: dict[str, dict[str, object]],
    field: str,
) -> None:
    aggregate = _aggregate_outcomes(outcomes)
    for name in (
        "scored_question_count",
        "infrastructure_failed_count",
        "correct_count",
        "accuracy",
        "by_category",
    ):
        if report.get(name) != aggregate[name]:
            raise ValueError(f"LoCoMo public {field} outcomes do not match its report")


def _aggregate_outcomes(outcomes: dict[str, dict[str, object]]) -> dict[str, object]:
    categories: dict[int, list[bool]] = {}
    infrastructure_failed = 0
    for outcome in outcomes.values():
        observed = outcome.get("outcome")
        if observed == "infrastructure_failed":
            infrastructure_failed += 1
            continue
        if observed not in {"correct", "wrong"}:
            raise ValueError("LoCoMo public outcome is invalid")
        category = _int(outcome, "category")
        categories.setdefault(category, []).append(observed == "correct")
    scored = sum(len(results) for results in categories.values())
    correct = sum(sum(results) for results in categories.values())
    return {
        "scored_question_count": scored,
        "infrastructure_failed_count": infrastructure_failed,
        "correct_count": correct,
        "accuracy": round(correct / scored, 6) if scored else None,
        "by_category": {
            str(category): {
                "name": CATEGORY_NAMES.get(category, "unknown"),
                "correct": sum(results),
                "count": len(results),
                "accuracy": round(sum(results) / len(results), 6),
            }
            for category, results in sorted(categories.items())
        },
    }


def _load_outcomes(root: Path) -> dict[str, dict[str, object]]:
    paths = sorted(root.glob("*/*.json"))
    if not paths:
        raise ValueError(f"LoCoMo public outcomes are missing: {root}")
    outcomes: dict[str, dict[str, object]] = {}
    for path in paths:
        outcome = _dict(read_json(path), field="LoCoMo public outcome")
        question_id = _str(outcome, "question_id")
        if question_id in outcomes:
            raise ValueError("LoCoMo public outcome inventory contains duplicates")
        outcomes[question_id] = outcome
    return outcomes


def _merge_usage(
    base_report: dict[str, object],
    repair_report: dict[str, object],
) -> dict[str, object]:
    base = _dict(base_report.get("usage"), field="base usage")
    repair = _dict(repair_report.get("usage"), field="repair usage")
    merged: dict[str, object] = {}
    for field in sorted(set(base) | set(repair)):
        values = (base.get(field), repair.get(field))
        numeric = [
            value
            for value in values
            if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        if any(value is not None and not isinstance(value, int | float) for value in values):
            raise ValueError(f"LoCoMo usage field is not numeric: {field}")
        if not numeric:
            merged[field] = None
        elif any(isinstance(value, float) for value in numeric):
            merged[field] = round(math.fsum(float(value) for value in numeric), 8)
        else:
            merged[field] = sum(cast(list[int], numeric))
    return merged


def _numeric_mean(records: list[dict[str, object]], field: str) -> float | None:
    values = [
        float(value)
        for record in records
        for value in (record.get(field),)
        if isinstance(value, int | float)
    ]
    return None if not values else mean(values)


def _mean(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 6)


def _percentile_nearest_rank(values: list[float], *, percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _str(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _int(record: dict[str, object], field: str) -> int:
    value = record.get(field)
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _string_set(value: object, *, field: str) -> set[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(cast(list[str], value)))
    ):
        raise ValueError(f"{field} must be a non-empty unique string array")
    return set(cast(list[str], value))
