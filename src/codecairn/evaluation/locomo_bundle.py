from __future__ import annotations

import math
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo import CATEGORY_NAMES, report_locomo
from codecairn.evaluation.locomo_repair import (
    LoCoMoRepairConfig,
    build_locomo_repair_report,
)

LOCOMO_PUBLIC_COMPOSITE_CONTRACT = "public-exact-repair-outcomes-v1"

_PUBLIC_MANIFEST_FIELDS = (
    "schema_version",
    "suite",
    "run_id",
    "mode",
    "scored",
    "repository_commit",
    "dataset",
    "selection",
    "retrieval",
    "answer_model",
    "judge_model",
    "answer_evidence_contract",
    "answer_retry_contract",
    "answer_response_max_attempts",
    "judge_contract",
    "judge_votes",
    "judge_response_max_attempts",
    "judge_response_max_chars",
    "model_attempt_journal_contract",
    "checkpoint_policy",
    "seed",
    "max_workers",
    "ingest_max_workers",
    "retrieval_max_workers",
    "retrieval_thread_count",
    "execution_phase_contract",
    "question_worker",
)
_PUBLIC_REPORT_FIELDS = (
    "schema_version",
    "suite",
    "run_id",
    "mode",
    "scored",
    "question_artifact_count",
    "completed_question_count",
    "scored_question_count",
    "infrastructure_failed_count",
    "correct_count",
    "accuracy",
    "by_category",
    "usage",
    "judge_votes",
    "model_output_scoring_contract",
    "answer_attempts",
    "model_attempt_journal",
    "answer_evidence",
    "retrieval_diagnostics",
)


def copy_locomo_composite_evidence(
    source: Path,
    target: Path,
    *,
    repository_root: Path,
) -> None:
    """Publish one exact-repair score as privacy-safe, offline-verifiable outcomes."""
    composite = _dict(read_json(source), field="LoCoMo composite")
    if composite.get("formal_score") is not True:
        raise ValueError("LoCoMo composite is not a formal score")
    sources = _dict(composite.get("sources"), field="LoCoMo composite sources")
    base_receipt = _dict(sources.get("base"), field="LoCoMo base receipt")
    repair_receipt = _dict(sources.get("repair"), field="LoCoMo repair receipt")
    base_run = source.parent / _required_str(base_receipt, "run_id")
    repair_run = source.parent / _required_str(repair_receipt, "run_id")
    if not base_run.is_dir() or not repair_run.is_dir():
        raise ValueError("LoCoMo composite source run is missing")

    target_definition = _find_question_set(
        repository_root,
        selection_id=_required_str(
            _dict(composite.get("target"), field="LoCoMo target"),
            "selection_id",
        ),
        expected_sha256=_required_str(
            _dict(composite.get("target"), field="LoCoMo target"),
            "question_set_sha256",
        ),
    )
    repair_definition = _find_question_set(
        repository_root,
        selection_id=_required_str(
            _dict(composite.get("repair_selection"), field="LoCoMo repair selection"),
            "selection_id",
        ),
        expected_sha256=_required_str(
            _dict(composite.get("repair_selection"), field="LoCoMo repair selection"),
            "question_set_sha256",
        ),
    )

    target.mkdir(parents=True, exist_ok=False)
    generated = build_locomo_repair_report(
        LoCoMoRepairConfig(
            target_question_set_path=target_definition,
            repair_question_set_path=repair_definition,
            base_run=base_run,
            repair_run=repair_run,
            output_path=target / "composite.json",
        )
    )
    if generated != composite:
        raise ValueError("LoCoMo composite does not match its immutable source runs")

    shutil.copyfile(target_definition, target / "target-question-set.json")
    shutil.copyfile(repair_definition, target / "repair-question-set.json")
    base_manifest = _dict(read_json(base_run / "manifest.json"), field="LoCoMo base manifest")
    repair_manifest = _dict(
        read_json(repair_run / "manifest.json"),
        field="LoCoMo repair manifest",
    )
    base_report = report_locomo(base_run)
    repair_report = report_locomo(repair_run)
    _write_public_source(
        target / "sources" / "base",
        run_dir=base_run,
        manifest=base_manifest,
        report=base_report,
        receipt=base_receipt,
    )
    _write_public_source(
        target / "sources" / "repair",
        run_dir=repair_run,
        manifest=repair_manifest,
        report=repair_report,
        receipt=repair_receipt,
    )
    _copy_public_ingests(
        base_run,
        target / "checkpoints" / "ingest",
        manifest=base_manifest,
    )

    base_outcomes = _load_outcomes(target / "sources" / "base" / "questions")
    repair_outcomes = _load_outcomes(target / "sources" / "repair" / "questions")
    failed_base_ids = {
        question_id
        for question_id, outcome in base_outcomes.items()
        if outcome["outcome"] == "infrastructure_failed"
    }
    if set(repair_outcomes) != failed_base_ids or any(
        outcome["outcome"] == "infrastructure_failed" for outcome in repair_outcomes.values()
    ):
        raise ValueError("LoCoMo repair outcomes do not exactly replace base failures")
    final_outcomes = {
        question_id: outcome
        for question_id, outcome in base_outcomes.items()
        if question_id not in failed_base_ids
    }
    final_outcomes.update(repair_outcomes)
    for question_id, outcome in sorted(final_outcomes.items()):
        sample_id = _required_str(outcome, "sample_id")
        public = dict(outcome)
        public["source"] = "repair" if question_id in repair_outcomes else "base"
        write_json_exclusive(
            target / "checkpoints" / "questions" / sample_id / f"{question_id}.json",
            public,
        )

    release_manifest = {
        "schema_version": 1,
        "suite": "locomo-public-composite",
        "contract": LOCOMO_PUBLIC_COMPOSITE_CONTRACT,
        "run_id": f"{_required_str(_dict(composite['target'], field='target'), 'selection_id')}"
        "-composite",
        "repository_commit": None,
        "repository_commits": {
            "base": base_manifest.get("repository_commit"),
            "repair": repair_manifest.get("repository_commit"),
        },
        "dataset": base_manifest.get("dataset"),
        "selection": base_manifest.get("selection"),
        "retrieval": base_manifest.get("retrieval"),
        "answer_model": base_manifest.get("answer_model"),
        "judge_model": base_manifest.get("judge_model"),
        "judge_votes": base_manifest.get("judge_votes"),
        "source_composite_sha256": file_sha256(source),
        "source_runs": composite.get("sources"),
    }
    write_json_exclusive(target / "manifest.json", release_manifest)
    write_json_exclusive(target / "summary.json", report_locomo_composite_evidence(target))


def report_locomo_composite_evidence(source: Path) -> dict[str, object]:
    """Recompute one public exact-repair score without private traces or providers."""
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
        root = source / "sources" / name
        receipt = _dict(receipts.get(name), field=f"LoCoMo {name} receipt")
        public_manifest = _dict(
            read_json(root / "manifest.json"),
            field=f"LoCoMo {name} manifest",
        )
        public_report = _dict(
            read_json(root / "report.json"),
            field=f"LoCoMo {name} report",
        )
        if public_manifest.get("source_manifest_sha256") != receipt.get("manifest_sha256"):
            raise ValueError(f"LoCoMo {name} manifest receipt does not match")
        if public_report.get("source_report_sha256") != receipt.get("report_sha256"):
            raise ValueError(f"LoCoMo {name} report receipt does not match")
        outcomes = _load_outcomes(root / "questions")
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
    repair_selection_receipt = _dict(
        composite.get("repair_selection"),
        field="LoCoMo repair selection",
    )
    if file_sha256(source / "target-question-set.json") != target_receipt.get(
        "question_set_sha256"
    ) or file_sha256(source / "repair-question-set.json") != repair_selection_receipt.get(
        "question_set_sha256"
    ):
        raise ValueError("LoCoMo public question-set receipt does not match")
    if target_definition.get("selection_id") != target_receipt.get("selection_id") or (
        repair_definition.get("selection_id") != repair_selection_receipt.get("selection_id")
    ):
        raise ValueError("LoCoMo public question-set identity does not match")

    base_outcomes = source_outcomes["base"]
    repair_outcomes = source_outcomes["repair"]
    failed_base_ids = {
        question_id
        for question_id, outcome in base_outcomes.items()
        if outcome["outcome"] == "infrastructure_failed"
    }
    repair_ids = _string_set(
        repair_definition.get("question_ids"),
        field="repair question IDs",
    )
    if failed_base_ids != repair_ids or set(repair_outcomes) != repair_ids:
        raise ValueError("LoCoMo public repair does not exactly replace base failures")

    final_outcomes = _load_outcomes(source / "checkpoints" / "questions")
    target_ids = set(base_outcomes)
    if set(final_outcomes) != target_ids or any(
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
    if (
        aggregate["scored_question_count"] != composite.get("scored_question_count")
        or aggregate["infrastructure_failed_count"] != composite.get("infrastructure_failed_count")
        or aggregate["correct_count"] != composite.get("correct_count")
        or aggregate["accuracy"] != composite.get("accuracy")
        or aggregate["by_category"] != composite.get("by_category")
    ):
        raise ValueError("LoCoMo public outcomes do not reproduce the composite score")
    usage = _merge_usage(source_reports["base"], source_reports["repair"])
    if usage != composite.get("usage"):
        raise ValueError("LoCoMo public source usage does not reproduce the composite")
    question_count = _required_int(composite, "question_count")
    return {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": _required_str(manifest, "run_id"),
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
        "judge_votes": _required_int(manifest, "judge_votes"),
        "composite_contract": composite.get("contract"),
        "model_output_scoring_contract": composite.get("model_output_scoring_contract"),
    }


def _write_public_source(
    target: Path,
    *,
    run_dir: Path,
    manifest: dict[str, object],
    report: dict[str, object],
    receipt: dict[str, object],
) -> None:
    public_manifest = {
        field: manifest.get(field) for field in _PUBLIC_MANIFEST_FIELDS if field in manifest
    }
    public_manifest["corpus_content_sha256"] = _dict(
        manifest.get("corpus"),
        field="LoCoMo corpus",
    ).get("content_sha256")
    public_manifest["query_vectors_content_sha256"] = _dict(
        manifest.get("query_vectors"),
        field="LoCoMo query vectors",
    ).get("content_sha256")
    public_manifest["source_manifest_sha256"] = receipt.get("manifest_sha256")
    public_report = {field: report.get(field) for field in _PUBLIC_REPORT_FIELDS if field in report}
    public_report["source_report_sha256"] = receipt.get("report_sha256")
    write_json_exclusive(target / "manifest.json", public_manifest)
    write_json_exclusive(target / "report.json", public_report)
    for path in sorted((run_dir / "checkpoints" / "questions").glob("*/*.json")):
        record = _dict(read_json(path), field="LoCoMo question checkpoint")
        outcome = _public_outcome(record, source_artifact_sha256=file_sha256(path))
        write_json_exclusive(
            target
            / "questions"
            / _required_str(outcome, "sample_id")
            / f"{_required_str(outcome, 'question_id')}.json",
            outcome,
        )


def _public_outcome(
    record: dict[str, object],
    *,
    source_artifact_sha256: str,
) -> dict[str, object]:
    status = record.get("status")
    votes = record.get("judge_votes")
    outcome = "infrastructure_failed"
    if status == "completed" and isinstance(votes, list) and votes:
        labels = [
            vote.get("label")
            for vote in votes
            if isinstance(vote, dict) and vote.get("label") in {"correct", "wrong"}
        ]
        if len(labels) == len(votes):
            outcome = "correct" if labels.count("correct") > len(labels) / 2 else "wrong"
    elif status == "infrastructure_failed" and record.get("phase") == "answer":
        receipt = record.get("answer_attempt_receipt")
        if isinstance(receipt, dict) and receipt.get("status") == "contract_exhausted":
            attempts = receipt.get("attempts")
            usage = receipt.get("usage")
            if (
                isinstance(attempts, list)
                and attempts
                and all(
                    isinstance(attempt, dict) and attempt.get("status") == "contract_rejected"
                    for attempt in attempts
                )
                and isinstance(usage, dict)
                and usage.get("response_count") == len(attempts)
                and usage.get("call_count") == len(attempts)
            ):
                outcome = "wrong"
    category = _required_int(record, "category")
    return {
        "schema_version": 1,
        "sample_id": _required_str(record, "sample_id"),
        "question_id": _required_str(record, "question_id"),
        "category": category,
        "category_name": CATEGORY_NAMES.get(category, "unknown"),
        "outcome": outcome,
        "source_artifact_sha256": source_artifact_sha256,
    }


def _copy_public_ingests(
    source: Path,
    target: Path,
    *,
    manifest: dict[str, object],
) -> None:
    ingest_root = source / "checkpoints" / "ingest"
    paths = sorted(ingest_root.glob("*.json"))
    if not paths:
        corpus = _dict(manifest.get("corpus"), field="LoCoMo corpus")
        content_sha256 = _required_str(corpus, "content_sha256")
        ingest_root = (
            source.parent / "corpora" / f"corpus-{content_sha256[:16]}" / "checkpoints" / "ingest"
        )
        paths = sorted(ingest_root.glob("*.json"))
    if not paths:
        raise ValueError("LoCoMo composite base ingest checkpoints are missing")
    for path in paths:
        raw = _dict(read_json(path), field="LoCoMo ingest checkpoint")
        public = {
            key: raw[key]
            for key in (
                "sample_id",
                "session_count",
                "turn_count",
                "accepted_memory_count",
                "rejected_memory_count",
            )
        }
        public["source_artifact_sha256"] = file_sha256(path)
        write_json_exclusive(target / path.name, public)


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


def _aggregate_outcomes(
    outcomes: dict[str, dict[str, object]],
) -> dict[str, object]:
    categories: dict[int, list[bool]] = {}
    infrastructure_failed = 0
    for outcome in outcomes.values():
        observed = outcome.get("outcome")
        if observed == "infrastructure_failed":
            infrastructure_failed += 1
            continue
        if observed not in {"correct", "wrong"}:
            raise ValueError("LoCoMo public outcome is invalid")
        category = _required_int(outcome, "category")
        categories.setdefault(category, []).append(observed == "correct")
    scored = sum(len(results) for results in categories.values())
    correct = sum(sum(results) for results in categories.values())
    by_category = {
        str(category): {
            "name": CATEGORY_NAMES.get(category, "unknown"),
            "correct": sum(results),
            "count": len(results),
            "accuracy": round(sum(results) / len(results), 6),
        }
        for category, results in sorted(categories.items())
    }
    return {
        "scored_question_count": scored,
        "infrastructure_failed_count": infrastructure_failed,
        "correct_count": correct,
        "accuracy": round(correct / scored, 6) if scored else None,
        "by_category": by_category,
    }


def _load_outcomes(root: Path) -> dict[str, dict[str, object]]:
    paths = sorted(root.glob("*/*.json"))
    if not paths:
        raise ValueError(f"LoCoMo public outcomes are missing: {root}")
    outcomes: dict[str, dict[str, object]] = {}
    for path in paths:
        outcome = _dict(read_json(path), field="LoCoMo public outcome")
        question_id = _required_str(outcome, "question_id")
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


def _find_question_set(
    repository_root: Path,
    *,
    selection_id: str,
    expected_sha256: str,
) -> Path:
    matches = [
        path
        for path in (repository_root / "benchmarks" / "locomo").glob("*.json")
        if file_sha256(path) == expected_sha256
    ]
    if len(matches) != 1:
        raise ValueError(f"LoCoMo question set is not uniquely available: {selection_id}")
    definition = _dict(read_json(matches[0]), field="LoCoMo question set")
    if definition.get("selection_id") != selection_id:
        raise ValueError("LoCoMo question-set receipt changes its selection identity")
    return matches[0]


def _string_set(value: object, *, field: str) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(cast(list[str], value)))
    ):
        raise ValueError(f"{field.capitalize()} must be a unique string array")
    return set(cast(list[str], value))


def _dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field.capitalize()} must be a JSON object")
    return cast(dict[str, object], value)


def _required_str(value: Mapping[str, object], field: str) -> str:
    observed = value.get(field)
    if not isinstance(observed, str) or not observed:
        raise ValueError(f"{field} must be a non-empty string")
    return observed


def _required_int(value: Mapping[str, object], field: str) -> int:
    observed = value.get(field)
    if type(observed) is not int:
        raise ValueError(f"{field} must be an integer")
    return observed
