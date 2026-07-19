from __future__ import annotations

from pathlib import Path

from codecairn.memory.models import CodingMemory, EvidenceReference, IndexCandidate
from codecairn.service.recall import RecallEngine
from codecairn.storage.lance import LanceMemoryIndex


class FixedEmbedder:
    def embed(self, text: str) -> tuple[float, ...]:
        return (1.0,) + (0.0,) * 255


class PurposeEmbedder:
    def embed(self, text: str) -> tuple[float, ...]:
        if text == "needle" or "vector decoy" in text:
            return (1.0,) + (0.0,) * 255
        return (0.0, 1.0) + (0.0,) * 254


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
        clock_ns=lambda: next(ticks),
    )

    result = engine.recall("fix the widget test", repo_key="acme/widgets", limit=5)

    assert [item.memory_id for item in result.sidecar.ranked] == ["memory-a", "memory-b"]
    assert result.sidecar.ranked[0].candidate_sources == ("lexical", "vector")
    assert result.sidecar.ranked[1].candidate_sources == ("lexical",)
    assert result.sidecar.ranked[1].vector_score is None
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


def test_lancedb_fts_candidate_is_independent_of_vector_shortlist(tmp_path: Path) -> None:
    index = LanceMemoryIndex(tmp_path / "index.lancedb", embedder=PurposeEmbedder())
    decoy = _memory("memory-decoy", title="Decoy", summary="vector decoy")
    lexical = _memory("memory-lexical", title="Exact", summary="contains needle token")
    index.upsert(decoy, markdown="vector decoy without the term")
    index.upsert(lexical, markdown="exact lexical needle lives here")

    vector = index.vector_candidates(
        repo_key="acme/widgets",
        vector=PurposeEmbedder().embed("needle"),
        limit=1,
    )
    lexical_candidates = index.lexical_candidates(
        repo_key="acme/widgets",
        query="needle",
        limit=1,
    )

    assert [item.memory_id for item in vector] == ["memory-decoy"]
    assert [item.memory_id for item in lexical_candidates] == ["memory-lexical"]


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
