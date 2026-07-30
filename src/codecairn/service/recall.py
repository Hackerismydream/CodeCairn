"""Lifecycle-aware hybrid recall and bounded context compilation."""

from __future__ import annotations

import hashlib
import math
import re
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
    RecallAdmissionTrace,
    RecallContextTrace,
    RecallOmission,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
)
from codecairn.memory.retrieval import EmbeddingProvider, RerankingProvider, retrieval_config_sha256
from codecairn.memory.schema import CodingMemory, MemoryType, canonical_json, coding_memory_to_dict

_RRF_K = 60
_ADMISSION_POLICY = "codecairn/relevance-admission-v1"
_TYPE_PRIORITY: dict[MemoryType, int] = {"repository_knowledge": 0, "user_preference": 1, "work_state": 2, "task_experience": 3}
_TYPE_CAPS: dict[MemoryType, int] = {"repository_knowledge": 40, "user_preference": 4, "work_state": 4, "task_experience": 8}


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
        self._relevance_threshold = 0.0 if embedder is None else embedder.relevance_threshold
        if not 0.0 <= self._relevance_threshold <= 1.0:
            raise ValueError("Embedding relevance threshold must be between 0 and 1")
        self._profile_identity = "codecairn/v01-hybrid:" + retrieval_config_sha256(
            {
                "embedding": None if embedder is None else embedder.index_identity,
                "reranker": None if reranker is None else reranker.model_id,
                "renderer": RENDERER_ID,
                "admission": {"policy": _ADMISSION_POLICY, "vector_threshold_milli": round(self._relevance_threshold * 1_000)},
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
        cap = min(100, max(20, limit * 4))
        vector = self._embedder.embed_query(query)
        lexical = self._index.lexical_candidates(repo_key=repo_key, query=query, include_superseded=include_superseded, limit=cap)
        vectors = self._index.vector_candidates(repo_key=repo_key, vector=vector, include_superseded=include_superseded, limit=cap)
        ranked, omissions, admission = self._rank(
            query,
            repo_key=repo_key,
            lexical=lexical,
            vector=vectors,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
        )
        compiled = compile_context(query, ranked, token_limit=token_budget)
        omissions.extend(RecallOmission(memory_id=memory_id, reason="token_budget") for memory_id in compiled.omitted_ids)
        source_cursor, index_cursor, semantic_state = self._state.recall_cursors(repo_key=repo_key)
        sidecar = RecallSidecar(
            query=query,
            repo_key=repo_key,
            limit=limit,
            latency_ms=(time.perf_counter() - started) * 1_000,
            vector_candidate_count=len(vectors),
            lexical_candidate_count=len(lexical),
            ranked=ranked,
            context_trace=RecallContextTrace(
                renderer=RENDERER_ID,
                rendered_memory_ids=compiled.rendered_ids,
                rendered_fact_ids=compiled.rendered_fact_ids,
                omitted_snippet_count=compiled.omitted_snippet_count,
                type_caps=tuple(_TYPE_CAPS.items()),
                tokenizer=TOKENIZER_ID,
                token_count=compiled.token_count,
                token_limit=token_budget,
            ),
            admission_trace=admission,
            retrieval_profile=self._profile_identity,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            omissions=tuple(omissions),
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
    ) -> tuple[tuple[RankedRecall, ...], list[RecallOmission], RecallAdmissionTrace]:
        sources: dict[str, set[CandidateSource]] = defaultdict(set)
        scores: dict[str, float] = defaultdict(float)
        vector_scores: dict[str, float] = {}
        snippet_scores: dict[str, float] = defaultdict(float)
        snippet_candidates: dict[str, IndexCandidate] = {}
        for source, candidates in (("lexical", lexical), ("vector", vector)):
            for rank, candidate in enumerate(candidates, start=1):
                if source == "vector" and candidate.relevance_score is not None:
                    vector_scores[candidate.memory_id] = max(vector_scores.get(candidate.memory_id, -1.0), candidate.relevance_score)
                if ":memory" not in candidate.document_id and candidate.document_id and candidate.content:
                    snippet_scores[candidate.document_id] += 1.0 / (_RRF_K + rank)
                    snippet_candidates[candidate.document_id] = candidate
                if cast(CandidateSource, source) in sources[candidate.memory_id]:
                    continue
                sources[candidate.memory_id].add(cast(CandidateSource, source))
                scores[candidate.memory_id] += 1.0 / (_RRF_K + rank)
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
            documents = tuple((memory_id, memory.content, scores[memory_id]) for memory_id, memory in valid.items())
            scores.update(self._reranker.rerank(query, documents))
        pinned_id = self._pinned_work_state(repo_key=repo_key, workstream_key=workstream_key)
        if pinned_id is not None and pinned_id not in valid:
            pinned = self._state.get_memory(repo_key=repo_key, memory_id=pinned_id)
            if pinned is not None:
                valid[pinned_id] = pinned
                statuses[pinned_id] = "active"
                scores[pinned_id] = max(scores.values(), default=0.0) + 1.0
                sources[pinned_id].add("lexical")
        relevant_ids = {candidate.memory_id for candidate in lexical}
        relevant_ids.update(memory_id for memory_id, score in vector_scores.items() if score >= self._relevance_threshold)
        if pinned_id is not None:
            relevant_ids.add(pinned_id)
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
            if memory.memory_id not in relevant_ids:
                omissions.append(RecallOmission(memory.memory_id, "relevance"))
            elif counts[memory.memory_type] >= _TYPE_CAPS[memory.memory_type]:
                omissions.append(RecallOmission(memory.memory_id, "type_cap"))
            elif len(admitted) >= limit:
                omissions.append(RecallOmission(memory.memory_id, "limit"))
            else:
                admitted.append(memory)
                counts[memory.memory_type] += 1
        ranked_snippets = self._rank_snippets(query, memories=tuple(admitted), candidates=snippet_candidates, scores=snippet_scores)
        regular_admitted = any(memory.memory_id != pinned_id for memory in admitted)
        admission = RecallAdmissionTrace(
            policy=_ADMISSION_POLICY,
            outcome="admitted" if admitted else "abstained",
            reason=(
                "relevant_candidate"
                if regular_admitted
                else "pinned_work_state"
                if admitted
                else "no_candidates"
                if not valid
                else "below_threshold"
            ),
            vector_threshold=self._relevance_threshold,
            max_vector_score=max(vector_scores.values(), default=None),
        )
        return (
            tuple(
                self._ranked(
                    memory,
                    rank=rank,
                    score=scores[memory.memory_id],
                    status=statuses[memory.memory_id],
                    sources=tuple(sorted(sources[memory.memory_id])),
                    pinned=memory.memory_id == pinned_id,
                    snippets=ranked_snippets.get(memory.memory_id, ()),
                )
                for rank, memory in enumerate(admitted, start=1)
            ),
            omissions,
            admission,
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
        pinned: bool,
        snippets: tuple[RecallSnippet, ...],
    ) -> RankedRecall:
        return RankedRecall(
            rank=rank,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.content,
            source_uri=f"codecairn://memory/{memory.memory_id}",
            content_sha256=hashlib.sha256(canonical_json(coding_memory_to_dict(memory)).encode()).hexdigest(),
            candidate_sources=sources,
            final_score=score,
            evidence=memory.evidence,
            status=status,
            pinned=pinned,
            snippets=snippets,
        )

    def _rank_snippets(
        self,
        query: str,
        *,
        memories: tuple[CodingMemory, ...],
        candidates: dict[str, IndexCandidate],
        scores: dict[str, float],
        per_memory_limit: int = 12,
    ) -> dict[str, tuple[RecallSnippet, ...]]:
        terms, memory_ids = set(re.findall(r"[a-z0-9]+", query.casefold())), {memory.memory_id for memory in memories}
        eligible = {document_id: candidate for document_id, candidate in candidates.items() if candidate.memory_id in memory_ids}
        if self._reranker is not None:
            scores.update(self._reranker.rerank(query, tuple((key, item.content, scores[key]) for key, item in eligible.items())))
        for memory in memories:
            lines = () if memory.facts else tuple(line.strip() for line in memory.content.splitlines() if line.strip())[:128]
            for index, line in enumerate(lines):
                eligible.setdefault(
                    document_id := f"{memory.memory_id}:snippet:{index:04d}", IndexCandidate(memory.memory_id, document_id, line)
                )
        for document_id, candidate in eligible.items():
            overlap = len(terms & set(re.findall(r"[a-z0-9]+", candidate.content.casefold()))) / max(1, len(terms))
            scores[document_id] = 1.5 + math.atan(scores[document_id]) / math.pi if document_id in candidates else overlap
        grouped: dict[str, list[RecallSnippet]] = defaultdict(list)
        for document_id, candidate in sorted(eligible.items(), key=lambda item: (-scores[item[0]], item[0])):
            if len(grouped[candidate.memory_id]) >= per_memory_limit:
                continue
            grouped[candidate.memory_id].append(RecallSnippet(document_id, candidate.content, scores[document_id]))
        return {memory_id: tuple(items) for memory_id, items in grouped.items()}
