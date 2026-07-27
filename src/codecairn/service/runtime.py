"""Application orchestration for capture, direct storage, and recall."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Protocol

from codecairn.memory.evidence import collect_evidence_facts
from codecairn.memory.models import (
    AgentTrace,
    ImportCheckpoint,
    ImportResult,
    MemoryArtifact,
    RecallResult,
    TraceEpisode,
)
from codecairn.memory.schema import (
    ActionFacet,
    CodingMemory,
    EvidenceFact,
    SourceOrderKey,
    TaskExperiencePayload,
    UserPreferencePayload,
)
from codecairn.memory.trace import extend_raw_prefix_sha256, segment_tasks


class TraceImporter(Protocol):
    def read(
        self,
        source_path: Path,
        *,
        source_root: Path | None = None,
        checkpoint: ImportCheckpoint | None = None,
    ) -> AgentTrace: ...


class MemoryStore(Protocol):
    def write(self, memory: CodingMemory) -> MemoryArtifact: ...


class RuntimeState(Protocol):
    def get_checkpoint(
        self,
        *,
        repo_key: str,
        source_path: str,
    ) -> ImportCheckpoint | None: ...

    def store_source_facts(self, facts: tuple[EvidenceFact, ...]) -> None: ...

    def store_memory(self, artifact: MemoryArtifact) -> bool: ...

    def resolve_source_facts(
        self,
        *,
        repo_key: str,
        fact_ids: tuple[str, ...],
    ) -> tuple[EvidenceFact, ...]: ...

    def commit_checkpoint(
        self,
        *,
        repo_key: str,
        provider: str,
        session_id: str,
        source_path: str,
        source_sha256: str,
        raw_event_count: int,
        committed_raw_event_index: int,
        resume_raw_event_index: int,
        resume_prefix_sha256: str,
        resume_call_ids: tuple[str, ...],
        resume_file_change_fact_count: int,
    ) -> None: ...

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]: ...

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...


class RecallService(Protocol):
    def recall(self, query: str, *, repo_key: str, limit: int) -> RecallResult: ...


class MemoryRuntime:
    """Coordinate adapters while preserving domain-owned identities."""

    def __init__(
        self,
        *,
        importer: TraceImporter,
        memory_store: MemoryStore,
        state: RuntimeState,
        recall_engine: RecallService | None = None,
    ) -> None:
        self._state = state
        self._markdown = memory_store
        self._importer = importer
        self._recall_engine = recall_engine

    def import_session(
        self,
        source_path: Path,
        *,
        repo_key: str,
        source_root: Path | None = None,
    ) -> ImportResult:
        if not repo_key.strip():
            raise ValueError("repo_key must not be empty")
        observed_path = str(Path(os.path.abspath(source_path)))
        checkpoint = self._state.get_checkpoint(
            repo_key=repo_key,
            source_path=observed_path,
        )
        trace = self._importer.read(
            source_path,
            source_root=source_root,
            checkpoint=checkpoint,
        )
        episodes = segment_tasks(trace, repo_key=repo_key)
        facts = collect_evidence_facts(episodes, repo_key=repo_key)
        self._state.store_source_facts(facts)
        created = 0
        for episode in episodes:
            selected = tuple(fact for fact in facts if fact.episode_id == episode.episode_id)
            memory = _task_experience(
                episode,
                facts=selected,
                repo_key=repo_key,
                created_at_ms=time.time_ns() // 1_000_000,
            )
            existing = self._state.get_memory(
                repo_key=memory.repo_key,
                memory_id=memory.memory_id,
            )
            if existing is not None:
                if not _same_capture(existing, memory):
                    raise ValueError(
                        f"Committed Task Experience conflicts with capture: {memory.memory_id}"
                    )
                continue
            artifact = self._markdown.write(memory)
            created += int(self._state.store_memory(artifact))

        committed_raw_event_index = trace.raw_event_count - 1
        resume = _next_resume_checkpoint(trace)
        self._state.commit_checkpoint(
            repo_key=repo_key,
            provider=trace.provider,
            session_id=trace.session_id,
            source_path=trace.source_path,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            resume_raw_event_index=resume[0],
            resume_prefix_sha256=resume[1],
            resume_call_ids=resume[2],
            resume_file_change_fact_count=resume[3],
        )
        return ImportResult(
            provider=trace.provider,
            session_id=trace.session_id,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            resumed_from_raw_event_index=trace.resumed_from_raw_event_index,
            processed_raw_event_count=len(trace.raw_suffix_event_sha256s),
            created_memory_count=created,
            skipped_memory_count=len(episodes) - created,
            repaired_memory_count=0,
        )

    def store_memory(self, memory: CodingMemory) -> CodingMemory:
        if memory.memory_type == "task_experience":
            raise ValueError("Task Experience is capture-only")
        if isinstance(memory.payload, UserPreferencePayload):
            source_fact_ids = memory.payload.source_fact_ids
            resolved = self._state.resolve_source_facts(
                repo_key=memory.repo_key,
                fact_ids=source_fact_ids,
            )
            if any(fact.role != "user" for fact in resolved):
                raise ValueError("User Preference requires user-authored Source Facts")
        artifact = self._markdown.write(memory)
        self._state.store_memory(artifact)
        return memory

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._state.list_memories(repo_key=repo_key)

    def recall(self, query: str, *, repo_key: str, limit: int = 5) -> RecallResult:
        if self._recall_engine is None:
            raise RuntimeError("Recall is not configured for this runtime")
        return self._recall_engine.recall(query, repo_key=repo_key, limit=limit)


def _task_experience(
    episode: TraceEpisode,
    *,
    facts: tuple[EvidenceFact, ...],
    repo_key: str,
    created_at_ms: int,
) -> CodingMemory:
    selected = tuple(sorted(facts, key=_fact_order))
    opening = next(
        (fact for fact in selected if fact.fact_kind == "message" and fact.role == "user"),
        None,
    )
    if opening is None:
        raise ValueError(f"Episode has no opening user task: {episode.episode_id}")
    actions = tuple(
        ActionFacet(
            kind=(
                "command"
                if fact.fact_kind == "command"
                else "file_change"
                if fact.fact_kind == "file_change"
                else "tool"
            ),
            summary=_bounded_display(fact.value, maximum=4_096),
            fact_ids=(fact.fact_id,),
        )
        for fact in selected
        if fact.fact_kind in {"command", "file_change", "tool_call"}
    )
    verification_ids = tuple(
        sorted(fact.fact_id for fact in selected if fact.fact_kind == "verification")
    )
    failures = tuple(
        _bounded_display(fact.value, maximum=2_048)
        for fact in selected
        if fact.fact_kind in {"command_result", "tool_result"}
        and fact.attributes.get("outcome") == "failure"
    )
    source = opening.reference
    outcome = episode.outcome if episode.outcome != "failure" else "failure"
    goal = opening.value
    result = {
        "success": "The observed task actions completed successfully.",
        "failure": "At least one observed task action failed.",
        "unknown": "No conclusive task result was observed.",
    }[episode.outcome]
    content = _bounded_display(
        f"Goal: {goal}\n\nObserved outcome: {result}",
        maximum=32_768,
    )
    return CodingMemory.create(
        repo_key=repo_key,
        memory_type="task_experience",
        title=_bounded_display(goal.splitlines()[0], maximum=256),
        content=content,
        category="other",
        tags=(),
        created_at_ms=created_at_ms,
        episode_id=episode.episode_id,
        evidence=tuple(fact.reference for fact in selected),
        facts=selected,
        origin="capture",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=SourceOrderKey(
            trusted_timestamp_ms=None,
            provider=source.provider,
            session_id=source.session_id,
            source_generation=source.source_generation,
            event_index=source.event_index,
        ),
        payload=TaskExperiencePayload(
            goal=goal,
            outcome=outcome,
            actions=actions,
            result=result,
            blockers=failures,
            verification_fact_ids=verification_ids,
        ),
    )


def _fact_order(fact: EvidenceFact) -> tuple[str, str, int, int, str]:
    reference = fact.reference
    return (
        reference.provider,
        reference.session_id,
        reference.source_generation,
        reference.event_index,
        fact.fact_id,
    )


def _same_capture(existing: CodingMemory, candidate: CodingMemory) -> bool:
    return (
        existing.memory_id == candidate.memory_id
        and existing.repo_key == candidate.repo_key
        and existing.memory_type == candidate.memory_type
        and existing.title == candidate.title
        and existing.content == candidate.content
        and existing.category == candidate.category
        and existing.tags == candidate.tags
        and existing.episode_id == candidate.episode_id
        and existing.evidence == candidate.evidence
        and existing.facts == candidate.facts
        and existing.origin == candidate.origin
        and existing.source_order_key == candidate.source_order_key
        and existing.payload == candidate.payload
    )


def _bounded_display(value: str, *, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore").rstrip() or "truncated"


def _next_resume_checkpoint(trace: AgentTrace) -> tuple[int, str, tuple[str, ...], int]:
    openings = [
        event.evidence.raw_event_index
        for event in trace.events
        if event.kind == "message" and event.role == "user"
    ]
    resume_raw_event_index = openings[-1] if openings else trace.resumed_from_raw_event_index
    prefix_sha256 = trace.raw_prefix_sha256
    call_ids = set(trace.raw_prefix_call_ids)
    file_change_count = trace.raw_prefix_file_change_fact_count
    for offset, raw_event_sha256 in enumerate(
        trace.raw_suffix_event_sha256s,
        start=trace.resumed_from_raw_event_index,
    ):
        if offset >= resume_raw_event_index:
            break
        prefix_sha256 = extend_raw_prefix_sha256(prefix_sha256, raw_event_sha256)
    for event in trace.events:
        if event.evidence.raw_event_index >= resume_raw_event_index:
            break
        if event.kind == "tool_call" and event.call_id is not None:
            call_ids.add(event.call_id)
        file_change_count += len(event.file_changes)
    return (
        resume_raw_event_index,
        prefix_sha256,
        tuple(sorted(call_ids)),
        file_change_count,
    )
