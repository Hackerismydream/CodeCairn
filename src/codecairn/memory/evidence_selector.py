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
MAX_FACT_RERANK_CANDIDATES_PER_PARENT = 16
MAX_SELECTED_FACTS_PER_PARENT = 8
MAX_FACT_RERANK_DOCUMENT_CHARS = 2_048
FACT_SELECTOR_ID = "bounded-authoritative-cross-encoder-v1"

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
        per_parent_limit = min(
            self._max_candidates_per_parent,
            max(1, self._max_candidates // len(ranked)),
        )
        candidates: list[_FactCandidate] = []
        for parent_index, item in enumerate(ranked):
            memory = memories.get(item.memory_id)
            if memory is None:
                continue
            parent_candidates = _parent_candidates(
                query,
                parent_index=parent_index,
                item=item,
                memory=memory,
                limit=per_parent_limit,
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
    semantic_text = _semantic_text_by_source(memory)
    ranked_facts = sorted(
        facts,
        key=lambda fact: (
            -int(existing_relations.get(fact.fact_id) == "matched"),
            -len(
                query_terms
                & _terms(
                    "\n".join(
                        part
                        for part in (
                            render_attributed_fact(fact),
                            semantic_text.get(fact.fact_id, ""),
                        )
                        if part
                    )
                )
            ),
            _fact_key(fact),
        ),
    )
    selected = ranked_facts[:limit]
    candidates: list[_FactCandidate] = []
    for ordinal, fact in enumerate(selected):
        exact_text = render_attributed_fact(fact)
        projection_text = semantic_text.get(fact.fact_id, "")
        candidates.append(
            _FactCandidate(
                candidate_id=f"fact-candidate-{parent_index:02d}-{ordinal:02d}",
                parent_index=parent_index,
                fact_id=fact.fact_id,
                snippet=RecallSnippet(
                    relation=existing_relations.get(fact.fact_id, "matched"),
                    source_memory_id=item.memory_id,
                    source_uri=item.source_uri,
                    fact_id=fact.fact_id,
                    text=exact_text,
                    source_title=item.title,
                    source_summary=item.summary,
                    raw_event_index=_fact_key(fact)[0],
                ),
                rerank_text=_bounded_rerank_text(
                    projection_text,
                    exact_text,
                    max_chars=max_document_chars,
                ),
                parent_score=item.final_score,
            )
        )
    return tuple(candidates)


def _semantic_text_by_source(memory: CodingMemory) -> dict[str, str]:
    values: dict[str, list[str]] = {}
    if memory.semantic_episode is None:
        return {}
    for atomic_fact in memory.semantic_episode.atomic_facts:
        for source_fact_id in atomic_fact.source_fact_ids:
            values.setdefault(source_fact_id, []).append(atomic_fact.text)
    return {fact_id: "\n".join(dict.fromkeys(texts)) for fact_id, texts in values.items()}


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
