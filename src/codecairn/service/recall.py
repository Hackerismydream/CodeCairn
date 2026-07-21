from __future__ import annotations

import hashlib
import re
import struct
import time
from collections.abc import Callable
from dataclasses import replace
from math import isfinite
from typing import Protocol
from urllib.parse import quote

from codecairn.memory.embedding import EmbeddingProvider
from codecairn.memory.models import (
    CandidateSource,
    CodingMemory,
    EvidenceFact,
    IndexCandidate,
    RankedRecall,
    RecallDocumentKind,
    RecallDocumentSource,
    RecallEvidence,
    RecallMatch,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
    RecallSnippetRelation,
    RerankDocument,
    RerankScore,
)
from codecairn.memory.recall_planner import RecallPlanner, RecallPlannerConfig
from codecairn.memory.reranking import RerankingProvider

_RRF_K = 60
_MAX_LIMIT = 20
_MAX_QUERY_CHARS = 8_000
_MAX_FUSED_CANDIDATES = 96
_MAX_ENTITY_POSTING_CANDIDATES = 24
_MAX_RERANK_BUNDLES = 32
_MAX_RERANK_BUNDLE_CHARS = 2_048
_ENTITY_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_MODALITY_ORDER: tuple[CandidateSource, ...] = ("lexical", "vector")


class RecallIndex(Protocol):
    def document_vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        document_kind: RecallDocumentKind,
        limit: int,
    ) -> tuple[IndexCandidate, ...]: ...

    def document_lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        document_kind: RecallDocumentKind,
        limit: int,
    ) -> tuple[IndexCandidate, ...]: ...


class RecallState(Protocol):
    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...

    def list_episode_memories(
        self,
        *,
        repo_key: str,
        episode_id: str,
    ) -> tuple[CodingMemory, ...]: ...


class RecallEngine:
    """Retrieve both hierarchy levels, lift fact hits, and emit attributed context."""

    def __init__(
        self,
        *,
        index: RecallIndex,
        state: RecallState,
        embedder: EmbeddingProvider,
        reranker: RerankingProvider | None = None,
        planner_config: RecallPlannerConfig | None = None,
        retrieval_config_sha256: str | None = None,
        clock_ns: Callable[[], int] | None = None,
    ) -> None:
        self._index = index
        self._state = state
        self._embedder = embedder
        self._reranker = reranker
        self._planner = RecallPlanner(planner_config)
        self._retrieval_config_sha256 = retrieval_config_sha256
        self._clock_ns = clock_ns or time.perf_counter_ns

    def recall(self, query: str, *, repo_key: str, limit: int = 5) -> RecallResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Recall query must not be empty")
        if len(normalized_query) > _MAX_QUERY_CHARS:
            raise ValueError(f"Recall query exceeds {_MAX_QUERY_CHARS} characters")
        if not repo_key.strip():
            raise ValueError("repo_key must not be empty")
        if not 1 <= limit <= _MAX_LIMIT:
            raise ValueError(f"Recall limit must be between 1 and {_MAX_LIMIT}")

        started = self._clock_ns()
        plan = self._planner.plan(normalized_query, limit=limit)
        query_vector = self._embedder.embed_query(normalized_query)
        episode_vector = self._documents(
            repo_key=repo_key,
            document_kind="episode",
            source="episode_vector",
            vector=query_vector,
            query=None,
            limit=plan.episode_candidate_limit,
        )
        episode_lexical = self._documents(
            repo_key=repo_key,
            document_kind="episode",
            source="episode_lexical",
            vector=None,
            query=normalized_query,
            limit=plan.episode_candidate_limit,
        )
        atomic_vector: tuple[IndexCandidate, ...] = ()
        atomic_lexical: tuple[IndexCandidate, ...] = ()
        if plan.atomic_fact_candidate_limit:
            atomic_vector = self._documents(
                repo_key=repo_key,
                document_kind="atomic_fact",
                source="atomic_fact_vector",
                vector=query_vector,
                query=None,
                limit=plan.atomic_fact_candidate_limit,
            )
            atomic_lexical = self._documents(
                repo_key=repo_key,
                document_kind="atomic_fact",
                source="atomic_fact_lexical",
                vector=None,
                query=normalized_query,
                limit=plan.atomic_fact_candidate_limit,
            )

        ranked = self._fuse(
            repo_key=repo_key,
            sources=(
                ("episode_lexical", episode_lexical),
                ("episode_vector", episode_vector),
                ("atomic_fact_lexical", atomic_lexical),
                ("atomic_fact_vector", atomic_vector),
            ),
        )
        ranked, entity_posting_candidate_count = self._expand_entity_postings(
            ranked,
            repo_key=repo_key,
            anchors=plan.query_sketch.anchors,
        )
        ranked, _ = self._attach_snippets(
            ranked,
            repo_key=repo_key,
            expand_neighbors=False,
        )
        ranked = self._rerank(
            normalized_query,
            ranked,
            coverage_slots=plan.query_sketch.coverage_slots,
        )
        selected_ranked, covered_slots, missing_slots = _coverage_select(
            ranked,
            coverage_slots=plan.query_sketch.coverage_slots,
            limit=limit,
        )
        neighbor_expansion_count = 0
        if plan.expand_neighbors:
            selected_ranked, neighbor_expansion_count = self._attach_snippets(
                selected_ranked,
                repo_key=repo_key,
                expand_neighbors=True,
                neighbor_snippet_budget=plan.neighbor_snippet_budget,
            )
        selected = tuple(
            replace(item, rank=rank) for rank, item in enumerate(selected_ranked, start=1)
        )
        latency_ms = round((self._clock_ns() - started) / 1_000_000, 3)
        sidecar = RecallSidecar(
            query=normalized_query,
            repo_key=repo_key,
            limit=limit,
            latency_ms=latency_ms,
            vector_candidate_count=len(episode_vector) + len(atomic_vector),
            lexical_candidate_count=len(episode_lexical) + len(atomic_lexical),
            ranked=selected,
            reranker_model=None if self._reranker is None else self._reranker.model_id,
            reranker_source=None if self._reranker is None else self._reranker.source_id,
            reranker_revision=None if self._reranker is None else self._reranker.revision,
            embedding_model=self._embedder.model_id,
            embedding_source=self._embedder.source_id,
            embedding_revision=self._embedder.revision,
            retrieval_config_sha256=self._retrieval_config_sha256,
            recall_route=plan.route,
            episode_vector_candidate_count=len(episode_vector),
            episode_lexical_candidate_count=len(episode_lexical),
            atomic_fact_vector_candidate_count=len(atomic_vector),
            atomic_fact_lexical_candidate_count=len(atomic_lexical),
            neighbor_expansion_count=neighbor_expansion_count,
            entity_posting_candidate_count=entity_posting_candidate_count,
            rerank_bundle_count=len(ranked),
            query_anchors=plan.query_sketch.anchors,
            covered_slots=covered_slots,
            missing_slots=missing_slots,
            completion="partial" if missing_slots or not selected else "complete",
            degraded_stages=("no_candidates",) if not selected else (),
            query_vector_sha256=_vector_digest(query_vector),
        )
        return RecallResult(
            markdown=_render_context(normalized_query, repo_key=repo_key, ranked=selected),
            sidecar=sidecar,
        )

    def _documents(
        self,
        *,
        repo_key: str,
        document_kind: RecallDocumentKind,
        source: RecallDocumentSource,
        vector: tuple[float, ...] | None,
        query: str | None,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        if vector is not None:
            candidates = self._index.document_vector_candidates(
                repo_key=repo_key,
                vector=vector,
                document_kind=document_kind,
                limit=limit,
            )
        else:
            assert query is not None
            candidates = self._index.document_lexical_candidates(
                repo_key=repo_key,
                query=query,
                document_kind=document_kind,
                limit=limit,
            )
        return _safe_document_candidates(
            candidates,
            repo_key=repo_key,
            document_kind=document_kind,
            source=source,
        )

    def _fuse(
        self,
        *,
        repo_key: str,
        sources: tuple[
            tuple[RecallDocumentSource, tuple[IndexCandidate, ...]],
            ...,
        ],
    ) -> list[RankedRecall]:
        contributions: dict[str, float] = {}
        matches: dict[str, list[RecallMatch]] = {}
        vector_components: dict[str, tuple[float, int]] = {}
        lexical_components: dict[str, tuple[float, int]] = {}
        modality_sources: dict[str, set[CandidateSource]] = {}
        for source, candidates in sources:
            parent_candidates = _max_pool_by_parent(candidates)
            for parent_rank, candidate in enumerate(parent_candidates, start=1):
                memory_id = candidate.memory_id
                contributions[memory_id] = contributions.get(memory_id, 0.0) + 1.0 / (
                    _RRF_K + parent_rank
                )
                modality: CandidateSource = "vector" if source.endswith("_vector") else "lexical"
                modality_sources.setdefault(memory_id, set()).add(modality)
                component_map = vector_components if modality == "vector" else lexical_components
                prior = component_map.get(memory_id)
                if prior is None or parent_rank < prior[1]:
                    component_map[memory_id] = (round(candidate.score, 12), parent_rank)
            match_counts: dict[str, int] = {}
            per_parent_limit = (
                self._planner.config.matched_facts_per_memory
                if source.startswith("atomic_fact_")
                else 1
            )
            for document_rank, candidate in enumerate(candidates, start=1):
                if match_counts.get(candidate.memory_id, 0) >= per_parent_limit:
                    continue
                match_counts[candidate.memory_id] = match_counts.get(candidate.memory_id, 0) + 1
                matches.setdefault(candidate.memory_id, []).append(
                    RecallMatch(
                        document_id=candidate.document_id,
                        document_kind=candidate.document_kind,
                        source=source,
                        score=round(candidate.score, 12),
                        rank=document_rank,
                        fact_id=candidate.fact_id,
                    )
                )

        ranked: list[RankedRecall] = []
        for memory_id in sorted(contributions):
            memory = self._state.get_memory(repo_key=repo_key, memory_id=memory_id)
            if memory is None or memory.repo_key != repo_key or memory.content_sha256 is None:
                continue
            vector_component = vector_components.get(memory_id)
            lexical_component = lexical_components.get(memory_id)
            ranked.append(
                RankedRecall(
                    rank=0,
                    memory_id=memory.memory_id,
                    memory_type=memory.memory_type,
                    title=memory.title,
                    summary=memory.summary,
                    source_uri=_memory_uri(memory.memory_id),
                    content_sha256=memory.content_sha256,
                    candidate_sources=tuple(
                        source
                        for source in _MODALITY_ORDER
                        if source in modality_sources[memory_id]
                    ),
                    vector_score=None if vector_component is None else vector_component[0],
                    vector_rank=None if vector_component is None else vector_component[1],
                    lexical_score=None if lexical_component is None else lexical_component[0],
                    lexical_rank=None if lexical_component is None else lexical_component[1],
                    final_score=round(contributions[memory_id], 12),
                    evidence=_recall_evidence(memory),
                    matched_documents=tuple(
                        sorted(
                            matches[memory_id],
                            key=lambda item: (item.source, item.rank, item.document_id),
                        )
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item.final_score, item.memory_id))
        return ranked

    def _expand_entity_postings(
        self,
        ranked: list[RankedRecall],
        *,
        repo_key: str,
        anchors: tuple[str, ...],
    ) -> tuple[list[RankedRecall], int]:
        method = getattr(self._state, "find_entity_memories", None)
        if not anchors or not callable(method):
            return ranked[:_MAX_FUSED_CANDIDATES], 0
        memories = method(
            repo_key=repo_key,
            entity_keys=anchors,
            limit=_MAX_ENTITY_POSTING_CANDIDATES,
        )
        existing = {item.memory_id for item in ranked}
        seed_ids = set(existing)
        for memory in memories:
            if (
                memory.repo_key != repo_key
                or memory.memory_id in existing
                or memory.content_sha256 is None
            ):
                continue
            matched_facts = tuple(
                fact for fact in memory.facts if set(anchors) & _entity_terms(fact.text)
            )
            if not matched_facts:
                continue
            ranked.append(
                RankedRecall(
                    rank=0,
                    memory_id=memory.memory_id,
                    memory_type=memory.memory_type,
                    title=memory.title,
                    summary=memory.summary,
                    source_uri=_memory_uri(memory.memory_id),
                    content_sha256=memory.content_sha256,
                    candidate_sources=(),
                    vector_score=None,
                    vector_rank=None,
                    lexical_score=None,
                    lexical_rank=None,
                    final_score=0.0,
                    evidence=_recall_evidence(memory),
                    matched_documents=tuple(
                        RecallMatch(
                            document_id=f"entity:{fact.fact_id}",
                            document_kind="atomic_fact",
                            source="entity_posting",
                            score=1.0,
                            rank=rank,
                            fact_id=fact.fact_id,
                        )
                        for rank, fact in enumerate(matched_facts, start=1)
                    ),
                )
            )
            existing.add(memory.memory_id)
        bounded = ranked[:_MAX_FUSED_CANDIDATES]
        included_postings = sum(item.memory_id not in seed_ids for item in bounded)
        return bounded, included_postings

    def _attach_snippets(
        self,
        ranked: list[RankedRecall],
        *,
        repo_key: str,
        expand_neighbors: bool,
        neighbor_snippet_budget: int = 0,
    ) -> tuple[list[RankedRecall], int]:
        memory_map = {
            item.memory_id: memory
            for item in ranked
            if (memory := self._state.get_memory(repo_key=repo_key, memory_id=item.memory_id))
            is not None
        }
        episode_groups: dict[str, list[CodingMemory]] = {}
        if expand_neighbors:
            for memory in memory_map.values():
                if memory.episode_id in episode_groups:
                    continue
                group = list(
                    self._state.list_episode_memories(
                        repo_key=repo_key,
                        episode_id=memory.episode_id,
                    )
                )
                group.sort(key=_chronology_key)
                episode_groups[memory.episode_id] = group

        neighbor_count = 0
        remaining_neighbor_budget = neighbor_snippet_budget
        enriched: list[RankedRecall] = []
        for item in ranked:
            candidate_memory = memory_map.get(item.memory_id)
            if candidate_memory is None:
                enriched.append(item)
                continue
            snippets = _memory_snippets(
                candidate_memory,
                matched_fact_ids=tuple(
                    match.fact_id
                    for match in item.matched_documents
                    if match.document_kind == "atomic_fact" and match.fact_id
                ),
                matched_limit=self._planner.config.matched_facts_per_memory,
                sibling_limit=self._planner.config.sibling_facts_per_memory,
            )
            if expand_neighbors:
                neighbors = _neighbor_snippets(
                    candidate_memory,
                    group=episode_groups.get(candidate_memory.episode_id, []),
                    window=self._planner.config.neighbor_window,
                    facts_per_neighbor=self._planner.config.matched_facts_per_memory,
                )
                bounded_neighbors = neighbors[:remaining_neighbor_budget]
                remaining_neighbor_budget -= len(bounded_neighbors)
                snippets = _deduplicate_snippets((*snippets, *bounded_neighbors))
                neighbor_count += len(bounded_neighbors)
            enriched.append(replace(item, snippets=snippets))
        return enriched, neighbor_count

    def _rerank(
        self,
        query: str,
        ranked: list[RankedRecall],
        *,
        coverage_slots: tuple[str, ...],
    ) -> list[RankedRecall]:
        ranked, _covered, _missing = _coverage_select(
            ranked,
            coverage_slots=coverage_slots,
            limit=_MAX_RERANK_BUNDLES,
        )
        fusion_scores = {item.memory_id: item.final_score for item in ranked}
        if self._reranker is None:
            ranked.sort(key=lambda item: (-item.final_score, item.memory_id))
            return ranked
        documents = tuple(
            RerankDocument(
                memory_id=item.memory_id,
                text=_rerank_text(item),
                fusion_score=item.final_score,
            )
            for item in ranked
        )
        scores = self._reranker.rerank(query, documents)
        score_map = _validated_rerank_scores(scores, documents=documents)
        rescored = [
            replace(
                item,
                final_score=round(score_map[item.memory_id], 12),
                reranker_score=round(score_map[item.memory_id], 12),
            )
            for item in ranked
        ]
        rescored.sort(
            key=lambda item: (
                -item.final_score,
                -fusion_scores[item.memory_id],
                item.memory_id,
            )
        )
        return rescored


def _safe_document_candidates(
    candidates: tuple[IndexCandidate, ...],
    *,
    repo_key: str,
    document_kind: RecallDocumentKind,
    source: RecallDocumentSource,
) -> tuple[IndexCandidate, ...]:
    best: dict[str, IndexCandidate] = {}
    for candidate in candidates:
        if not isfinite(candidate.score):
            raise ValueError("Recall index returned a non-finite candidate score")
        if candidate.repo_key != repo_key:
            continue
        if candidate.document_kind != document_kind:
            raise ValueError(f"Recall index returned the wrong document kind for {source}")
        document_id = candidate.document_id or f"{document_kind}:{candidate.memory_id}"
        normalized = replace(candidate, document_id=document_id)
        prior = best.get(document_id)
        if prior is None or normalized.score > prior.score:
            best[document_id] = normalized
    return tuple(
        sorted(best.values(), key=lambda item: (-item.score, item.document_id, item.memory_id))
    )


def _coverage_select(
    ranked: list[RankedRecall],
    *,
    coverage_slots: tuple[str, ...],
    limit: int,
) -> tuple[list[RankedRecall], tuple[str, ...], tuple[str, ...]]:
    if not coverage_slots:
        return ranked[:limit], (), ()
    slot_sets = {
        item.memory_id: set(coverage_slots) & _entity_terms(_recall_search_text(item))
        for item in ranked
    }
    uncovered = set(coverage_slots)
    remaining = list(ranked)
    selected: list[RankedRecall] = []
    while remaining and len(selected) < limit:
        best = min(
            remaining,
            key=lambda item: (
                -len(slot_sets[item.memory_id] & uncovered),
                -item.final_score,
                item.memory_id,
            ),
        )
        if not (slot_sets[best.memory_id] & uncovered):
            break
        selected.append(best)
        uncovered.difference_update(slot_sets[best.memory_id])
        remaining.remove(best)
    for item in ranked:
        if len(selected) >= limit:
            break
        if item not in selected:
            selected.append(item)
    covered = tuple(slot for slot in coverage_slots if slot not in uncovered)
    missing = tuple(slot for slot in coverage_slots if slot in uncovered)
    return selected, covered, missing


def _recall_search_text(item: RankedRecall) -> str:
    return "\n".join(
        (
            item.title,
            item.summary,
            *(snippet.text for snippet in item.snippets),
        )
    )


def _entity_terms(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _ENTITY_TERM.finditer(text)}


def _max_pool_by_parent(candidates: tuple[IndexCandidate, ...]) -> tuple[IndexCandidate, ...]:
    best: dict[str, IndexCandidate] = {}
    for candidate in candidates:
        prior = best.get(candidate.memory_id)
        if (
            prior is None
            or candidate.score > prior.score
            or (candidate.score == prior.score and candidate.document_id < prior.document_id)
        ):
            best[candidate.memory_id] = candidate
    return tuple(
        sorted(best.values(), key=lambda item: (-item.score, item.memory_id, item.document_id))
    )


def _recall_evidence(memory: CodingMemory) -> tuple[RecallEvidence, ...]:
    return tuple(
        RecallEvidence(
            provider=item.provider,
            session_id=item.session_id,
            raw_event_sha256=item.raw_event_sha256,
            raw_event_index=item.raw_event_index,
            raw_event_type=item.raw_event_type,
            call_id=item.call_id,
        )
        for item in memory.evidence
    )


def _memory_snippets(
    memory: CodingMemory,
    *,
    matched_fact_ids: tuple[str, ...],
    matched_limit: int,
    sibling_limit: int,
) -> tuple[RecallSnippet, ...]:
    facts = {fact.fact_id: fact for fact in memory.facts}
    ordered_matched = tuple(dict.fromkeys(matched_fact_ids))[:matched_limit]
    snippets: list[RecallSnippet] = []
    sibling_ids: list[str] = []
    chronological_facts = sorted(memory.facts, key=_fact_chronology_key)
    fact_positions = {fact.fact_id: position for position, fact in enumerate(chronological_facts)}
    for fact_id in ordered_matched:
        if fact_id not in facts:
            continue
        snippets.append(_snippet(memory, fact_id=fact_id, relation="matched"))
        position = fact_positions[fact_id]
        # A conversational question is commonly followed by its answer. Prefer the
        # next fact, then the previous fact, before unrelated session siblings.
        for adjacent_position in (position + 1, position - 1):
            if not 0 <= adjacent_position < len(chronological_facts):
                continue
            adjacent_id = chronological_facts[adjacent_position].fact_id
            if adjacent_id not in ordered_matched and adjacent_id not in sibling_ids:
                sibling_ids.append(adjacent_id)
    if ordered_matched:
        sibling_ids.extend(
            fact.fact_id
            for fact in chronological_facts
            if fact.fact_id not in ordered_matched and fact.fact_id not in sibling_ids
        )
        snippets.extend(
            _snippet(memory, fact_id=fact_id, relation="sibling")
            for fact_id in sibling_ids[:sibling_limit]
        )
    return tuple(snippets)


def _fact_chronology_key(fact: EvidenceFact) -> tuple[int, str]:
    raw_event_index = min(
        (reference.raw_event_index for reference in fact.evidence),
        default=-1,
    )
    return raw_event_index, fact.fact_id


def _neighbor_snippets(
    memory: CodingMemory,
    *,
    group: list[CodingMemory],
    window: int,
    facts_per_neighbor: int,
) -> tuple[RecallSnippet, ...]:
    if window == 0 or len(group) < 2:
        return ()
    try:
        position = next(
            index for index, item in enumerate(group) if item.memory_id == memory.memory_id
        )
    except StopIteration:
        return ()
    start = max(0, position - window)
    stop = min(len(group), position + window + 1)
    return tuple(
        _snippet(neighbor, fact_id=fact.fact_id, relation="neighbor")
        for neighbor in group[start:stop]
        if neighbor.memory_id != memory.memory_id
        for fact in neighbor.facts[:facts_per_neighbor]
    )


def _snippet(
    memory: CodingMemory,
    *,
    fact_id: str,
    relation: RecallSnippetRelation,
) -> RecallSnippet:
    fact = next(item for item in memory.facts if item.fact_id == fact_id)
    raw_event_index = min(
        (reference.raw_event_index for reference in fact.evidence),
        default=None,
    )
    return RecallSnippet(
        relation=relation,
        source_memory_id=memory.memory_id,
        source_uri=_memory_uri(memory.memory_id),
        fact_id=fact.fact_id,
        text=fact.text,
        source_title=memory.title,
        source_summary=memory.summary,
        raw_event_index=raw_event_index,
    )


def _deduplicate_snippets(snippets: tuple[RecallSnippet, ...]) -> tuple[RecallSnippet, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[RecallSnippet] = []
    for snippet in snippets:
        key = (snippet.source_memory_id, snippet.fact_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(snippet)
    return tuple(result)


def _chronology_key(memory: CodingMemory) -> tuple[str, int, str]:
    session_id = min((reference.session_id for reference in memory.evidence), default="")
    raw_event_index = min(
        (reference.raw_event_index for reference in memory.evidence),
        default=-1,
    )
    return session_id, raw_event_index, memory.memory_id


def _rerank_text(item: RankedRecall) -> str:
    lines = [item.title]
    lines.extend(f"{snippet.relation}: {snippet.text}" for snippet in item.snippets)
    lines.append(_single_line(item.summary, limit=320))
    text = "\n".join(lines)
    if len(text) <= _MAX_RERANK_BUNDLE_CHARS:
        return text
    return text[: _MAX_RERANK_BUNDLE_CHARS - 1] + "…"


def _validated_rerank_scores(
    scores: tuple[RerankScore, ...],
    *,
    documents: tuple[RerankDocument, ...],
) -> dict[str, float]:
    expected = {document.memory_id for document in documents}
    observed: dict[str, float] = {}
    for score in scores:
        if score.memory_id in observed:
            raise ValueError("Reranker returned a duplicate memory ID")
        if score.memory_id not in expected:
            raise ValueError("Reranker returned an unknown memory ID")
        if not isfinite(score.score):
            raise ValueError("Reranker returned a non-finite score")
        observed[score.memory_id] = score.score
    if observed.keys() != expected:
        raise ValueError("Reranker did not score every candidate")
    return observed


def _render_context(
    query: str,
    *,
    repo_key: str,
    ranked: tuple[RankedRecall, ...],
) -> str:
    lines = [
        "# Recall Context",
        "",
        f"Task: {_single_line(query, limit=400)}",
        f"Repository: `{_single_line(repo_key, limit=200)}`",
    ]
    if not ranked:
        lines.extend(("", "No evidence-backed memory matched this task."))
        return "\n".join(lines) + "\n"
    for item in ranked:
        lines.extend(
            (
                "",
                f"## {item.rank}. {_single_line(item.title, limit=120)}",
                "",
                _single_line(item.summary, limit=240),
                "",
                f"- Type: `{item.memory_type}`",
                f"- Source: [{item.memory_id}]({item.source_uri})",
                f"- Evidence: {len(item.evidence)} cited raw event(s)",
            )
        )
        if item.snippets:
            lines.extend(("", "Evidence excerpts:"))
            for snippet in item.snippets:
                lines.append(
                    f"- {snippet.relation}: {_single_line(snippet.text, limit=360)} "
                    f"([{snippet.source_memory_id}]({snippet.source_uri}))"
                )
    return "\n".join(lines) + "\n"


def _memory_uri(memory_id: str) -> str:
    return f"codecairn://memory/{quote(memory_id, safe='')}"


def _single_line(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.replace("\x00", "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _vector_digest(vector: tuple[float, ...]) -> str:
    return hashlib.sha256(struct.pack(f"<{len(vector)}f", *vector)).hexdigest()
