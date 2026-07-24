from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import codecairn.service.recall as recall_service
from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    IndexCandidate,
    RankedRecall,
    RecallMatch,
    RecallSnippet,
    RerankDocument,
    RerankScore,
)
from codecairn.memory.recall_planner import (
    RecallPlannerConfig,
    RelationCoverageRequirement,
    TemporalCoverageRequirement,
)
from codecairn.service.recall import RecallEngine
from codecairn.service.recall import _compile_context as compile_context
from codecairn.service.recall import _core_preserving_select as core_preserving_select
from codecairn.service.recall import _render_context as render_context
from codecairn.storage.lance import LanceMemoryIndex


class FixedEmbedder:
    model_id = "test/fixed"
    source_id = "test/fixed-source"
    revision = "test-v1"
    dimension = 256
    index_identity = "test:test/fixed-source@test-v1:256"

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0,) + (0.0,) * 255

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.embed_query(text) for text in texts)


class PurposeEmbedder:
    model_id = "test/purpose"
    source_id = "test/purpose-source"
    revision = "test-v1"
    dimension = 256
    index_identity = "test:test/purpose-source@test-v1:256"

    def embed_query(self, text: str) -> tuple[float, ...]:
        if text == "needle" or "vector decoy" in text:
            return (1.0,) + (0.0,) * 255
        return (0.0, 1.0) + (0.0,) * 254

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.embed_query(text) for text in texts)


class ShiftedPurposeEmbedder(PurposeEmbedder):
    revision = "test-v2"
    index_identity = "test:test/purpose-source@test-v2:256"

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (0.0, 1.0) + (0.0,) * 254


class PurposeReranker:
    model_id = "test-purpose-reranker"
    source_id = "test/purpose-reranker-source"
    revision = "test-v1"

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]:
        assert query == "fix the widget test"
        return tuple(
            RerankScore(
                memory_id=document.memory_id,
                score=10.0 if document.memory_id == "memory-b" else -1.0,
            )
            for document in documents
        )


class IncompleteReranker(PurposeReranker):
    model_id = "test/incomplete-reranker"

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]:
        return (RerankScore(memory_id=documents[0].memory_id, score=1.0),)


class CandidateIndex:
    def vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        return (
            IndexCandidate(repo_key=repo_key, memory_id="memory-a", score=0.9),
            IndexCandidate(repo_key="other/repo", memory_id="leak", score=1.0),
        )

    def lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        return (
            IndexCandidate(repo_key=repo_key, memory_id="memory-b", score=3.0),
            IndexCandidate(repo_key=repo_key, memory_id="memory-a", score=2.0),
        )

    def document_vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        document_kind: str,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        if document_kind == "atomic_fact":
            return ()
        return self.vector_candidates(repo_key=repo_key, vector=vector, limit=limit)

    def document_lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        document_kind: str,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        if document_kind == "atomic_fact":
            return ()
        return self.lexical_candidates(repo_key=repo_key, query=query, limit=limit)


class MemoryState:
    def __init__(self, memories: tuple[CodingMemory, ...]) -> None:
        self._memories = {(item.repo_key, item.memory_id): item for item in memories}
        self.requested: list[tuple[str, str]] = []
        self.episode_requests: list[tuple[str, str]] = []
        self.episode_limits: list[int] = []

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None:
        self.requested.append((repo_key, memory_id))
        return self._memories.get((repo_key, memory_id))

    def list_episode_memories(
        self,
        *,
        repo_key: str,
        episode_id: str,
        limit: int,
    ) -> tuple[CodingMemory, ...]:
        self.episode_requests.append((repo_key, episode_id))
        self.episode_limits.append(limit)
        return tuple(
            memory
            for (memory_repo_key, _memory_id), memory in self._memories.items()
            if memory_repo_key == repo_key and memory.episode_id == episode_id
        )[:limit]

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return tuple(
            memory
            for (memory_repo_key, _memory_id), memory in self._memories.items()
            if memory_repo_key == repo_key
        )

    def find_entity_memories(
        self,
        *,
        repo_key: str,
        entity_keys: tuple[str, ...],
        limit: int,
    ) -> tuple[CodingMemory, ...]:
        return tuple(
            memory
            for memory in self.list_memories(repo_key=repo_key)
            if any(
                entity in fact.text.casefold() for fact in memory.facts for entity in entity_keys
            )
        )[:limit]


def test_hybrid_recall_unions_candidates_before_deterministic_reranking() -> None:
    memories = (
        replace(
            _memory_with_fact(
                "memory-a",
                fact_id="fact-a",
                fact_text="Shared candidate.",
                event_index=1,
            ),
            title="Vector and lexical",
        ),
        replace(
            _memory_with_fact(
                "memory-b",
                fact_id="fact-b",
                fact_text="BM25 found this memory.",
                event_index=1,
            ),
            title="Lexical only",
        ),
    )
    state = MemoryState(memories)
    ticks = iter((1_000_000, 3_500_000))
    engine = RecallEngine(
        index=CandidateIndex(),
        state=state,
        embedder=FixedEmbedder(),
        reranker=PurposeReranker(),
        clock_ns=lambda: next(ticks),
    )

    result = engine.recall("fix the widget test", repo_key="acme/widgets", limit=5)

    assert [item.memory_id for item in result.sidecar.ranked] == ["memory-b", "memory-a"]
    assert result.sidecar.ranked[0].candidate_sources == ("lexical",)
    assert result.sidecar.ranked[1].candidate_sources == ("lexical", "vector")
    assert result.sidecar.ranked[0].vector_score is None
    assert result.sidecar.ranked[0].reranker_score == 10.0
    assert result.sidecar.reranker_model == "test-purpose-reranker"
    assert result.sidecar.reranker_source == "test/purpose-reranker-source"
    assert result.sidecar.reranker_revision == "test-v1"
    assert result.sidecar.embedding_model == "test/fixed"
    assert result.sidecar.embedding_source == "test/fixed-source"
    assert result.sidecar.embedding_revision == "test-v1"
    assert result.sidecar.latency_ms == 2.5
    assert all(repo_key == "acme/widgets" for repo_key, _memory_id in state.requested)
    assert "other/repo" not in result.markdown
    assert "BM25 found this memory." in result.markdown
    assert "codecairn://memory/memory-b" not in result.markdown
    assert result.sidecar.ranked[0].source_uri == "codecairn://memory/memory-b"
    assert result.sidecar.ranked[0].evidence[0].raw_event_index == 1
    assert [trace.stage for trace in result.sidecar.stage_trace] == [
        "candidate_recall",
        "fusion",
        "rerank",
        "selection",
        "context",
    ]
    assert [trace.output_memory_ids for trace in result.sidecar.stage_trace] == [
        ("memory-b", "memory-a"),
        ("memory-a", "memory-b"),
        ("memory-b", "memory-a"),
        ("memory-b", "memory-a"),
        ("memory-b", "memory-a"),
    ]


def test_recall_engine_applies_asymmetric_route_budgets_to_every_index_lane() -> None:
    class LimitEchoIndex(CandidateIndex):
        def _candidates(
            self,
            *,
            repo_key: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return tuple(
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id=f"{document_kind}-memory-{index}",
                    document_id=f"{document_kind}-document-{index}",
                    document_kind=document_kind,
                    parent_document_id=f"episode-document-{index}",
                    fact_id=(f"fact-{index}" if document_kind == "atomic_fact" else ""),
                    score=float(limit - index),
                )
                for index in range(limit)
            )

        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return self._candidates(
                repo_key=repo_key,
                document_kind=document_kind,
                limit=limit,
            )

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return self._candidates(
                repo_key=repo_key,
                document_kind=document_kind,
                limit=limit,
            )

    engine = RecallEngine(
        index=LimitEchoIndex(),
        state=MemoryState(()),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    fact_first = engine.recall("When did it happen?", repo_key="acme/widgets", limit=20)
    episode_first = engine.recall(
        "Summarize the debugging approach",
        repo_key="acme/widgets",
        limit=20,
    )

    assert fact_first.sidecar.recall_route == "fact_first"
    assert (
        fact_first.sidecar.episode_vector_candidate_count,
        fact_first.sidecar.episode_lexical_candidate_count,
        fact_first.sidecar.atomic_fact_vector_candidate_count,
        fact_first.sidecar.atomic_fact_lexical_candidate_count,
    ) == (20, 20, 40, 40)
    assert episode_first.sidecar.recall_route == "episode_first"
    assert (
        episode_first.sidecar.episode_vector_candidate_count,
        episode_first.sidecar.episode_lexical_candidate_count,
        episode_first.sidecar.atomic_fact_vector_candidate_count,
        episode_first.sidecar.atomic_fact_lexical_candidate_count,
    ) == (40, 40, 20, 20)


def test_episode_only_selects_query_matched_source_fact_beyond_episode_prefix() -> None:
    base = _memory("memory-a", summary="A long attributed conversation.")
    facts = tuple(
        EvidenceFact(
            fact_id=f"fact-{index}",
            repo_key=base.repo_key,
            episode_id=base.episode_id,
            kind="user_quote",
            text=(
                "The rare-tail-token answer is a cobalt telescope."
                if index == 8
                else f"Unrelated filler turn {index}."
            ),
            role="user",
            evidence=(
                replace(
                    base.evidence[0],
                    raw_event_index=index,
                    raw_event_sha256=f"{index:064x}",
                ),
            ),
        )
        for index in range(10)
    )
    memory = replace(base, facts=facts)

    result = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        planner_config=RecallPlannerConfig.for_mode("episode-only"),
        clock_ns=lambda: 0,
    ).recall(
        "What is the rare-tail-token answer?",
        repo_key=memory.repo_key,
        limit=1,
    )

    assert "cobalt telescope" in result.markdown
    assert result.sidecar.context_trace is not None
    assert "fact-8" in result.sidecar.context_trace.rendered_fact_ids
    assert result.sidecar.context_trace.omitted_memory_ids == ()


def test_episode_only_does_not_add_a_parent_through_fact_postings() -> None:
    class EmptyIndex(CandidateIndex):
        def vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

        def lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

    memory = _memory_with_fact(
        "memory-posting-only",
        fact_id="fact-alice",
        fact_text="Alice fixed the failed build and verified it.",
        event_index=1,
    )

    result = RecallEngine(
        index=EmptyIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        planner_config=RecallPlannerConfig.for_mode("episode-only"),
        clock_ns=lambda: 0,
    ).recall(
        "How did Alice fix the failed build and verify it?",
        repo_key=memory.repo_key,
        limit=1,
    )

    assert result.sidecar.ranked == ()
    assert result.sidecar.entity_posting_candidate_count == 0
    assert result.sidecar.provenance_expansion_count == 0


def test_query_matched_episode_facts_are_cached_across_recall_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _memory_with_fact(
        "memory-a",
        fact_id="fact-a",
        fact_text="The rare answer is cobalt.",
        event_index=1,
    )
    original = recall_service._query_matched_fact_ids
    call_count = 0

    def counting_query_match(
        candidate: CodingMemory,
        *,
        query: str,
        limit: int,
    ) -> tuple[str, ...]:
        nonlocal call_count
        call_count += 1
        return original(candidate, query=query, limit=limit)

    monkeypatch.setattr(recall_service, "_query_matched_fact_ids", counting_query_match)

    RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall("What is the rare answer?", repo_key=memory.repo_key, limit=1)

    assert call_count == 1


def test_coverage_selection_keeps_evidence_for_distinct_named_anchors() -> None:
    class AnchorIndex(CandidateIndex):
        def vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return tuple(
                IndexCandidate(repo_key=repo_key, memory_id=memory_id, score=score)
                for memory_id, score in (
                    ("memory-a", 3.0),
                    ("memory-b", 2.0),
                    ("memory-c", 1.0),
                )
            )

        def lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return self.vector_candidates(repo_key=repo_key, vector=(), limit=limit)

    memories = (
        _memory_with_fact(
            "memory-a", fact_id="fact-a", fact_text="Alice selected the venue.", event_index=1
        ),
        _memory_with_fact(
            "memory-b", fact_id="fact-b", fact_text="Alice ordered the flowers.", event_index=2
        ),
        _memory_with_fact(
            "memory-c", fact_id="fact-c", fact_text="Bob selected the music.", event_index=3
        ),
    )
    result = RecallEngine(
        index=AnchorIndex(),
        state=MemoryState(memories),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall("What did Alice and Bob select?", repo_key="acme/widgets", limit=2)

    assert [item.memory_id for item in result.sidecar.ranked] == ["memory-a", "memory-c"]
    assert result.sidecar.covered_slots == ("alice", "bob")
    assert result.sidecar.missing_slots == ()


def test_entity_postings_render_far_apart_anchors_from_one_parent() -> None:
    class OneFactIndex(CandidateIndex):
        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            if document_kind == "atomic_fact":
                return (
                    IndexCandidate(
                        repo_key=repo_key,
                        memory_id="memory-session",
                        score=1.0,
                        document_id="fact-alice",
                        document_kind="atomic_fact",
                        fact_id="fact-alice",
                    ),
                )
            return (
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id="memory-session",
                    score=1.0,
                    document_id="memory-session",
                    document_kind="episode",
                ),
            )

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return self.document_vector_candidates(
                repo_key=repo_key,
                vector=(),
                document_kind=document_kind,
                limit=limit,
            )

    base = _memory("memory-session", summary="One conversation session.")
    facts = tuple(
        EvidenceFact(
            fact_id=fact_id,
            repo_key=base.repo_key,
            episode_id=base.episode_id,
            kind="user_quote",
            text=text,
            role="user",
            evidence=(replace(base.evidence[0], raw_event_index=index),),
        )
        for index, (fact_id, text) in enumerate(
            (
                ("fact-alice", "Alice booked the venue."),
                ("fact-middle-1", "The weather stayed dry."),
                ("fact-middle-2", "The band arrived early."),
                ("fact-bob", "Bob selected the music."),
            ),
            start=1,
        )
    )
    memory = replace(base, facts=facts)
    result = RecallEngine(
        index=OneFactIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall("What did Alice and Bob do?", repo_key=base.repo_key, limit=1)

    assert result.sidecar.covered_slots == ("alice", "bob")
    assert result.sidecar.missing_slots == ()
    assert "Alice booked the venue." in result.markdown
    assert "Bob selected the music." in result.markdown
    assert [snippet.fact_id for snippet in result.sidecar.ranked[0].snippets[:2]] == [
        "fact-alice",
        "fact-bob",
    ]


def test_typed_entity_query_variant_gets_its_own_bounded_lexical_lane() -> None:
    class EntityVariantIndex:
        def __init__(self) -> None:
            self.lexical_queries: list[tuple[str, str, int]] = []

        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            self.lexical_queries.append((query, document_kind, limit))
            if query != "alice bob":
                return ()
            return (
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id="memory-shared",
                    score=2.0,
                    document_id=(
                        "fact-shared" if document_kind == "atomic_fact" else "memory-shared"
                    ),
                    document_kind=document_kind,
                    fact_id="fact-shared" if document_kind == "atomic_fact" else "",
                ),
            )

    index = EntityVariantIndex()
    memory = _memory_with_fact(
        "memory-shared",
        fact_id="fact-shared",
        fact_text="Alice and Bob completed the shared project.",
        event_index=1,
    )

    result = RecallEngine(
        index=index,
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall("What did Alice and Bob do?", repo_key="acme/widgets", limit=1)

    assert result.sidecar.ranked[0].memory_id == "memory-shared"
    assert ("alice bob", "episode", 3) in index.lexical_queries
    assert ("alice bob", "atomic_fact", 3) in index.lexical_queries


def test_expanded_rerank_pool_preserves_a_stable_core_lane() -> None:
    class ExpandedIndex(CandidateIndex):
        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            if document_kind == "atomic_fact":
                return ()
            return tuple(
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id=f"memory-{rank:02d}",
                    score=float(48 - rank),
                )
                for rank in range(min(limit, 48))
            )

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return self.document_vector_candidates(
                repo_key=repo_key,
                vector=(),
                document_kind=document_kind,
                limit=limit,
            )

    class ExplorerFirstReranker:
        model_id = "test/explorer-first"
        source_id = "test/explorer-first-source"
        revision = "test-v1"

        def rerank(
            self,
            query: str,
            documents: tuple[RerankDocument, ...],
        ) -> tuple[RerankScore, ...]:
            return tuple(
                RerankScore(
                    memory_id=document.memory_id,
                    score=100.0 if int(document.memory_id.rsplit("-", 1)[1]) >= 32 else 1.0,
                )
                for document in documents
            )

    memories = tuple(
        _memory(
            f"memory-{rank:02d}",
            summary=f"Candidate {rank}",
            episode_id=f"episode-{rank:02d}",
            event_index=rank,
        )
        for rank in range(48)
    )
    result = RecallEngine(
        index=ExpandedIndex(),
        state=MemoryState(memories),
        embedder=FixedEmbedder(),
        reranker=ExplorerFirstReranker(),
        clock_ns=lambda: 0,
    ).recall("repository convention", repo_key="acme/widgets", limit=20)

    selected = [item.memory_id for item in result.sidecar.ranked]
    assert selected[:16] == [f"memory-{rank:02d}" for rank in range(16)]
    assert selected[16:] == [f"memory-{rank:02d}" for rank in range(32, 36)]


def test_temporal_exploration_lane_reserves_an_explicit_month_candidate() -> None:
    ranked = [
        RankedRecall(
            rank=index + 1,
            memory_id=f"memory-{index:02d}",
            memory_type="user_preference",
            title=f"Candidate {index}",
            summary=(
                "2023-10-12T10:00:00+00:00 — Sam tried kayaking."
                if index == 20
                else f"2023-09-{index + 1:02d}T10:00:00+00:00 — Candidate {index}."
            ),
            source_uri=f"codecairn://memory/memory-{index:02d}",
            content_sha256=f"{index:064x}",
            candidate_sources=("vector",),
            vector_score=float(21 - index),
            vector_rank=index + 1,
            lexical_score=None,
            lexical_rank=None,
            final_score=float(21 - index),
            evidence=(),
        )
        for index in range(21)
    ]

    selected, _covered, _missing = core_preserving_select(
        ranked,
        core_memory_ids={f"memory-{index:02d}" for index in range(20)},
        coverage_slots=(),
        temporal_prefixes=("2023-10",),
        limit=20,
        exploration_limit=4,
    )

    assert "memory-20" in {item.memory_id for item in selected}


def test_temporal_coverage_accumulates_across_distinct_evidence_parents() -> None:
    def candidate(
        memory_id: str,
        *,
        score: float,
        fact_id: str,
        text: str,
    ) -> RankedRecall:
        return RankedRecall(
            rank=1,
            memory_id=memory_id,
            memory_type="conversation_episode",
            title=memory_id,
            summary="Conversation episode",
            source_uri=f"codecairn://memory/{memory_id}",
            content_sha256="a" * 64,
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=score,
            lexical_rank=1,
            final_score=score,
            evidence=(),
            snippets=(
                RecallSnippet(
                    relation="matched",
                    source_memory_id=memory_id,
                    source_uri=f"codecairn://memory/{memory_id}",
                    fact_id=fact_id,
                    text=text,
                    source_title=memory_id,
                    source_summary="Conversation episode",
                    raw_event_index=1,
                ),
            ),
        )

    ranked = [
        candidate("decoy", score=10.0, fact_id="fact-decoy", text="Unrelated evidence."),
        candidate(
            "time-a",
            score=9.0,
            fact_id="fact-a",
            text="2023-01-01T10:00:00+00:00 — Alice started the project.",
        ),
        candidate(
            "time-b",
            score=8.0,
            fact_id="fact-b",
            text="2023-02-01T10:00:00+00:00 — Alice finished the project.",
        ),
    ]

    selected, covered, missing = core_preserving_select(
        ranked,
        core_memory_ids={item.memory_id for item in ranked},
        coverage_slots=(),
        coverage_requirements=(
            TemporalCoverageRequirement(operation="order", prefixes=()),
            RelationCoverageRequirement(relation="temporal_order"),
        ),
        temporal_prefixes=(),
        limit=2,
        exploration_limit=0,
    )

    assert [item.memory_id for item in selected] == ["time-a", "time-b"]
    assert covered == ("temporal:order:any", "relation:temporal_order")
    assert missing == ()


def test_priority_memory_receives_neighbor_budget_before_higher_ranked_item() -> None:
    first = _memory_with_fact(
        "memory-first",
        fact_id="fact-first",
        fact_text="First matched fact.",
        event_index=1,
        episode_id="episode-first",
    )
    first_neighbor = _memory_with_fact(
        "memory-first-neighbor",
        fact_id="fact-first-neighbor",
        fact_text="First neighbor.",
        event_index=2,
        episode_id=first.episode_id,
    )
    priority = _memory_with_fact(
        "memory-priority",
        fact_id="fact-priority",
        fact_text="Priority matched fact.",
        event_index=3,
        episode_id="episode-priority",
    )
    priority_neighbor = _memory_with_fact(
        "memory-priority-neighbor",
        fact_id="fact-priority-neighbor",
        fact_text="Priority neighbor.",
        event_index=4,
        episode_id=priority.episode_id,
    )

    def ranked_item(memory: CodingMemory, *, rank: int) -> RankedRecall:
        return RankedRecall(
            rank=rank,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri=f"codecairn://memory/{memory.memory_id}",
            content_sha256=memory.content_sha256,
            candidate_sources=("vector",),
            vector_score=1.0,
            vector_rank=rank,
            lexical_score=None,
            lexical_rank=None,
            final_score=1.0,
            evidence=(),
        )

    ranked = [ranked_item(first, rank=1), ranked_item(priority, rank=2)]
    engine = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((first, first_neighbor, priority, priority_neighbor)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    enriched, neighbor_count = engine._attach_snippets(
        ranked,
        repo_key="acme/widgets",
        expand_neighbors=True,
        neighbor_window=1,
        neighbor_snippet_budget=1,
        priority_memory_ids={priority.memory_id},
    )

    assert neighbor_count == 1
    assert enriched[0].snippets == ()
    assert [item.text for item in enriched[1].snippets] == ["Priority neighbor."]


def test_snippet_selection_keeps_one_distinct_vector_fact_after_lexical_matches() -> None:
    base = _memory("memory-session", summary="Session summary.", event_index=10)
    memory = replace(
        base,
        facts=tuple(
            EvidenceFact(
                fact_id=f"fact-{index}",
                repo_key=base.repo_key,
                episode_id=base.episode_id,
                kind="user_quote",
                text=f"Fact {index}.",
                role="user",
                evidence=(
                    replace(
                        base.evidence[0],
                        raw_event_index=10 + index,
                        raw_event_sha256=f"{10 + index:064x}",
                    ),
                ),
            )
            for index in range(4)
        ),
    )
    matches = (
        *(
            RecallMatch(
                document_id=f"lexical-{index}",
                document_kind="atomic_fact",
                source="atomic_fact_lexical",
                score=10.0 - index,
                rank=index + 1,
                fact_id=f"fact-{index}",
            )
            for index in range(3)
        ),
        RecallMatch(
            document_id="vector-3",
            document_kind="atomic_fact",
            source="atomic_fact_vector",
            score=0.9,
            rank=1,
            fact_id="fact-3",
        ),
    )
    ranked = [
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri=f"codecairn://memory/{memory.memory_id}",
            content_sha256=memory.content_sha256,
            candidate_sources=("lexical", "vector"),
            vector_score=0.9,
            vector_rank=1,
            lexical_score=10.0,
            lexical_rank=1,
            final_score=1.0,
            evidence=(),
            matched_documents=matches,
        )
    ]
    engine = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    enriched, _ = engine._attach_snippets(
        ranked,
        repo_key=memory.repo_key,
        expand_neighbors=False,
    )

    assert [snippet.fact_id for snippet in enriched[0].snippets] == [
        "fact-0",
        "fact-1",
        "fact-2",
        "fact-3",
    ]


def test_temporal_priority_uses_a_bounded_local_fact_window() -> None:
    base = _memory("memory-temporal", summary="Temporal session.", event_index=20)
    memory = replace(
        base,
        facts=tuple(
            EvidenceFact(
                fact_id=f"fact-{index}",
                repo_key=base.repo_key,
                episode_id=base.episode_id,
                kind="user_quote",
                text=f"Turn {index}.",
                role="user",
                evidence=(
                    replace(
                        base.evidence[0],
                        raw_event_index=20 + index,
                        raw_event_sha256=f"{20 + index:064x}",
                    ),
                ),
            )
            for index in range(7)
        ),
    )
    ranked = [
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri=f"codecairn://memory/{memory.memory_id}",
            content_sha256=memory.content_sha256,
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=5.0,
            lexical_rank=1,
            final_score=1.0,
            evidence=(),
            matched_documents=(
                RecallMatch(
                    document_id="lexical-2",
                    document_kind="atomic_fact",
                    source="atomic_fact_lexical",
                    score=5.0,
                    rank=1,
                    fact_id="fact-2",
                ),
            ),
        )
    ]
    engine = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    enriched, _ = engine._attach_snippets(
        ranked,
        repo_key=memory.repo_key,
        expand_neighbors=False,
        wide_sibling_memory_ids={memory.memory_id},
    )

    assert [(snippet.fact_id, snippet.relation) for snippet in enriched[0].snippets] == [
        ("fact-2", "matched"),
        ("fact-3", "sibling"),
        ("fact-1", "sibling"),
        ("fact-4", "sibling"),
        ("fact-0", "sibling"),
        ("fact-5", "sibling"),
    ]


def test_context_budget_preserves_complete_facts_and_prioritizes_temporal_siblings() -> None:
    ranked = tuple(
        RankedRecall(
            rank=rank,
            memory_id=f"memory-{rank}",
            memory_type="repository_convention",
            title=f"Memory {rank}",
            summary="S" * 1_000,
            source_uri=f"codecairn://memory/memory-{rank}",
            content_sha256=f"{rank:064x}",
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=1.0,
            lexical_rank=rank,
            final_score=1.0 / rank,
            evidence=(),
            snippets=tuple(
                RecallSnippet(
                    relation=("matched" if index < 2 else "sibling"),
                    source_memory_id=f"memory-{rank}",
                    source_uri=f"codecairn://memory/memory-{rank}",
                    fact_id=f"fact-{rank}-{index}",
                    text=f"evidence-{rank}-{index}-" + "X" * 1_000 + f"-TAIL-{rank}-{index}",
                    source_title=f"Memory {rank}",
                    source_summary="summary",
                    raw_event_index=index,
                )
                for index in range(8)
            ),
        )
        for rank in range(1, 21)
    )

    compiled = compile_context(
        "When did the event happen?",
        repo_key="acme/widgets",
        ranked=ranked,
        temporal_priority_memory_ids={"memory-1"},
        config=RecallPlannerConfig(),
    )

    trace = compiled.trace
    assert trace is not None
    assert trace.token_count <= trace.token_limit == 4_000
    assert 0 < len(trace.rendered_memory_ids) < len(ranked)
    assert set(trace.rendered_memory_ids).isdisjoint(trace.omitted_memory_ids)
    assert set(trace.rendered_memory_ids) | set(trace.omitted_memory_ids) == {
        item.memory_id for item in ranked
    }
    for fact_id in trace.rendered_fact_ids:
        _prefix, rank, index = fact_id.split("-")
        assert f"[{fact_id}]" in compiled.markdown
        assert f"-TAIL-{rank}-{index}" in compiled.markdown

    temporal_rendered = render_context(
        "When did the event happen?",
        repo_key="acme/widgets",
        ranked=ranked[:1],
        temporal_priority_memory_ids={"memory-1"},
        config=RecallPlannerConfig(),
    )
    assert temporal_rendered.index("evidence-1-0") < temporal_rendered.index("evidence-1-2")
    assert temporal_rendered.index("evidence-1-0") < temporal_rendered.index("evidence-1-1")
    if "evidence-1-2" in temporal_rendered:
        assert temporal_rendered.index("evidence-1-1") < temporal_rendered.index("evidence-1-2")


def test_context_budget_keeps_compact_evidence_from_every_large_parent() -> None:
    ranked = tuple(
        RankedRecall(
            rank=rank,
            memory_id=f"memory-{rank}",
            memory_type="conversation_episode",
            title=f"Episode {rank}",
            summary="Oversized parent",
            source_uri=f"codecairn://memory/memory-{rank}",
            content_sha256=f"{rank:064x}",
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=1.0,
            lexical_rank=rank,
            final_score=1.0 / rank,
            evidence=(),
            snippets=(
                RecallSnippet(
                    relation="matched",
                    source_memory_id=f"memory-{rank}",
                    source_uri=f"codecairn://memory/memory-{rank}",
                    fact_id=f"fact-{rank}",
                    text=f"Anchor {rank}",
                    source_title=f"Episode {rank}",
                    source_summary="Oversized parent",
                    raw_event_index=rank,
                ),
            ),
            episode_text="X" * 30_000,
        )
        for rank in (1, 2)
    )

    compiled = compile_context(
        "Find the anchor",
        repo_key="acme/widgets",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert len(compiled.markdown) <= 23_900
    assert compiled.partial_episode_ids == ("memory-1", "memory-2")
    assert compiled.dropped_episode_ids == ()
    assert "## 1. Episode 1" not in compiled.markdown
    assert "## 2. Episode 2" not in compiled.markdown
    assert "[fact-1]" in compiled.markdown
    assert "[fact-2]" in compiled.markdown
    assert "Anchor 1" in compiled.markdown
    assert "Anchor 2" in compiled.markdown


@pytest.mark.parametrize(
    ("query", "expected_prefix"),
    (
        ("Which activity did Sam consider in October 2023?", "2023-10"),
        ("Which activity did Sam consider on 4th October, 2023?", "2023-10-04"),
    ),
)
def test_explicit_calendar_prefix_adds_a_bounded_temporal_lexical_channel(
    query: str,
    expected_prefix: str,
) -> None:
    class TemporalIndex(CandidateIndex):
        def __init__(self) -> None:
            self.lexical_queries: list[tuple[str, str, int]] = []

        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            self.lexical_queries.append((document_kind, query, limit))
            if not query.startswith(expected_prefix):
                return ()
            return (
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id="memory-october",
                    score=2.0,
                    document_id=(
                        "memory-october" if document_kind == "episode" else "fact-october"
                    ),
                    document_kind=document_kind,
                    fact_id="" if document_kind == "episode" else "fact-october",
                ),
            )

    memory = _memory_with_fact(
        "memory-october",
        fact_id="fact-october",
        fact_text="Sam considered kayaking.",
        event_index=1,
    )
    index = TemporalIndex()
    result = RecallEngine(
        index=index,
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall(
        query,
        repo_key="acme/widgets",
        limit=5,
    )

    expected_query = f"{expected_prefix} sam"
    assert any(query == expected_query for _, query, _ in index.lexical_queries)
    assert all(
        limit <= 32
        for _, query, limit in index.lexical_queries
        if query.startswith(expected_prefix)
    )
    assert result.sidecar.episode_temporal_lexical_candidate_count == 1
    assert result.sidecar.atomic_fact_temporal_lexical_candidate_count == 1
    assert result.sidecar.ranked[0].memory_id == "memory-october"


def test_empty_recall_reports_partial_completion() -> None:
    class EmptyIndex(CandidateIndex):
        def vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

        def lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

    result = RecallEngine(
        index=EmptyIndex(),
        state=MemoryState(()),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall("repository convention", repo_key="acme/widgets")

    assert result.sidecar.ranked == ()
    assert result.sidecar.completion == "partial"
    assert result.sidecar.degraded_stages == ("no_candidates",)


def test_rerank_budget_preserves_focal_fact_before_long_parent_summary() -> None:
    class FactIndex(CandidateIndex):
        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            if document_kind == "episode":
                return ()
            return (
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id="memory-long",
                    document_id="fact-document",
                    document_kind="atomic_fact",
                    parent_document_id="episode-document",
                    fact_id="fact-focal",
                    score=1.0,
                ),
            )

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

    class CaptureReranker:
        model_id = "test/capture"
        source_id = "test/capture-source"
        revision = "test-v1"

        def __init__(self) -> None:
            self.text = ""

        def rerank(
            self,
            query: str,
            documents: tuple[RerankDocument, ...],
        ) -> tuple[RerankScore, ...]:
            self.text = documents[0].text
            return (RerankScore(memory_id=documents[0].memory_id, score=1.0),)

    memory = _memory_with_fact(
        "memory-long",
        fact_id="fact-focal",
        fact_text="FOCAL-EVIDENCE must survive the rerank budget.",
        event_index=1,
    )
    memory = replace(memory, summary="unrelated " * 1_000)
    reranker = CaptureReranker()

    RecallEngine(
        index=FactIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        reranker=reranker,
        clock_ns=lambda: 0,
    ).recall("Which focal evidence?", repo_key="acme/widgets", limit=1)

    assert "FOCAL-EVIDENCE" in reranker.text
    assert len(reranker.text) <= 2_048


def test_equal_component_scores_use_memory_id_as_the_stable_tie_breaker() -> None:
    class EqualIndex(CandidateIndex):
        def vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return (
                IndexCandidate(repo_key=repo_key, memory_id="memory-b", score=1.0),
                IndexCandidate(repo_key=repo_key, memory_id="memory-a", score=1.0),
            )

        def lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

    engine = RecallEngine(
        index=EqualIndex(),
        state=MemoryState((_memory("memory-a"), _memory("memory-b"))),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    result = engine.recall("same score", repo_key="acme/widgets", limit=2)

    assert [item.memory_id for item in result.sidecar.ranked] == ["memory-a", "memory-b"]


def test_recall_fails_closed_when_the_reranker_omits_a_candidate() -> None:
    engine = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((_memory("memory-a"), _memory("memory-b"))),
        embedder=FixedEmbedder(),
        reranker=IncompleteReranker(),
        clock_ns=lambda: 0,
    )

    with pytest.raises(ValueError, match="did not score every candidate"):
        engine.recall("fix the widget test", repo_key="acme/widgets", limit=2)


def test_recall_fails_closed_on_a_non_finite_candidate_score() -> None:
    class NonFiniteIndex(CandidateIndex):
        def vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return (IndexCandidate(repo_key=repo_key, memory_id="memory-a", score=float("nan")),)

    engine = RecallEngine(
        index=NonFiniteIndex(),
        state=MemoryState((_memory("memory-a"),)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    with pytest.raises(ValueError, match="non-finite candidate score"):
        engine.recall("same score", repo_key="acme/widgets", limit=1)


def test_lancedb_fts_candidate_is_independent_of_vector_shortlist(tmp_path: Path) -> None:
    index = LanceMemoryIndex(tmp_path / "index.lancedb", embedder=PurposeEmbedder())
    decoy = _memory("memory-decoy", title="Decoy", summary="vector decoy")
    lexical = _memory("memory-lexical", title="Exact", summary="contains needle token")
    index.upsert(decoy, markdown="vector decoy without the term")
    index.upsert(lexical, markdown="exact lexical needle lives here")

    vector = index.vector_candidates(
        repo_key="acme/widgets",
        vector=PurposeEmbedder().embed_query("needle"),
        limit=1,
    )
    lexical_candidates = index.lexical_candidates(
        repo_key="acme/widgets",
        query="needle",
        limit=1,
    )

    assert [item.memory_id for item in vector] == ["memory-decoy"]
    assert [item.memory_id for item in lexical_candidates] == ["memory-lexical"]


def test_episode_recall_searches_its_aggregated_fact_text(tmp_path: Path) -> None:
    index = LanceMemoryIndex(tmp_path / "index.lancedb", embedder=FixedEmbedder())
    base = _memory("memory-hierarchical")
    fact = EvidenceFact(
        fact_id="fact-child-only",
        repo_key=base.repo_key,
        episode_id=base.episode_id,
        kind="repository_rule",
        text="childonlytoken",
        role=None,
        evidence=base.evidence,
    )
    memory = replace(base, facts=(fact,))
    markdown = '---\nfacts: [{"text": "childonlytoken"}]\n---\n\n# Memory\n'

    index.upsert(memory, markdown=markdown)

    candidates = index.lexical_candidates(
        repo_key=memory.repo_key,
        query="childonlytoken",
        limit=5,
    )

    assert [item.memory_id for item in candidates] == [memory.memory_id]
    assert len(index.document_fingerprints()) == 2


def test_hierarchical_recall_lifts_atomic_fact_hits_to_the_parent_memory(
    tmp_path: Path,
) -> None:
    index = LanceMemoryIndex(tmp_path / "index.lancedb", embedder=FixedEmbedder())
    base = _memory("memory-hierarchical", summary="Parent text omits the rare token")
    fact = EvidenceFact(
        fact_id="fact-child-only",
        repo_key=base.repo_key,
        episode_id=base.episode_id,
        kind="repository_rule",
        text="childonlytoken",
        role=None,
        evidence=base.evidence,
    )
    memory = replace(base, facts=(fact,))
    index.upsert(memory, markdown="# Parent without the queried term")
    state = MemoryState((memory,))
    engine = RecallEngine(
        index=index,
        state=state,
        embedder=FixedEmbedder(),
        planner_config=RecallPlannerConfig.for_mode("hierarchy-no-neighbors"),
        clock_ns=lambda: 0,
    )

    result = engine.recall("childonlytoken", repo_key=memory.repo_key, limit=1)

    assert [item.memory_id for item in result.sidecar.ranked] == [memory.memory_id]
    assert result.sidecar.atomic_fact_lexical_candidate_count == 1
    assert {match.source for match in result.sidecar.ranked[0].matched_documents} >= {
        "atomic_fact_lexical"
    }
    assert [snippet.relation for snippet in result.sidecar.ranked[0].snippets] == ["matched"]
    assert "childonlytoken" in result.markdown
    assert set(state.requested) == {(memory.repo_key, memory.memory_id)}
    assert state.episode_requests == []


def test_hierarchical_recall_pairs_a_matched_turn_with_its_following_fact() -> None:
    class QuestionFactIndex(CandidateIndex):
        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            if document_kind == "episode":
                return ()
            return (
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id="memory-session",
                    document_id="fact-question-document",
                    document_kind="atomic_fact",
                    parent_document_id="episode-session",
                    fact_id="fact-question",
                    score=7.0,
                ),
            )

    base = _memory("memory-session", summary="A long session summary.", event_index=10)
    facts = tuple(
        EvidenceFact(
            fact_id=fact_id,
            repo_key=base.repo_key,
            episode_id=base.episode_id,
            kind="user_quote",
            text=text,
            role="user",
            evidence=(
                replace(
                    base.evidence[0],
                    raw_event_index=event_index,
                    raw_event_sha256=f"{event_index:064x}",
                ),
            ),
        )
        for fact_id, text, event_index in (
            ("fact-before", "John introduces the topic.", 10),
            ("fact-question", "What kind of music do you like?", 11),
            ("fact-answer", "John likes electronic and rock music.", 12),
            ("fact-after", "They continue chatting.", 13),
        )
    )
    memory = replace(base, facts=facts)

    result = RecallEngine(
        index=QuestionFactIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        planner_config=RecallPlannerConfig.for_mode("hierarchy-no-neighbors"),
        clock_ns=lambda: 0,
    ).recall("What music does John like?", repo_key=base.repo_key, limit=1)

    assert [
        (snippet.fact_id, snippet.relation) for snippet in result.sidecar.ranked[0].snippets
    ] == [
        ("fact-question", "matched"),
        ("fact-answer", "sibling"),
        ("fact-before", "sibling"),
    ]
    assert "John likes electronic and rock music." in result.markdown
    assert "A long session summary." not in result.markdown


def test_hierarchical_recall_expands_only_adjacent_memories_in_the_same_episode() -> None:
    class FactOnlyIndex(CandidateIndex):
        def document_vector_candidates(
            self,
            *,
            repo_key: str,
            vector: tuple[float, ...],
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            return ()

        def document_lexical_candidates(
            self,
            *,
            repo_key: str,
            query: str,
            document_kind: str,
            limit: int,
        ) -> tuple[IndexCandidate, ...]:
            if document_kind == "episode":
                return ()
            return (
                IndexCandidate(
                    repo_key=repo_key,
                    memory_id="memory-b",
                    document_id="fact-document-b",
                    document_kind="atomic_fact",
                    parent_document_id="episode-document-b",
                    fact_id="fact-b",
                    score=7.0,
                ),
            )

    before = _memory_with_fact(
        "memory-a", fact_id="fact-a", fact_text="Alice booked the venue.", event_index=1
    )
    matched = _memory_with_fact(
        "memory-b", fact_id="fact-b", fact_text="Bob chose blue flowers.", event_index=2
    )
    after = _memory_with_fact(
        "memory-c", fact_id="fact-c", fact_text="Carol ordered the cake.", event_index=3
    )
    second_after = _memory_with_fact(
        "memory-e", fact_id="fact-e", fact_text="Dana confirmed the date.", event_index=4
    )
    unrelated = _memory_with_fact(
        "memory-d",
        fact_id="fact-d",
        fact_text="This must remain isolated.",
        event_index=5,
        episode_id="episode-other",
    )
    state = MemoryState((before, matched, after, second_after, unrelated))
    engine = RecallEngine(
        index=FactOnlyIndex(),
        state=state,
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    result = engine.recall("When did Bob choose the flowers?", repo_key="acme/widgets", limit=1)

    snippets = result.sidecar.ranked[0].snippets
    assert [(item.source_memory_id, item.relation) for item in snippets] == [
        ("memory-b", "matched"),
        ("memory-a", "neighbor"),
        ("memory-c", "neighbor"),
        ("memory-e", "neighbor"),
    ]
    assert result.sidecar.neighbor_expansion_count == 3
    assert result.sidecar.neighbor_window == 2
    assert "Alice booked the venue." in result.markdown
    assert "Carol ordered the cake." in result.markdown
    assert "This must remain isolated." not in result.markdown
    assert state.episode_requests == [("acme/widgets", matched.episode_id)]


def test_neighbor_context_is_added_only_after_reranking_and_top_k_selection() -> None:
    class CapturingReranker:
        model_id = "test/capturing-reranker"
        source_id = "test/capturing-reranker-source"
        revision = "test-v1"

        def __init__(self) -> None:
            self.documents: tuple[RerankDocument, ...] = ()

        def rerank(
            self,
            query: str,
            documents: tuple[RerankDocument, ...],
        ) -> tuple[RerankScore, ...]:
            self.documents = documents
            return tuple(
                RerankScore(
                    memory_id=document.memory_id,
                    score=2.0 if document.memory_id == "memory-b" else 1.0,
                )
                for document in documents
            )

    before = _memory_with_fact(
        "memory-a", fact_id="fact-a", fact_text="Alice booked the venue.", event_index=1
    )
    selected = _memory_with_fact(
        "memory-b", fact_id="fact-b", fact_text="Bob chose blue flowers.", event_index=2
    )
    reranker = CapturingReranker()
    engine = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((before, selected)),
        embedder=FixedEmbedder(),
        reranker=reranker,
        clock_ns=lambda: 0,
    )

    result = engine.recall("Who chose the flowers?", repo_key="acme/widgets", limit=1)

    assert all("neighbor:" not in document.text for document in reranker.documents)
    assert [item.memory_id for item in result.sidecar.ranked] == ["memory-b"]
    snippet_relations = [
        (item.source_memory_id, item.relation) for item in result.sidecar.ranked[0].snippets
    ]
    assert snippet_relations == [
        ("memory-b", "matched"),
        ("memory-a", "neighbor"),
    ]
    assert result.sidecar.neighbor_expansion_count == 1


def test_neighbor_context_obeys_one_global_snippet_budget() -> None:
    selected = _memory_with_fact(
        "memory-a", fact_id="fact-a", fact_text="Selected fact.", event_index=1
    )
    first_neighbor = _memory_with_fact(
        "memory-b", fact_id="fact-b", fact_text="First neighbor.", event_index=2
    )
    second_neighbor = _memory_with_fact(
        "memory-c", fact_id="fact-c", fact_text="Second neighbor.", event_index=3
    )
    state = MemoryState((selected, first_neighbor, second_neighbor))
    engine = RecallEngine(
        index=CandidateIndex(),
        state=state,
        embedder=FixedEmbedder(),
        planner_config=RecallPlannerConfig(neighbor_window=2, neighbor_snippet_budget=1),
        clock_ns=lambda: 0,
    )

    result = engine.recall("repository convention", repo_key="acme/widgets", limit=1)

    assert result.sidecar.neighbor_expansion_count == 1
    assert state.episode_limits == [2]
    assert [(item.source_memory_id, item.text) for item in result.sidecar.ranked[0].snippets] == [
        ("memory-a", "Selected fact."),
        ("memory-b", "First neighbor."),
    ]


def test_typed_temporal_requirement_marks_single_undated_fact_partial() -> None:
    memory = _memory_with_fact(
        "memory-alice",
        fact_id="fact-alice",
        fact_text="Alice completed the task.",
        event_index=1,
    )
    result = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall("What did Alice do before the next task?", repo_key="acme/widgets", limit=1)

    assert "relation:temporal_order" in result.sidecar.missing_requirements
    assert result.sidecar.completion == "partial"


def test_entity_posting_expansion_is_a_fact_budget_not_a_parent_budget() -> None:
    base = _memory("memory-many-entities", summary="Many entity facts.")
    facts = tuple(
        EvidenceFact(
            fact_id=f"fact-{index}",
            repo_key=base.repo_key,
            episode_id=base.episode_id,
            kind="user_quote",
            text=f"Alpha{index} completed item {index}.",
            role="user",
            evidence=(replace(base.evidence[0], raw_event_index=index),),
        )
        for index in range(15)
    )
    memory = replace(base, facts=facts)
    query = "What did " + " ".join(f"Alpha{index}" for index in range(15)) + " do?"

    result = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((memory,)),
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    ).recall(query, repo_key=base.repo_key, limit=1)

    expansion_components = (
        result.sidecar.episode_entity_lexical_candidate_count
        + result.sidecar.atomic_fact_entity_lexical_candidate_count
        + result.sidecar.episode_temporal_lexical_candidate_count
        + result.sidecar.atomic_fact_temporal_lexical_candidate_count
        + result.sidecar.entity_posting_candidate_count
        + result.sidecar.provenance_expansion_count
        + result.sidecar.neighbor_expansion_count
    )
    assert result.sidecar.entity_posting_candidate_count <= 12
    assert result.sidecar.expansion_fact_count == expansion_components
    assert result.sidecar.expansion_fact_count <= result.sidecar.expansion_fact_limit == 24


def test_lancedb_reembeds_existing_documents_when_the_model_identity_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "index.lancedb"
    initial = LanceMemoryIndex(path, embedder=PurposeEmbedder())
    memory = _memory("memory-model-migration", summary="migration target")
    initial.upsert(memory, markdown="migration target")

    migrated = LanceMemoryIndex(path, embedder=ShiftedPurposeEmbedder())
    candidates = migrated.vector_candidates(
        repo_key=memory.repo_key,
        vector=ShiftedPurposeEmbedder().embed_query("migration target"),
        limit=1,
    )

    assert migrated.embedding_config == {
        "adapter": "fastembed-compatible",
        "model": "test/purpose",
        "source": "test/purpose-source",
        "revision": "test-v2",
        "index_identity": "test:test/purpose-source@test-v2:256",
        "dimension": 256,
    }
    assert candidates[0].memory_id == memory.memory_id
    assert candidates[0].score == pytest.approx(1.0)


def test_lancedb_rejects_non_finite_document_vectors(tmp_path: Path) -> None:
    class NonFiniteEmbedder(FixedEmbedder):
        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            vector = (float("inf"),) + (0.0,) * 255
            return tuple(vector for _text in texts)

    index = LanceMemoryIndex(tmp_path / "index.lancedb", embedder=NonFiniteEmbedder())

    with pytest.raises(ValueError, match="only finite values"):
        index.upsert(_memory("memory-non-finite"), markdown="invalid vector")


def test_lancedb_serializes_revision_migration_with_concurrent_upsert(tmp_path: Path) -> None:
    path = tmp_path / "index.lancedb"
    initial = LanceMemoryIndex(path, embedder=PurposeEmbedder())
    initial.upsert(_memory("memory-a"), markdown="initial")
    migrating = LanceMemoryIndex(path, embedder=ShiftedPurposeEmbedder())
    writing = LanceMemoryIndex(path, embedder=ShiftedPurposeEmbedder())

    with ThreadPoolExecutor(max_workers=2) as executor:
        migration = executor.submit(
            migrating.vector_candidates,
            repo_key="acme/widgets",
            vector=ShiftedPurposeEmbedder().embed_query("initial"),
            limit=1,
        )
        upsert = executor.submit(
            writing.upsert,
            _memory("memory-b"),
            markdown="concurrent",
        )
        migration.result()
        upsert.result()

    assert {memory_id for _repo, memory_id, _digest in migrating.fingerprints()} == {
        "memory-a",
        "memory-b",
    }


def _memory(
    memory_id: str,
    *,
    title: str = "Memory",
    summary: str = "Summary",
    episode_id: str = "episode-1",
    event_index: int = 1,
) -> CodingMemory:
    return CodingMemory(
        memory_id=memory_id,
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title=title,
        summary=summary,
        episode_id=episode_id,
        command=None,
        exit_code=None,
        evidence=(
            EvidenceReference(
                provider="codex",
                session_id="session-1",
                source_path="/private/session.jsonl",
                raw_event_sha256="a" * 64,
                raw_event_index=event_index,
                raw_event_type="response_item",
            ),
        ),
        content_sha256=(memory_id.encode().hex() + "0" * 64)[:64],
        markdown_path=f"/runtime/{memory_id}.md",
    )


def _memory_with_fact(
    memory_id: str,
    *,
    fact_id: str,
    fact_text: str,
    event_index: int,
    episode_id: str = "episode-1",
) -> CodingMemory:
    base = _memory(
        memory_id,
        summary=fact_text,
        episode_id=episode_id,
        event_index=event_index,
    )
    return replace(
        base,
        facts=(
            EvidenceFact(
                fact_id=fact_id,
                repo_key=base.repo_key,
                episode_id=base.episode_id,
                kind="user_quote",
                text=fact_text,
                role="user",
                evidence=base.evidence,
            ),
        ),
    )
