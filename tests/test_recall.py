from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    IndexCandidate,
    RerankDocument,
    RerankScore,
)
from codecairn.memory.recall_planner import RecallPlannerConfig
from codecairn.service.recall import RecallEngine
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

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None:
        self.requested.append((repo_key, memory_id))
        return self._memories.get((repo_key, memory_id))

    def list_episode_memories(
        self,
        *,
        repo_key: str,
        episode_id: str,
    ) -> tuple[CodingMemory, ...]:
        self.episode_requests.append((repo_key, episode_id))
        return tuple(
            memory
            for (memory_repo_key, _memory_id), memory in self._memories.items()
            if memory_repo_key == repo_key and memory.episode_id == episode_id
        )

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
        _memory("memory-a", title="Vector and lexical", summary="Shared candidate."),
        _memory("memory-b", title="Lexical only", summary="BM25 found this memory."),
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
    assert "codecairn://memory/memory-b" in result.markdown
    assert result.sidecar.ranked[0].evidence[0].raw_event_index == 1


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
        _memory("memory-a", summary="Alice selected the venue."),
        _memory("memory-b", summary="Alice ordered the flowers."),
        _memory("memory-c", summary="Bob selected the music."),
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


def test_episode_recall_does_not_search_atomic_fact_only_text(tmp_path: Path) -> None:
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

    assert (
        index.lexical_candidates(
            repo_key=memory.repo_key,
            query="childonlytoken",
            limit=5,
        )
        == ()
    )
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
    assert result.markdown.count("A long session summary.") == 1


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
    unrelated = _memory_with_fact(
        "memory-d",
        fact_id="fact-d",
        fact_text="This must remain isolated.",
        event_index=4,
        episode_id="episode-other",
    )
    state = MemoryState((before, matched, after, unrelated))
    engine = RecallEngine(
        index=FactOnlyIndex(),
        state=state,
        embedder=FixedEmbedder(),
        clock_ns=lambda: 0,
    )

    result = engine.recall("Who chose the flowers?", repo_key="acme/widgets", limit=1)

    snippets = result.sidecar.ranked[0].snippets
    assert [(item.source_memory_id, item.relation) for item in snippets] == [
        ("memory-b", "matched"),
        ("memory-a", "neighbor"),
        ("memory-c", "neighbor"),
    ]
    assert result.sidecar.neighbor_expansion_count == 2
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
    assert snippet_relations == [("memory-a", "neighbor")]
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
    engine = RecallEngine(
        index=CandidateIndex(),
        state=MemoryState((selected, first_neighbor, second_neighbor)),
        embedder=FixedEmbedder(),
        planner_config=RecallPlannerConfig(neighbor_window=2, neighbor_snippet_budget=1),
        clock_ns=lambda: 0,
    )

    result = engine.recall("repository convention", repo_key="acme/widgets", limit=1)

    assert result.sidecar.neighbor_expansion_count == 1
    assert [(item.source_memory_id, item.text) for item in result.sidecar.ranked[0].snippets] == [
        ("memory-b", "First neighbor.")
    ]


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
