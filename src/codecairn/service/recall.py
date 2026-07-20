from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from math import isfinite
from typing import Protocol, cast
from urllib.parse import quote

from codecairn.memory.embedding import EmbeddingProvider
from codecairn.memory.models import (
    CandidateSource,
    CodingMemory,
    IndexCandidate,
    RankedRecall,
    RecallEvidence,
    RecallResult,
    RecallSidecar,
    RerankDocument,
    RerankScore,
)
from codecairn.memory.reranking import RerankingProvider

_RRF_K = 60
_MAX_LIMIT = 20
_MAX_QUERY_CHARS = 8_000


class RecallIndex(Protocol):
    def vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[IndexCandidate, ...]: ...

    def lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        limit: int,
    ) -> tuple[IndexCandidate, ...]: ...


class RecallState(Protocol):
    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...


class RecallEngine:
    """Union independent vector and lexical candidates before stable reranking."""

    def __init__(
        self,
        *,
        index: RecallIndex,
        state: RecallState,
        embedder: EmbeddingProvider,
        reranker: RerankingProvider | None = None,
        retrieval_config_sha256: str | None = None,
        clock_ns: Callable[[], int] | None = None,
    ) -> None:
        self._index = index
        self._state = state
        self._embedder = embedder
        self._reranker = reranker
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
        candidate_limit = max(20, limit * 4)
        vector = _safe_candidates(
            self._index.vector_candidates(
                repo_key=repo_key,
                vector=self._embedder.embed_query(normalized_query),
                limit=candidate_limit,
            ),
            repo_key=repo_key,
        )
        lexical = _safe_candidates(
            self._index.lexical_candidates(
                repo_key=repo_key,
                query=normalized_query,
                limit=candidate_limit,
            ),
            repo_key=repo_key,
        )
        vector_components = _component_map(vector)
        lexical_components = _component_map(lexical)
        ranked: list[RankedRecall] = []
        for memory_id in sorted(vector_components.keys() | lexical_components.keys()):
            memory = self._state.get_memory(repo_key=repo_key, memory_id=memory_id)
            if memory is None or memory.repo_key != repo_key or memory.content_sha256 is None:
                continue
            vector_component = vector_components.get(memory_id)
            lexical_component = lexical_components.get(memory_id)
            sources = tuple(
                cast(CandidateSource, source)
                for source, component in (
                    ("lexical", lexical_component),
                    ("vector", vector_component),
                )
                if component is not None
            )
            final_score = sum(
                1.0 / (_RRF_K + component[1])
                for component in (vector_component, lexical_component)
                if component is not None
            )
            ranked.append(
                RankedRecall(
                    rank=0,
                    memory_id=memory.memory_id,
                    memory_type=memory.memory_type,
                    title=memory.title,
                    summary=memory.summary,
                    source_uri=f"codecairn://memory/{quote(memory.memory_id, safe='')}",
                    content_sha256=memory.content_sha256,
                    candidate_sources=sources,
                    vector_score=None if vector_component is None else vector_component[0],
                    vector_rank=None if vector_component is None else vector_component[1],
                    lexical_score=None if lexical_component is None else lexical_component[0],
                    lexical_rank=None if lexical_component is None else lexical_component[1],
                    final_score=round(final_score, 12),
                    evidence=tuple(
                        RecallEvidence(
                            provider=item.provider,
                            session_id=item.session_id,
                            raw_event_sha256=item.raw_event_sha256,
                            raw_event_index=item.raw_event_index,
                            raw_event_type=item.raw_event_type,
                            call_id=item.call_id,
                        )
                        for item in memory.evidence
                    ),
                )
            )
        ranked = self._rerank(normalized_query, ranked)
        selected = tuple(
            _with_rank(item, rank=rank) for rank, item in enumerate(ranked[:limit], start=1)
        )
        latency_ms = round((self._clock_ns() - started) / 1_000_000, 3)
        sidecar = RecallSidecar(
            query=normalized_query,
            repo_key=repo_key,
            limit=limit,
            latency_ms=latency_ms,
            vector_candidate_count=len(vector),
            lexical_candidate_count=len(lexical),
            ranked=selected,
            reranker_model=None if self._reranker is None else self._reranker.model_id,
            reranker_source=None if self._reranker is None else self._reranker.source_id,
            reranker_revision=None if self._reranker is None else self._reranker.revision,
            embedding_model=self._embedder.model_id,
            embedding_source=self._embedder.source_id,
            embedding_revision=self._embedder.revision,
            retrieval_config_sha256=self._retrieval_config_sha256,
        )
        return RecallResult(
            markdown=_render_context(normalized_query, repo_key=repo_key, ranked=selected),
            sidecar=sidecar,
        )

    def _rerank(self, query: str, ranked: list[RankedRecall]) -> list[RankedRecall]:
        fusion_scores = {item.memory_id: item.final_score for item in ranked}
        if self._reranker is None:
            ranked.sort(key=lambda item: (-item.final_score, item.memory_id))
            return ranked
        documents = tuple(
            RerankDocument(
                memory_id=item.memory_id,
                text=f"{item.title}\n{item.summary}",
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


def _safe_candidates(
    candidates: tuple[IndexCandidate, ...],
    *,
    repo_key: str,
) -> tuple[IndexCandidate, ...]:
    best: dict[str, IndexCandidate] = {}
    for candidate in candidates:
        if not isfinite(candidate.score):
            raise ValueError("Recall index returned a non-finite candidate score")
        if candidate.repo_key != repo_key:
            continue
        prior = best.get(candidate.memory_id)
        if prior is None or candidate.score > prior.score:
            best[candidate.memory_id] = candidate
    return tuple(sorted(best.values(), key=lambda item: (-item.score, item.memory_id)))


def _component_map(
    candidates: tuple[IndexCandidate, ...],
) -> dict[str, tuple[float, int]]:
    return {
        candidate.memory_id: (round(candidate.score, 12), rank)
        for rank, candidate in enumerate(candidates, start=1)
    }


def _with_rank(item: RankedRecall, *, rank: int) -> RankedRecall:
    return RankedRecall(
        rank=rank,
        memory_id=item.memory_id,
        memory_type=item.memory_type,
        title=item.title,
        summary=item.summary,
        source_uri=item.source_uri,
        content_sha256=item.content_sha256,
        candidate_sources=item.candidate_sources,
        vector_score=item.vector_score,
        vector_rank=item.vector_rank,
        lexical_score=item.lexical_score,
        lexical_rank=item.lexical_rank,
        final_score=item.final_score,
        evidence=item.evidence,
        reranker_score=item.reranker_score,
    )


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
                _single_line(item.summary, limit=500),
                "",
                f"- Type: `{item.memory_type}`",
                f"- Source: [{item.memory_id}]({item.source_uri})",
                f"- Evidence: {len(item.evidence)} cited raw event(s)",
            )
        )
    return "\n".join(lines) + "\n"


def _single_line(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.replace("\x00", "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"
