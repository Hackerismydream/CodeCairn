from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol

from codecairn.memory.evidence import EvidenceGate
from codecairn.memory.models import (
    AgentTrace,
    CodingMemory,
    EvidenceFact,
    GateAudit,
    GateDecision,
    ImportCheckpoint,
    ImportResult,
    MemoryProposal,
    MemoryRepairPlan,
    PendingRecoveryAudit,
    RecallResult,
)
from codecairn.memory.trace import (
    extend_raw_prefix_sha256,
    extract_failed_commands,
    segment_tasks,
)
from codecairn.service.recall import RecallEngine


class TraceImporter(Protocol):
    def read(
        self,
        source_path: Path,
        *,
        source_root: Path | None = None,
        checkpoint: ImportCheckpoint | None = None,
    ) -> AgentTrace: ...


class MemoryStore(Protocol):
    def write(self, memory: CodingMemory) -> CodingMemory: ...

    def plan_repair(self, memory: CodingMemory) -> MemoryRepairPlan | None: ...

    def repair(self, memory: CodingMemory, plan: MemoryRepairPlan) -> CodingMemory: ...


class ImportState(Protocol):
    def get_checkpoint(
        self,
        *,
        repo_key: str,
        source_path: str,
    ) -> ImportCheckpoint | None: ...

    def list_pending_recoveries(self, *, repo_key: str) -> tuple[PendingRecoveryAudit, ...]: ...

    def start_recovery(self, plan: MemoryRepairPlan) -> int: ...

    def finish_recovery(
        self,
        audit_id: int,
        *,
        status: Literal["completed", "failed"],
        error_type: str | None = None,
    ) -> None: ...

    def commit_import(
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
        memories: tuple[CodingMemory, ...],
    ) -> int: ...

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]: ...

    def commit_gate_decision(
        self,
        decision: GateDecision,
        *,
        proposal: MemoryProposal,
    ) -> None: ...

    def list_gate_audits(self, *, repo_key: str) -> tuple[GateAudit, ...]: ...


class MemoryRuntime:
    """Deep module for importing and inspecting durable coding memory."""

    def __init__(
        self,
        *,
        importer: TraceImporter,
        memory_store: MemoryStore,
        state: ImportState,
        evidence_gate: EvidenceGate,
        recall_engine: RecallEngine | None = None,
    ) -> None:
        self._state = state
        self._markdown = memory_store
        self._importer = importer
        self._evidence_gate = evidence_gate
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
        repaired_memory_count = self._repair_committed_memories(repo_key=repo_key)
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
        candidates = extract_failed_commands(episodes, repo_key=repo_key)
        persisted = tuple(self._markdown.write(candidate) for candidate in candidates)

        committed_raw_event_index = trace.raw_event_count - 1
        (
            resume_raw_event_index,
            resume_prefix_sha256,
            resume_call_ids,
            resume_file_change_fact_count,
        ) = _next_resume_checkpoint(trace)
        created_count = self._state.commit_import(
            repo_key=repo_key,
            provider=trace.provider,
            session_id=trace.session_id,
            source_path=trace.source_path,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            resume_raw_event_index=resume_raw_event_index,
            resume_prefix_sha256=resume_prefix_sha256,
            resume_call_ids=resume_call_ids,
            resume_file_change_fact_count=resume_file_change_fact_count,
            memories=persisted,
        )
        return ImportResult(
            provider=trace.provider,
            session_id=trace.session_id,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            resumed_from_raw_event_index=trace.resumed_from_raw_event_index,
            processed_raw_event_count=len(trace.raw_suffix_event_sha256s),
            created_memory_count=created_count,
            skipped_memory_count=len(persisted) - created_count,
            repaired_memory_count=repaired_memory_count,
        )

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._state.list_memories(repo_key=repo_key)

    def evaluate_proposal(
        self,
        proposal: MemoryProposal,
        *,
        facts: tuple[EvidenceFact, ...],
    ) -> GateDecision:
        decision = self._evidence_gate.evaluate(proposal, facts=facts)
        if decision.memory is not None:
            persisted = self._markdown.write(decision.memory)
            decision = replace(decision, memory=persisted)
        self._state.commit_gate_decision(decision, proposal=proposal)
        return decision

    def list_gate_audits(self, *, repo_key: str) -> tuple[GateAudit, ...]:
        return self._state.list_gate_audits(repo_key=repo_key)

    def recall(self, query: str, *, repo_key: str, limit: int = 5) -> RecallResult:
        if self._recall_engine is None:
            raise RuntimeError("Recall is not configured for this runtime")
        return self._recall_engine.recall(query, repo_key=repo_key, limit=limit)

    def _repair_committed_memories(self, *, repo_key: str) -> int:
        memories = {
            memory.memory_id: memory for memory in self._state.list_memories(repo_key=repo_key)
        }
        repaired_count = 0
        handled: set[str] = set()
        for pending in self._state.list_pending_recoveries(repo_key=repo_key):
            memory = memories.get(pending.plan.memory_id)
            if memory is None:
                self._state.finish_recovery(
                    pending.audit_id,
                    status="failed",
                    error_type="MissingCommittedMemory",
                )
                raise ValueError(
                    f"Recovery audit references missing memory: {pending.plan.memory_id}"
                )
            current_plan = self._markdown.plan_repair(memory)
            if current_plan is None:
                self._state.finish_recovery(pending.audit_id, status="completed")
                handled.add(memory.memory_id)
                continue
            if current_plan != pending.plan:
                self._state.finish_recovery(
                    pending.audit_id,
                    status="failed",
                    error_type="RecoveryPlanChanged",
                )
                continue
            self._apply_repair(memory, current_plan, audit_id=pending.audit_id)
            repaired_count += 1
            handled.add(memory.memory_id)

        for memory in memories.values():
            if memory.memory_id in handled:
                continue
            plan = self._markdown.plan_repair(memory)
            if plan is None:
                continue
            audit_id = self._state.start_recovery(plan)
            self._apply_repair(memory, plan, audit_id=audit_id)
            repaired_count += 1
        return repaired_count

    def _apply_repair(
        self,
        memory: CodingMemory,
        plan: MemoryRepairPlan,
        *,
        audit_id: int,
    ) -> None:
        try:
            self._markdown.repair(memory, plan)
        except Exception as exc:
            self._state.finish_recovery(
                audit_id,
                status="failed",
                error_type=type(exc).__name__,
            )
            raise
        self._state.finish_recovery(audit_id, status="completed")


def _next_resume_checkpoint(trace: AgentTrace) -> tuple[int, str, tuple[str, ...], int]:
    openings = [
        event.evidence.raw_event_index
        for event in trace.events
        if event.kind == "message" and event.role == "user"
    ]
    resume_raw_event_index = openings[-1] if openings else trace.resumed_from_raw_event_index
    prefix_sha256 = trace.raw_prefix_sha256
    call_ids = set(trace.raw_prefix_call_ids)
    file_change_fact_count = trace.raw_prefix_file_change_fact_count
    for offset, raw_event_sha256 in enumerate(
        trace.raw_suffix_event_sha256s,
        start=trace.resumed_from_raw_event_index,
    ):
        if offset >= resume_raw_event_index:
            break
        prefix_sha256 = extend_raw_prefix_sha256(
            prefix_sha256,
            raw_event_sha256,
        )
    for event in trace.events:
        if event.evidence.raw_event_index >= resume_raw_event_index:
            break
        if event.kind == "tool_call" and event.call_id is not None:
            call_ids.add(event.call_id)
        file_change_fact_count += len(event.file_changes)
    return (
        resume_raw_event_index,
        prefix_sha256,
        tuple(sorted(call_ids)),
        file_change_fact_count,
    )
