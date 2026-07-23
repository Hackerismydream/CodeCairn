from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite

from codecairn.memory.episode import render_attributed_fact
from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    RankedRecall,
    RecallSnippet,
    RerankDocument,
)
from codecairn.memory.reranking import RerankingProvider

MAX_FACT_RERANK_CANDIDATES = 256
MAX_FACT_RERANK_CANDIDATES_PER_PARENT = 24
MAX_SELECTED_FACTS_PER_PARENT = 12
MAX_FACT_RERANK_DOCUMENT_CHARS = 2_048
FACT_SELECTOR_ID = "bounded-dialogue-aware-cross-encoder-v5"

_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class _FactCandidate:
    candidate_id: str
    parent_index: int
    fact_id: str
    snippet: RecallSnippet
    rerank_text: str
    parent_score: float


class EvidenceSelector:
    """Select authoritative source facts after parent ranking."""

    def __init__(
        self,
        *,
        reranker: RerankingProvider,
        max_candidates: int = MAX_FACT_RERANK_CANDIDATES,
        max_candidates_per_parent: int = MAX_FACT_RERANK_CANDIDATES_PER_PARENT,
        max_selected_per_parent: int = MAX_SELECTED_FACTS_PER_PARENT,
        max_document_chars: int = MAX_FACT_RERANK_DOCUMENT_CHARS,
    ) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if not 1 <= max_candidates_per_parent <= max_candidates:
            raise ValueError("max_candidates_per_parent exceeds the global limit")
        if not 1 <= max_selected_per_parent <= max_candidates_per_parent:
            raise ValueError("max_selected_per_parent exceeds the parent candidate limit")
        if max_document_chars < 256:
            raise ValueError("max_document_chars must be at least 256")
        self._reranker = reranker
        self._max_candidates = max_candidates
        self._max_candidates_per_parent = max_candidates_per_parent
        self._max_selected_per_parent = max_selected_per_parent
        self._max_document_chars = max_document_chars

    def select(
        self,
        query: str,
        *,
        ranked: tuple[RankedRecall, ...],
        memories: Mapping[str, CodingMemory],
    ) -> tuple[RankedRecall, ...]:
        if not ranked:
            return ()
        parent_limits = _capacity_aware_parent_limits(
            ranked,
            memories=memories,
            max_candidates=self._max_candidates,
            max_candidates_per_parent=self._max_candidates_per_parent,
            coverage_floor=self._max_selected_per_parent,
        )
        candidates: list[_FactCandidate] = []
        for parent_index, (item, parent_limit) in enumerate(
            zip(ranked, parent_limits, strict=True)
        ):
            if parent_limit == 0:
                continue
            memory = memories.get(item.memory_id)
            if memory is None:
                continue
            parent_candidates = _parent_candidates(
                query,
                parent_index=parent_index,
                item=item,
                memory=memory,
                limit=parent_limit,
                max_document_chars=self._max_document_chars,
            )
            candidates.extend(parent_candidates)
        if not candidates:
            return ranked
        candidates = candidates[: self._max_candidates]
        documents = tuple(
            RerankDocument(
                memory_id=candidate.candidate_id,
                text=candidate.rerank_text,
                fusion_score=candidate.parent_score,
            )
            for candidate in candidates
        )
        raw_scores = self._reranker.rerank(query, documents)
        expected = {candidate.candidate_id for candidate in candidates}
        scores: dict[str, float] = {}
        for score in raw_scores:
            if score.memory_id not in expected:
                raise ValueError("Fact reranker returned an unknown candidate")
            if score.memory_id in scores:
                raise ValueError("Fact reranker returned a duplicate candidate")
            if not isfinite(score.score):
                raise ValueError("Fact reranker returned a non-finite score")
            scores[score.memory_id] = score.score
        if scores.keys() != expected:
            raise ValueError("Fact reranker did not score every candidate")

        selected_by_parent: dict[int, list[RecallSnippet]] = {}
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                -scores[candidate.candidate_id],
                ranked[candidate.parent_index].rank,
                candidate.snippet.raw_event_index
                if candidate.snippet.raw_event_index is not None
                else -1,
                candidate.fact_id,
            ),
        )
        for candidate in ordered:
            selected = selected_by_parent.setdefault(candidate.parent_index, [])
            if len(selected) >= self._max_selected_per_parent:
                continue
            selected.append(
                replace(
                    candidate.snippet,
                    relevance_score=round(scores[candidate.candidate_id], 12),
                    selection_source=FACT_SELECTOR_ID,
                )
            )

        result: list[RankedRecall] = []
        for parent_index, item in enumerate(ranked):
            selected_snippets = tuple(selected_by_parent.get(parent_index, ()))
            neighbors = tuple(
                snippet
                for snippet in item.snippets
                if snippet.source_memory_id != item.memory_id or snippet.relation == "neighbor"
            )
            result.append(
                item
                if not selected_snippets
                else replace(item, snippets=_deduplicate((*selected_snippets, *neighbors)))
            )
        return tuple(result)


def _parent_candidates(
    query: str,
    *,
    parent_index: int,
    item: RankedRecall,
    memory: CodingMemory,
    limit: int,
    max_document_chars: int,
) -> tuple[_FactCandidate, ...]:
    existing_relations = {
        snippet.fact_id: snippet.relation
        for snippet in item.snippets
        if snippet.source_memory_id == item.memory_id
    }
    query_terms = _terms(query)
    facts = tuple(sorted(memory.facts, key=_fact_key))
    fact_positions = {fact.fact_id: position for position, fact in enumerate(facts)}
    matched_positions = tuple(
        fact_positions[fact_id]
        for fact_id, relation in existing_relations.items()
        if relation == "matched" and fact_id in fact_positions
    )
    semantic_text, context_semantic_text, semantic_fact_ids = _semantic_projection_by_source(memory)

    def priority(
        fact: EvidenceFact,
    ) -> tuple[int, int, int, int, int, int, int, int, str]:
        relation = existing_relations.get(fact.fact_id)
        position = fact_positions[fact.fact_id]
        distance = min(
            (abs(position - matched_position) for matched_position in matched_positions),
            default=len(facts),
        )
        projected = semantic_text.get(fact.fact_id, "")
        exact_text = render_attributed_fact(fact)
        semantic_overlap = len(query_terms & _terms(projected))
        exact_overlap = len(query_terms & _terms(exact_text))
        return (
            -int(relation == "matched"),
            -int(relation == "sibling"),
            -int(distance <= 2),
            min(distance, 3),
            -semantic_overlap,
            -exact_overlap,
            -int(bool(projected)),
            *_fact_key(fact),
        )

    ranked_facts = sorted(
        facts,
        key=priority,
    )
    selected = ranked_facts[:limit]
    candidates: list[_FactCandidate] = []
    for ordinal, fact in enumerate(selected):
        exact_text = render_attributed_fact(fact)
        projection_text = semantic_text.get(fact.fact_id, "")
        position = fact_positions[fact.fact_id]
        previous_fact = facts[position - 1] if position > 0 else None
        previous_text = (
            render_attributed_fact(previous_fact)
            if (
                previous_fact is not None
                and previous_fact.actor != fact.actor
                and (_needs_previous_turn(fact) or _is_question(previous_fact))
            )
            else ""
        )
        candidates.append(
            _FactCandidate(
                candidate_id=f"fact-candidate-{parent_index:02d}-{ordinal:02d}",
                parent_index=parent_index,
                fact_id=fact.fact_id,
                snippet=RecallSnippet(
                    relation=existing_relations.get(fact.fact_id, "sibling"),
                    source_memory_id=item.memory_id,
                    source_uri=item.source_uri,
                    fact_id=fact.fact_id,
                    text=exact_text,
                    source_title=item.title,
                    source_summary=item.summary,
                    raw_event_index=_fact_key(fact)[0],
                    semantic_text=context_semantic_text.get(fact.fact_id) or None,
                    semantic_fact_ids=semantic_fact_ids.get(fact.fact_id, ()),
                ),
                rerank_text=_bounded_rerank_text(
                    "\n".join(
                        part
                        for part in (
                            f"Semantic projection:\n{projection_text}" if projection_text else "",
                            f"Previous turn:\n{previous_text}" if previous_text else "",
                            f"Target turn:\n{exact_text}",
                        )
                        if part
                    ),
                    "",
                    max_chars=max_document_chars,
                ),
                parent_score=item.final_score,
            )
        )
    return tuple(candidates)


def _semantic_projection_by_source(
    memory: CodingMemory,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, tuple[str, ...]],
]:
    values: dict[str, list[str]] = {}
    single_source_values: dict[str, list[str]] = {}
    fact_ids: dict[str, list[str]] = {}
    if memory.semantic_episode is None:
        return {}, {}, {}
    for atomic_fact in memory.semantic_episode.atomic_facts:
        for source_fact_id in atomic_fact.source_fact_ids:
            values.setdefault(source_fact_id, []).append(atomic_fact.text)
            if atomic_fact.source_fact_ids == (source_fact_id,):
                single_source_values.setdefault(source_fact_id, []).append(atomic_fact.text)
                fact_ids.setdefault(source_fact_id, []).append(atomic_fact.fact_id)
    return (
        {fact_id: "\n".join(dict.fromkeys(texts)) for fact_id, texts in values.items()},
        {
            fact_id: "\n".join(dict.fromkeys(texts))
            for fact_id, texts in single_source_values.items()
        },
        {fact_id: tuple(dict.fromkeys(ids)) for fact_id, ids in fact_ids.items()},
    )


def _capacity_aware_parent_limits(
    ranked: tuple[RankedRecall, ...],
    *,
    memories: Mapping[str, CodingMemory],
    max_candidates: int,
    max_candidates_per_parent: int,
    coverage_floor: int,
) -> tuple[int, ...]:
    """Preserve parent breadth, then spend spare work where direct evidence is strongest."""

    if not ranked:
        return ()
    capacities: list[int] = []
    matched_counts: list[int] = []
    for item in ranked:
        memory = memories.get(item.memory_id)
        fact_ids = set() if memory is None else {fact.fact_id for fact in memory.facts}
        capacities.append(min(max_candidates_per_parent, len(fact_ids)))
        matched_counts.append(
            len(
                {
                    snippet.fact_id
                    for snippet in item.snippets
                    if (
                        snippet.source_memory_id == item.memory_id
                        and snippet.relation == "matched"
                        and snippet.fact_id in fact_ids
                    )
                }
            )
        )
    return _allocate_parent_limits(
        tuple(capacities),
        tuple(matched_counts),
        max_candidates=max_candidates,
        coverage_floor=coverage_floor,
    )


def _allocate_parent_limits(
    capacities: tuple[int, ...],
    matched_counts: tuple[int, ...],
    *,
    max_candidates: int,
    coverage_floor: int,
) -> tuple[int, ...]:
    if len(capacities) != len(matched_counts):
        raise ValueError("Parent capacity and direct-match profiles must align")
    limits = [0 for _capacity in capacities]
    remaining = max_candidates
    for _floor_slot in range(coverage_floor):
        for index, capacity in enumerate(capacities):
            if remaining <= 0:
                break
            if limits[index] < capacity:
                limits[index] += 1
                remaining -= 1
    priority = sorted(
        range(len(capacities)),
        key=lambda index: (-matched_counts[index], -capacities[index], index),
    )
    for index in priority:
        if remaining <= 0:
            break
        available = capacities[index] - limits[index]
        assigned = min(available, remaining)
        limits[index] += assigned
        remaining -= assigned
    return tuple(limits)


def _needs_previous_turn(fact: EvidenceFact) -> bool:
    text = " ".join(fact.text.split())
    if len(text) <= 96 or len(text.split()) <= 12:
        return True
    lowered = text.casefold()
    return lowered.startswith(
        (
            "also ",
            "because ",
            "he ",
            "here ",
            "it ",
            "no ",
            "same ",
            "she ",
            "that ",
            "there ",
            "they ",
            "this ",
            "too ",
            "we ",
            "yeah ",
            "yep ",
            "yes ",
        )
    )


def _is_question(fact: EvidenceFact) -> bool:
    return any(mark in fact.text for mark in ("?", "\uff1f"))


def _bounded_rerank_text(projection_text: str, exact_text: str, *, max_chars: int) -> str:
    text = "\n".join(part for part in (projection_text, exact_text) if part)
    if len(text) <= max_chars:
        return text
    separator = "\n…\n"
    prefix_chars = (max_chars - len(separator)) // 2
    suffix_chars = max_chars - len(separator) - prefix_chars
    return text[:prefix_chars] + separator + text[-suffix_chars:]


def _terms(text: str) -> set[str]:
    return {
        term
        for match in _TERM.finditer(text)
        if (term := match.group(0).casefold()) not in _STOPWORDS
    }


def _fact_key(fact: EvidenceFact) -> tuple[int, str]:
    return (
        min((reference.raw_event_index for reference in fact.evidence), default=-1),
        fact.fact_id,
    )


def _deduplicate(snippets: tuple[RecallSnippet, ...]) -> tuple[RecallSnippet, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[RecallSnippet] = []
    for snippet in snippets:
        key = (snippet.source_memory_id, snippet.fact_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(snippet)
    return tuple(result)
