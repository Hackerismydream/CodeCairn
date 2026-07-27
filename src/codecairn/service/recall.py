"""Lifecycle-aware hybrid recall and bounded context compilation."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from typing import Protocol, cast

from codecairn.memory.context import RENDERER_ID, TOKENIZER_ID, compile_context
from codecairn.memory.errors import IndexNotReady
from codecairn.memory.models import (
    CandidateSource,
    IndexCandidate,
    MemoryStatus,
    RankedRecall,
    RecallBudget,
    RecallContextTrace,
    RecallEvidence,
    RecallOmission,
    RecallResult,
    RecallSidecar,
)
from codecairn.memory.retrieval import EmbeddingProvider, RerankingProvider, retrieval_config_sha256
from codecairn.memory.schema import CodingMemory, MemoryType, canonical_json, coding_memory_to_dict

_RRF_K = 60
_TYPE_PRIORITY: dict[MemoryType, int] = {"repository_knowledge": 0, "user_preference": 1, "work_state": 2, "task_experience": 3}
_TYPE_CAPS: dict[MemoryType, int] = {"repository_knowledge": 8, "user_preference": 4, "work_state": 4, "task_experience": 8}


class RecallIndex(Protocol):
    @property
    def profile_identity(self) -> str: ...

    def lexical_candidates(self, *, repo_key: str, query: str, include_superseded: bool, limit: int) -> tuple[IndexCandidate, ...]: ...

    def vector_candidates(
        self, *, repo_key: str, vector: tuple[float, ...], include_superseded: bool, limit: int
    ) -> tuple[IndexCandidate, ...]: ...


class RecallState(Protocol):
    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...

    def memory_status(self, *, repo_key: str, memory_id: str) -> str | None: ...

    def active_workstream_heads(self, *, repo_key: str) -> tuple[tuple[str, str], ...]: ...

    def recall_cursors(self, *, repo_key: str) -> tuple[int, int, str]: ...


class IndexPreflight(Protocol):
    def preflight(self, *, repo_key: str, worker_id: str = "recall", max_jobs: int = 128) -> bool: ...


class RecallEngine:
    def __init__(
        self,
        *,
        state: RecallState,
        index: RecallIndex | None = None,
        embedder: EmbeddingProvider | None = None,
        reranker: RerankingProvider | None = None,
        preflight: IndexPreflight | None = None,
        preflight_job_cap: int = 128,
    ) -> None:
        self._state = state
        self._index = index
        self._embedder = embedder
        self._reranker = reranker
        self._preflight = preflight
        self._preflight_job_cap = preflight_job_cap
        self._profile_identity = "codecairn/v01-hybrid:" + retrieval_config_sha256(
            {
                "embedding": None if embedder is None else embedder.index_identity,
                "reranker": None if reranker is None else reranker.model_id,
                "renderer": RENDERER_ID,
            }
        )

    def recall(
        self,
        query: str,
        *,
        repo_key: str,
        limit: int = 20,
        include_superseded: bool = False,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult:
        started = time.perf_counter()
        query = query.strip()
        if not query or len(query.encode()) > 8_192:
            raise ValueError("Recall task must contain between 1 and 8192 bytes")
        if not repo_key.strip() or not 1 <= limit <= 100:
            raise ValueError("Recall namespace or limit is invalid")
        if self._index is None or self._embedder is None or self._preflight is None:
            raise IndexNotReady("No retrieval profile is configured")
        if not self._preflight.preflight(repo_key=repo_key, max_jobs=self._preflight_job_cap):
            raise IndexNotReady("The current namespace index is not ready")
        candidate_limit = min(100, max(20, limit * 4))
        vector = self._embedder.embed_query(query)
        lexical = self._index.lexical_candidates(
            repo_key=repo_key, query=query, include_superseded=include_superseded, limit=candidate_limit
        )
        vector_candidates = self._index.vector_candidates(
            repo_key=repo_key, vector=vector, include_superseded=include_superseded, limit=candidate_limit
        )
        ranked, omissions = self._rank(
            query,
            repo_key=repo_key,
            lexical=lexical,
            vector=vector_candidates,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
        )
        compiled = compile_context(query, ranked, token_limit=token_budget)
        omissions.extend(RecallOmission(memory_id=memory_id, reason="token_budget") for memory_id in compiled.omitted_ids)
        rendered = tuple(item for item in ranked if item.memory_id in set(compiled.rendered_ids))
        source_cursor, index_cursor, semantic_state = self._state.recall_cursors(repo_key=repo_key)
        sidecar = RecallSidecar(
            query=query,
            repo_key=repo_key,
            limit=limit,
            latency_ms=(time.perf_counter() - started) * 1_000,
            vector_candidate_count=len(vector_candidates),
            lexical_candidate_count=len(lexical),
            ranked=ranked,
            completion="partial" if omissions else "complete",
            degraded_stages=(),
            context_trace=RecallContextTrace(
                renderer=RENDERER_ID,
                char_count=len(compiled.markdown),
                rendered_memory_ids=compiled.rendered_ids,
                rendered_fact_ids=tuple(fact_id for item in rendered for fact_id in item.episode_fact_ids),
                omitted_memory_ids=tuple(item.memory_id for item in omissions),
                omitted_snippet_count=0,
                tokenizer=TOKENIZER_ID,
                token_count=compiled.token_count,
                token_limit=token_budget,
            ),
            retrieval_profile=self._profile_identity,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            omissions=tuple(omissions),
            budget=RecallBudget(token_limit=token_budget, token_count=compiled.token_count, type_caps=tuple(_TYPE_CAPS.items())),
            source_cursor=source_cursor,
            index_cursor=index_cursor,
            semantic_state=semantic_state,
            freshness="semantic_pending" if semantic_state != "complete" else "fresh",
        )
        return RecallResult(markdown=compiled.markdown, sidecar=sidecar)

    def _rank(
        self,
        query: str,
        *,
        repo_key: str,
        lexical: tuple[IndexCandidate, ...],
        vector: tuple[IndexCandidate, ...],
        limit: int,
        include_superseded: bool,
        workstream_key: str | None,
    ) -> tuple[tuple[RankedRecall, ...], list[RecallOmission]]:
        sources: dict[str, set[CandidateSource]] = defaultdict(set)
        scores: dict[str, float] = defaultdict(float)
        positions: dict[tuple[str, CandidateSource], tuple[int, float]] = {}
        for source, candidates in (("lexical", lexical), ("vector", vector)):
            typed_source = cast(CandidateSource, source)
            for rank, candidate in enumerate(candidates, start=1):
                if (candidate.memory_id, typed_source) in positions:
                    continue
                sources[candidate.memory_id].add(typed_source)
                scores[candidate.memory_id] += 1.0 / (_RRF_K + rank)
                positions[(candidate.memory_id, typed_source)] = (rank, candidate.score)
        memories = {
            memory_id: memory
            for memory_id in scores
            if (memory := self._state.get_memory(repo_key=repo_key, memory_id=memory_id)) is not None
        }
        statuses = {
            memory_id: cast(MemoryStatus, self._state.memory_status(repo_key=repo_key, memory_id=memory_id)) for memory_id in memories
        }
        valid = {memory_id: memory for memory_id, memory in memories.items() if statuses[memory_id] == "active" or include_superseded}
        if self._reranker is not None:
            reranked = dict(
                self._reranker.rerank(
                    query, tuple((memory_id, memory.content, scores[memory_id]) for memory_id, memory in valid.items())
                )
            )
            scores.update(reranked)
        pinned_id = self._pinned_work_state(repo_key=repo_key, workstream_key=workstream_key)
        if pinned_id is not None and pinned_id not in valid:
            pinned = self._state.get_memory(repo_key=repo_key, memory_id=pinned_id)
            if pinned is not None:
                valid[pinned_id] = pinned
                statuses[pinned_id] = "active"
                scores[pinned_id] = max(scores.values(), default=0.0) + 1.0
                sources[pinned_id].add("lexical")
        ordered = sorted(
            valid.values(),
            key=lambda memory: (
                memory.memory_id != pinned_id,
                -scores[memory.memory_id],
                _TYPE_PRIORITY[memory.memory_type],
                -memory.created_at_ms,
                memory.memory_id,
            ),
        )
        omissions: list[RecallOmission] = []
        admitted: list[CodingMemory] = []
        counts: dict[MemoryType, int] = defaultdict(int)
        for memory in ordered:
            if counts[memory.memory_type] >= _TYPE_CAPS[memory.memory_type]:
                omissions.append(RecallOmission(memory.memory_id, "type_cap"))
            elif len(admitted) >= limit:
                omissions.append(RecallOmission(memory.memory_id, "limit"))
            else:
                admitted.append(memory)
                counts[memory.memory_type] += 1
        return (
            tuple(
                self._ranked(
                    memory,
                    rank=rank,
                    score=scores[memory.memory_id],
                    status=statuses[memory.memory_id],
                    sources=tuple(sorted(sources[memory.memory_id])),
                    positions=positions,
                    pinned=memory.memory_id == pinned_id,
                )
                for rank, memory in enumerate(admitted, start=1)
            ),
            omissions,
        )

    def _pinned_work_state(self, *, repo_key: str, workstream_key: str | None) -> str | None:
        if workstream_key is None:
            return None
        matches = tuple(memory_id for key, memory_id in self._state.active_workstream_heads(repo_key=repo_key) if key == workstream_key)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _ranked(
        memory: CodingMemory,
        *,
        rank: int,
        score: float,
        status: MemoryStatus,
        sources: tuple[CandidateSource, ...],
        positions: dict[tuple[str, CandidateSource], tuple[int, float]],
        pinned: bool,
    ) -> RankedRecall:
        lexical = positions.get((memory.memory_id, "lexical"))
        vector = positions.get((memory.memory_id, "vector"))
        return RankedRecall(
            rank=rank,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.content,
            source_uri=f"codecairn://memory/{memory.memory_id}",
            content_sha256=hashlib.sha256(canonical_json(coding_memory_to_dict(memory)).encode()).hexdigest(),
            candidate_sources=sources,
            vector_score=None if vector is None else vector[1],
            vector_rank=None if vector is None else vector[0],
            lexical_score=None if lexical is None else lexical[1],
            lexical_rank=None if lexical is None else lexical[0],
            final_score=score,
            evidence=tuple(
                RecallEvidence(
                    provider=item.provider,
                    session_id=item.session_id,
                    raw_event_sha256=item.event_sha256,
                    raw_event_index=item.event_index,
                    raw_event_type="normalized_event",
                    call_id=None,
                )
                for item in memory.evidence
            ),
            status=status,
            selection_reason="pinned_work_state" if pinned else "ranked",
            pinned=pinned,
            episode_text=memory.content,
            episode_fact_ids=tuple(fact.fact_id for fact in memory.facts),
        )
