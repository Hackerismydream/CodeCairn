"""Application orchestration for capture, direct storage, and recall."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from codecairn.memory.capture import CaptureCheckpoint, ExpectedMemoryFile, PreparedCapture
from codecairn.memory.episode import BoundaryKind, ClosedEpisode, close_trace_episodes, is_episode_signal
from codecairn.memory.evidence import collect_evidence_facts
from codecairn.memory.evolution import (
    EvolutionArtifact,
    EvolutionProposal,
    EvolutionProposer,
    EvolutionRecord,
    EvolutionRejected,
    EvolutionRelation,
    ExpectedEvolutionFile,
    MemoryHistory,
    MemoryStatus,
    PreparedEvolutionCommit,
    ProposalResolution,
    evaluate_proposal,
)
from codecairn.memory.models import AgentTrace, ImportCheckpoint, ImportResult, MemoryArtifact, RecallResult
from codecairn.memory.schema import (
    ActionFacet,
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    IdentityConflict,
    SourceOrderKey,
    TaskEpisode,
    TaskExperiencePayload,
    UserPreferencePayload,
)
from codecairn.memory.semantic import (
    CompiledSemanticBatch,
    PreparedSemanticCommit,
    SemanticExtractor,
    SemanticJob,
    SemanticProcessReport,
    SemanticRequest,
    compile_semantic_extraction,
)
from codecairn.memory.trace import extend_raw_prefix_sha256


class TraceImporter(Protocol):
    def read(self, source_path: Path, *, source_root: Path | None = None, checkpoint: ImportCheckpoint | None = None) -> AgentTrace: ...


class MemoryStore(Protocol):
    def prepare(self, memory: CodingMemory) -> MemoryArtifact: ...

    def write(
        self, memory: CodingMemory, *, on_stage: Callable[[str], None] | None = None, stage_prefix: str = "capture"
    ) -> MemoryArtifact: ...

    def relative_path_for(self, memory: CodingMemory) -> str: ...

    def prepare_evolution(self, record: EvolutionRecord) -> EvolutionArtifact: ...

    def write_evolution(self, record: EvolutionRecord, *, on_stage: Callable[[str], None] | None = None) -> EvolutionArtifact: ...

    def relative_evolution_path_for(self, record: EvolutionRecord) -> str: ...


class RuntimeState(Protocol):
    def get_checkpoint(self, *, repo_key: str, source_path: str) -> ImportCheckpoint | None: ...

    def list_episodes(
        self, *, repo_key: str, provider: str, session_id: str, source_generation: int = 1
    ) -> tuple[TaskEpisode, ...]: ...

    def prepare_capture(self, capture: PreparedCapture) -> str: ...

    def list_prepared_captures(self) -> tuple[PreparedCapture, ...]: ...

    def complete_capture(
        self, capture: PreparedCapture, artifacts: tuple[MemoryArtifact, ...], *, on_stage: Callable[[str], None] | None = None
    ) -> int: ...

    def conflict_write_intent(self, *, operation_id: str, error_code: str) -> None: ...

    def store_memory(self, artifact: MemoryArtifact) -> bool: ...

    def resolve_source_facts(self, *, repo_key: str, fact_ids: tuple[str, ...]) -> tuple[EvidenceFact, ...]: ...

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]: ...

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...

    def open_workstream_keys(self, *, repo_key: str) -> tuple[str, ...]: ...

    def active_workstream_heads(self, *, repo_key: str) -> tuple[tuple[str, str], ...]: ...

    def lease_semantic_jobs(
        self, *, worker_id: str, max_jobs: int, now_ms: int, lease_duration_ms: int, max_attempts: int
    ) -> tuple[SemanticJob, ...]: ...

    def fail_semantic_job(self, *, job_id: str, worker_id: str, error_code: str, error_detail: str, now_ms: int) -> None: ...

    def semantic_job_counts(self) -> dict[str, int]: ...

    def prepare_semantic_commit(self, commit: PreparedSemanticCommit) -> str: ...

    def list_prepared_semantic_commits(self) -> tuple[PreparedSemanticCommit, ...]: ...

    def complete_semantic_commit(
        self, commit: PreparedSemanticCommit, artifacts: tuple[MemoryArtifact, ...], *, on_stage: Callable[[str], None] | None = None
    ) -> int: ...

    def prepare_evolution(self, commit: PreparedEvolutionCommit) -> str: ...

    def list_prepared_evolutions(self) -> tuple[PreparedEvolutionCommit, ...]: ...

    def complete_evolution(
        self,
        commit: PreparedEvolutionCommit,
        evolution_artifact: EvolutionArtifact,
        memory_artifact: MemoryArtifact | None,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> bool: ...

    def record_proposal_outcome(self, proposal: EvolutionProposal, resolution: ProposalResolution, *, created_at_ms: int) -> None: ...

    def memory_status(self, *, repo_key: str, memory_id: str) -> str | None: ...

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory: ...

    def active_lineage_tips(self, *, repo_key: str, memory_id: str) -> tuple[CodingMemory, ...]: ...


class RecallService(Protocol):
    def recall(
        self,
        query: str,
        *,
        repo_key: str,
        limit: int,
        include_superseded: bool = False,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult: ...


class MemoryRuntime:
    """Coordinate adapters while preserving domain-owned identities."""

    def __init__(
        self,
        *,
        importer: TraceImporter,
        memory_store: MemoryStore,
        state: RuntimeState,
        recall_engine: RecallService | None = None,
        semantic_extractor: SemanticExtractor | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._state = state
        self._markdown = memory_store
        self._importer = importer
        self._recall_engine = recall_engine
        self._semantic_extractor = semantic_extractor
        self._fault_injector = fault_injector

    def import_session(
        self, source_path: Path, *, repo_key: str, source_root: Path | None = None, boundary_kind: BoundaryKind | None = None
    ) -> ImportResult:
        if not repo_key.strip():
            raise ValueError("repo_key must not be empty")
        repaired = self._recover_prepared_operations()
        observed_path = str(Path(os.path.abspath(source_path)))
        checkpoint = self._state.get_checkpoint(repo_key=repo_key, source_path=observed_path)
        trace = self._importer.read(source_path, source_root=source_root, checkpoint=checkpoint)
        existing_episodes = self._state.list_episodes(repo_key=repo_key, provider=trace.provider, session_id=trace.session_id)
        episodes = close_trace_episodes(trace, repo_key=repo_key, existing=existing_episodes, final_boundary=boundary_kind)
        facts = collect_evidence_facts(episodes, repo_key=repo_key)
        memories: list[CodingMemory] = []
        for episode in episodes:
            selected = tuple(fact for fact in facts if fact.episode_id == episode.record.episode_id)
            memory = _task_experience(episode, facts=selected, repo_key=repo_key, created_at_ms=0)
            existing = self._state.get_memory(repo_key=memory.repo_key, memory_id=memory.memory_id)
            if existing is not None:
                if not _same_capture(existing, memory):
                    raise ValueError(f"Committed Task Experience conflicts with capture: {memory.memory_id}")
                continue
            memories.append(memory)

        committed_raw_event_index = trace.raw_event_count - 1
        committed_episodes = (*existing_episodes, *(episode.record for episode in episodes))
        resume = _next_resume_checkpoint(trace, episodes=committed_episodes)
        capture_checkpoint = CaptureCheckpoint(
            repo_key=repo_key,
            provider=trace.provider,
            session_id=trace.session_id,
            source_path=trace.source_path,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            resume=ImportCheckpoint(
                provider=trace.provider,
                session_id=trace.session_id,
                committed_raw_event_index=committed_raw_event_index,
                resume_raw_event_index=resume[0],
                resume_prefix_sha256=resume[1],
                resume_call_ids=resume[2],
                resume_file_change_fact_count=resume[3],
            ),
            prior_source_cursor=(checkpoint.committed_raw_event_index if checkpoint is not None else -1),
        )
        prepared_artifacts = tuple(self._markdown.prepare(memory) for memory in memories)
        capture = PreparedCapture.create(
            repo_key=repo_key,
            episodes=tuple(episode.record for episode in episodes),
            facts=facts,
            memories=tuple(memories),
            expected_files=tuple(
                ExpectedMemoryFile(
                    relative_path=self._markdown.relative_path_for(artifact.memory),
                    content_sha256=artifact.content_sha256,
                    memory_id=artifact.memory.memory_id,
                )
                for artifact in prepared_artifacts
            ),
            checkpoint=capture_checkpoint,
            created_at_ms=time.time_ns() // 1_000_000,
        )
        status = self._state.prepare_capture(capture)
        if status == "closure_lost":
            return ImportResult(
                provider=trace.provider,
                session_id=trace.session_id,
                source_sha256=trace.source_sha256,
                raw_event_count=trace.raw_event_count,
                committed_raw_event_index=committed_raw_event_index,
                resumed_from_raw_event_index=trace.resumed_from_raw_event_index,
                processed_raw_event_count=len(trace.raw_suffix_event_sha256s),
                created_memory_count=0,
                skipped_memory_count=len(episodes),
                repaired_memory_count=repaired,
            )
        self._stage("capture_after_intent_prepared")
        if status == "completed":
            created = 0
        else:
            try:
                artifacts = tuple(self._write_capture_memories(capture))
                self._stage("capture_after_markdown_files")
                created = self._state.complete_capture(capture, artifacts, on_stage=self._fault_injector)
            except IdentityConflict:
                self._mark_conflicted(capture.operation_id)
                raise
            self._stage("capture_after_complete")
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
            repaired_memory_count=repaired,
        )

    def store_memory(self, memory: CodingMemory) -> CodingMemory:
        if memory.memory_type == "task_experience":
            raise ValueError("Task Experience is capture-only")
        if isinstance(memory.payload, UserPreferencePayload):
            source_fact_ids = memory.payload.source_fact_ids
            resolved = self._state.resolve_source_facts(repo_key=memory.repo_key, fact_ids=source_fact_ids)
            if any(fact.role != "user" for fact in resolved):
                raise ValueError("User Preference requires user-authored Source Facts")
        artifact = self._markdown.write(memory)
        self._state.store_memory(artifact)
        return memory

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._state.list_memories(repo_key=repo_key)

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None:
        return self._state.get_memory(repo_key=repo_key, memory_id=memory_id)

    def memory_status(self, *, repo_key: str, memory_id: str) -> str | None:
        return self._state.memory_status(repo_key=repo_key, memory_id=memory_id)

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
        if self._recall_engine is None:
            raise RuntimeError("Recall is not configured for this runtime")
        return self._recall_engine.recall(
            query,
            repo_key=repo_key,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )

    def supersede(
        self, *, repo_key: str, predecessor_id: str, successor_id: str, reason: str, proposer: EvolutionProposer
    ) -> EvolutionRecord:
        predecessor = self._required_memory(repo_key, predecessor_id)
        successor = self._required_memory(repo_key, successor_id)
        relation = {
            "work_state": "work_state_update",
            "user_preference": "preference_override",
            "repository_knowledge": "knowledge_contradiction",
        }.get(successor.memory_type)
        if relation is None:
            raise EvolutionRejected("append_only_experience", "Task Experience cannot supersede another memory")
        proposal = EvolutionProposal.create(
            repo_key=repo_key,
            decision="supersede",
            relation_kind=cast(EvolutionRelation, relation),
            predecessor_id=predecessor.memory_id,
            successor_id=successor.memory_id,
            supporting_fact_ids=tuple(sorted(fact.fact_id for fact in successor.facts)),
            source_order_key=successor.source_order_key,
            proposer=proposer,
            reason=reason,
        )
        return self._apply_evolution(proposal, evidence=successor.evidence)

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory:
        return self._state.memory_history(repo_key=repo_key, memory_id=memory_id)

    def restore(self, *, repo_key: str, memory_id: str) -> CodingMemory:
        original = self._required_memory(repo_key, memory_id)
        if original.memory_type == "task_experience":
            raise EvolutionRejected("append_only_experience", "Task Experience cannot be restored")
        if self._state.memory_status(repo_key=repo_key, memory_id=memory_id) == "active":
            raise EvolutionRejected("already_active", "Active memory cannot be restored")
        tips = self._state.active_lineage_tips(repo_key=repo_key, memory_id=memory_id)
        if len(tips) != 1:
            raise EvolutionRejected("ambiguous_lineage", "Restore requires one unique active lineage tip")
        predecessor = tips[0]
        restored = CodingMemory.create(
            repo_key=original.repo_key,
            memory_type=original.memory_type,
            title=original.title,
            content=original.content,
            category=original.category,
            tags=original.tags,
            created_at_ms=time.time_ns() // 1_000_000,
            episode_id=original.episode_id,
            evidence=original.evidence,
            facts=original.facts,
            origin="restored",
            restored_from=original.memory_id,
            restore_predecessor_id=predecessor.memory_id,
            source_order_key=original.source_order_key,
            payload=original.payload,
        )
        proposal = EvolutionProposal.create(
            repo_key=repo_key,
            decision="supersede",
            relation_kind="explicit_restore",
            predecessor_id=predecessor.memory_id,
            successor_id=restored.memory_id,
            supporting_fact_ids=tuple(sorted(fact.fact_id for fact in restored.facts)),
            source_order_key=restored.source_order_key,
            proposer="user",
            reason=f"Restore {original.memory_id}",
        )
        self._apply_evolution(proposal, evidence=restored.evidence, new_memory=restored)
        return restored

    def process_pending(self, *, worker_id: str, max_jobs: int = 8) -> SemanticProcessReport:
        if not worker_id or max_jobs < 1:
            raise ValueError("Semantic worker parameters are invalid")
        self._recover_prepared_operations()
        if self._semantic_extractor is None:
            counts = self._state.semantic_job_counts()
            return SemanticProcessReport(leased=0, completed=0, failed=counts["failed"], pending=counts["pending"])
        now_ms = time.time_ns() // 1_000_000
        jobs = self._state.lease_semantic_jobs(
            worker_id=worker_id, max_jobs=max_jobs, now_ms=now_ms, lease_duration_ms=60_000, max_attempts=3
        )
        completed = 0
        failed = 0
        for job in jobs:
            task = self._state.get_memory(repo_key=job.repo_key, memory_id=job.memory_id)
            if task is None:
                self._fail_semantic(
                    job, worker_id=worker_id, code="semantic_input_missing", error=RuntimeError("Task Experience is missing")
                )
                failed += 1
                continue
            workstream_keys = _workstream_candidates(task)
            active_heads = self._state.active_workstream_heads(repo_key=task.repo_key)
            request = SemanticRequest(
                task_experience=task,
                allowed_workstream_keys=workstream_keys,
                closable_workstream_keys=tuple(
                    sorted(set(workstream_keys) & set(self._state.open_workstream_keys(repo_key=task.repo_key)))
                ),
                active_work_state_heads=tuple(item for item in active_heads if item[0] in workstream_keys),
            )
            try:
                extraction = self._semantic_extractor.extract(request)
            except Exception as error:
                self._fail_semantic(job, worker_id=worker_id, code="semantic_provider_error", error=error)
                failed += 1
                continue
            try:
                batch = compile_semantic_extraction(extraction, request)
            except (TypeError, ValueError) as error:
                self._fail_semantic(job, worker_id=worker_id, code="semantic_output_invalid", error=error)
                failed += 1
                continue
            self._commit_semantic_batch(job, batch)
            for proposal in batch.evolution:
                self._apply_automatic_evolution(proposal)
            completed += 1
        counts = self._state.semantic_job_counts()
        return SemanticProcessReport(leased=len(jobs), completed=completed, failed=failed, pending=counts["pending"])

    def _recover_prepared_operations(self) -> int:
        repaired = 0
        for capture in self._state.list_prepared_captures():
            try:
                artifacts = tuple(self._write_capture_memories(capture))
                repaired += self._state.complete_capture(capture, artifacts)
            except IdentityConflict:
                self._mark_conflicted(capture.operation_id)
                raise
        for commit in self._state.list_prepared_semantic_commits():
            try:
                artifacts = tuple(self._markdown.write(memory) for memory in commit.memories)
                repaired += self._state.complete_semantic_commit(commit, artifacts)
            except IdentityConflict:
                self._mark_conflicted(commit.operation_id)
                raise
        for evolution_commit in self._state.list_prepared_evolutions():
            try:
                memory_artifact = None if evolution_commit.new_memory is None else self._markdown.write(evolution_commit.new_memory)
                evolution_artifact = self._markdown.write_evolution(evolution_commit.record)
                repaired += int(self._state.complete_evolution(evolution_commit, evolution_artifact, memory_artifact))
            except IdentityConflict:
                self._mark_conflicted(evolution_commit.operation_id)
                raise
        return repaired

    def _commit_semantic_batch(self, job: SemanticJob, batch: CompiledSemanticBatch) -> None:
        prepared = tuple(self._markdown.prepare(memory) for memory in batch.memories)
        commit = PreparedSemanticCommit.create(
            repo_key=job.repo_key,
            job_id=job.job_id,
            memories=batch.memories,
            expected_files=tuple(
                ExpectedMemoryFile(
                    relative_path=self._markdown.relative_path_for(artifact.memory),
                    content_sha256=artifact.content_sha256,
                    memory_id=artifact.memory.memory_id,
                )
                for artifact in prepared
            ),
            canonical_batch=batch.canonical_batch,
            output_fingerprint=batch.output_fingerprint,
            created_at_ms=time.time_ns() // 1_000_000,
        )
        status = self._state.prepare_semantic_commit(commit)
        self._stage("semantic_after_intent_prepared")
        if status == "completed":
            return
        try:
            artifacts = tuple(
                self._markdown.write(memory, on_stage=self._fault_injector, stage_prefix="semantic") for memory in commit.memories
            )
            self._state.complete_semantic_commit(commit, artifacts, on_stage=self._fault_injector)
        except IdentityConflict:
            self._mark_conflicted(commit.operation_id)
            raise
        self._stage("semantic_after_complete")

    def _mark_conflicted(self, operation_id: str) -> None:
        self._state.conflict_write_intent(operation_id=operation_id, error_code="identity_conflict")

    def _apply_automatic_evolution(self, proposal: EvolutionProposal) -> None:
        predecessor = (
            None
            if proposal.predecessor_id is None
            else self._state.get_memory(repo_key=proposal.repo_key, memory_id=proposal.predecessor_id)
        )
        successor = self._state.get_memory(repo_key=proposal.repo_key, memory_id=proposal.successor_id)
        if successor is None:
            resolution = ProposalResolution("rejected", "unknown_successor")
        else:
            resolution = evaluate_proposal(
                proposal,
                predecessor=predecessor,
                successor=successor,
                predecessor_status=(
                    None
                    if predecessor is None
                    else cast(
                        MemoryStatus | None, self._state.memory_status(repo_key=proposal.repo_key, memory_id=predecessor.memory_id)
                    )
                ),
            )
        if resolution.outcome != "applied":
            self._state.record_proposal_outcome(proposal, resolution, created_at_ms=time.time_ns() // 1_000_000)
            return
        try:
            assert successor is not None
            self._apply_evolution(proposal, evidence=successor.evidence)
        except EvolutionRejected as error:
            self._state.record_proposal_outcome(
                proposal, type(resolution)("rejected", error.code), created_at_ms=time.time_ns() // 1_000_000
            )

    def _apply_evolution(
        self, proposal: EvolutionProposal, *, evidence: tuple[EvidenceReference, ...], new_memory: CodingMemory | None = None
    ) -> EvolutionRecord:
        now_ms = time.time_ns() // 1_000_000
        record = EvolutionRecord.from_proposal(proposal, evidence=evidence, created_at_ms=now_ms)
        prepared_evolution = self._markdown.prepare_evolution(record)
        prepared_memory = None if new_memory is None else self._markdown.prepare(new_memory)
        expected_memory_file = None
        if new_memory is not None and prepared_memory is not None:
            expected_memory_file = ExpectedMemoryFile(
                relative_path=self._markdown.relative_path_for(new_memory),
                content_sha256=prepared_memory.content_sha256,
                memory_id=new_memory.memory_id,
            )
        commit = PreparedEvolutionCommit.create(
            proposal=proposal,
            record=record,
            new_memory=new_memory,
            expected_memory_file=expected_memory_file,
            expected_evolution_file=ExpectedEvolutionFile(
                relative_path=self._markdown.relative_evolution_path_for(record),
                content_sha256=prepared_evolution.content_sha256,
                evolution_id=record.evolution_id,
            ),
            created_at_ms=now_ms,
        )
        status = self._state.prepare_evolution(commit)
        if status == "completed":
            return record
        self._stage("evolution_after_intent_prepared")
        try:
            memory_artifact = (
                None
                if new_memory is None
                else self._markdown.write(new_memory, on_stage=self._fault_injector, stage_prefix="evolution_memory")
            )
            evolution_artifact = self._markdown.write_evolution(record, on_stage=self._fault_injector)
            self._state.complete_evolution(commit, evolution_artifact, memory_artifact, on_stage=self._fault_injector)
        except IdentityConflict:
            self._mark_conflicted(commit.operation_id)
            raise
        self._stage("evolution_after_complete")
        return record

    def _required_memory(self, repo_key: str, memory_id: str) -> CodingMemory:
        memory = self._state.get_memory(repo_key=repo_key, memory_id=memory_id)
        if memory is None:
            raise EvolutionRejected("unknown_memory", f"Unknown memory: {memory_id}")
        return memory

    def _fail_semantic(self, job: SemanticJob, *, worker_id: str, code: str, error: Exception) -> None:
        self._state.fail_semantic_job(
            job_id=job.job_id,
            worker_id=worker_id,
            error_code=code,
            error_detail=type(error).__name__,
            now_ms=time.time_ns() // 1_000_000,
        )

    def _write_capture_memories(self, capture: PreparedCapture) -> tuple[MemoryArtifact, ...]:
        artifacts: list[MemoryArtifact] = []
        for memory in capture.memories:
            artifacts.append(self._markdown.write(memory, on_stage=self._fault_injector))
            self._stage("capture_after_markdown_file")
        return tuple(artifacts)

    def _stage(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)


def _task_experience(episode: ClosedEpisode, *, facts: tuple[EvidenceFact, ...], repo_key: str, created_at_ms: int) -> CodingMemory:
    selected = _select_experience_facts(facts)
    opening = next((fact for fact in selected if fact.fact_kind == "message" and fact.role == "user"), None)
    if opening is None and episode.record.continues_episode_id is None:
        raise ValueError(f"Episode has no opening user task: {episode.record.episode_id}")
    actions = tuple(
        ActionFacet(
            kind=("command" if fact.fact_kind == "command" else "file_change" if fact.fact_kind == "file_change" else "tool"),
            summary=_bounded_display(fact.value, maximum=4_096),
            fact_ids=(fact.fact_id,),
        )
        for fact in selected
        if fact.fact_kind in {"command", "file_change", "tool_call"}
    )
    verification_ids = tuple(sorted(fact.fact_id for fact in selected if fact.fact_kind == "verification"))
    failures = tuple(
        _bounded_display(fact.value, maximum=2_048)
        for fact in selected
        if fact.fact_kind in {"command_result", "tool_result"} and fact.attributes.get("outcome") == "failure"
    )
    source = opening.reference if opening is not None else min(selected, key=_fact_order).reference
    outcome = episode.outcome
    goal = opening.value if opening is not None else f"Continue episode {episode.record.continues_episode_id}"
    result = {
        "success": "The observed task actions completed successfully.",
        "failure": "At least one observed task action failed.",
        "partial": "Observed task actions had mixed successful and failed results.",
        "unknown": "No conclusive task result was observed.",
    }[episode.outcome]
    content = _bounded_display(f"Goal: {goal}\n\nObserved outcome: {result}", maximum=32_768)
    return CodingMemory.create(
        repo_key=repo_key,
        memory_type="task_experience",
        title=_bounded_display(goal.splitlines()[0], maximum=256),
        content=content,
        category="other",
        tags=(),
        created_at_ms=created_at_ms,
        episode_id=episode.record.episode_id,
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
            goal=goal, outcome=outcome, actions=actions, result=result, blockers=failures, verification_fact_ids=verification_ids
        ),
    )


def _fact_order(fact: EvidenceFact) -> tuple[str, str, int, int, str]:
    reference = fact.reference
    return (reference.provider, reference.session_id, reference.source_generation, reference.event_index, fact.fact_id)


def _select_experience_facts(facts: tuple[EvidenceFact, ...], *, limit: int = 128) -> tuple[EvidenceFact, ...]:
    ordered = tuple(sorted(facts, key=_fact_order))
    by_id = {fact.fact_id: fact for fact in ordered}
    opening = next((fact for fact in ordered if fact.fact_kind == "message" and fact.role == "user"), None)
    selected: dict[str, EvidenceFact] = {}

    def add(fact: EvidenceFact) -> None:
        dependencies = tuple(
            value
            for key, value in fact.attributes.items()
            if key in {"command_fact_id", "tool_call_fact_id"} and isinstance(value, str) and value in by_id
        )
        required = tuple(by_id[fact_id] for fact_id in dependencies if fact_id not in selected)
        if fact.fact_id in selected or len(selected) + len(required) + 1 > limit:
            return
        for dependency in required:
            selected[dependency.fact_id] = dependency
        selected[fact.fact_id] = fact

    if opening is not None:
        add(opening)
    candidates = sorted(
        ordered,
        key=lambda fact: (
            0
            if fact.attributes.get("outcome") == "failure"
            else 1
            if fact.fact_kind == "verification"
            else 2
            if fact.fact_kind in {"command", "file_change", "tool_call"}
            else 3
            if fact.fact_kind == "message" and fact.role == "user"
            else 4,
            _fact_order(fact),
        ),
    )
    for fact in candidates:
        add(fact)
        if len(selected) == limit:
            break
    return tuple(sorted(selected.values(), key=_fact_order))


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


def _next_resume_checkpoint(trace: AgentTrace, *, episodes: tuple[TaskEpisode, ...]) -> tuple[int, str, tuple[str, ...], int]:
    last_closed_cursor = max((episode.end_event_index_exclusive for episode in episodes), default=trace.resumed_from_raw_event_index)
    unclosed = tuple(
        event for event in trace.events if event.evidence.raw_event_index >= last_closed_cursor and is_episode_signal(event)
    )
    resume_raw_event_index = unclosed[0].evidence.raw_event_index if unclosed else trace.raw_event_count
    prefix_sha256 = trace.raw_prefix_sha256
    call_ids = set(trace.raw_prefix_call_ids)
    file_change_count = trace.raw_prefix_file_change_fact_count
    for offset, raw_event_sha256 in enumerate(trace.raw_suffix_event_sha256s, start=trace.resumed_from_raw_event_index):
        if offset >= resume_raw_event_index:
            break
        prefix_sha256 = extend_raw_prefix_sha256(prefix_sha256, raw_event_sha256)
    for event in trace.events:
        if event.evidence.raw_event_index >= resume_raw_event_index:
            break
        if event.kind == "tool_call" and event.call_id is not None:
            call_ids.add(event.call_id)
        file_change_count += len(event.file_changes)
    return (resume_raw_event_index, prefix_sha256, tuple(sorted(call_ids)), file_change_count)


def _workstream_candidates(task: CodingMemory) -> tuple[str, ...]:
    if task.episode_id is None:
        return ()
    keys = {f"task:{task.episode_id}"}
    goal = task.payload.goal if isinstance(task.payload, TaskExperiencePayload) else ""
    for match in re.finditer(r"(?<![A-Za-z0-9])#([1-9][0-9]*)\b", goal):
        keys.add(f"issue:{task.repo_key.lower()}#{match.group(1)}")
    return tuple(sorted(keys))
