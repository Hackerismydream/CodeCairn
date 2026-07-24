from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import (
    canonical_sha256,
    file_sha256,
    read_json,
    write_json_exclusive,
)
from codecairn.evaluation.locomo import (
    CATEGORY_NAMES,
    LOCOMO_MODEL_OUTPUT_SCORING_CONTRACT,
    _is_scored_answer_contract_failure,
    _valid_judge_vote_retry_metadata,
    report_locomo,
)

LOCOMO_REPAIR_CONTRACT = "failed-question-exact-replacement-v1"

_CONSTANT_MANIFEST_FIELDS = (
    "dataset",
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


@dataclass(frozen=True, slots=True)
class LoCoMoRepairConfig:
    target_question_set_path: Path
    repair_question_set_path: Path
    base_run: Path
    repair_run: Path
    output_path: Path


def build_locomo_repair_report(config: LoCoMoRepairConfig) -> dict[str, object]:
    """Compose one formal score by replacing exactly one base run's failed questions."""
    base_manifest = _run_manifest(config.base_run, field="base")
    repair_manifest = _run_manifest(config.repair_run, field="repair")
    base_report = _run_report(config.base_run, manifest=base_manifest, field="base")
    repair_report = _run_report(config.repair_run, manifest=repair_manifest, field="repair")
    if any(
        report.get("model_output_scoring_contract") != LOCOMO_MODEL_OUTPUT_SCORING_CONTRACT
        for report in (base_report, repair_report)
    ):
        raise ValueError("LoCoMo repair sources use an unsupported model-output scoring contract")
    target_definition = _question_set_definition(
        config.target_question_set_path,
        field="target",
    )
    repair_definition = _question_set_definition(
        config.repair_question_set_path,
        field="repair",
    )
    if repair_definition.get("protocol") != target_definition.get("protocol"):
        raise ValueError("LoCoMo repair changes the frozen question-set protocol")

    base_question_set = _bound_question_set(
        base_manifest,
        definition_path=config.target_question_set_path,
        definition=target_definition,
        field="base",
    )
    repair_question_set = _bound_question_set(
        repair_manifest,
        definition_path=config.repair_question_set_path,
        definition=repair_definition,
        field="repair",
    )
    target_ids = _string_set(base_question_set.get("question_ids"), field="base question IDs")
    repair_ids = _string_set(
        repair_question_set.get("question_ids"),
        field="repair question IDs",
    )
    scored_base_ids, failed_base_ids = _classify_run_questions(
        config.base_run,
        manifest=base_manifest,
    )
    scored_repair_ids, failed_repair_ids = _classify_run_questions(
        config.repair_run,
        manifest=repair_manifest,
    )
    if scored_base_ids | failed_base_ids != target_ids or scored_base_ids & failed_base_ids:
        raise ValueError("Base LoCoMo question outcomes do not partition the target selection")
    if repair_ids != failed_base_ids:
        raise ValueError("LoCoMo repair selection must exactly replace the base failure set")
    if scored_repair_ids != repair_ids or failed_repair_ids:
        raise ValueError("LoCoMo repair run must score every replacement question")
    if scored_base_ids & repair_ids or scored_base_ids | scored_repair_ids != target_ids:
        raise ValueError("LoCoMo repair sources do not form one disjoint complete target score")
    if _required_int(base_report, "scored_question_count") != len(scored_base_ids):
        raise ValueError("Base LoCoMo report does not match its scored question inventory")
    if _required_int(base_report, "infrastructure_failed_count") != len(failed_base_ids):
        raise ValueError("Base LoCoMo report does not match its failed question inventory")
    if _required_int(repair_report, "scored_question_count") != len(scored_repair_ids):
        raise ValueError("Repair LoCoMo report does not match its scored question inventory")
    if _required_int(repair_report, "infrastructure_failed_count") != 0:
        raise ValueError("Repair LoCoMo run still contains infrastructure failures")

    contract = _validate_constant_contract(base_manifest, repair_manifest)
    by_category = _merge_categories(base_report, repair_report)
    question_count = len(target_ids)
    correct_count = _required_int(base_report, "correct_count") + _required_int(
        repair_report,
        "correct_count",
    )
    if sum(_required_int(row, "count") for row in by_category.values()) != question_count:
        raise ValueError("Composite LoCoMo category totals do not match the target selection")
    if sum(_required_int(row, "correct") for row in by_category.values()) != correct_count:
        raise ValueError("Composite LoCoMo category correct totals do not match its score")

    report: dict[str, object] = {
        "schema_version": 1,
        "suite": "locomo-repair-composite",
        "contract": LOCOMO_REPAIR_CONTRACT,
        "model_output_scoring_contract": LOCOMO_MODEL_OUTPUT_SCORING_CONTRACT,
        "formal_score": True,
        "question_count": question_count,
        "scored_question_count": question_count,
        "infrastructure_failed_count": 0,
        "correct_count": correct_count,
        "accuracy": round(correct_count / question_count, 6),
        "by_category": by_category,
        "usage": _merge_usage(base_report, repair_report),
        "benchmark_contract": contract,
        "benchmark_contract_sha256": canonical_sha256(contract),
        "target": {
            "selection_id": _required_str(target_definition, "selection_id"),
            "question_set_sha256": file_sha256(config.target_question_set_path),
            "selection_sha256": _required_str(target_definition, "selection_sha256"),
            "question_count": question_count,
        },
        "repair_selection": {
            "selection_id": _required_str(repair_definition, "selection_id"),
            "question_set_sha256": file_sha256(config.repair_question_set_path),
            "selection_sha256": _required_str(repair_definition, "selection_sha256"),
            "question_count": len(repair_ids),
        },
        "sources": {
            "base": _source_receipt(
                config.base_run,
                manifest=base_manifest,
                report=base_report,
                reused_scored_question_count=len(scored_base_ids),
            ),
            "repair": _source_receipt(
                config.repair_run,
                manifest=repair_manifest,
                report=repair_report,
                replacement_question_count=len(repair_ids),
            ),
        },
    }
    write_json_exclusive(config.output_path, report)
    return report


def _run_manifest(run_dir: Path, *, field: str) -> dict[str, object]:
    manifest = _dict(read_json(run_dir / "manifest.json"), field=f"{field} manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("suite") != "locomo"
        or manifest.get("mode") != "full"
        or manifest.get("scored") is not True
    ):
        raise ValueError(f"{field.capitalize()} source is not one scored full LoCoMo run")
    return manifest


def _run_report(
    run_dir: Path,
    *,
    manifest: dict[str, object],
    field: str,
) -> dict[str, object]:
    report = report_locomo(run_dir)
    if (
        report.get("suite") != "locomo"
        or report.get("mode") != "full"
        or report.get("scored") is not True
        or report.get("run_id") != manifest.get("run_id")
    ):
        raise ValueError(f"{field.capitalize()} LoCoMo report does not match its manifest")
    return report


def _question_set_definition(path: Path, *, field: str) -> dict[str, object]:
    definition = _dict(read_json(path), field=f"{field} question set")
    if definition.get("schema_version") != 1:
        raise ValueError(f"{field.capitalize()} LoCoMo question set schema is unsupported")
    _required_str(definition, "selection_id")
    _required_str(definition, "dataset_sha256")
    _required_str(definition, "selection_sha256")
    return definition


def _bound_question_set(
    manifest: dict[str, object],
    *,
    definition_path: Path,
    definition: dict[str, object],
    field: str,
) -> dict[str, object]:
    selection = _dict(manifest.get("selection"), field=f"{field} selection")
    question_set = _dict(selection.get("question_set"), field=f"{field} question-set binding")
    expected = {
        "selection_id": _required_str(definition, "selection_id"),
        "definition_sha256": file_sha256(definition_path),
        "dataset_sha256": _required_str(definition, "dataset_sha256"),
        "algorithm": _required_str(definition, "algorithm"),
        "seed": _required_str(definition, "seed"),
        "category_targets": _dict(
            definition.get("category_targets"),
            field=f"{field} question-set category targets",
        ),
        "selection_sha256": _required_str(definition, "selection_sha256"),
    }
    for name, value in expected.items():
        if question_set.get(name) != value:
            raise ValueError(f"{field.capitalize()} run changes its frozen question set: {name}")
    protocol = definition.get("protocol")
    expected_protocol_sha256 = None if protocol is None else canonical_sha256(protocol)
    if question_set.get("protocol_sha256") != expected_protocol_sha256:
        raise ValueError(f"{field.capitalize()} run changes its frozen question-set protocol")
    question_ids = _string_set(question_set.get("question_ids"), field=f"{field} question IDs")
    if question_set.get("question_count") != len(question_ids):
        raise ValueError(f"{field.capitalize()} question count does not match its inventory")
    selection_sha256 = _selection_sha256(question_ids)
    if selection_sha256 != question_set.get("selection_sha256"):
        raise ValueError(f"{field.capitalize()} run question inventory digest is invalid")
    if definition.get("algorithm") == "explicit-question-ids-v1":
        explicit_ids = _string_set(
            definition.get("question_ids"),
            field=f"{field} explicit question IDs",
        )
        if explicit_ids != question_ids or _selection_sha256(explicit_ids) != selection_sha256:
            raise ValueError(
                f"{field.capitalize()} explicit question set does not match its run inventory"
            )
    return question_set


def _classify_run_questions(
    run_dir: Path,
    *,
    manifest: dict[str, object],
) -> tuple[set[str], set[str]]:
    expected_votes = _required_int(manifest, "judge_votes")
    max_attempts = _required_int(manifest, "judge_response_max_attempts")
    max_response_chars = _required_int(manifest, "judge_response_max_chars")
    answer_max_attempts = _required_int(manifest, "answer_response_max_attempts")
    scored: set[str] = set()
    failed: set[str] = set()
    for path in sorted((run_dir / "checkpoints" / "questions").glob("*/*.json")):
        record = _dict(read_json(path), field="question checkpoint")
        question_id = _required_str(record, "question_id")
        votes = record.get("judge_votes")
        labels = (
            [vote.get("label") for vote in votes if isinstance(vote, dict)]
            if isinstance(votes, list)
            else []
        )
        is_scored = (
            record.get("status") == "completed"
            and isinstance(votes, list)
            and len(votes) == expected_votes
            and all(
                _valid_judge_vote_retry_metadata(
                    vote,
                    expected_vote_index=vote_index,
                    max_attempts=max_attempts,
                    max_response_chars=max_response_chars,
                )
                for vote_index, vote in enumerate(votes)
            )
            and len(labels) == expected_votes
            and all(label in {"correct", "wrong"} for label in labels)
        )
        if not is_scored:
            raw_receipt = record.get("answer_attempt_receipt")
            is_scored = _is_scored_answer_contract_failure(
                record,
                mode="full",
                answer_attempt_receipt=(
                    cast(dict[str, object], raw_receipt) if isinstance(raw_receipt, dict) else None
                ),
                expected_max_attempts=answer_max_attempts,
            )
        (scored if is_scored else failed).add(question_id)
    return scored, failed


def _validate_constant_contract(
    base: dict[str, object],
    repair: dict[str, object],
) -> dict[str, object]:
    contract = {field: base.get(field) for field in _CONSTANT_MANIFEST_FIELDS}
    for field in _CONSTANT_MANIFEST_FIELDS:
        if repair.get(field) != contract[field]:
            raise ValueError(f"LoCoMo repair changes the benchmark contract: {field}")
    base_corpus = _dict(base.get("corpus"), field="base corpus")
    repair_corpus = _dict(repair.get("corpus"), field="repair corpus")
    base_vectors = _dict(base.get("query_vectors"), field="base query vectors")
    repair_vectors = _dict(repair.get("query_vectors"), field="repair query vectors")
    for field, left, right in (
        ("corpus", base_corpus.get("content_sha256"), repair_corpus.get("content_sha256")),
        (
            "query_vectors",
            base_vectors.get("content_sha256"),
            repair_vectors.get("content_sha256"),
        ),
    ):
        if not isinstance(left, str) or right != left:
            raise ValueError(f"LoCoMo repair changes the benchmark contract: {field}")
    contract["corpus_content_sha256"] = base_corpus["content_sha256"]
    contract["query_vectors_content_sha256"] = base_vectors["content_sha256"]
    return contract


def _merge_categories(
    base_report: dict[str, object],
    repair_report: dict[str, object],
) -> dict[str, dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for report in (base_report, repair_report):
        rows = _dict(report.get("by_category"), field="LoCoMo category report")
        for raw_category, raw_row in rows.items():
            row = _dict(raw_row, field=f"LoCoMo category {raw_category}")
            category = int(raw_category)
            target = merged.setdefault(
                raw_category,
                {
                    "name": CATEGORY_NAMES.get(category, "unknown"),
                    "correct": 0,
                    "count": 0,
                },
            )
            if row.get("name") != target["name"]:
                raise ValueError("LoCoMo category report changes a category name")
            target["correct"] = cast(int, target["correct"]) + _required_int(row, "correct")
            target["count"] = cast(int, target["count"]) + _required_int(row, "count")
    for row in merged.values():
        count = cast(int, row["count"])
        correct = cast(int, row["correct"])
        row["accuracy"] = round(correct / count, 6)
    return dict(sorted(merged.items(), key=lambda item: int(item[0])))


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
            total = math.fsum(float(value) for value in numeric)
            merged[field] = round(total, 8)
        else:
            merged[field] = sum(cast(list[int], numeric))
    return merged


def _source_receipt(
    run_dir: Path,
    *,
    manifest: dict[str, object],
    report: dict[str, object],
    reused_scored_question_count: int | None = None,
    replacement_question_count: int | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "run_id": _required_str(manifest, "run_id"),
        "repository_commit": _required_str(manifest, "repository_commit"),
        "manifest_sha256": file_sha256(run_dir / "manifest.json"),
        "report_sha256": canonical_sha256(report),
    }
    if reused_scored_question_count is not None:
        receipt["reused_scored_question_count"] = reused_scored_question_count
    if replacement_question_count is not None:
        receipt["replacement_question_count"] = replacement_question_count
    return receipt


def _selection_sha256(question_ids: set[str]) -> str:
    return hashlib.sha256(
        json.dumps(
            sorted(question_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


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


def _string_set(value: object, *, field: str) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(cast(list[str], value)))
    ):
        raise ValueError(f"{field.capitalize()} must be a unique string array")
    return set(cast(list[str], value))
