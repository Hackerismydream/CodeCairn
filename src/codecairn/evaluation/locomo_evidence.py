from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codecairn.evaluation.artifacts import read_json, write_json_exclusive
from codecairn.evaluation.locomo import CATEGORY_NAMES, load_locomo_dataset, report_locomo
from codecairn.evaluation.locomo_oracle import (
    build_locomo_oracle_context,
    compile_locomo_source_facts,
)
from codecairn.memory.context import CONTEXT_TOKENIZER_ID, count_context_tokens

EvidenceCoverageStatus = Literal["all", "partial", "none", "no_gold", "unknown_gold"]


@dataclass(frozen=True, slots=True)
class LoCoMoEvidenceCoverageConfig:
    run_dir: Path
    dataset_path: Path
    output_path: Path | None = None
    oracle_max_tokens: int = 4_000


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    status: EvidenceCoverageStatus
    matched_count: int
    gold_count: int


class _PinnedTokenCounter:
    def count(self, text: str) -> int:
        return count_context_tokens(text)


def classify_evidence_coverage(
    *,
    gold_fact_ids: tuple[str, ...],
    observed_fact_ids: set[str],
    has_unknown_gold: bool = False,
) -> EvidenceCoverage:
    if has_unknown_gold:
        return EvidenceCoverage(
            status="unknown_gold",
            matched_count=0,
            gold_count=len(gold_fact_ids),
        )
    if not gold_fact_ids:
        return EvidenceCoverage(status="no_gold", matched_count=0, gold_count=0)
    matched = len(set(gold_fact_ids) & observed_fact_ids)
    if matched == len(set(gold_fact_ids)):
        status: EvidenceCoverageStatus = "all"
    elif matched:
        status = "partial"
    else:
        status = "none"
    return EvidenceCoverage(status=status, matched_count=matched, gold_count=len(gold_fact_ids))


def report_locomo_evidence_coverage(
    config: LoCoMoEvidenceCoverageConfig,
) -> dict[str, object]:
    if config.oracle_max_tokens < 1:
        raise ValueError("oracle_max_tokens must be positive")
    validated_run = report_locomo(config.run_dir)
    dataset = load_locomo_dataset(config.dataset_path)
    manifest = _object(read_json(config.run_dir / "manifest.json"), field="run manifest")
    manifest_dataset = _object(manifest.get("dataset"), field="run dataset")
    if manifest_dataset.get("sha256") != dataset.sha256:
        raise ValueError("LoCoMo evidence report dataset does not match the run manifest")

    conversations = {item.sample_id: item for item in dataset.conversations}
    questions = {
        question.question_id: (conversation, question)
        for conversation in dataset.conversations
        for question in conversation.questions
    }
    source_maps = {
        sample_id: {
            item.dia_id: item.fact.fact_id
            for item in compile_locomo_source_facts(
                conversation,
                dataset_sha256=dataset.sha256,
            )
        }
        for sample_id, conversation in conversations.items()
    }
    question_paths = sorted((config.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    records: list[dict[str, object]] = []
    aggregate = _new_aggregate()
    by_category: dict[int, Counter[str]] = {}
    for question_path in question_paths:
        record = _object(read_json(question_path), field="question checkpoint")
        question_id = _text(record.get("question_id"), field="question_id")
        if question_id not in questions:
            raise ValueError("LoCoMo evidence report found an unknown question")
        conversation, question = questions[question_id]
        if record.get("sample_id") != conversation.sample_id:
            raise ValueError("LoCoMo evidence report question belongs to another conversation")
        mapping = source_maps[conversation.sample_id]
        unknown_gold = tuple(dia_id for dia_id in question.evidence if dia_id not in mapping)
        gold_fact_ids = tuple(mapping[dia_id] for dia_id in question.evidence if dia_id in mapping)
        (
            ranked_fact_ids,
            candidate_snippet_fact_ids,
            rendered_fact_ids,
            token_count,
        ) = _checkpoint_evidence(record)
        ranked = classify_evidence_coverage(
            gold_fact_ids=gold_fact_ids,
            observed_fact_ids=ranked_fact_ids,
            has_unknown_gold=bool(unknown_gold),
        )
        candidate_snippets = classify_evidence_coverage(
            gold_fact_ids=gold_fact_ids,
            observed_fact_ids=candidate_snippet_fact_ids,
            has_unknown_gold=bool(unknown_gold),
        )
        rendered = classify_evidence_coverage(
            gold_fact_ids=gold_fact_ids,
            observed_fact_ids=rendered_fact_ids,
            has_unknown_gold=bool(unknown_gold),
        )
        oracle_buildable = False
        if gold_fact_ids and not unknown_gold:
            try:
                build_locomo_oracle_context(
                    conversation,
                    dataset_sha256=dataset.sha256,
                    gold_dia_ids=question.evidence,
                    token_counter=_PinnedTokenCounter(),
                    max_tokens=config.oracle_max_tokens,
                )
            except ValueError:
                oracle_buildable = False
            else:
                oracle_buildable = True
        _accumulate(
            aggregate,
            ranked=ranked,
            candidate_snippets=candidate_snippets,
            rendered=rendered,
            oracle_buildable=oracle_buildable,
            token_count=token_count,
        )
        category = by_category.setdefault(question.category, _new_aggregate())
        _accumulate(
            category,
            ranked=ranked,
            candidate_snippets=candidate_snippets,
            rendered=rendered,
            oracle_buildable=oracle_buildable,
            token_count=token_count,
        )
        records.append(
            {
                "sample_id": conversation.sample_id,
                "question_id": question.question_id,
                "category": question.category,
                "gold_evidence_count": len(question.evidence),
                "unknown_gold_dia_ids": list(unknown_gold),
                "ranked_coverage": ranked.status,
                "candidate_snippet_coverage": candidate_snippets.status,
                "context_coverage": rendered.status,
                "oracle_context_buildable": oracle_buildable,
                "context_token_count": token_count,
            }
        )

    report: dict[str, object] = {
        "schema_version": 2,
        "suite": "locomo-evidence-coverage",
        "run_id": validated_run["run_id"],
        "dataset_sha256": dataset.sha256,
        "context_tokenizer": CONTEXT_TOKENIZER_ID,
        "oracle_max_tokens": config.oracle_max_tokens,
        "overall": _finalize_aggregate(aggregate),
        "by_category": {
            str(category): {
                "name": CATEGORY_NAMES[category],
                **_finalize_aggregate(values),
            }
            for category, values in sorted(by_category.items())
        },
        "questions": records,
    }
    if config.output_path is not None:
        write_json_exclusive(config.output_path, report)
    return report


def _checkpoint_evidence(
    record: dict[str, object],
) -> tuple[set[str], set[str], set[str], int | None]:
    retrieval = _object(record.get("retrieval"), field="question retrieval")
    ranked = retrieval.get("ranked")
    if not isinstance(ranked, list):
        raise ValueError("LoCoMo evidence report requires ranked retrieval evidence")
    ranked_fact_ids: set[str] = set()
    candidate_snippet_fact_ids: set[str] = set()
    for raw_item in ranked:
        item = _object(raw_item, field="ranked recall")
        raw_episode_fact_ids = item.get("episode_fact_ids")
        if not isinstance(raw_episode_fact_ids, list) or any(
            not isinstance(fact_id, str) or not fact_id for fact_id in raw_episode_fact_ids
        ):
            raise ValueError("LoCoMo evidence report ranked parent fact IDs must be an array")
        ranked_fact_ids.update(cast(list[str], raw_episode_fact_ids))
        snippets = item.get("snippets", [])
        if not isinstance(snippets, list):
            raise ValueError("LoCoMo evidence report ranked snippets must be an array")
        for raw_snippet in snippets:
            snippet = _object(raw_snippet, field="ranked snippet")
            fact_id = snippet.get("fact_id")
            if isinstance(fact_id, str) and fact_id:
                candidate_snippet_fact_ids.add(fact_id)
    trace = _object(retrieval.get("context_trace"), field="context trace")
    raw_rendered = trace.get("rendered_fact_ids")
    if not isinstance(raw_rendered, list) or any(
        not isinstance(item, str) for item in raw_rendered
    ):
        raise ValueError("LoCoMo evidence report rendered fact IDs must be an array")
    raw_token_count = trace.get("token_count")
    token_count = raw_token_count if type(raw_token_count) is int else None
    return (
        ranked_fact_ids,
        candidate_snippet_fact_ids,
        set(cast(list[str], raw_rendered)),
        token_count,
    )


def _new_aggregate() -> Counter[str]:
    return Counter()


def _accumulate(
    aggregate: Counter[str],
    *,
    ranked: EvidenceCoverage,
    candidate_snippets: EvidenceCoverage,
    rendered: EvidenceCoverage,
    oracle_buildable: bool,
    token_count: int | None,
) -> None:
    aggregate["question_count"] += 1
    aggregate[f"ranked_{ranked.status}"] += 1
    aggregate[f"candidate_snippet_{candidate_snippets.status}"] += 1
    aggregate[f"context_{rendered.status}"] += 1
    if ranked.status in {"all", "partial", "none"}:
        aggregate["resolvable_question_count"] += 1
    aggregate["oracle_buildable_count"] += int(oracle_buildable)
    if token_count is not None:
        aggregate["token_count_observation_count"] += 1
        aggregate["token_count_total"] += token_count


def _finalize_aggregate(aggregate: Counter[str]) -> dict[str, object]:
    resolvable = aggregate["resolvable_question_count"]
    token_observations = aggregate["token_count_observation_count"]
    return {
        "question_count": aggregate["question_count"],
        "resolvable_question_count": resolvable,
        "no_gold_evidence_count": aggregate["context_no_gold"],
        "unknown_gold_evidence_count": aggregate["context_unknown_gold"],
        "ranked_all_count": aggregate["ranked_all"],
        "ranked_partial_count": aggregate["ranked_partial"],
        "ranked_none_count": aggregate["ranked_none"],
        "ranked_all_coverage": _ratio(aggregate["ranked_all"], resolvable),
        "candidate_snippet_all_count": aggregate["candidate_snippet_all"],
        "candidate_snippet_partial_count": aggregate["candidate_snippet_partial"],
        "candidate_snippet_none_count": aggregate["candidate_snippet_none"],
        "candidate_snippet_all_coverage": _ratio(
            aggregate["candidate_snippet_all"],
            resolvable,
        ),
        "candidate_snippet_any_coverage": _ratio(
            aggregate["candidate_snippet_all"] + aggregate["candidate_snippet_partial"],
            resolvable,
        ),
        "context_all_count": aggregate["context_all"],
        "context_partial_count": aggregate["context_partial"],
        "context_none_count": aggregate["context_none"],
        "context_all_coverage": _ratio(aggregate["context_all"], resolvable),
        "context_any_coverage": _ratio(
            aggregate["context_all"] + aggregate["context_partial"],
            resolvable,
        ),
        "oracle_context_buildable_count": aggregate["oracle_buildable_count"],
        "oracle_context_buildable_rate": _ratio(
            aggregate["oracle_buildable_count"],
            resolvable,
        ),
        "average_context_tokens": (
            round(aggregate["token_count_total"] / token_observations, 3)
            if token_observations
            else None
        ),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    return value
