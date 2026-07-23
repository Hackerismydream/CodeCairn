from __future__ import annotations

from typing import Protocol

from codecairn.evaluation.grounded_answer import GroundedContext, RenderedEvidence
from codecairn.memory.episode import render_attributed_fact
from codecairn.memory.models import EvidenceFact


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


def build_oracle_context(
    *,
    source_facts: tuple[EvidenceFact, ...],
    gold_source_fact_ids: tuple[str, ...],
    token_counter: TokenCounter,
    max_tokens: int = 4_000,
) -> GroundedContext:
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError("Oracle context token budget must be a positive integer")
    source_fact_ids = tuple(fact.fact_id for fact in source_facts)
    if len(source_fact_ids) != len(set(source_fact_ids)):
        raise ValueError("Oracle context source inventory contains duplicate source fact IDs")
    facts_by_id = {fact.fact_id: fact for fact in source_facts}
    if len(gold_source_fact_ids) != len(set(gold_source_fact_ids)):
        raise ValueError("Oracle context contains duplicate gold source fact IDs")
    unknown_ids = tuple(fact_id for fact_id in gold_source_fact_ids if fact_id not in facts_by_id)
    if unknown_ids:
        raise ValueError("Oracle context contains an unknown gold source fact ID")
    selected = tuple(facts_by_id[fact_id] for fact_id in gold_source_fact_ids)
    if any(not fact.evidence for fact in selected):
        raise ValueError("Oracle context source facts require provenance")
    evidence = tuple(
        RenderedEvidence(
            source_fact_id=fact.fact_id,
            text=render_attributed_fact(fact),
            source_uri=fact.evidence[0].source_path,
        )
        for fact in selected
    )
    lines = ["# Grounded Context", ""]
    for item in evidence:
        lines.extend(
            (
                f"- [{item.source_fact_id}] {item.text}",
                f"  Source: {item.source_uri}",
            )
        )
    markdown = "\n".join(lines) + "\n"
    token_count = token_counter.count(markdown)
    if type(token_count) is not int or token_count < 0:
        raise ValueError("TokenCounter must return a non-negative integer")
    if token_count > max_tokens:
        raise ValueError(f"Oracle context exceeds the {max_tokens} token budget")
    selected_ids = set(gold_source_fact_ids)
    return GroundedContext(
        markdown=markdown,
        evidence=evidence,
        token_count=token_count,
        token_limit=max_tokens,
        omitted_source_fact_ids=tuple(
            fact.fact_id for fact in source_facts if fact.fact_id not in selected_ids
        ),
    )
