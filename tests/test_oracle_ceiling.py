from __future__ import annotations

from dataclasses import replace

import pytest

from codecairn.evaluation.oracle_ceiling import build_oracle_context
from codecairn.memory.models import EvidenceFact, EvidenceReference


class WordTokenCounter:
    def count(self, text: str) -> int:
        return len(text.split())


class OverBudgetTokenCounter:
    def count(self, text: str) -> int:
        return 4_001


class NegativeTokenCounter:
    def count(self, text: str) -> int:
        return -1


def test_oracle_context_contains_only_explicit_gold_source_facts() -> None:
    selected = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)
    unrelated = _source_fact("fact-race", "Caroline finished a race.", raw_event_index=2)

    context = build_oracle_context(
        source_facts=(selected, unrelated),
        gold_source_fact_ids=(selected.fact_id,),
        token_counter=WordTokenCounter(),
    )

    assert [item.source_fact_id for item in context.evidence] == ["fact-poppy"]
    assert "Caroline adopted Poppy." in context.markdown
    assert "finished a race" not in context.markdown
    assert context.token_count == WordTokenCounter().count(context.markdown)
    assert context.token_limit == 4_000
    assert context.omitted_source_fact_ids == ("fact-race",)
    assert context.semantic_clause_ids == ()


def test_oracle_context_rejects_output_above_default_token_budget() -> None:
    fact = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)

    with pytest.raises(ValueError, match="4000 token budget"):
        build_oracle_context(
            source_facts=(fact,),
            gold_source_fact_ids=(fact.fact_id,),
            token_counter=OverBudgetTokenCounter(),
        )


def test_oracle_context_rejects_unknown_gold_source_fact_id() -> None:
    fact = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)

    with pytest.raises(ValueError, match="unknown gold source fact"):
        build_oracle_context(
            source_facts=(fact,),
            gold_source_fact_ids=("fact-forged",),
            token_counter=WordTokenCounter(),
        )


def test_oracle_context_rejects_duplicate_gold_source_fact_ids() -> None:
    fact = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)

    with pytest.raises(ValueError, match="duplicate gold source fact"):
        build_oracle_context(
            source_facts=(fact,),
            gold_source_fact_ids=(fact.fact_id, fact.fact_id),
            token_counter=WordTokenCounter(),
        )


@pytest.mark.parametrize("forbidden_field", ("gold_answer", "category"))
def test_oracle_context_interface_rejects_evaluation_labels(forbidden_field: str) -> None:
    fact = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)
    arguments: dict[str, object] = {
        "source_facts": (fact,),
        "gold_source_fact_ids": (fact.fact_id,),
        "token_counter": WordTokenCounter(),
        forbidden_field: "must-not-cross-the-seam",
    }

    with pytest.raises(TypeError, match="unexpected keyword"):
        build_oracle_context(**arguments)  # type: ignore[arg-type]


def test_oracle_context_rejects_ambiguous_source_fact_inventory() -> None:
    first = _source_fact("fact-poppy", "First text.", raw_event_index=1)
    conflicting = _source_fact("fact-poppy", "Conflicting text.", raw_event_index=2)

    with pytest.raises(ValueError, match="duplicate source fact"):
        build_oracle_context(
            source_facts=(first, conflicting),
            gold_source_fact_ids=(first.fact_id,),
            token_counter=WordTokenCounter(),
        )


def test_oracle_context_rejects_selected_fact_without_provenance() -> None:
    fact = replace(
        _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1),
        evidence=(),
    )

    with pytest.raises(ValueError, match="provenance"):
        build_oracle_context(
            source_facts=(fact,),
            gold_source_fact_ids=(fact.fact_id,),
            token_counter=WordTokenCounter(),
        )


def test_oracle_context_requires_positive_token_budget() -> None:
    fact = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)

    with pytest.raises(ValueError, match="positive"):
        build_oracle_context(
            source_facts=(fact,),
            gold_source_fact_ids=(fact.fact_id,),
            token_counter=WordTokenCounter(),
            max_tokens=0,
        )


def test_oracle_context_rejects_invalid_token_count() -> None:
    fact = _source_fact("fact-poppy", "Caroline adopted Poppy.", raw_event_index=1)

    with pytest.raises(ValueError, match="non-negative integer"):
        build_oracle_context(
            source_facts=(fact,),
            gold_source_fact_ids=(fact.fact_id,),
            token_counter=NegativeTokenCounter(),
        )


def _source_fact(fact_id: str, text: str, *, raw_event_index: int) -> EvidenceFact:
    return EvidenceFact(
        fact_id=fact_id,
        repo_key="locomo/conv-1",
        episode_id="episode-1",
        kind="conversation_turn",
        text=text,
        role="participant",
        actor="Caroline",
        occurred_at="2023-05-08T13:56:00+00:00",
        evidence=(
            EvidenceReference(
                provider="locomo",
                session_id="conv-1/session-1",
                source_path="locomo://dataset/conv-1/session-1",
                raw_event_sha256=f"{raw_event_index:064x}",
                raw_event_index=raw_event_index,
                raw_event_type="locomo_turn",
            ),
        ),
    )
