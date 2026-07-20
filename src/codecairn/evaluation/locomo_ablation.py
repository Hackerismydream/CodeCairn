from __future__ import annotations

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
    minimum_episode_delta = _number(gates, "hierarchy_vs_episode_minimum_accuracy_delta_points")
    minimum_neighbor_delta = _number(
        gates, "hierarchy_vs_no_neighbors_minimum_accuracy_delta_points"
    )
    maximum_p95 = _number(gates, "hierarchy_maximum_retrieval_p95_ms")
    episode_accuracy = _accuracy(reports["episode-only"])
    no_neighbor_accuracy = _accuracy(reports["hierarchy-no-neighbors"])
    hierarchy_accuracy = _accuracy(reports["hierarchy"])
    episode_delta = round((hierarchy_accuracy - episode_accuracy) * 100, 3)
    neighbor_delta = round((hierarchy_accuracy - no_neighbor_accuracy) * 100, 3)
    hierarchy_p95 = _retrieval_p95(reports["hierarchy"])

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
    checks.extend(
        (
            _check(
                "hierarchy.accuracy_delta_vs_episode_points",
                observed=episode_delta,
                threshold=minimum_episode_delta,
                passed=episode_delta >= minimum_episode_delta,
            ),
            _check(
                "hierarchy.accuracy_delta_vs_no_neighbors_points",
                observed=neighbor_delta,
                threshold=minimum_neighbor_delta,
                passed=neighbor_delta >= minimum_neighbor_delta,
            ),
            _check(
                "hierarchy.retrieval_p95_ms",
                observed=hierarchy_p95,
                threshold=maximum_p95,
                passed=hierarchy_p95 <= maximum_p95,
            ),
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
            "hierarchy_vs_episode_only": episode_delta,
            "hierarchy_vs_hierarchy_no_neighbors": neighbor_delta,
        },
        "checks": checks,
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
    )
    for variant, manifest in manifests.items():
        for field in fields:
            if manifest.get(field) != reference.get(field):
                raise ValueError(f"{variant} changes the frozen LoCoMo protocol field: {field}")
        retrieval = _dict(manifest.get("retrieval"), field=f"{variant} retrieval")
        reference_retrieval = _dict(reference.get("retrieval"), field="hierarchy retrieval")
        for field in ("embedding", "reranker", "inference_threads", "top_k"):
            if retrieval.get(field) != reference_retrieval.get(field):
                raise ValueError(f"{variant} changes the frozen retrieval field: {field}")


def _validate_definition_protocol(
    manifest: dict[str, object],
    *,
    protocol: dict[str, object],
) -> None:
    answer = _dict(manifest.get("answer_model"), field="answer model")
    judge = _dict(manifest.get("judge_model"), field="judge model")
    retrieval = _dict(manifest.get("retrieval"), field="retrieval")
    observed = {
        "answer_model": answer.get("model"),
        "judge_model": judge.get("model"),
        "judge_votes": manifest.get("judge_votes"),
        "top_k": retrieval.get("top_k"),
        "inference_threads": retrieval.get("inference_threads"),
        "max_workers": manifest.get("max_workers"),
        "ingest_max_workers": manifest.get("ingest_max_workers"),
    }
    for field, value in observed.items():
        if value != protocol.get(field):
            raise ValueError(f"LoCoMo run changes the diagnostic protocol field: {field}")


def _accuracy(report: dict[str, object]) -> float:
    value = report.get("accuracy")
    if not isinstance(value, int | float):
        raise ValueError("LoCoMo ablation run has no scored accuracy")
    return float(value)


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
