from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo import _FROZEN_PLANNER_PROTOCOL_FIELDS, report_locomo

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CANONICAL_VARIANTS = ("episode-only", "hierarchy-no-neighbors", "hierarchy")


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
    protocol = _dict(definition.get("protocol"), field="protocol")
    raw_neighbor_windows = protocol.get("neighbor_windows")
    neighbor_windows = (
        None
        if raw_neighbor_windows is None
        else _dict(raw_neighbor_windows, field="neighbor windows")
    )
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
    manifest_receipts: dict[str, dict[str, object]] = {}
    for variant, run_path in run_paths.items():
        manifest_path = run_path / "manifest.json"
        manifest = _dict(read_json(manifest_path), field=f"{variant} manifest")
        _validate_run_manifest(
            manifest,
            variant=variant,
            expected_mode=expected_variants[variant],
            expected_neighbor_windows=(
                None
                if neighbor_windows is None
                else _dict(neighbor_windows.get(variant), field=f"{variant} neighbor windows")
            ),
            definition_sha256=definition_sha256,
            selection_sha256=expected_selection_sha256,
        )
        manifests[variant] = manifest
        manifest_receipts[variant] = {
            "run_id": _str(manifest, "run_id"),
            "manifest_sha256": file_sha256(manifest_path),
        }
        reports[variant] = report_locomo(run_path)
    _validate_constant_protocol(manifests)
    validate_locomo_manifest_protocol(
        manifests["hierarchy"],
        protocol=protocol,
    )

    gates = _dict(definition.get("gates"), field="gates")
    outcome = _derive_ablation_outcome(reports, gates=gates)
    selected_variant = _str(outcome, "selected_variant")
    run_contracts = {
        variant: _selected_run_contract(manifest) for variant, manifest in manifests.items()
    }
    report = {
        "schema_version": 1,
        "suite": "locomo-ablation",
        "selection_id": _str(definition, "selection_id"),
        "question_set_sha256": definition_sha256,
        "selection_sha256": expected_selection_sha256,
        "question_set_protocol_sha256": _canonical_sha256(protocol),
        "question_set_gates": dict(gates),
        "question_set_gates_sha256": _canonical_sha256(gates),
        "repository_commit": _str(manifests["hierarchy"], "repository_commit"),
        "variants": reports,
        "run_manifests": manifest_receipts,
        "run_contracts": run_contracts,
        **outcome,
        "selected_variant": selected_variant,
        "selected_run_id": _str(manifests[selected_variant], "run_id"),
        "selected_run_contract": run_contracts[selected_variant],
    }
    write_json_exclusive(config.output_path, report)
    return report


def validate_locomo_ablation_report(
    report: dict[str, object],
    *,
    selection_id: str,
    question_set_sha256: str,
    selection_sha256: str,
    protocol_sha256: str,
    gates_sha256: str,
) -> tuple[str, dict[str, object]]:
    """Recompute a 40-question selection artifact before promotion."""
    if report.get("schema_version") != 1 or report.get("suite") != "locomo-ablation":
        raise ValueError("LoCoMo selection report schema or suite is invalid")
    expected_identity = {
        "selection_id": selection_id,
        "question_set_sha256": question_set_sha256,
        "selection_sha256": selection_sha256,
        "question_set_protocol_sha256": protocol_sha256,
        "question_set_gates_sha256": gates_sha256,
    }
    for field, expected in expected_identity.items():
        if report.get(field) != expected:
            raise ValueError(f"LoCoMo selection report changes its frozen field: {field}")

    gates = _dict(report.get("question_set_gates"), field="selection report gates")
    if _canonical_sha256(gates) != gates_sha256:
        raise ValueError("LoCoMo selection report gates do not match their frozen digest")
    variants = _dict(report.get("variants"), field="selection report variants")
    manifests = _dict(report.get("run_manifests"), field="selection report manifests")
    contracts = _dict(report.get("run_contracts"), field="selection report run contracts")
    expected_variants = set(_CANONICAL_VARIANTS)
    for field, values in (
        ("variants", variants),
        ("run manifests", manifests),
        ("run contracts", contracts),
    ):
        if set(values) != expected_variants:
            raise ValueError(
                f"LoCoMo selection report {field} must contain three canonical variants"
            )

    repository_commit = _str(report, "repository_commit")
    reference_contract: dict[str, object] | None = None
    for variant in _CANONICAL_VARIANTS:
        variant_report = _dict(variants[variant], field=f"{variant} selection run report")
        if (
            variant_report.get("suite") != "locomo"
            or variant_report.get("mode") != "full"
            or variant_report.get("scored") is not True
        ):
            raise ValueError(f"{variant} selection run report is not one scored full LoCoMo run")
        manifest_receipt = _dict(manifests[variant], field=f"{variant} manifest receipt")
        if set(manifest_receipt) != {"run_id", "manifest_sha256"}:
            raise ValueError(f"{variant} manifest receipt has unsupported fields")
        run_id = _str(manifest_receipt, "run_id")
        _sha256(manifest_receipt, "manifest_sha256")
        if variant_report.get("run_id") != run_id:
            raise ValueError(f"{variant} report does not match its manifest receipt")
        contract = _validate_run_contract(
            _dict(contracts[variant], field=f"{variant} run contract"),
            variant=variant,
            repository_commit=repository_commit,
        )
        if reference_contract is None:
            reference_contract = contract
        else:
            for field in (
                "repository_commit",
                "corpus",
                "query_vectors",
                "answer_model",
                "judge_model",
            ):
                if contract[field] != reference_contract[field]:
                    raise ValueError(f"{variant} changes the selected protocol contract: {field}")

    expected_outcome = _derive_ablation_outcome(
        {
            variant: _dict(variants[variant], field=f"{variant} report")
            for variant in _CANONICAL_VARIANTS
        },
        gates=gates,
    )
    for field, expected_value in expected_outcome.items():
        if report.get(field) != expected_value:
            raise ValueError(f"LoCoMo selection report derived field is invalid: {field}")
    selected_variant = _str(expected_outcome, "selected_variant")
    selected_manifest = _dict(manifests[selected_variant], field="selected manifest receipt")
    if report.get("selected_run_id") != selected_manifest.get("run_id"):
        raise ValueError("LoCoMo selection report selected run does not match its manifest")
    selected_contract = _dict(contracts[selected_variant], field="selected run contract")
    if report.get("selected_run_contract") != selected_contract:
        raise ValueError("LoCoMo selection report selected contract is not the chosen run contract")
    if report.get("gate_passed") is not True:
        raise ValueError("LoCoMo promotion requires a successful 40-question selection report")
    return selected_variant, dict(selected_contract)


def validate_locomo_ablation_sources(
    report: dict[str, object],
    *,
    run_paths: dict[str, Path],
    selection_id: str,
    question_set_sha256: str,
    selection_sha256: str,
    protocol_sha256: str,
    gates_sha256: str,
    protocol: dict[str, object],
    reporter: Callable[[Path], dict[str, object]] = report_locomo,
) -> tuple[str, dict[str, object]]:
    """Bind a selection report to the three immutable run directories it summarizes."""
    selected_variant, selected_contract = validate_locomo_ablation_report(
        report,
        selection_id=selection_id,
        question_set_sha256=question_set_sha256,
        selection_sha256=selection_sha256,
        protocol_sha256=protocol_sha256,
        gates_sha256=gates_sha256,
    )
    if set(run_paths) != set(_CANONICAL_VARIANTS):
        raise ValueError("LoCoMo promotion requires three canonical 40-question run directories")
    if _canonical_sha256(protocol) != protocol_sha256:
        raise ValueError("LoCoMo promotion source protocol does not match its frozen digest")
    windows = _dict(protocol.get("neighbor_windows"), field="source neighbor windows")
    reported_manifests = _dict(report.get("run_manifests"), field="selection report manifests")
    reported_contracts = _dict(report.get("run_contracts"), field="selection report contracts")
    reported_variants = _dict(report.get("variants"), field="selection report variants")
    actual_manifests: dict[str, dict[str, object]] = {}
    for variant in _CANONICAL_VARIANTS:
        run_path = run_paths[variant]
        manifest_path = run_path / "manifest.json"
        manifest = _dict(read_json(manifest_path), field=f"{variant} source manifest")
        expected_windows = _dict(windows.get(variant), field=f"{variant} source windows")
        _validate_run_manifest(
            manifest,
            variant=variant,
            expected_mode=variant,
            expected_neighbor_windows=expected_windows,
            definition_sha256=question_set_sha256,
            selection_sha256=selection_sha256,
        )
        receipt = _dict(reported_manifests[variant], field=f"{variant} manifest receipt")
        if receipt.get("run_id") != manifest.get("run_id") or receipt.get(
            "manifest_sha256"
        ) != file_sha256(manifest_path):
            raise ValueError(f"{variant} manifest receipt does not match the source run")
        actual_report = reporter(run_path)
        if actual_report != reported_variants[variant]:
            raise ValueError(f"{variant} report does not match the source run checkpoints")
        actual_contract = _selected_run_contract(manifest)
        if actual_contract != reported_contracts[variant]:
            raise ValueError(f"{variant} contract does not match the source run manifest")
        actual_manifests[variant] = manifest
    _validate_constant_protocol(actual_manifests)
    validate_locomo_manifest_protocol(actual_manifests["hierarchy"], protocol=protocol)
    selected_manifest = actual_manifests[selected_variant]
    if report.get("selected_run_id") != selected_manifest.get("run_id"):
        raise ValueError("LoCoMo selection report does not select its bound source run")
    if selected_contract != _selected_run_contract(selected_manifest):
        raise ValueError("LoCoMo selection report contract differs from its bound source run")
    return selected_variant, selected_contract


def _derive_ablation_outcome(
    reports: dict[str, dict[str, object]],
    *,
    gates: dict[str, object],
) -> dict[str, object]:
    if set(reports) != set(_CANONICAL_VARIANTS):
        raise ValueError("LoCoMo ablation requires three canonical run reports")
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
    for variant in _CANONICAL_VARIANTS:
        variant_report = reports[variant]
        scored = _int(variant_report, "scored_question_count")
        failures = _int(variant_report, "infrastructure_failed_count")
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
    return {
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


def _validate_run_contract(
    contract: dict[str, object],
    *,
    variant: str,
    repository_commit: str,
) -> dict[str, object]:
    expected_fields = {
        "repository_commit",
        "recall_mode",
        "corpus",
        "query_vectors",
        "answer_model",
        "judge_model",
    }
    if set(contract) != expected_fields:
        raise ValueError(f"{variant} run contract has unsupported fields")
    if contract.get("repository_commit") != repository_commit:
        raise ValueError(f"{variant} run contract changes the repository commit")
    if contract.get("recall_mode") != variant:
        raise ValueError(f"{variant} run contract changes the recall mode")
    for field in ("corpus", "query_vectors", "answer_model", "judge_model"):
        _dict(contract.get(field), field=f"{variant} {field}")
    return dict(contract)


def _validate_run_manifest(
    manifest: dict[str, object],
    *,
    variant: str,
    expected_mode: str,
    expected_neighbor_windows: dict[str, object] | None,
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
    if expected_neighbor_windows is not None:
        for field in ("neighbor_window", "temporal_neighbor_window"):
            if planner.get(field) != expected_neighbor_windows.get(field):
                raise ValueError(f"{variant} changes the frozen planner field: {field}")


def _validate_constant_protocol(manifests: dict[str, dict[str, object]]) -> None:
    reference = manifests["hierarchy"]
    fields = (
        "repository_commit",
        "dataset",
        "selection",
        "answer_model",
        "answer_evidence_contract",
        "judge_model",
        "judge_contract",
        "judge_votes",
        "judge_response_max_attempts",
        "judge_response_max_chars",
        "seed",
        "max_workers",
        "ingest_max_workers",
        "retrieval_max_workers",
        "retrieval_thread_count",
        "execution_phase_contract",
        "question_worker",
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
        for field in _FROZEN_PLANNER_PROTOCOL_FIELDS:
            if planner.get(field) != reference_planner.get(field):
                raise ValueError(f"{variant} changes the frozen planner field: {field}")


def validate_locomo_manifest_protocol(
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
    raw_worker = manifest.get("question_worker")
    worker = {} if raw_worker is None else _dict(raw_worker, field="question worker")
    observed = {
        "answer_model": answer.get("model"),
        "answer_evidence_contract": manifest.get("answer_evidence_contract"),
        "judge_model": judge.get("model"),
        "judge_contract": manifest.get("judge_contract"),
        "judge_votes": manifest.get("judge_votes"),
        "judge_response_max_attempts": manifest.get("judge_response_max_attempts"),
        "judge_response_max_chars": manifest.get("judge_response_max_chars"),
        "seed": manifest.get("seed"),
        "top_k": retrieval.get("top_k"),
        "inference_threads": retrieval.get("inference_threads"),
        "tokenizer_parallelism": retrieval.get("tokenizer_parallelism"),
        "tokenizer_threads": retrieval.get("tokenizer_threads"),
        "max_workers": manifest.get("max_workers"),
        "ingest_max_workers": manifest.get("ingest_max_workers"),
        "retrieval_max_workers": manifest.get("retrieval_max_workers"),
        "retrieval_thread_count": manifest.get("retrieval_thread_count"),
        "execution_phase_contract": manifest.get("execution_phase_contract"),
        "worker_contract": worker.get("name"),
        "worker_max_rss_bytes": worker.get("max_rss_bytes"),
        "worker_stall_timeout_seconds": worker.get("stall_timeout_seconds"),
        "worker_poll_interval_seconds": worker.get("poll_interval_seconds"),
        "worker_rss_poll_interval_seconds": worker.get("rss_poll_interval_seconds"),
        "worker_progress_signal": worker.get("progress_signal"),
        "worker_publish_policy": worker.get("publish_policy"),
        "embedding_adapter": embedding.get("adapter"),
        "embedding_model": embedding.get("model"),
        "embedding_dimension": embedding.get("dimension"),
        "reranker_model": reranker.get("model"),
        "reranker_batch_size": reranker.get("batch_size"),
        **{field: planner.get(field) for field in _FROZEN_PLANNER_PROTOCOL_FIELDS},
    }
    for field, value in observed.items():
        if (
            field
            in {
                "judge_response_max_attempts",
                "judge_response_max_chars",
                "seed",
            }
            and field not in protocol
        ):
            continue
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


def _selected_run_contract(manifest: dict[str, object]) -> dict[str, object]:
    retrieval = _dict(manifest.get("retrieval"), field="selected retrieval")
    planner = _dict(retrieval.get("planner"), field="selected planner")
    return {
        "repository_commit": _str(manifest, "repository_commit"),
        "recall_mode": _str(planner, "mode"),
        "corpus": _artifact_identity(
            manifest.get("corpus"),
            field="selected corpus",
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
            field="selected query vectors",
            identity_fields=("artifact_id", "content_sha256", "selection_sha256"),
        ),
        "answer_model": dict(_dict(manifest.get("answer_model"), field="selected answer model")),
        "judge_model": dict(_dict(manifest.get("judge_model"), field="selected judge model")),
    }


def _artifact_identity(
    value: object,
    *,
    field: str,
    identity_fields: tuple[str, ...],
) -> dict[str, object] | None:
    if value is None:
        return None
    artifact = _dict(value, field=field)
    return {identity_field: _str(artifact, identity_field) for identity_field in identity_fields}


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


def _sha256(value: dict[str, object], field: str) -> str:
    raw = _str(value, field)
    if _SHA256.fullmatch(raw) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return raw


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _number(value: dict[str, object], field: str) -> float:
    raw = value.get(field)
    if not isinstance(raw, int | float):
        raise ValueError(f"{field} must be numeric")
    return float(raw)
