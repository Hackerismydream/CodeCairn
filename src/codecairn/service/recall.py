from __future__ import annotations

import hashlib
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal, Protocol
from urllib.parse import quote

from codecairn.memory.context import (
    CONTEXT_DIRECT_MATCH_PRIOR,
    CONTEXT_RENDERER_ID,
    CONTEXT_TOKENIZER_ID,
    count_context_tokens,
)
from codecairn.memory.embedding import EmbeddingProvider
from codecairn.memory.episode import render_attributed_fact, render_episode
from codecairn.memory.evidence_selector import EvidenceSelector
from codecairn.memory.models import (
    CandidateSource,
    CodingMemory,
    EvidenceFact,
    IndexCandidate,
    RankedRecall,
    RecallContextSlotAttempt,
    RecallContextSlotTrace,
    RecallContextTrace,
    RecallDocumentKind,
    RecallDocumentSource,
    RecallEvidence,
    RecallMatch,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
    RecallSnippetRelation,
    RecallStageTrace,
    RerankDocument,
    RerankScore,
)
from codecairn.memory.recall_planner import (
    ContextEvidenceSlot,
    CoverageRequirement,
    EntityCoverageRequirement,
    ExpansionPlan,
    ProvenanceCoverageRequirement,
    RecallPlanner,
    RecallPlannerConfig,
    RelationCoverageRequirement,
    SetCoverageRequirement,
    TemporalCoverageRequirement,
)
from codecairn.memory.reranking import RerankingProvider

_RRF_K = 60
_MAX_LIMIT = 20
_MAX_QUERY_CHARS = 8_000
_MAX_FUSED_CANDIDATES = 96
_MAX_ENTITY_POSTING_CANDIDATES = 24
_MAX_ENTITY_LEXICAL_CANDIDATES = 32
_MAX_TEMPORAL_LEXICAL_CANDIDATES = 32
_MAX_RERANK_BUNDLE_CHARS = 2_048
_ENTITY_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_ISO_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_CONTEXT_QUANTITY_TRANSITION = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|another|\d+(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)
_CONTEXT_ANAPHORIC_QUANTITY = re.compile(
    r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|\d+(?:st|nd|rd|th))\s+(?:one|it|this|that)\b",
    re.IGNORECASE,
)
_CONTEXT_VOCATIVE = re.compile(
    r"^\s*(?:hey|hi|hello)\s+([A-Za-z][A-Za-z'-]{1,31})\b",
    re.IGNORECASE,
)
_CONTEXT_EXCLUSIVITY = re.compile(
    r"\b(all that|only|nothing but|no one|nobody|alone|lonely)\b",
    re.IGNORECASE,
)
_CONTEXT_AFFECT = re.compile(
    r"\b(happiness|happy|joy|friend|date|meet)\w*\b",
    re.IGNORECASE,
)
_ContextAdmissionOutcome = Literal[
    "admitted",
    "budget",
    "duplicate",
    "parent_limit",
]
_PROVENANCE_TERMS: dict[str, frozenset[str]] = {
    "failure": frozenset({"error", "fail", "failed", "failure", "timeout"}),
    "change": frozenset({"change", "changed", "fix", "fixed", "patch", "patched"}),
    "verification": frozenset({"pass", "passed", "verify", "verified", "success"}),
}
_QUERY_STOPWORDS = frozenset(
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
        limit: int,
    ) -> tuple[CodingMemory, ...]: ...

    def list_adjacent_memories(
        self,
        *,
        repo_key: str,
        memory_id: str,
        adjacency_group_id: str,
        adjacency_index: int,
        window: int,
        limit: int,
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
        entity_expansion_limit, temporal_expansion_limit, provenance_expansion_limit = (
            _expansion_lane_limits(
                plan.query_sketch.coverage_requirements,
                expansion_plan=plan.expansion_plan,
            )
        )
        entity_lexical_budget = (entity_expansion_limit + 1) // 2
        episode_entity_limit, atomic_entity_limit = _split_hierarchy_budget(
            entity_lexical_budget,
            atomic_enabled=plan.atomic_fact_candidate_limit > 0,
        )
        episode_temporal_limit, atomic_temporal_limit = _split_hierarchy_budget(
            temporal_expansion_limit,
            atomic_enabled=plan.atomic_fact_candidate_limit > 0,
        )
        query_fact_cache: dict[str, tuple[str, ...]] = {}
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
        entity_lexical_query = next(
            (
                variant.text
                for variant in plan.query_sketch.query_variants
                if variant.kind == "entity" and variant.text != normalized_query
            ),
            None,
        )
        episode_entity_lexical: tuple[IndexCandidate, ...] = ()
        if entity_lexical_query is not None and episode_entity_limit:
            episode_entity_lexical = self._documents(
                repo_key=repo_key,
                document_kind="episode",
                source="episode_entity_lexical",
                vector=None,
                query=entity_lexical_query,
                limit=min(episode_entity_limit, _MAX_ENTITY_LEXICAL_CANDIDATES),
            )
        temporal_lexical_query = _temporal_lexical_query(
            plan.query_sketch.temporal_prefixes,
            plan.query_sketch.anchors,
        )
        episode_temporal_lexical: tuple[IndexCandidate, ...] = ()
        if temporal_lexical_query is not None and episode_temporal_limit:
            episode_temporal_lexical = self._documents(
                repo_key=repo_key,
                document_kind="episode",
                source="episode_temporal_lexical",
                vector=None,
                query=temporal_lexical_query,
                limit=min(episode_temporal_limit, _MAX_TEMPORAL_LEXICAL_CANDIDATES),
            )
        atomic_vector: tuple[IndexCandidate, ...] = ()
        atomic_lexical: tuple[IndexCandidate, ...] = ()
        atomic_entity_lexical: tuple[IndexCandidate, ...] = ()
        atomic_temporal_lexical: tuple[IndexCandidate, ...] = ()
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
            if entity_lexical_query is not None and atomic_entity_limit:
                atomic_entity_lexical = self._documents(
                    repo_key=repo_key,
                    document_kind="atomic_fact",
                    source="atomic_fact_entity_lexical",
                    vector=None,
                    query=entity_lexical_query,
                    limit=min(atomic_entity_limit, _MAX_ENTITY_LEXICAL_CANDIDATES),
                )
            if temporal_lexical_query is not None and atomic_temporal_limit:
                atomic_temporal_lexical = self._documents(
                    repo_key=repo_key,
                    document_kind="atomic_fact",
                    source="atomic_fact_temporal_lexical",
                    vector=None,
                    query=temporal_lexical_query,
                    limit=min(atomic_temporal_limit, _MAX_TEMPORAL_LEXICAL_CANDIDATES),
                )

        sources: tuple[tuple[RecallDocumentSource, tuple[IndexCandidate, ...]], ...] = (
            ("episode_lexical", episode_lexical),
            ("episode_entity_lexical", episode_entity_lexical),
            ("episode_temporal_lexical", episode_temporal_lexical),
            ("episode_vector", episode_vector),
            ("atomic_fact_lexical", atomic_lexical),
            ("atomic_fact_entity_lexical", atomic_entity_lexical),
            ("atomic_fact_temporal_lexical", atomic_temporal_lexical),
            ("atomic_fact_vector", atomic_vector),
        )
        candidate_input_count = sum(len(candidates) for _source, candidates in sources)
        candidate_memory_ids = tuple(
            dict.fromkeys(
                candidate.memory_id for _source, candidates in sources for candidate in candidates
            )
        )
        core_ranked = self._fuse(
            repo_key=repo_key,
            sources=(
                ("episode_lexical", episode_lexical[: plan.core_episode_candidate_limit]),
                (
                    "episode_entity_lexical",
                    episode_entity_lexical[: plan.core_episode_candidate_limit],
                ),
                ("episode_vector", episode_vector[: plan.core_episode_candidate_limit]),
                (
                    "atomic_fact_lexical",
                    atomic_lexical[: plan.core_atomic_fact_candidate_limit],
                ),
                (
                    "atomic_fact_entity_lexical",
                    atomic_entity_lexical[: plan.core_atomic_fact_candidate_limit],
                ),
                (
                    "atomic_fact_vector",
                    atomic_vector[: plan.core_atomic_fact_candidate_limit],
                ),
            ),
        )
        core_ranked, _core_neighbor_count = self._attach_snippets(
            core_ranked,
            repo_key=repo_key,
            query=normalized_query,
            query_fact_cache=query_fact_cache,
            expand_neighbors=False,
        )
        core_ranked, _core_covered, _core_missing = _coverage_select(
            core_ranked,
            coverage_slots=plan.query_sketch.coverage_slots,
            coverage_requirements=plan.query_sketch.coverage_requirements,
            limit=plan.core_rerank_candidate_limit,
        )
        core_memory_ids = {item.memory_id for item in core_ranked}

        ranked = self._fuse(
            repo_key=repo_key,
            sources=sources,
        )
        entity_lexical_candidate_count = len(episode_entity_lexical) + len(atomic_entity_lexical)
        entity_posting_candidate_count = 0
        provenance_expansion_count = 0
        if self._planner.config.mode != "episode-only":
            ranked, entity_posting_candidate_count = self._expand_entity_postings(
                ranked,
                repo_key=repo_key,
                query=normalized_query,
                query_fact_cache=query_fact_cache,
                anchors=plan.query_sketch.anchors,
                limit=max(0, entity_expansion_limit - entity_lexical_candidate_count),
            )
            ranked, provenance_expansion_count = self._expand_provenance_postings(
                ranked,
                repo_key=repo_key,
                requirements=plan.query_sketch.coverage_requirements,
                limit=provenance_expansion_limit,
            )
        ranked, _ = self._attach_snippets(
            ranked,
            repo_key=repo_key,
            query=normalized_query,
            query_fact_cache=query_fact_cache,
            expand_neighbors=False,
        )
        fused_memory_ids = tuple(item.memory_id for item in ranked)
        ranked = self._rerank(
            normalized_query,
            ranked,
            coverage_slots=plan.query_sketch.coverage_slots,
            coverage_requirements=plan.query_sketch.coverage_requirements,
            candidate_limit=plan.rerank_candidate_limit,
        )
        reranked_memory_ids = tuple(item.memory_id for item in ranked)
        selected_ranked, covered_slots, missing_slots = _core_preserving_select(
            ranked,
            core_memory_ids=core_memory_ids,
            coverage_slots=plan.query_sketch.coverage_slots,
            coverage_requirements=plan.query_sketch.coverage_requirements,
            temporal_prefixes=plan.query_sketch.temporal_prefixes,
            limit=limit,
            exploration_limit=plan.exploration_result_limit,
        )
        if plan.query_sketch.temporal_prefixes:
            temporal_snippet_priority_ids = {
                item.memory_id
                for item in selected_ranked
                if _matches_temporal_prefix(item, plan.query_sketch.temporal_prefixes)
            }
        elif plan.query_sketch.temporal_op != "none":
            temporal_snippet_priority_ids = {
                item.memory_id
                for item in selected_ranked[: self._planner.config.maximum_exploration_results]
            }
        else:
            temporal_snippet_priority_ids = set()
        neighbor_expansion_count = 0
        if plan.expand_neighbors:
            temporal_exploration_ids = {
                item.memory_id
                for item in selected_ranked
                if item.memory_id not in core_memory_ids
                and _matches_temporal_prefix(item, plan.query_sketch.temporal_prefixes)
            }
            expansion_before_neighbors = (
                entity_lexical_candidate_count
                + entity_posting_candidate_count
                + len(episode_temporal_lexical)
                + len(atomic_temporal_lexical)
                + provenance_expansion_count
            )
            selected_ranked, neighbor_expansion_count = self._attach_snippets(
                selected_ranked,
                repo_key=repo_key,
                query=normalized_query,
                query_fact_cache=query_fact_cache,
                expand_neighbors=True,
                neighbor_window=plan.neighbor_window,
                neighbor_snippet_budget=min(
                    plan.neighbor_snippet_budget,
                    max(0, plan.expansion_plan.max_total_facts - expansion_before_neighbors),
                ),
                priority_memory_ids=temporal_exploration_ids,
                wide_sibling_memory_ids=temporal_snippet_priority_ids,
            )
        if self._reranker is not None and getattr(self._reranker, "batch_size", None) is not None:
            selected_memories = {
                item.memory_id: memory
                for item in selected_ranked
                if (
                    memory := self._state.get_memory(
                        repo_key=repo_key,
                        memory_id=item.memory_id,
                    )
                )
                is not None
            }
            selected_ranked = list(
                EvidenceSelector(
                    reranker=self._reranker,
                    max_candidates=self._planner.config.fact_rerank_max_candidates,
                    max_candidates_per_parent=(
                        self._planner.config.fact_rerank_max_candidates_per_parent
                    ),
                    max_selected_per_parent=(
                        self._planner.config.fact_rerank_max_selected_per_parent
                    ),
                    max_document_chars=self._planner.config.fact_rerank_max_document_chars,
                ).select(
                    normalized_query,
                    ranked=tuple(selected_ranked),
                    memories=selected_memories,
                )
            )
        selected = tuple(
            replace(item, rank=rank) for rank, item in enumerate(selected_ranked, start=1)
        )
        selected_memory_ids = tuple(item.memory_id for item in selected)
        rendered_context = _compile_context(
            normalized_query,
            repo_key=repo_key,
            ranked=selected,
            temporal_priority_memory_ids=temporal_snippet_priority_ids,
            config=self._planner.config,
            wants_procedure=plan.query_sketch.wants_procedure,
            evidence_slots=plan.query_sketch.evidence_slots,
        )
        rendered_terms = _entity_terms(rendered_context.evidence_text)
        covered_slots = tuple(
            slot for slot in plan.query_sketch.coverage_slots if slot in rendered_terms
        )
        missing_slots = tuple(
            slot for slot in plan.query_sketch.coverage_slots if slot not in rendered_terms
        )
        covered_requirements, missing_requirements = _requirement_coverage(
            rendered_context.evidence_text,
            requirements=plan.query_sketch.coverage_requirements,
        )
        # Full parent transcripts are renderer working state, not audit metadata. Keeping
        # them in every ranked sidecar would duplicate the same long episode for every
        # question and can dominate LoCoMo memory and artifact size.
        hydrated_episode_ids = set(rendered_context.hydrated_episode_ids)
        audited_selected = tuple(
            replace(
                item,
                episode_text="",
                snippets=(
                    _deduplicate_snippets((*item.snippets, *item.episode_snippets))
                    if item.memory_id in hydrated_episode_ids
                    else item.snippets
                ),
                episode_snippets=(),
            )
            for item in selected
        )
        latency_ms = round((self._clock_ns() - started) / 1_000_000, 3)
        sidecar = RecallSidecar(
            query=normalized_query,
            repo_key=repo_key,
            limit=limit,
            latency_ms=latency_ms,
            vector_candidate_count=len(episode_vector) + len(atomic_vector),
            lexical_candidate_count=(
                len(episode_lexical)
                + len(episode_entity_lexical)
                + len(atomic_lexical)
                + len(atomic_entity_lexical)
                + len(episode_temporal_lexical)
                + len(atomic_temporal_lexical)
            ),
            ranked=audited_selected,
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
            episode_temporal_lexical_candidate_count=len(episode_temporal_lexical),
            atomic_fact_temporal_lexical_candidate_count=len(atomic_temporal_lexical),
            episode_entity_lexical_candidate_count=len(episode_entity_lexical),
            atomic_fact_entity_lexical_candidate_count=len(atomic_entity_lexical),
            neighbor_expansion_count=neighbor_expansion_count,
            entity_posting_candidate_count=entity_posting_candidate_count,
            rerank_bundle_count=len(ranked),
            query_anchors=plan.query_sketch.anchors,
            query_temporal_prefixes=plan.query_sketch.temporal_prefixes,
            query_sketcher_id=plan.query_sketch.sketcher_id,
            covered_slots=covered_slots,
            missing_slots=missing_slots,
            covered_requirements=covered_requirements,
            missing_requirements=missing_requirements,
            expansion_fact_count=(
                entity_lexical_candidate_count
                + entity_posting_candidate_count
                + len(episode_temporal_lexical)
                + len(atomic_temporal_lexical)
                + provenance_expansion_count
                + neighbor_expansion_count
            ),
            expansion_fact_limit=plan.expansion_plan.max_total_facts,
            provenance_expansion_count=provenance_expansion_count,
            completion=(
                "partial"
                if (
                    missing_slots
                    or missing_requirements
                    or not selected
                    or rendered_context.dropped_episode_ids
                )
                else "complete"
            ),
            degraded_stages=(
                ("no_candidates",)
                if not selected
                else (("context_budget",) if rendered_context.dropped_episode_ids else ())
            ),
            query_vector_sha256=_vector_digest(query_vector),
            neighbor_window=plan.neighbor_window if plan.expand_neighbors else 0,
            hydrated_episode_count=len(rendered_context.hydrated_episode_ids),
            hydrated_episode_ids=rendered_context.hydrated_episode_ids,
            partial_episode_ids=rendered_context.partial_episode_ids,
            dropped_episode_ids=rendered_context.dropped_episode_ids,
            stage_trace=(
                RecallStageTrace(
                    stage="candidate_recall",
                    input_count=candidate_input_count,
                    output_count=len(candidate_memory_ids),
                    output_memory_ids=candidate_memory_ids,
                ),
                RecallStageTrace(
                    stage="fusion",
                    input_count=len(candidate_memory_ids),
                    output_count=len(fused_memory_ids),
                    output_memory_ids=fused_memory_ids,
                ),
                RecallStageTrace(
                    stage="rerank",
                    input_count=len(fused_memory_ids),
                    output_count=len(reranked_memory_ids),
                    output_memory_ids=reranked_memory_ids,
                ),
                RecallStageTrace(
                    stage="selection",
                    input_count=len(reranked_memory_ids),
                    output_count=len(selected_memory_ids),
                    output_memory_ids=selected_memory_ids,
                ),
                RecallStageTrace(
                    stage="context",
                    input_count=len(selected_memory_ids),
                    output_count=(
                        0
                        if rendered_context.trace is None
                        else len(rendered_context.trace.rendered_memory_ids)
                    ),
                    output_memory_ids=(
                        ()
                        if rendered_context.trace is None
                        else rendered_context.trace.rendered_memory_ids
                    ),
                ),
            ),
            context_trace=rendered_context.trace,
        )
        return RecallResult(
            markdown=rendered_context.markdown,
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
        )[:limit]

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
                    episode_text=_episode_text(memory),
                    episode_fact_ids=tuple(fact.fact_id for fact in memory.facts),
                    episode_snippets=_episode_snippets(memory),
                )
            )
        ranked.sort(key=lambda item: (-item.final_score, item.memory_id))
        return ranked

    def _expand_entity_postings(
        self,
        ranked: list[RankedRecall],
        *,
        repo_key: str,
        query: str,
        query_fact_cache: dict[str, tuple[str, ...]],
        anchors: tuple[str, ...],
        limit: int,
    ) -> tuple[list[RankedRecall], int]:
        method = getattr(self._state, "find_entity_memories", None)
        if not anchors or limit <= 0 or not callable(method):
            return ranked[:_MAX_FUSED_CANDIDATES], 0
        memories = method(
            repo_key=repo_key,
            entity_keys=anchors,
            limit=min(limit, _MAX_ENTITY_POSTING_CANDIDATES),
        )
        existing = {item.memory_id for item in ranked}
        remaining = limit
        expansion_count = 0
        for memory in memories:
            if remaining == 0:
                break
            if memory.repo_key != repo_key or memory.content_sha256 is None:
                continue
            prior = next(
                (item for item in ranked if item.memory_id == memory.memory_id),
                None,
            )
            existing_snippets = (
                ()
                if prior is None
                else _memory_snippets(
                    memory,
                    query=query,
                    query_fact_cache=query_fact_cache,
                    matches=prior.matched_documents,
                    matched_limit=self._planner.config.matched_facts_per_memory,
                    diverse_matched_limit=(self._planner.config.diverse_matched_facts_per_memory),
                    sibling_limit=self._planner.config.sibling_facts_per_memory,
                    wide_sibling_window=False,
                )
            )
            matched_facts = _entity_posting_facts(
                memory,
                anchors=anchors,
                existing_snippets=existing_snippets,
            )[:remaining]
            if not matched_facts:
                continue
            if memory.memory_id not in existing and len(ranked) >= _MAX_FUSED_CANDIDATES:
                continue
            posting_matches = tuple(
                RecallMatch(
                    document_id=f"entity:{fact.fact_id}",
                    document_kind="atomic_fact",
                    source="entity_posting",
                    score=1.0,
                    rank=rank,
                    fact_id=fact.fact_id,
                )
                for rank, fact in enumerate(matched_facts, start=1)
            )
            expansion_count += len(posting_matches)
            remaining -= len(posting_matches)
            if memory.memory_id in existing:
                position = next(
                    index for index, item in enumerate(ranked) if item.memory_id == memory.memory_id
                )
                prior = ranked[position]
                ranked[position] = replace(
                    prior,
                    matched_documents=_merge_recall_matches(
                        prior.matched_documents,
                        posting_matches,
                    ),
                )
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
                    matched_documents=posting_matches,
                    episode_text=_episode_text(memory),
                    episode_fact_ids=tuple(fact.fact_id for fact in memory.facts),
                    episode_snippets=_episode_snippets(memory),
                )
            )
            existing.add(memory.memory_id)
        return ranked[:_MAX_FUSED_CANDIDATES], expansion_count

    def _expand_provenance_postings(
        self,
        ranked: list[RankedRecall],
        *,
        repo_key: str,
        requirements: tuple[CoverageRequirement, ...],
        limit: int,
    ) -> tuple[list[RankedRecall], int]:
        stages = _required_provenance_stages(requirements)
        if not stages or limit <= 0:
            return ranked, 0
        remaining = limit
        expansion_count = 0
        for position, item in enumerate(ranked):
            if remaining == 0:
                break
            memory = self._state.get_memory(repo_key=repo_key, memory_id=item.memory_id)
            if memory is None or memory.repo_key != repo_key:
                continue
            facts = _provenance_posting_facts(memory, stages=stages)[:remaining]
            if not facts:
                continue
            matches = tuple(
                RecallMatch(
                    document_id=f"provenance:{fact.fact_id}",
                    document_kind="atomic_fact",
                    source="provenance_posting",
                    score=1.0,
                    rank=expansion_count + rank,
                    fact_id=fact.fact_id,
                )
                for rank, fact in enumerate(facts, start=1)
            )
            ranked[position] = replace(
                item,
                matched_documents=_merge_recall_matches(item.matched_documents, matches),
            )
            expansion_count += len(matches)
            remaining -= len(matches)
        return ranked, expansion_count

    def _attach_snippets(
        self,
        ranked: list[RankedRecall],
        *,
        repo_key: str,
        expand_neighbors: bool,
        query: str = "",
        query_fact_cache: dict[str, tuple[str, ...]] | None = None,
        neighbor_window: int | None = None,
        neighbor_snippet_budget: int = 0,
        priority_memory_ids: set[str] | None = None,
        wide_sibling_memory_ids: set[str] | None = None,
    ) -> tuple[list[RankedRecall], int]:
        memory_map = {
            item.memory_id: memory
            for item in ranked
            if (memory := self._state.get_memory(repo_key=repo_key, memory_id=item.memory_id))
            is not None
        }
        resolved_neighbor_window = (
            self._planner.config.neighbor_window if neighbor_window is None else neighbor_window
        )
        neighbor_groups: dict[str, list[CodingMemory]] = {}
        if expand_neighbors and neighbor_snippet_budget > 0:
            for memory in memory_map.values():
                if memory.adjacency_group_id is not None and memory.adjacency_index is not None:
                    group = [
                        memory,
                        *self._state.list_adjacent_memories(
                            repo_key=repo_key,
                            memory_id=memory.memory_id,
                            adjacency_group_id=memory.adjacency_group_id,
                            adjacency_index=memory.adjacency_index,
                            window=resolved_neighbor_window,
                            limit=max(
                                1,
                                min(
                                    neighbor_snippet_budget,
                                    resolved_neighbor_window * 2,
                                ),
                            ),
                        ),
                    ]
                else:
                    group = list(
                        self._state.list_episode_memories(
                            repo_key=repo_key,
                            episode_id=memory.episode_id,
                            limit=max(1, neighbor_snippet_budget + 1),
                        )
                    )
                group.sort(key=_chronology_key)
                neighbor_groups[memory.memory_id] = group

        allocated_neighbors: dict[str, tuple[RecallSnippet, ...]] = {}
        if expand_neighbors:
            priorities = priority_memory_ids or set()
            allocation_order = sorted(
                ranked,
                key=lambda item: item.memory_id not in priorities,
            )
            remaining_neighbor_budget = neighbor_snippet_budget
            for item in allocation_order:
                candidate_memory = memory_map.get(item.memory_id)
                if candidate_memory is None or remaining_neighbor_budget == 0:
                    continue
                neighbors = _neighbor_snippets(
                    candidate_memory,
                    group=neighbor_groups.get(candidate_memory.memory_id, []),
                    window=resolved_neighbor_window,
                    facts_per_neighbor=self._planner.config.matched_facts_per_memory,
                )
                bounded = neighbors[:remaining_neighbor_budget]
                allocated_neighbors[item.memory_id] = bounded
                remaining_neighbor_budget -= len(bounded)

        neighbor_count = sum(len(items) for items in allocated_neighbors.values())
        enriched: list[RankedRecall] = []
        for item in ranked:
            candidate_memory = memory_map.get(item.memory_id)
            if candidate_memory is None:
                enriched.append(item)
                continue
            snippets = _memory_snippets(
                candidate_memory,
                query=query,
                query_fact_cache=query_fact_cache,
                matches=item.matched_documents,
                matched_limit=self._planner.config.matched_facts_per_memory,
                diverse_matched_limit=(self._planner.config.diverse_matched_facts_per_memory),
                sibling_limit=(
                    self._planner.config.temporal_sibling_facts_per_memory
                    if item.memory_id in (wide_sibling_memory_ids or set())
                    else self._planner.config.sibling_facts_per_memory
                ),
                wide_sibling_window=item.memory_id in (wide_sibling_memory_ids or set()),
            )
            if expand_neighbors:
                snippets = _deduplicate_snippets(
                    (*snippets, *allocated_neighbors.get(item.memory_id, ()))
                )
            enriched.append(replace(item, snippets=snippets))
        return enriched, neighbor_count

    def _rerank(
        self,
        query: str,
        ranked: list[RankedRecall],
        *,
        coverage_slots: tuple[str, ...],
        coverage_requirements: tuple[CoverageRequirement, ...],
        candidate_limit: int,
    ) -> list[RankedRecall]:
        ranked, _covered, _missing = _coverage_select(
            ranked,
            coverage_slots=coverage_slots,
            coverage_requirements=coverage_requirements,
            limit=candidate_limit,
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


def _expansion_lane_limits(
    requirements: tuple[CoverageRequirement, ...],
    *,
    expansion_plan: ExpansionPlan,
) -> tuple[int, int, int]:
    remaining = expansion_plan.max_total_facts
    has_entity = any(isinstance(item, EntityCoverageRequirement) for item in requirements)
    has_temporal = any(
        isinstance(item, TemporalCoverageRequirement)
        or (isinstance(item, RelationCoverageRequirement) and item.relation == "temporal_order")
        for item in requirements
    )
    has_provenance = any(
        isinstance(item, ProvenanceCoverageRequirement)
        or (isinstance(item, RelationCoverageRequirement) and item.relation == "procedure_order")
        for item in requirements
    )
    entity_limit = min(expansion_plan.max_entity_facts, remaining) if has_entity else 0
    remaining -= entity_limit
    temporal_limit = min(expansion_plan.max_time_facts, remaining) if has_temporal else 0
    remaining -= temporal_limit
    provenance_limit = min(expansion_plan.max_provenance_facts, remaining) if has_provenance else 0
    return entity_limit, temporal_limit, provenance_limit


def _split_hierarchy_budget(total: int, *, atomic_enabled: bool) -> tuple[int, int]:
    if total <= 0:
        return 0, 0
    if not atomic_enabled:
        return total, 0
    atomic = total // 2
    return total - atomic, atomic


def _requirement_key(requirement: CoverageRequirement) -> str:
    if isinstance(requirement, EntityCoverageRequirement):
        return f"entity:{requirement.entity_key}"
    if isinstance(requirement, TemporalCoverageRequirement):
        prefixes = ",".join(requirement.prefixes) or "any"
        return f"temporal:{requirement.operation}:{prefixes}"
    if isinstance(requirement, SetCoverageRequirement):
        return f"set:{requirement.operation}:{','.join(requirement.members)}"
    if isinstance(requirement, RelationCoverageRequirement):
        return f"relation:{requirement.relation}"
    return f"provenance:{','.join(requirement.stages)}"


def _requirement_coverage(
    text: str,
    *,
    requirements: tuple[CoverageRequirement, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    terms = _entity_terms(text)
    timestamp_count = len(_ISO_TIMESTAMP.findall(text))
    covered: list[str] = []
    missing: list[str] = []
    for requirement in requirements:
        satisfied = False
        if isinstance(requirement, EntityCoverageRequirement):
            satisfied = requirement.entity_key in terms
        elif isinstance(requirement, TemporalCoverageRequirement):
            satisfied = (
                any(prefix in text for prefix in requirement.prefixes)
                if requirement.prefixes
                else timestamp_count > 0
            )
            if requirement.operation == "order":
                satisfied = satisfied and (
                    timestamp_count >= 2 or bool({"before", "after"} & terms)
                )
        elif isinstance(requirement, SetCoverageRequirement):
            satisfied = all(member in terms for member in requirement.members)
        elif isinstance(requirement, RelationCoverageRequirement):
            if requirement.relation == "temporal_order":
                satisfied = timestamp_count >= 2 or bool({"before", "after"} & terms)
            else:
                stage_hits = sum(
                    bool(stage_terms & terms) for stage_terms in _PROVENANCE_TERMS.values()
                )
                satisfied = stage_hits >= 2
        elif isinstance(requirement, ProvenanceCoverageRequirement):
            satisfied = all(bool(_PROVENANCE_TERMS[stage] & terms) for stage in requirement.stages)
        key = _requirement_key(requirement)
        (covered if satisfied else missing).append(key)
    return tuple(covered), tuple(missing)


def _coverage_select(
    ranked: list[RankedRecall],
    *,
    coverage_slots: tuple[str, ...],
    coverage_requirements: tuple[CoverageRequirement, ...] = (),
    required_keys: tuple[str, ...] | None = None,
    initial_text: str = "",
    limit: int,
) -> tuple[list[RankedRecall], tuple[str, ...], tuple[str, ...]]:
    requirement_keys = tuple(_requirement_key(item) for item in coverage_requirements)
    requested = (
        tuple(dict.fromkeys((*coverage_slots, *requirement_keys)))
        if required_keys is None
        else tuple(dict.fromkeys(required_keys))
    )
    if not requested:
        return ranked[:limit], (), ()
    remaining = list(ranked)
    selected: list[RankedRecall] = []
    bundle_parts = [initial_text] if initial_text else []
    progress, covered_set = _coverage_progress(
        initial_text,
        coverage_slots=coverage_slots,
        requirements=coverage_requirements,
    )
    while remaining and len(selected) < limit:
        choices: list[tuple[int, int, float, str, RankedRecall, dict[str, int], set[str], str]] = []
        for item in remaining:
            item_text = _recall_search_text(item)
            candidate_text = "\n".join((*bundle_parts, item_text))
            candidate_progress, candidate_covered = _coverage_progress(
                candidate_text,
                coverage_slots=coverage_slots,
                requirements=coverage_requirements,
            )
            gain = sum(
                max(0, candidate_progress.get(key, 0) - progress.get(key, 0)) for key in requested
            )
            completed_gain = len((candidate_covered - covered_set) & set(requested))
            choices.append(
                (
                    gain,
                    completed_gain,
                    item.final_score,
                    item.memory_id,
                    item,
                    candidate_progress,
                    candidate_covered,
                    item_text,
                )
            )
        best = min(choices, key=lambda choice: (-choice[0], -choice[1], -choice[2], choice[3]))
        if best[0] <= 0:
            break
        selected.append(best[4])
        progress = best[5]
        covered_set = best[6]
        bundle_parts.append(best[7])
        remaining.remove(best[4])
    for item in ranked:
        if len(selected) >= limit:
            break
        if item not in selected:
            selected.append(item)
    final_text = "\n".join(
        (
            *((initial_text,) if initial_text else ()),
            *(_recall_search_text(item) for item in selected),
        )
    )
    _progress, covered_set = _coverage_progress(
        final_text,
        coverage_slots=coverage_slots,
        requirements=coverage_requirements,
    )
    covered = tuple(key for key in requested if key in covered_set)
    missing = tuple(key for key in requested if key not in covered_set)
    return selected, covered, missing


def _coverage_progress(
    text: str,
    *,
    coverage_slots: tuple[str, ...],
    requirements: tuple[CoverageRequirement, ...],
) -> tuple[dict[str, int], set[str]]:
    terms = _entity_terms(text)
    timestamp_count = len(_ISO_TIMESTAMP.findall(text))
    progress = {slot: int(slot in terms) for slot in coverage_slots}
    covered = {slot for slot in coverage_slots if slot in terms}
    requirement_covered, _missing = _requirement_coverage(text, requirements=requirements)
    covered.update(requirement_covered)
    for requirement in requirements:
        key = _requirement_key(requirement)
        if isinstance(requirement, EntityCoverageRequirement):
            value = int(requirement.entity_key in terms)
        elif isinstance(requirement, TemporalCoverageRequirement):
            prefix_hit = int(any(prefix in text for prefix in requirement.prefixes))
            if requirement.operation == "order":
                order_progress = min(timestamp_count, 2)
                if {"before", "after"} & terms:
                    order_progress = 2
                value = order_progress + (prefix_hit if requirement.prefixes else 0)
            elif requirement.prefixes:
                value = prefix_hit
            else:
                value = min(timestamp_count, 1)
        elif isinstance(requirement, SetCoverageRequirement):
            value = sum(member in terms for member in requirement.members)
        elif isinstance(requirement, RelationCoverageRequirement):
            if requirement.relation == "temporal_order":
                value = min(timestamp_count, 2)
                if {"before", "after"} & terms:
                    value = 2
            else:
                value = min(
                    2,
                    sum(bool(stage_terms & terms) for stage_terms in _PROVENANCE_TERMS.values()),
                )
        else:
            value = sum(bool(_PROVENANCE_TERMS[stage] & terms) for stage in requirement.stages)
        progress[key] = value
    return progress, covered


def _core_preserving_select(
    ranked: list[RankedRecall],
    *,
    core_memory_ids: set[str],
    coverage_slots: tuple[str, ...],
    coverage_requirements: tuple[CoverageRequirement, ...] = (),
    temporal_prefixes: tuple[str, ...],
    limit: int,
    exploration_limit: int,
) -> tuple[list[RankedRecall], tuple[str, ...], tuple[str, ...]]:
    reserved_core_limit = max(0, limit - exploration_limit)
    core = [item for item in ranked if item.memory_id in core_memory_ids]
    selected, _covered, missing = _coverage_select(
        core,
        coverage_slots=coverage_slots,
        coverage_requirements=coverage_requirements,
        limit=reserved_core_limit,
    )
    selected_ids = {item.memory_id for item in selected}
    remaining = [item for item in ranked if item.memory_id not in selected_ids]
    if temporal_prefixes:
        remaining.sort(
            key=lambda item: (
                not _matches_temporal_prefix(item, temporal_prefixes),
                -item.final_score,
                item.memory_id,
            )
        )
    fill, _fill_covered, _fill_missing = _coverage_select(
        remaining,
        coverage_slots=coverage_slots,
        coverage_requirements=coverage_requirements,
        required_keys=missing,
        initial_text="\n".join(_recall_search_text(item) for item in selected),
        limit=limit - len(selected),
    )
    selected.extend(fill)
    _ordered, covered, missing = _coverage_select(
        selected,
        coverage_slots=coverage_slots,
        coverage_requirements=coverage_requirements,
        limit=len(selected),
    )
    return selected, covered, missing


def _matches_temporal_prefix(
    item: RankedRecall,
    temporal_prefixes: tuple[str, ...],
) -> bool:
    summary = item.summary.lstrip()
    return any(summary.startswith(prefix) for prefix in temporal_prefixes)


def _temporal_lexical_query(
    temporal_prefixes: tuple[str, ...],
    anchors: tuple[str, ...],
) -> str | None:
    if not temporal_prefixes:
        return None
    return " ".join((*temporal_prefixes, *anchors))


def _recall_search_text(item: RankedRecall) -> str:
    # Coverage is evidence-backed. A generic title or parent summary must not
    # satisfy a named-anchor subgoal shared by every candidate episode.
    return "\n".join(snippet.text for snippet in item.snippets if snippet.text)


def _merge_recall_matches(
    existing: tuple[RecallMatch, ...],
    postings: tuple[RecallMatch, ...],
) -> tuple[RecallMatch, ...]:
    merged: dict[tuple[str, str | None], RecallMatch] = {}
    ordered: list[RecallMatch] = []
    for match in (*existing, *postings):
        key = (match.document_id, match.fact_id)
        if key in merged:
            continue
        merged[key] = match
        ordered.append(match)
    return tuple(ordered)


def _entity_posting_facts(
    memory: CodingMemory,
    *,
    anchors: tuple[str, ...],
    existing_snippets: tuple[RecallSnippet, ...],
) -> tuple[EvidenceFact, ...]:
    already_covered = _entity_terms("\n".join(item.text for item in existing_snippets))
    selected: list[EvidenceFact] = []
    for anchor in anchors:
        if anchor in already_covered:
            continue
        candidates = [
            fact
            for fact in memory.facts
            if anchor in _entity_terms(_fact_search_text(memory, fact))
        ]
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda fact: (
                -len(set(anchors) & _entity_terms(_fact_search_text(memory, fact))),
                _fact_chronology_key(fact),
            ),
        )
        if best not in selected:
            selected.append(best)
        already_covered.update(_entity_terms(_fact_search_text(memory, best)))
    return tuple(selected)


def _required_provenance_stages(
    requirements: tuple[CoverageRequirement, ...],
) -> tuple[str, ...]:
    stages: list[str] = []
    for requirement in requirements:
        if isinstance(requirement, ProvenanceCoverageRequirement):
            stages.extend(requirement.stages)
        elif (
            isinstance(requirement, RelationCoverageRequirement)
            and requirement.relation == "procedure_order"
        ):
            stages.extend(("failure", "change", "verification"))
    return tuple(dict.fromkeys(stages))


def _provenance_posting_facts(
    memory: CodingMemory,
    *,
    stages: tuple[str, ...],
) -> tuple[EvidenceFact, ...]:
    selected: list[EvidenceFact] = []
    covered: set[str] = set()
    for fact in sorted(memory.facts, key=_fact_chronology_key):
        terms = _entity_terms(_fact_search_text(memory, fact))
        matched = tuple(
            stage
            for stage in stages
            if stage not in covered and bool(_PROVENANCE_TERMS[stage] & terms)
        )
        if not matched:
            continue
        selected.append(fact)
        covered.update(matched)
        if covered == set(stages):
            break
    return tuple(selected)


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
    query: str,
    query_fact_cache: dict[str, tuple[str, ...]] | None,
    matches: tuple[RecallMatch, ...],
    matched_limit: int,
    diverse_matched_limit: int,
    sibling_limit: int,
    wide_sibling_window: bool,
) -> tuple[RecallSnippet, ...]:
    facts = {fact.fact_id: fact for fact in memory.facts}
    ordered_matched = _matched_fact_ids(
        matches,
        memory=memory,
        matched_limit=matched_limit,
        diverse_limit=diverse_matched_limit,
    )
    if not ordered_matched and any(match.document_kind == "episode" for match in matches):
        cached_matched = (
            None if query_fact_cache is None else query_fact_cache.get(memory.memory_id)
        )
        if cached_matched is None:
            cached_matched = _query_matched_fact_ids(
                memory,
                query=query,
                limit=matched_limit,
            )
            if query_fact_cache is not None:
                query_fact_cache[memory.memory_id] = cached_matched
        ordered_matched = cached_matched
    snippets: list[RecallSnippet] = []
    sibling_ids: list[str] = []
    chronological_facts = sorted(memory.facts, key=_fact_chronology_key)
    fact_positions = {fact.fact_id: position for position, fact in enumerate(chronological_facts)}
    for fact_id in ordered_matched:
        if fact_id not in facts:
            continue
        snippets.append(_snippet(memory, fact_id=fact_id, relation="matched"))
        if not wide_sibling_window:
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
        if wide_sibling_window:
            anchor_id = _best_lexical_fact_id(matches, memory=memory) or ordered_matched[0]
            sibling_ids.extend(
                _nearby_fact_ids(
                    chronological_facts,
                    anchor_id=anchor_id,
                    excluded=set(ordered_matched),
                )
            )
        else:
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


def _query_matched_fact_ids(
    memory: CodingMemory,
    *,
    query: str,
    limit: int,
) -> tuple[str, ...]:
    """Choose real source facts when only the parent Episode lane matched."""
    chronological = sorted(memory.facts, key=_fact_chronology_key)
    if not chronological or limit < 1:
        return ()
    query_terms = _content_terms(query)
    semantic_terms: dict[str, set[str]] = {}
    for atomic_fact in (
        memory.semantic_episode.atomic_facts if memory.semantic_episode is not None else ()
    ):
        terms = _content_terms(atomic_fact.text)
        for source_fact_id in atomic_fact.source_fact_ids:
            semantic_terms.setdefault(source_fact_id, set()).update(terms)
    fact_terms = {
        fact.fact_id: _content_terms(render_attributed_fact(fact))
        | semantic_terms.get(fact.fact_id, set())
        for fact in chronological
    }
    overlap_counts = {
        fact.fact_id: len(query_terms & fact_terms[fact.fact_id]) for fact in chronological
    }
    scored = sorted(
        chronological,
        key=lambda fact: (
            -overlap_counts[fact.fact_id],
            _fact_chronology_key(fact),
        ),
    )
    selected = [fact for fact in scored if overlap_counts[fact.fact_id] > 0][:limit]
    selected_ids = {fact.fact_id for fact in selected}
    for fact in _coverage_facts(chronological, limit=limit):
        if len(selected) == limit:
            break
        if fact.fact_id not in selected_ids:
            selected.append(fact)
            selected_ids.add(fact.fact_id)
    return tuple(fact.fact_id for fact in selected)


def _content_terms(text: str) -> set[str]:
    return {
        term
        for match in _ENTITY_TERM.finditer(text)
        if (term := match.group(0).casefold()) not in _QUERY_STOPWORDS
    }


def _coverage_facts(
    chronological: list[EvidenceFact],
    *,
    limit: int,
) -> tuple[EvidenceFact, ...]:
    if len(chronological) <= limit:
        return tuple(chronological)
    if limit == 1:
        return (chronological[len(chronological) // 2],)
    indexes = tuple(
        round(position * (len(chronological) - 1) / (limit - 1)) for position in range(limit)
    )
    return tuple(chronological[index] for index in dict.fromkeys(indexes))


def _matched_fact_ids(
    matches: tuple[RecallMatch, ...],
    *,
    memory: CodingMemory,
    matched_limit: int,
    diverse_limit: int,
) -> tuple[str, ...]:
    atomic_matches = tuple(
        match for match in matches if match.document_kind == "atomic_fact" and match.fact_id
    )
    regular = list(
        dict.fromkeys(
            source_fact_id
            for match in atomic_matches
            if match.source != "entity_posting"
            for source_fact_id in _source_fact_ids(memory, match.fact_id)
        )
    )
    posting = list(
        dict.fromkeys(
            source_fact_id
            for match in atomic_matches
            if match.source == "entity_posting"
            for source_fact_id in _source_fact_ids(memory, match.fact_id)
            if source_fact_id not in regular
        )
    )
    regular_budget = max(0, matched_limit - len(posting))
    ordered = [*regular[:regular_budget], *posting[:matched_limit]]
    if diverse_limit == 0:
        return tuple(ordered)
    diverse_added = 0
    for match in sorted(
        (item for item in atomic_matches if item.source.endswith("_vector")),
        key=lambda item: (item.rank, item.document_id),
    ):
        for source_fact_id in _source_fact_ids(memory, match.fact_id):
            if source_fact_id in ordered:
                continue
            ordered.append(source_fact_id)
            diverse_added += 1
            if diverse_added == diverse_limit:
                break
        if diverse_added == diverse_limit:
            break
    return tuple(ordered)


def _best_lexical_fact_id(
    matches: tuple[RecallMatch, ...],
    *,
    memory: CodingMemory,
) -> str | None:
    lexical = tuple(
        match
        for match in matches
        if match.document_kind == "atomic_fact"
        and match.fact_id
        and match.source.endswith("_lexical")
    )
    if not lexical:
        return None
    match = min(lexical, key=lambda item: (item.rank, item.document_id))
    source_fact_ids = _source_fact_ids(memory, match.fact_id)
    return source_fact_ids[0] if source_fact_ids else None


def _source_fact_ids(memory: CodingMemory, retrieval_fact_id: str) -> tuple[str, ...]:
    if any(fact.fact_id == retrieval_fact_id for fact in memory.facts):
        return (retrieval_fact_id,)
    if memory.semantic_episode is None:
        return ()
    semantic_fact = next(
        (
            fact
            for fact in memory.semantic_episode.atomic_facts
            if fact.fact_id == retrieval_fact_id
        ),
        None,
    )
    return () if semantic_fact is None else semantic_fact.source_fact_ids


def _nearby_fact_ids(
    chronological_facts: list[EvidenceFact],
    *,
    anchor_id: str,
    excluded: set[str],
) -> tuple[str, ...]:
    positions = {fact.fact_id: position for position, fact in enumerate(chronological_facts)}
    anchor_position = positions.get(anchor_id)
    if anchor_position is None:
        return ()
    result: list[str] = []
    for distance in range(1, len(chronological_facts)):
        for position in (anchor_position + distance, anchor_position - distance):
            if not 0 <= position < len(chronological_facts):
                continue
            fact_id = chronological_facts[position].fact_id
            if fact_id not in excluded:
                result.append(fact_id)
    return tuple(result)


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
        text=render_attributed_fact(fact),
        source_title=memory.title,
        source_summary=memory.summary,
        raw_event_index=raw_event_index,
    )


def _fact_search_text(memory: CodingMemory, fact: EvidenceFact) -> str:
    semantic_text = "\n".join(
        atomic.text
        for atomic in (memory.semantic_episode.atomic_facts if memory.semantic_episode else ())
        if fact.fact_id in atomic.source_fact_ids
    )
    return "\n".join(part for part in (render_attributed_fact(fact), semantic_text) if part)


def _episode_text(memory: CodingMemory) -> str:
    if memory.semantic_episode is None:
        return ""
    # Semantic projections are retrieval aids. The answer context hydrates only
    # authoritative source turns so a model-written narrative cannot become evidence.
    return render_episode(memory.facts)


def _episode_snippets(memory: CodingMemory) -> tuple[RecallSnippet, ...]:
    return tuple(
        _snippet(memory, fact_id=fact.fact_id, relation="sibling")
        for fact in sorted(memory.facts, key=_fact_chronology_key)
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


def _chronology_key(memory: CodingMemory) -> tuple[int, str, int, str]:
    if memory.adjacency_group_id is not None and memory.adjacency_index is not None:
        return 0, memory.adjacency_group_id, memory.adjacency_index, memory.memory_id
    session_id = min((reference.session_id for reference in memory.evidence), default="")
    raw_event_index = min(
        (reference.raw_event_index for reference in memory.evidence),
        default=-1,
    )
    return 1, session_id, raw_event_index, memory.memory_id


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


@dataclass(frozen=True, slots=True)
class _CompiledContext:
    markdown: str
    evidence_text: str = ""
    hydrated_episode_ids: tuple[str, ...] = ()
    partial_episode_ids: tuple[str, ...] = ()
    dropped_episode_ids: tuple[str, ...] = ()
    trace: RecallContextTrace | None = None


def _render_context(
    query: str,
    *,
    repo_key: str,
    ranked: tuple[RankedRecall, ...],
    temporal_priority_memory_ids: set[str],
    config: RecallPlannerConfig,
) -> str:
    return _compile_context(
        query,
        repo_key=repo_key,
        ranked=ranked,
        temporal_priority_memory_ids=temporal_priority_memory_ids,
        config=config,
    ).markdown


def _compile_context(
    query: str,
    *,
    repo_key: str,
    ranked: tuple[RankedRecall, ...],
    temporal_priority_memory_ids: set[str],
    config: RecallPlannerConfig,
    wants_procedure: bool = False,
    evidence_slots: tuple[ContextEvidenceSlot, ...] = (),
) -> _CompiledContext:
    empty_suffix = ["", "No evidence-backed memory matched this task."]
    header = _context_header(
        query,
        repo_key=repo_key,
        token_limit=(
            config.context_max_tokens
            if ranked
            else config.context_max_tokens - _line_token_cost(empty_suffix)
        ),
    )
    if not ranked:
        header.extend(empty_suffix)
        markdown = "\n".join(header) + "\n"
        token_count = count_context_tokens(markdown)
        if token_count > config.context_max_tokens:
            raise AssertionError("Recall Context exceeded its deterministic token budget")
        return _CompiledContext(
            markdown=markdown,
            trace=RecallContextTrace(
                renderer=CONTEXT_RENDERER_ID,
                char_count=len(markdown),
                rendered_memory_ids=(),
                rendered_fact_ids=(),
                omitted_memory_ids=(),
                omitted_snippet_count=0,
                token_count=token_count,
                token_limit=config.context_max_tokens,
                tokenizer_id=CONTEXT_TOKENIZER_ID,
                admission_candidate_fact_ids=(),
                slot_traces=tuple(
                    RecallContextSlotTrace(
                        slot_kind=slot.kind,
                        max_facts=slot.max_facts,
                        attempts=(),
                    )
                    for slot in evidence_slots
                ),
            ),
        )

    bases: list[list[str]] = []
    snippet_values: list[tuple[RecallSnippet, ...]] = []
    snippet_lines: list[tuple[str, ...]] = []
    for item in ranked:
        bases.append(_compact_evidence_base(item))
        snippets = _compact_evidence_snippets(
            item,
            temporal_priority=item.memory_id in temporal_priority_memory_ids,
        )
        snippet_values.append(snippets)
        snippet_lines.append(
            tuple(
                _context_snippet_line(
                    snippet,
                    parent_memory_id=item.memory_id,
                )
                for snippet in snippets
            )
        )

    remaining_bytes = config.context_max_tokens * 2 - _line_byte_cost(header)
    selected_snippet_indexes: list[list[int]] = [[] for _item in ranked]
    attempted_snippet_indexes: list[set[int]] = [set() for _item in ranked]
    rendered_indexes: list[int] = []
    dropped: list[str] = []
    slot_traces: list[RecallContextSlotTrace] = []
    has_fact_relevance = any(
        snippet.relevance_score is not None for snippets in snippet_values for snippet in snippets
    )
    if has_fact_relevance:
        candidates = sorted(
            (
                (item_index, snippet_index, snippet)
                for item_index, snippets in enumerate(snippet_values)
                for snippet_index, snippet in enumerate(snippets)
            ),
            key=lambda value: (
                -_context_effective_relevance(
                    value[2],
                    parent_memory_id=ranked[value[0]].memory_id,
                ),
                ranked[value[0]].rank,
                value[1],
                value[2].fact_id,
            ),
        )
        rendered_fact_keys: set[tuple[str, str]] = set()

        def admit_candidate(
            candidate: tuple[int, int, RecallSnippet],
        ) -> _ContextAdmissionOutcome:
            nonlocal remaining_bytes
            item_index, snippet_index, snippet = candidate
            attempted_snippet_indexes[item_index].add(snippet_index)
            fact_key = (snippet.source_memory_id, snippet.fact_id)
            if fact_key in rendered_fact_keys:
                return "duplicate"
            item = ranked[item_index]
            allowed = (
                config.context_temporal_snippets_per_memory
                if item.memory_id in temporal_priority_memory_ids
                else config.context_snippets_per_memory
            )
            if len(selected_snippet_indexes[item_index]) >= allowed:
                return "parent_limit"
            first_parent_fact = not selected_snippet_indexes[item_index]
            allocation = (
                [*bases[item_index], snippet_lines[item_index][snippet_index]]
                if first_parent_fact
                else [snippet_lines[item_index][snippet_index]]
            )
            cost = _line_byte_cost(allocation)
            if cost > remaining_bytes:
                return "budget"
            if first_parent_fact:
                rendered_indexes.append(item_index)
            selected_snippet_indexes[item_index].append(snippet_index)
            remaining_bytes -= cost
            rendered_fact_keys.add(fact_key)
            return "admitted"

        candidate_values = tuple(candidates)
        if any(slot.kind == "quantity_transition" for slot in evidence_slots):
            for candidate in candidate_values[:3]:
                admit_candidate(candidate)
        for slot in evidence_slots:
            attempts: list[RecallContextSlotAttempt] = []
            for candidate in _context_slot_candidates(
                slot,
                ranked=ranked,
                snippet_values=tuple(snippet_values),
                candidates=candidate_values,
            ):
                attempts.append(
                    RecallContextSlotAttempt(
                        fact_id=candidate[2].fact_id,
                        outcome=admit_candidate(candidate),
                    )
                )
            slot_traces.append(
                RecallContextSlotTrace(
                    slot_kind=slot.kind,
                    max_facts=slot.max_facts,
                    attempts=tuple(attempts),
                )
            )
        for candidate in candidate_values:
            admit_candidate(candidate)
        rendered_indexes.sort()
        dropped.extend(
            item.memory_id
            for item_index, item in enumerate(ranked)
            if not selected_snippet_indexes[item_index]
        )
    else:
        slot_traces.extend(
            RecallContextSlotTrace(
                slot_kind=slot.kind,
                max_facts=slot.max_facts,
                attempts=(),
            )
            for slot in evidence_slots
        )
        # Legacy and deterministic-test Adapters have no fact relevance score.
        # Preserve their breadth-first context contract.
        for item_index, item in enumerate(ranked):
            excerpts = snippet_lines[item_index]
            for snippet_index in range(len(excerpts)):
                attempted_snippet_indexes[item_index].add(snippet_index)
                first_evidence = [*bases[item_index], excerpts[snippet_index]]
                cost = _line_byte_cost(first_evidence)
                if cost > remaining_bytes:
                    continue
                rendered_indexes.append(item_index)
                selected_snippet_indexes[item_index].append(snippet_index)
                remaining_bytes -= cost
                break
            if not selected_snippet_indexes[item_index]:
                dropped.append(item.memory_id)

        maximum_rounds = max(
            config.context_snippets_per_memory,
            config.context_temporal_snippets_per_memory,
        )
        for relation in ("matched", "sibling", "neighbor"):
            for _round_index in range(1, maximum_rounds):
                selected_in_round = False
                for item_index in rendered_indexes:
                    item = ranked[item_index]
                    allowed = (
                        config.context_temporal_snippets_per_memory
                        if item.memory_id in temporal_priority_memory_ids
                        else config.context_snippets_per_memory
                    )
                    excerpts = snippet_lines[item_index]
                    selected_indexes = selected_snippet_indexes[item_index]
                    if len(selected_indexes) >= allowed:
                        continue
                    attempted_indexes = attempted_snippet_indexes[item_index]
                    for snippet_index, snippet in enumerate(snippet_values[item_index]):
                        if snippet_index in attempted_indexes or snippet.relation != relation:
                            continue
                        attempted_indexes.add(snippet_index)
                        cost = _line_byte_cost([excerpts[snippet_index]])
                        if cost > remaining_bytes:
                            continue
                        selected_indexes.append(snippet_index)
                        remaining_bytes -= cost
                        selected_in_round = True
                        break
                if not selected_in_round:
                    break

    hydrated_indexes: set[int] = set()
    hydration_lines: dict[int, list[str]] = {}
    hydration_snippets: dict[int, tuple[RecallSnippet, ...]] = {}
    already_rendered_fact_ids = {
        snippet_values[item_index][snippet_index].fact_id
        for item_index in rendered_indexes
        for snippet_index in selected_snippet_indexes[item_index]
    }
    if wants_procedure:
        for item_index in rendered_indexes[:2]:
            complete_parent = ranked[item_index].episode_snippets
            if not complete_parent:
                continue
            parent_snippets = tuple(
                snippet
                for snippet in complete_parent
                if snippet.fact_id not in already_rendered_fact_ids
            )
            if not parent_snippets:
                completion_block = [
                    "",
                    "Complete parent episode: all authoritative source facts are rendered above.",
                ]
                completion_cost = _line_byte_cost(completion_block)
                if completion_cost <= remaining_bytes:
                    hydration_lines[item_index] = completion_block
                    remaining_bytes -= completion_cost
                hydrated_indexes.add(item_index)
                hydration_snippets[item_index] = ()
                continue
            block = [
                "",
                "Complete parent episode:",
                "",
                *(_hydrated_fact_line(snippet) for snippet in parent_snippets),
            ]
            cost = _line_byte_cost(block)
            if cost > remaining_bytes:
                continue
            hydrated_indexes.add(item_index)
            hydration_lines[item_index] = block
            hydration_snippets[item_index] = parent_snippets
            already_rendered_fact_ids.update(snippet.fact_id for snippet in parent_snippets)
            remaining_bytes -= cost

    lines = list(header)
    evidence_parts: list[str] = []
    rendered_memory_ids: list[str] = []
    rendered_fact_ids: list[str] = []
    for item_index in rendered_indexes:
        item = ranked[item_index]
        lines.extend(bases[item_index])
        selected_indexes = selected_snippet_indexes[item_index]
        lines.extend(snippet_lines[item_index][index] for index in selected_indexes)
        lines.extend(hydration_lines.get(item_index, ()))
        rendered_memory_ids.append(item.memory_id)
        selected_snippets = tuple(snippet_values[item_index][index] for index in selected_indexes)
        evidence_parts.extend(_context_fact_text(snippet) for snippet in selected_snippets)
        rendered_fact_ids.extend(
            snippet.fact_id for snippet in selected_snippets if snippet.fact_id
        )
        if item_index in hydrated_indexes:
            evidence_parts.extend(
                _context_fact_text(snippet) for snippet in hydration_snippets[item_index]
            )
            rendered_fact_ids.extend(
                snippet.fact_id for snippet in hydration_snippets[item_index] if snippet.fact_id
            )

    if dropped:
        notice = f"{len(dropped)} selected parent episodes omitted by the context budget."
        notice_block = ["", notice]
        if _line_byte_cost(notice_block) <= remaining_bytes:
            lines.extend(notice_block)
    markdown = "\n".join(lines) + "\n"
    if len(markdown) > config.context_max_chars:
        raise AssertionError("Recall Context exceeded its deterministic character budget")
    token_count = count_context_tokens(markdown)
    if token_count > config.context_max_tokens:
        raise AssertionError("Recall Context exceeded its deterministic token budget")
    available_fact_ids = {
        snippet.fact_id for snippets in snippet_values for snippet in snippets if snippet.fact_id
    }
    admission_candidate_fact_ids = tuple(
        dict.fromkeys(
            snippet.fact_id
            for snippets in snippet_values
            for snippet in snippets
            if snippet.fact_id
        )
    )
    available_fact_ids.update(
        snippet.fact_id
        for snippets in hydration_snippets.values()
        for snippet in snippets
        if snippet.fact_id
    )
    omitted_snippet_count = len(available_fact_ids - set(rendered_fact_ids))
    return _CompiledContext(
        markdown=markdown,
        evidence_text="\n".join(evidence_parts),
        hydrated_episode_ids=tuple(
            ranked[index].memory_id for index in rendered_indexes if index in hydrated_indexes
        ),
        partial_episode_ids=tuple(
            ranked[index].memory_id for index in rendered_indexes if index not in hydrated_indexes
        ),
        dropped_episode_ids=tuple(dropped),
        trace=RecallContextTrace(
            renderer=CONTEXT_RENDERER_ID,
            char_count=len(markdown),
            rendered_memory_ids=tuple(rendered_memory_ids),
            rendered_fact_ids=tuple(dict.fromkeys(rendered_fact_ids)),
            omitted_memory_ids=tuple(dropped),
            omitted_snippet_count=omitted_snippet_count,
            token_count=token_count,
            token_limit=config.context_max_tokens,
            tokenizer_id=CONTEXT_TOKENIZER_ID,
            omitted_fact_ids=tuple(sorted(available_fact_ids - set(rendered_fact_ids))),
            admission_candidate_fact_ids=admission_candidate_fact_ids,
            slot_traces=tuple(slot_traces),
        ),
    )


def replay_context_slot_traces(
    query: str,
    *,
    repo_key: str,
    ranked: tuple[RankedRecall, ...],
    config: RecallPlannerConfig,
    limit: int,
) -> tuple[RecallContextSlotTrace, ...]:
    """Replay the deterministic evidence-slot admission transcript.

    Evaluation uses this entry point to verify that a persisted slot trace is
    the one produced by the frozen query sketch and context compiler. Procedure
    hydration runs after slot admission, so it is deliberately disabled here.
    """

    plan = RecallPlanner(config).plan(query, limit=limit)
    if plan.query_sketch.temporal_prefixes:
        temporal_priority_memory_ids = {
            item.memory_id
            for item in ranked
            if _matches_temporal_prefix(item, plan.query_sketch.temporal_prefixes)
        }
    elif plan.query_sketch.temporal_op != "none":
        temporal_priority_memory_ids = {
            item.memory_id for item in ranked[: config.maximum_exploration_results]
        }
    else:
        temporal_priority_memory_ids = set()
    compiled = _compile_context(
        query,
        repo_key=repo_key,
        ranked=ranked,
        temporal_priority_memory_ids=temporal_priority_memory_ids,
        config=config,
        wants_procedure=False,
        evidence_slots=plan.query_sketch.evidence_slots,
    )
    if compiled.trace is None:
        raise AssertionError("Context slot replay produced no trace")
    return compiled.trace.slot_traces


def _context_effective_relevance(
    snippet: RecallSnippet,
    *,
    parent_memory_id: str,
) -> float:
    score = snippet.relevance_score
    if score is None:
        return float("-inf")
    if snippet.relation == "matched" and snippet.source_memory_id == parent_memory_id:
        return score + CONTEXT_DIRECT_MATCH_PRIOR
    return score


def _context_slot_candidates(
    slot: ContextEvidenceSlot,
    *,
    ranked: tuple[RankedRecall, ...],
    snippet_values: tuple[tuple[RecallSnippet, ...], ...],
    candidates: tuple[tuple[int, int, RecallSnippet], ...],
) -> tuple[tuple[int, int, RecallSnippet], ...]:
    if slot.kind == "vocative_alias":
        selected = _vocative_alias_candidates(
            slot,
            ranked=ranked,
            candidates=candidates,
        )
    elif slot.kind == "quantity_transition":
        selected = _quantity_transition_candidates(
            slot,
            ranked=ranked,
            snippet_values=snippet_values,
            candidates=candidates,
        )
    elif slot.kind == "prior_state":
        selected = _prior_state_candidates(
            slot,
            ranked=ranked,
            candidates=candidates,
        )
    else:
        selected = tuple(
            sorted(
                (
                    candidate
                    for candidate in candidates
                    if _semantic_child_support_score(
                        ranked[candidate[0]],
                        candidate[2],
                    )
                    > 0.0
                ),
                key=lambda candidate: (
                    -_semantic_child_support_score(
                        ranked[candidate[0]],
                        candidate[2],
                    ),
                    ranked[candidate[0]].rank,
                    -_context_effective_relevance(
                        candidate[2],
                        parent_memory_id=ranked[candidate[0]].memory_id,
                    ),
                    candidate[1],
                    candidate[2].fact_id,
                ),
            )
        )
    return selected[: slot.max_facts]


def _vocative_alias_candidates(
    slot: ContextEvidenceSlot,
    *,
    ranked: tuple[RankedRecall, ...],
    candidates: tuple[tuple[int, int, RecallSnippet], ...],
) -> tuple[tuple[int, int, RecallSnippet], ...]:
    anchors = set(slot.anchors)
    selected: list[tuple[int, int, RecallSnippet]] = []
    for candidate in candidates:
        item_index, _snippet_index, snippet = candidate
        item = ranked[item_index]
        if snippet.source_memory_id != item.memory_id:
            continue
        speaker, utterance = _context_turn_parts(snippet.text)
        speaker_key = speaker.casefold()
        if speaker_key not in anchors:
            continue
        match = _CONTEXT_VOCATIVE.search(utterance)
        if match is None:
            continue
        address = match.group(1).casefold()
        if not any(
            anchor != speaker_key and anchor != address and anchor.startswith(address)
            for anchor in anchors
        ):
            continue
        selected.append(candidate)
    selected.sort(
        key=lambda candidate: (
            ranked[candidate[0]].rank,
            candidate[2].raw_event_index if candidate[2].raw_event_index is not None else 0,
            -_context_effective_relevance(
                candidate[2],
                parent_memory_id=ranked[candidate[0]].memory_id,
            ),
            candidate[2].fact_id,
        )
    )
    return tuple(selected)


def _quantity_transition_candidates(
    slot: ContextEvidenceSlot,
    *,
    ranked: tuple[RankedRecall, ...],
    snippet_values: tuple[tuple[RecallSnippet, ...], ...],
    candidates: tuple[tuple[int, int, RecallSnippet], ...],
) -> tuple[tuple[int, int, RecallSnippet], ...]:
    anchors = set(slot.anchors)
    topic_terms = set(slot.topic_terms)
    grouped: dict[str, list[tuple[int, int, RecallSnippet]]] = {}
    for candidate in candidates:
        item_index, _snippet_index, snippet = candidate
        item = ranked[item_index]
        if snippet.source_memory_id != item.memory_id:
            continue
        combined = "\n".join(value for value in (snippet.semantic_text, snippet.text) if value)
        transitions = tuple(
            dict.fromkeys(
                _quantity_transition_key(match.group(1))
                for match in _CONTEXT_QUANTITY_TRANSITION.finditer(combined)
            )
        )
        if not transitions:
            continue
        overlap, _anchor_speaker, anchored_anaphoric, semantic_support = (
            _quantity_candidate_signals(
                candidate,
                ranked=ranked,
                anchors=anchors,
                topic_terms=topic_terms,
            )
        )
        if overlap == 0 and not anchored_anaphoric and semantic_support <= 0.0:
            continue
        for transition in transitions:
            grouped.setdefault(transition, []).append(candidate)

    selected: list[tuple[int, int, RecallSnippet]] = []
    for transition in sorted(grouped, key=_quantity_transition_order):
        best = min(
            grouped[transition],
            key=lambda candidate: _quantity_candidate_priority(
                candidate,
                ranked=ranked,
                anchors=anchors,
                topic_terms=topic_terms,
            ),
        )
        selected.append(best)
        if _CONTEXT_ANAPHORIC_QUANTITY.search(
            "\n".join(value for value in (best[2].semantic_text, best[2].text) if value)
        ):
            following = _following_context_candidate(
                best,
                snippet_values=snippet_values,
            )
            if following is not None:
                selected.append(following)
    return tuple(dict.fromkeys(selected))


def _quantity_candidate_priority(
    candidate: tuple[int, int, RecallSnippet],
    *,
    ranked: tuple[RankedRecall, ...],
    anchors: set[str],
    topic_terms: set[str],
) -> tuple[int, int, int, float, float, int, int, str]:
    item_index, _snippet_index, snippet = candidate
    overlap, anchor_speaker, anchored_anaphoric, semantic_support = _quantity_candidate_signals(
        candidate,
        ranked=ranked,
        anchors=anchors,
        topic_terms=topic_terms,
    )
    return (
        -overlap,
        int(not anchor_speaker),
        int(not anchored_anaphoric),
        -semantic_support,
        -_context_effective_relevance(
            snippet,
            parent_memory_id=ranked[item_index].memory_id,
        ),
        ranked[item_index].rank,
        snippet.raw_event_index if snippet.raw_event_index is not None else 1_000_000_000,
        snippet.fact_id,
    )


def _quantity_candidate_signals(
    candidate: tuple[int, int, RecallSnippet],
    *,
    ranked: tuple[RankedRecall, ...],
    anchors: set[str],
    topic_terms: set[str],
) -> tuple[int, bool, bool, float]:
    item_index, _snippet_index, snippet = candidate
    combined = "\n".join(value for value in (snippet.semantic_text, snippet.text) if value)
    speaker, _utterance = _context_turn_parts(snippet.text)
    anchor_mention = any(_contains_context_term(combined, anchor) for anchor in anchors)
    return (
        len(
            topic_terms
            & _context_topic_terms(
                combined,
                excluded_terms=anchors,
            )
        ),
        speaker.casefold() in anchors,
        anchor_mention and _CONTEXT_ANAPHORIC_QUANTITY.search(combined) is not None,
        _semantic_child_support_score(ranked[item_index], snippet),
    )


def _prior_state_candidates(
    slot: ContextEvidenceSlot,
    *,
    ranked: tuple[RankedRecall, ...],
    candidates: tuple[tuple[int, int, RecallSnippet], ...],
) -> tuple[tuple[int, int, RecallSnippet], ...]:
    anchors = set(slot.anchors)
    selected: list[tuple[int, int, RecallSnippet]] = []
    for candidate in candidates:
        item_index, _snippet_index, snippet = candidate
        item = ranked[item_index]
        if snippet.source_memory_id != item.memory_id:
            continue
        speaker, _utterance = _context_turn_parts(snippet.text)
        combined = "\n".join(value for value in (snippet.semantic_text, snippet.text) if value)
        if (
            speaker.casefold() in anchors
            and _CONTEXT_EXCLUSIVITY.search(combined) is not None
            and _CONTEXT_AFFECT.search(combined) is not None
        ):
            selected.append(candidate)
    selected.sort(
        key=lambda candidate: (
            ranked[candidate[0]].rank,
            -_context_effective_relevance(
                candidate[2],
                parent_memory_id=ranked[candidate[0]].memory_id,
            ),
            candidate[2].raw_event_index
            if candidate[2].raw_event_index is not None
            else 1_000_000_000,
            candidate[2].fact_id,
        )
    )
    return tuple(selected)


def _semantic_child_support_score(
    item: RankedRecall,
    snippet: RecallSnippet,
) -> float:
    semantic_fact_ids = set(snippet.semantic_fact_ids)
    return sum(
        1.0 / (_RRF_K + match.rank)
        for match in item.matched_documents
        if match.document_kind == "atomic_fact" and match.fact_id in semantic_fact_ids
    )


def _following_context_candidate(
    candidate: tuple[int, int, RecallSnippet],
    *,
    snippet_values: tuple[tuple[RecallSnippet, ...], ...],
) -> tuple[int, int, RecallSnippet] | None:
    item_index, _snippet_index, snippet = candidate
    if snippet.raw_event_index is None:
        return None
    expected_index = snippet.raw_event_index + 1
    following = (
        (item_index, index, value)
        for index, value in enumerate(snippet_values[item_index])
        if value.raw_event_index == expected_index
        and value.source_memory_id == snippet.source_memory_id
    )
    return min(
        following,
        default=None,
        key=lambda value: (
            value[2].relation != "matched",
            -_context_effective_relevance(
                value[2],
                parent_memory_id=snippet.source_memory_id,
            ),
            value[2].fact_id,
        ),
    )


def _context_turn_parts(text: str) -> tuple[str, str]:
    _prefix, separator, attributed = text.partition(" — ")
    if not separator:
        return "", text
    speaker, colon, utterance = attributed.partition(":")
    if not colon:
        return "", attributed
    return speaker.strip(), utterance.strip()


def _context_topic_terms(
    text: str,
    *,
    excluded_terms: set[str],
) -> set[str]:
    return {
        _normalize_context_term(match.group(0))
        for match in _ENTITY_TERM.finditer(text)
        if match.group(0).casefold() not in _QUERY_STOPWORDS
        and match.group(0).casefold() not in excluded_terms
    }


def _normalize_context_term(value: str) -> str:
    term = value.casefold()
    if term in {"news", "series", "species"}:
        return term
    if len(term) > 4 and term.endswith("ies"):
        return f"{term[:-3]}y"
    if len(term) > 4 and term.endswith(("sses", "shes", "ches", "xes", "zes")):
        return term[:-2]
    if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        return term[:-1]
    return term


def _contains_context_term(text: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None


def _quantity_transition_key(value: str) -> str:
    return value.casefold()


def _quantity_transition_order(value: str) -> tuple[int, str]:
    order = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
        "another": 11,
    }
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", value)
    numeric = int(match.group(1)) if match is not None else order.get(value, 1_000_000_000)
    return numeric, value


def _line_token_cost(lines: list[str]) -> int:
    return count_context_tokens("\n".join(lines) + "\n")


def _line_byte_cost(lines: list[str]) -> int:
    return len(("\n".join(lines) + "\n").encode("utf-8"))


def _context_header(query: str, *, repo_key: str, token_limit: int) -> list[str]:
    query_limit = min(400, max(16, len(query)))
    repo_limit = min(200, max(16, len(repo_key)))

    def render() -> list[str]:
        return [
            "# Recall Context",
            "",
            f"Task: {_single_line(query, limit=query_limit)}",
            f"Repository: `{_single_line(repo_key, limit=repo_limit)}`",
        ]

    header = render()
    while _line_token_cost(header) > token_limit and query_limit > 16:
        query_limit -= 1
        header = render()
    while _line_token_cost(header) > token_limit and repo_limit > 16:
        repo_limit -= 1
        header = render()
    if _line_token_cost(header) > token_limit:
        raise ValueError("Recall Context token budget cannot fit its required header")
    return header


def _compact_evidence_base(item: RankedRecall) -> list[str]:
    return [
        "",
        (
            f"## {item.rank}. {_single_line(item.title, limit=120)} "
            f"[{item.memory_id}]({item.source_uri})"
        ),
        "Evidence excerpts:",
    ]


def _compact_evidence_snippets(
    item: RankedRecall,
    *,
    temporal_priority: bool,
) -> tuple[RecallSnippet, ...]:
    return _context_snippets(item, temporal_priority=temporal_priority)


def _context_snippets(
    item: RankedRecall,
    *,
    temporal_priority: bool,
) -> tuple[RecallSnippet, ...]:
    del temporal_priority
    matched = tuple(snippet for snippet in item.snippets if snippet.relation == "matched")
    siblings = tuple(snippet for snippet in item.snippets if snippet.relation == "sibling")
    neighbors = tuple(snippet for snippet in item.snippets if snippet.relation == "neighbor")
    return (*matched, *siblings, *neighbors)


def _context_snippet_line(
    snippet: RecallSnippet,
    *,
    parent_memory_id: str,
) -> str:
    text = _context_fact_text(snippet)
    if snippet.source_memory_id == parent_memory_id:
        return f"- [{snippet.fact_id}] {snippet.relation}: {text}"
    return (
        f"- [{snippet.fact_id}] {snippet.relation}: {text} "
        f"([{snippet.source_memory_id}]({snippet.source_uri}))"
    )


def _hydrated_fact_line(snippet: RecallSnippet) -> str:
    return f"- [{snippet.fact_id}] source: {_context_fact_text(snippet)}"


def _context_fact_text(snippet: RecallSnippet) -> str:
    """Render only the complete authoritative source fact.

    Semantic projections remain useful reranking features, but source linkage is
    not an entailment proof. A fact ID enters the context trace only beside its
    exact attributed evidence, including any source timestamp.
    """

    return " ".join(snippet.text.replace("\x00", "").split())


def _memory_uri(memory_id: str) -> str:
    return f"codecairn://memory/{quote(memory_id, safe='')}"


def _single_line(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.replace("\x00", "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _vector_digest(vector: tuple[float, ...]) -> str:
    return hashlib.sha256(struct.pack(f"<{len(vector)}f", *vector)).hexdigest()
