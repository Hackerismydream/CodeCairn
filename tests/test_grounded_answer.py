from __future__ import annotations

import pytest

from codecairn.evaluation.grounded_answer import (
    GroundedAnswer,
    GroundedContext,
    RenderedEvidence,
    parse_grounded_answer,
)


def test_parse_grounded_answer_accepts_rendered_source_fact_citations() -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n\n- Caroline adopted Poppy.\n",
        evidence=(
            RenderedEvidence(
                source_fact_id="fact-poppy",
                text="Caroline adopted Poppy.",
                source_uri="locomo://conv-1/session-1#0",
            ),
        ),
        token_count=12,
        token_limit=4_000,
    )

    answer = parse_grounded_answer(
        '{"answer":"A beagle named Poppy.",'
        '"supporting_evidence_ids":["fact-poppy"],'
        '"insufficient":false}',
        context=context,
    )

    assert answer == GroundedAnswer(
        answer="A beagle named Poppy.",
        supporting_evidence_ids=("fact-poppy",),
        insufficient=False,
    )


def test_parse_grounded_answer_normalizes_a_supported_list_answer() -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-family", "Family activities", "locomo://family"),),
        token_count=4,
        token_limit=4_000,
    )

    answer = parse_grounded_answer(
        '{"answer":["hiking","camping","painting"],'
        '"supporting_evidence_ids":["fact-family"],'
        '"insufficient":false}',
        context=context,
    )

    assert answer == GroundedAnswer(
        answer="hiking; camping; painting",
        supporting_evidence_ids=("fact-family",),
        insufficient=False,
    )


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        "[]",
        '{"answer":"Poppy","supporting_evidence_ids":["fact-poppy"]}',
        '{"answer":"Poppy","supporting_evidence_ids":["fact-poppy"],'
        '"insufficient":false,"gold_answer":"Poppy"}',
        '{"answer":1,"supporting_evidence_ids":["fact-poppy"],"insufficient":false}',
        '{"answer":[],"supporting_evidence_ids":["fact-poppy"],"insufficient":false}',
        '{"answer":["Poppy",1],"supporting_evidence_ids":["fact-poppy"],"insufficient":false}',
        '{"answer":"Poppy","supporting_evidence_ids":"fact-poppy","insufficient":false}',
        '{"answer":"Poppy","supporting_evidence_ids":[1],"insufficient":false}',
        '{"answer":"Poppy","supporting_evidence_ids":["fact-poppy"],"insufficient":0}',
        '{"answer":"Poppy","answer":"Forged",'
        '"supporting_evidence_ids":["fact-poppy"],"insufficient":false}',
        '{"answer":"   ","supporting_evidence_ids":["fact-poppy"],"insufficient":false}',
        '{"answer":"Poppy","supporting_evidence_ids":["   "],"insufficient":false}',
    ),
)
def test_parse_grounded_answer_rejects_non_exact_json_schema(payload: str) -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-poppy", "Poppy", "locomo://poppy"),),
        token_count=4,
        token_limit=4_000,
    )

    with pytest.raises(ValueError, match="schema"):
        parse_grounded_answer(payload, context=context)


def test_parse_grounded_answer_rejects_unknown_citation() -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-poppy", "Poppy", "locomo://poppy"),),
        token_count=4,
        token_limit=4_000,
    )

    with pytest.raises(ValueError, match="unknown"):
        parse_grounded_answer(
            '{"answer":"Poppy","supporting_evidence_ids":["fact-forged"],"insufficient":false}',
            context=context,
        )


def test_parse_grounded_answer_rejects_duplicate_citation() -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-poppy", "Poppy", "locomo://poppy"),),
        token_count=4,
        token_limit=4_000,
    )

    with pytest.raises(ValueError, match="duplicate"):
        parse_grounded_answer(
            '{"answer":"Poppy","supporting_evidence_ids":'
            '["fact-poppy","fact-poppy"],"insufficient":false}',
            context=context,
        )


def test_parse_grounded_answer_rejects_omitted_source_fact_citation() -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-poppy", "Poppy", "locomo://poppy"),),
        token_count=4,
        token_limit=4_000,
        omitted_source_fact_ids=("fact-omitted",),
    )

    with pytest.raises(ValueError, match="omitted"):
        parse_grounded_answer(
            '{"answer":"Hidden","supporting_evidence_ids":["fact-omitted"],"insufficient":false}',
            context=context,
        )


def test_parse_grounded_answer_rejects_semantic_clause_citation() -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-poppy", "Poppy", "locomo://poppy"),),
        token_count=4,
        token_limit=4_000,
        semantic_clause_ids=("clause-poppy",),
    )

    with pytest.raises(ValueError, match="semantic clause"):
        parse_grounded_answer(
            '{"answer":"Poppy","supporting_evidence_ids":["clause-poppy"],"insufficient":false}',
            context=context,
        )


@pytest.mark.parametrize(
    "payload",
    (
        '{"answer":"Poppy","supporting_evidence_ids":[],"insufficient":false}',
        '{"answer":"Insufficient","supporting_evidence_ids":["fact-poppy"],"insufficient":true}',
    ),
)
def test_parse_grounded_answer_rejects_inconsistent_insufficient_state(payload: str) -> None:
    context = GroundedContext(
        markdown="# Grounded Context\n",
        evidence=(RenderedEvidence("fact-poppy", "Poppy", "locomo://poppy"),),
        token_count=4,
        token_limit=4_000,
    )

    with pytest.raises(ValueError, match="insufficient"):
        parse_grounded_answer(payload, context=context)
