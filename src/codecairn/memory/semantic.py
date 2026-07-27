"""Untrusted semantic proposals and system-owned compilation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from codecairn.memory.capture import ExpectedMemoryFile
from codecairn.memory.evolution import EvolutionProposal
from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    MemoryType,
    RepositoryKnowledgePayload,
    SourceOrderKey,
    UserPreferencePayload,
    WorkStatePayload,
    _record_from_dict,
    _record_to_dict,
    canonical_json,
    coding_memory_to_dict,
    typed_id,
)

SemanticMemoryType = Literal["repository_knowledge", "user_preference", "work_state"]
SemanticJobStatus = Literal["pending", "leased", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    memory_type: SemanticMemoryType
    title: str
    content: str
    category: str
    source_fact_ids: tuple[str, ...]
    subject_key: str | None = None
    claim: str | None = None
    preference: str | None = None
    workstream_key: str | None = None
    workstream_state: Literal["open", "closed"] | None = None
    goal: str | None = None
    progress: str | None = None
    blockers: tuple[str, ...] = ()
    next_step: str | None = None
    terminal_outcome: str | None = None

    def __post_init__(self) -> None:
        if self.memory_type not in {"repository_knowledge", "user_preference", "work_state"}:
            raise ValueError("Semantic candidate type is invalid")
        if not self.title or not self.content or not self.category:
            raise ValueError("Semantic candidate display fields must not be empty")
        if not self.source_fact_ids or len(self.source_fact_ids) != len(set(self.source_fact_ids)):
            raise ValueError("Semantic candidate citations must be non-empty and unique")
        if self.memory_type == "repository_knowledge":
            if self.subject_key is None or self.claim is None:
                raise ValueError("Repository Knowledge proposal is incomplete")
        elif self.memory_type == "user_preference":
            if self.subject_key is None or self.preference is None:
                raise ValueError("User Preference proposal is incomplete")
        elif self.workstream_key is None or self.workstream_state is None or self.goal is None or self.progress is None:
            raise ValueError("Work State proposal is incomplete")
        elif self.workstream_state == "open" and (self.next_step is None or self.terminal_outcome is not None):
            raise ValueError("Open Work State proposal has invalid terminal fields")
        elif self.workstream_state == "closed" and (self.next_step is not None or self.terminal_outcome is None):
            raise ValueError("Closed Work State proposal has invalid terminal fields")


@dataclass(frozen=True, slots=True)
class SemanticEvolutionSuggestion:
    decision: Literal["keep_both", "supersede"]
    relation_kind: Literal["work_state_update", "preference_override", "knowledge_obsolete", "knowledge_contradiction"]
    predecessor_id: str | None
    successor_candidate_index: int
    supporting_fact_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.decision not in {"keep_both", "supersede"}:
            raise ValueError("Semantic evolution decision is invalid")
        if self.relation_kind not in {"work_state_update", "preference_override", "knowledge_obsolete", "knowledge_contradiction"}:
            raise ValueError("Semantic evolution relation kind is invalid")
        if self.decision == "supersede" and self.predecessor_id is None:
            raise ValueError("Supersession suggestion requires a predecessor")
        if self.decision == "keep_both" and self.predecessor_id is not None:
            raise ValueError("Keep-both suggestion cannot name a predecessor")
        if self.predecessor_id is not None and not self.predecessor_id.startswith("mem_"):
            raise ValueError("Semantic evolution predecessor is invalid")
        if len(self.supporting_fact_ids) != len(set(self.supporting_fact_ids)):
            raise ValueError("Semantic evolution citations must be unique")
        if not self.reason or len(self.reason.encode()) > 4_096:
            raise ValueError("Semantic evolution reason is empty or too large")


@dataclass(frozen=True, slots=True)
class SemanticExtraction:
    extractor_id: str
    revision: str
    candidates: tuple[SemanticCandidate, ...]
    evolution: tuple[SemanticEvolutionSuggestion, ...] = ()

    def __post_init__(self) -> None:
        if not self.extractor_id or not self.revision:
            raise ValueError("Semantic extractor identity must not be empty")
        if sum(item.memory_type == "work_state" for item in self.candidates) > 1:
            raise ValueError("Semantic extraction may propose at most one Work State")
        if any(
            item.successor_candidate_index < 0 or item.successor_candidate_index >= len(self.candidates) for item in self.evolution
        ):
            raise ValueError("Semantic evolution successor index is invalid")


@dataclass(frozen=True, slots=True)
class SemanticRequest:
    task_experience: CodingMemory
    allowed_workstream_keys: tuple[str, ...]
    closable_workstream_keys: tuple[str, ...] = ()
    active_work_state_heads: tuple[tuple[str, str], ...] = ()


class SemanticExtractor(Protocol):
    def extract(self, request: SemanticRequest) -> SemanticExtraction: ...


@dataclass(frozen=True, slots=True)
class SemanticJob:
    job_id: str
    repo_key: str
    episode_id: str
    memory_id: str
    status: SemanticJobStatus
    input_fingerprint: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class SemanticProcessReport:
    leased: int
    completed: int
    failed: int
    pending: int


@dataclass(frozen=True, slots=True)
class CompiledSemanticBatch:
    memories: tuple[CodingMemory, ...]
    evolution: tuple[EvolutionProposal, ...]
    canonical_batch: dict[str, object]
    output_fingerprint: str


@dataclass(frozen=True, slots=True)
class PreparedSemanticCommit:
    operation_id: str
    repo_key: str
    job_id: str
    memories: tuple[CodingMemory, ...]
    expected_files: tuple[ExpectedMemoryFile, ...]
    canonical_batch: dict[str, object]
    output_fingerprint: str
    created_at_ms: int

    def __post_init__(self) -> None:
        if self.created_at_ms < 0:
            raise ValueError("Semantic Write Intent time must not be negative")
        _digest(self.output_fingerprint)
        if tuple(item.memory_id for item in self.expected_files) != tuple(item.memory_id for item in self.memories):
            raise ValueError("Semantic Write Intent files do not match Memories")
        if self.operation_id != typed_id("op", semantic_commit_payload(self)):
            raise ValueError("Semantic Write Intent identity does not match its payload")

    @classmethod
    def create(
        cls,
        *,
        repo_key: str,
        job_id: str,
        memories: tuple[CodingMemory, ...],
        expected_files: tuple[ExpectedMemoryFile, ...],
        canonical_batch: dict[str, object],
        output_fingerprint: str,
        created_at_ms: int,
    ) -> PreparedSemanticCommit:
        payload = _semantic_payload(
            repo_key=repo_key,
            job_id=job_id,
            memories=memories,
            expected_files=expected_files,
            canonical_batch=canonical_batch,
            output_fingerprint=output_fingerprint,
        )
        return cls(
            operation_id=typed_id("op", payload),
            repo_key=repo_key,
            job_id=job_id,
            memories=memories,
            expected_files=expected_files,
            canonical_batch=canonical_batch,
            output_fingerprint=output_fingerprint,
            created_at_ms=created_at_ms,
        )


def compile_semantic_extraction(extraction: SemanticExtraction, request: SemanticRequest) -> CompiledSemanticBatch:
    task = request.task_experience
    if task.memory_type != "task_experience" or task.episode_id is None:
        raise ValueError("Semantic extraction requires one Task Experience")
    facts = {fact.fact_id: fact for fact in task.facts}
    memories: list[CodingMemory] = []
    for candidate in extraction.candidates:
        selected = _select_facts(candidate.source_fact_ids, facts=facts)
        if candidate.memory_type == "user_preference" and any(fact.role != "user" for fact in selected):
            raise ValueError("User Preference requires user-authored Source Facts")
        if candidate.memory_type == "work_state" and (candidate.workstream_key not in request.allowed_workstream_keys):
            raise ValueError("Work State key is not system-derived")
        if candidate.memory_type == "work_state":
            task_outcome = getattr(task.payload, "outcome", None)
            if candidate.workstream_state == "open" and task_outcome == "success":
                raise ValueError("Successful Episode cannot open a Work State")
            if candidate.workstream_state == "closed" and candidate.workstream_key not in request.closable_workstream_keys:
                raise ValueError("Closed Work State requires an existing open Workstream")
        memories.append(_candidate_memory(candidate, facts=selected, task=task))
    for proposal in extraction.evolution:
        _select_facts(proposal.supporting_fact_ids, facts=facts)
    compiled_evolution = [
        EvolutionProposal.create(
            repo_key=task.repo_key,
            decision=item.decision,
            relation_kind=item.relation_kind,
            predecessor_id=item.predecessor_id,
            successor_id=memories[item.successor_candidate_index].memory_id,
            supporting_fact_ids=item.supporting_fact_ids,
            source_order_key=memories[item.successor_candidate_index].source_order_key,
            proposer="capture_model",
            reason=item.reason,
        )
        for item in extraction.evolution
    ]
    proposed_successors = {item.successor_id for item in compiled_evolution}
    active_heads = dict(request.active_work_state_heads)
    for memory in memories:
        if (
            isinstance(memory.payload, WorkStatePayload)
            and memory.memory_id not in proposed_successors
            and (predecessor_id := active_heads.get(memory.payload.workstream_key)) is not None
        ):
            compiled_evolution.append(
                EvolutionProposal.create(
                    repo_key=task.repo_key,
                    decision="supersede",
                    relation_kind="work_state_update",
                    predecessor_id=predecessor_id,
                    successor_id=memory.memory_id,
                    supporting_fact_ids=tuple(sorted(fact.fact_id for fact in memory.facts)),
                    source_order_key=memory.source_order_key,
                    proposer="system",
                    reason="Advance the active Work State for this workstream.",
                )
            )
    batch = {
        "schema_version": 1,
        "extractor_id": extraction.extractor_id,
        "revision": extraction.revision,
        "source_memory_id": task.memory_id,
        "candidate_memory_ids": [memory.memory_id for memory in memories],
        "candidates": [_candidate_to_dict(item) for item in extraction.candidates],
        "evolution": [_evolution_to_dict(item) for item in extraction.evolution],
    }
    fingerprint = hashlib.sha256(canonical_json(batch).encode()).hexdigest()
    return CompiledSemanticBatch(
        memories=tuple(memories), evolution=tuple(compiled_evolution), canonical_batch=batch, output_fingerprint=fingerprint
    )


def semantic_commit_payload(commit: PreparedSemanticCommit) -> dict[str, object]:
    return _semantic_payload(
        repo_key=commit.repo_key,
        job_id=commit.job_id,
        memories=commit.memories,
        expected_files=commit.expected_files,
        canonical_batch=commit.canonical_batch,
        output_fingerprint=commit.output_fingerprint,
    )


def semantic_commit_from_payload(value: object, *, operation_id: str, created_at_ms: int) -> PreparedSemanticCommit:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "operation_kind",
        "repo_key",
        "job_id",
        "memories",
        "expected_files",
        "canonical_batch",
        "output_fingerprint",
    }:
        raise ValueError("Semantic Write Intent payload is invalid")
    if value["schema_version"] != 1 or value["operation_kind"] != "semantic_commit":
        raise ValueError("Semantic Write Intent envelope is invalid")
    raw_memories = value["memories"]
    raw_files = value["expected_files"]
    if not isinstance(raw_memories, list) or not isinstance(raw_files, list):
        raise ValueError("Semantic memories and files must be arrays")
    batch = value["canonical_batch"]
    if not isinstance(batch, dict):
        raise ValueError("Semantic batch must be an object")
    return PreparedSemanticCommit(
        operation_id=operation_id,
        repo_key=_string(value["repo_key"], field="repo_key"),
        job_id=_string(value["job_id"], field="job_id"),
        memories=tuple(_record_from_dict(CodingMemory, item) for item in raw_memories),
        expected_files=tuple(_expected_file(item) for item in raw_files),
        canonical_batch=cast(dict[str, object], batch),
        output_fingerprint=_string(value["output_fingerprint"], field="output_fingerprint"),
        created_at_ms=created_at_ms,
    )


def _candidate_memory(candidate: SemanticCandidate, *, facts: tuple[EvidenceFact, ...], task: CodingMemory) -> CodingMemory:
    first = min(facts, key=lambda item: item.reference.event_index)
    payload: RepositoryKnowledgePayload | UserPreferencePayload | WorkStatePayload
    if candidate.memory_type == "repository_knowledge":
        payload = RepositoryKnowledgePayload(subject_key=cast(str, candidate.subject_key), claim=cast(str, candidate.claim))
    elif candidate.memory_type == "user_preference":
        payload = UserPreferencePayload(
            subject_key=cast(str, candidate.subject_key),
            preference=cast(str, candidate.preference),
            source_fact_ids=tuple(sorted(candidate.source_fact_ids)),
        )
    else:
        payload = WorkStatePayload(
            workstream_key=cast(str, candidate.workstream_key),
            workstream_state=cast(Literal["open", "closed"], candidate.workstream_state),
            goal=cast(str, candidate.goal),
            progress=cast(str, candidate.progress),
            blockers=candidate.blockers,
            next_step=cast(str, candidate.next_step),
            terminal_outcome=candidate.terminal_outcome,
        )
    reference = first.reference
    return CodingMemory.create(
        repo_key=task.repo_key,
        memory_type=cast(MemoryType, candidate.memory_type),
        title=candidate.title,
        content=candidate.content,
        category=candidate.category,
        tags=(),
        created_at_ms=0,
        episode_id=task.episode_id,
        evidence=tuple(fact.reference for fact in facts),
        facts=facts,
        origin="capture",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=SourceOrderKey(
            trusted_timestamp_ms=None,
            provider=reference.provider,
            session_id=reference.session_id,
            source_generation=reference.source_generation,
            event_index=reference.event_index,
        ),
        payload=payload,
    )


def _select_facts(fact_ids: tuple[str, ...], *, facts: dict[str, EvidenceFact]) -> tuple[EvidenceFact, ...]:
    if not fact_ids or len(fact_ids) != len(set(fact_ids)):
        raise ValueError("Semantic citations must be non-empty and unique")
    missing = tuple(fact_id for fact_id in fact_ids if fact_id not in facts)
    if missing:
        raise ValueError(f"Semantic proposal cites unknown Source Facts: {missing!r}")
    return tuple(
        sorted(
            (facts[fact_id] for fact_id in fact_ids),
            key=lambda item: (
                item.reference.provider,
                item.reference.session_id,
                item.reference.source_generation,
                item.reference.event_index,
                item.fact_id,
            ),
        )
    )


def _candidate_to_dict(candidate: SemanticCandidate) -> dict[str, object]:
    return _record_to_dict(candidate)


def _evolution_to_dict(item: SemanticEvolutionSuggestion) -> dict[str, object]:
    return _record_to_dict(item)


def _semantic_payload(
    *,
    repo_key: str,
    job_id: str,
    memories: tuple[CodingMemory, ...],
    expected_files: tuple[ExpectedMemoryFile, ...],
    canonical_batch: dict[str, object],
    output_fingerprint: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_kind": "semantic_commit",
        "repo_key": repo_key,
        "job_id": job_id,
        "memories": [coding_memory_to_dict(item) for item in memories],
        "expected_files": [{"record_kind": "coding_memory", **_record_to_dict(item)} for item in expected_files],
        "canonical_batch": canonical_batch,
        "output_fingerprint": output_fingerprint,
    }


def _expected_file(value: object) -> ExpectedMemoryFile:
    if not isinstance(value, dict) or value.get("record_kind") != "coding_memory":
        raise ValueError("Semantic expected file is invalid")
    return _record_from_dict(ExpectedMemoryFile, {key: item for key, item in value.items() if key != "record_kind"})


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Semantic {field} must be a non-empty string")
    return value


def _digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("Semantic output fingerprint is invalid")
