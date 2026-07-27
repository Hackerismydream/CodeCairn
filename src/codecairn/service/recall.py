"""Small deterministic recall baseline retained during v0.1 domain migration."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Protocol

from codecairn.memory.models import (
    RankedRecall,
    RecallContextTrace,
    RecallEvidence,
    RecallResult,
    RecallSidecar,
)
from codecairn.memory.schema import CodingMemory, canonical_json, coding_memory_to_dict

_TOKEN = re.compile(r"[\w./:#-]+", flags=re.UNICODE)


class RecallState(Protocol):
    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]: ...


class RecallEngine:
    """Rank immutable active-by-default memories with deterministic lexical overlap."""

    def __init__(self, *, state: RecallState) -> None:
        self._state = state

    def recall(self, query: str, *, repo_key: str, limit: int = 5) -> RecallResult:
        started = time.perf_counter()
        if not query.strip():
            raise ValueError("Recall query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("Recall limit must be between 1 and 100")
        query_terms = _terms(query)
        scored = [
            (memory, _score(query_terms, memory))
            for memory in self._state.list_memories(repo_key=repo_key)
        ]
        selected = sorted(
            (item for item in scored if item[1] > 0),
            key=lambda item: (-item[1], item[0].memory_id),
        )[:limit]
        ranked = tuple(
            _ranked(memory, score=score, rank=rank)
            for rank, (memory, score) in enumerate(selected, start=1)
        )
        markdown = _render(query, ranked)
        latency_ms = (time.perf_counter() - started) * 1_000
        rendered_ids = tuple(item.memory_id for item in ranked)
        rendered_facts = tuple(
            fact_id for memory, _score_value in selected for fact_id in _fact_ids(memory)
        )
        sidecar = RecallSidecar(
            query=query,
            repo_key=repo_key,
            limit=limit,
            latency_ms=latency_ms,
            vector_candidate_count=0,
            lexical_candidate_count=sum(score > 0 for _memory, score in scored),
            ranked=ranked,
            completion="complete",
            degraded_stages=("vector", "reranker"),
            context_trace=RecallContextTrace(
                renderer="codecairn/v01-lexical-context",
                char_count=len(markdown),
                rendered_memory_ids=rendered_ids,
                rendered_fact_ids=rendered_facts,
                omitted_memory_ids=tuple(
                    memory.memory_id
                    for memory, score in scored
                    if score > 0 and memory.memory_id not in rendered_ids
                ),
                omitted_snippet_count=0,
            ),
        )
        return RecallResult(markdown=markdown, sidecar=sidecar)


def _score(query_terms: set[str], memory: CodingMemory) -> float:
    document_terms = _terms(
        "\n".join(
            (
                memory.title,
                memory.content,
                memory.category,
                " ".join(memory.tags),
                " ".join(fact.value for fact in memory.facts),
            )
        )
    )
    if not query_terms or not document_terms:
        return 0.0
    overlap = len(query_terms & document_terms)
    return overlap / len(query_terms)


def _ranked(memory: CodingMemory, *, score: float, rank: int) -> RankedRecall:
    evidence = tuple(
        RecallEvidence(
            provider=reference.provider,
            session_id=reference.session_id,
            raw_event_sha256=reference.event_sha256,
            raw_event_index=reference.event_index,
            raw_event_type="normalized_event",
            call_id=None,
        )
        for reference in memory.evidence
    )
    digest = hashlib.sha256(
        canonical_json(coding_memory_to_dict(memory)).encode("utf-8")
    ).hexdigest()
    return RankedRecall(
        rank=rank,
        memory_id=memory.memory_id,
        memory_type=memory.memory_type,
        title=memory.title,
        summary=memory.content,
        source_uri=f"codecairn://memory/{memory.memory_id}",
        content_sha256=digest,
        candidate_sources=("lexical",),
        vector_score=None,
        vector_rank=None,
        lexical_score=score,
        lexical_rank=rank,
        final_score=score,
        evidence=evidence,
        episode_text=memory.content,
        episode_fact_ids=_fact_ids(memory),
    )


def _fact_ids(memory: CodingMemory) -> tuple[str, ...]:
    return tuple(fact.fact_id for fact in memory.facts)


def _terms(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN.finditer(value)}


def _render(query: str, ranked: tuple[RankedRecall, ...]) -> str:
    lines = ["# Recall Context", "", f"Task: {query}"]
    for item in ranked:
        lines.extend(
            (
                "",
                f"## {item.title}",
                "",
                item.summary,
                "",
                f"Source: {item.source_uri}",
            )
        )
    return "\n".join(lines) + "\n"
