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


class MemoryState:
    def __init__(self, memories: tuple[CodingMemory, ...]) -> None:
        self._memories = {(item.repo_key, item.memory_id): item for item in memories}
        self.requested: list[tuple[str, str]] = []

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None:
        self.requested.append((repo_key, memory_id))
        return self._memories.get((repo_key, memory_id))


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
) -> CodingMemory:
    return CodingMemory(
        memory_id=memory_id,
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title=title,
        summary=summary,
        episode_id="episode-1",
        command=None,
        exit_code=None,
        evidence=(
            EvidenceReference(
                provider="codex",
                session_id="session-1",
                source_path="/private/session.jsonl",
                raw_event_sha256="a" * 64,
                raw_event_index=1,
                raw_event_type="response_item",
            ),
        ),
        content_sha256=(memory_id.encode().hex() + "0" * 64)[:64],
        markdown_path=f"/runtime/{memory_id}.md",
    )
