"""Immutable Supersession domain records and type policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codecairn.memory.capture import ExpectedMemoryFile
from codecairn.memory.schema import (
    SCHEMA_VERSION,
    CodingMemory,
    EvidenceReference,
    RepositoryKnowledgePayload,
    SourceOrderKey,
    UserPreferencePayload,
    WorkStatePayload,
    _record_from_dict,
    _record_to_dict,
    coding_memory_from_dict,
    coding_memory_to_dict,
    memory_subject_key,
    typed_id,
)

MemoryStatus = Literal["active", "superseded"]
EvolutionDecision = Literal["keep_both", "supersede"]
EvolutionRelation = Literal[
    "work_state_update", "preference_override", "knowledge_obsolete", "knowledge_contradiction", "explicit_restore"
]
EvolutionProposer = Literal["capture_model", "agent", "user", "system"]
ProposalOutcome = Literal["pending", "applied", "kept_both", "rejected"]

_RELATIONS = {"work_state_update", "preference_override", "knowledge_obsolete", "knowledge_contradiction", "explicit_restore"}
_PROPOSERS = {"capture_model", "agent", "user", "system"}


class EvolutionRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EvolutionProposal:
    schema_version: int
    proposal_id: str
    repo_key: str
    decision: EvolutionDecision
    relation_kind: EvolutionRelation
    predecessor_id: str | None
    successor_id: str
    supporting_fact_ids: tuple[str, ...]
    source_order_key: SourceOrderKey | None
    proposer: EvolutionProposer
    reason: str

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Evolution Proposal schema is unsupported")
        if self.decision not in {"keep_both", "supersede"}:
            raise ValueError("Evolution Proposal decision is invalid")
        if self.relation_kind not in _RELATIONS or self.proposer not in _PROPOSERS:
            raise ValueError("Evolution Proposal enum is invalid")
        if (self.decision == "supersede") != (self.predecessor_id is not None):
            raise ValueError("Evolution Proposal predecessor does not match its decision")
        _memory_id(self.successor_id)
        if self.predecessor_id is not None:
            _memory_id(self.predecessor_id)
        if tuple(sorted(self.supporting_fact_ids)) != self.supporting_fact_ids:
            raise ValueError("Evolution Proposal facts must be sorted and unique")
        if not self.reason or len(self.reason.encode()) > 4_096:
            raise ValueError("Evolution Proposal reason is empty or too large")
        if self.proposal_id != typed_id("proposal", _proposal_identity(self)):
            raise ValueError("Evolution Proposal identity does not match its payload")

    @classmethod
    def create(
        cls,
        *,
        repo_key: str,
        decision: EvolutionDecision,
        relation_kind: EvolutionRelation,
        predecessor_id: str | None,
        successor_id: str,
        supporting_fact_ids: tuple[str, ...],
        source_order_key: SourceOrderKey | None,
        proposer: EvolutionProposer,
        reason: str,
    ) -> EvolutionProposal:
        facts = tuple(sorted(set(supporting_fact_ids)))
        provisional = _proposal_identity_fields(
            schema_version=SCHEMA_VERSION,
            repo_key=repo_key,
            decision=decision,
            relation_kind=relation_kind,
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            supporting_fact_ids=facts,
            source_order_key=source_order_key,
            proposer=proposer,
            reason=reason,
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            proposal_id=typed_id("proposal", provisional),
            repo_key=repo_key,
            decision=decision,
            relation_kind=relation_kind,
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            supporting_fact_ids=facts,
            source_order_key=source_order_key,
            proposer=proposer,
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class EvolutionRecord:
    schema_version: int
    evolution_id: str
    repo_key: str
    relation_kind: EvolutionRelation
    predecessor_id: str
    successor_id: str
    proposal_id: str | None
    supporting_fact_ids: tuple[str, ...]
    source_order_key: SourceOrderKey | None
    proposer: EvolutionProposer
    reason: str
    evidence: tuple[EvidenceReference, ...]
    created_at_ms: int

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Evolution Record schema is unsupported")
        if self.relation_kind not in _RELATIONS or self.proposer not in _PROPOSERS:
            raise ValueError("Evolution Record enum is invalid")
        _memory_id(self.predecessor_id)
        _memory_id(self.successor_id)
        if self.predecessor_id == self.successor_id:
            raise ValueError("Evolution Record cannot reference itself")
        if self.proposal_id is not None and not self.proposal_id.startswith("proposal_"):
            raise ValueError("Evolution Record proposal identity is invalid")
        if tuple(sorted(self.supporting_fact_ids)) != self.supporting_fact_ids:
            raise ValueError("Evolution Record facts must be sorted and unique")
        if tuple(sorted(self.evidence, key=_evidence_order)) != self.evidence:
            raise ValueError("Evolution Record evidence is not in source order")
        if self.created_at_ms < 0 or not self.reason:
            raise ValueError("Evolution Record time or reason is invalid")
        if self.evolution_id != evolution_identity(
            repo_key=self.repo_key, relation_kind=self.relation_kind, predecessor_id=self.predecessor_id, successor_id=self.successor_id
        ):
            raise ValueError("Evolution Record identity does not match its edge")

    @classmethod
    def from_proposal(
        cls, proposal: EvolutionProposal, *, evidence: tuple[EvidenceReference, ...], created_at_ms: int
    ) -> EvolutionRecord:
        if proposal.decision != "supersede" or proposal.predecessor_id is None:
            raise ValueError("Only a Supersession Proposal creates an Evolution Record")
        return cls(
            schema_version=SCHEMA_VERSION,
            evolution_id=evolution_identity(
                repo_key=proposal.repo_key,
                relation_kind=proposal.relation_kind,
                predecessor_id=proposal.predecessor_id,
                successor_id=proposal.successor_id,
            ),
            repo_key=proposal.repo_key,
            relation_kind=proposal.relation_kind,
            predecessor_id=proposal.predecessor_id,
            successor_id=proposal.successor_id,
            proposal_id=proposal.proposal_id,
            supporting_fact_ids=proposal.supporting_fact_ids,
            source_order_key=proposal.source_order_key,
            proposer=proposal.proposer,
            reason=proposal.reason,
            evidence=tuple(sorted(evidence, key=_evidence_order)),
            created_at_ms=created_at_ms,
        )


@dataclass(frozen=True, slots=True)
class EvolutionArtifact:
    record: EvolutionRecord
    path: Path
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ExpectedEvolutionFile:
    relative_path: str
    content_sha256: str
    evolution_id: str


@dataclass(frozen=True, slots=True)
class PreparedEvolutionCommit:
    operation_id: str
    repo_key: str
    proposal: EvolutionProposal
    record: EvolutionRecord
    new_memory: CodingMemory | None
    expected_memory_file: ExpectedMemoryFile | None
    expected_evolution_file: ExpectedEvolutionFile
    created_at_ms: int

    def __post_init__(self) -> None:
        if self.record.repo_key != self.repo_key or self.proposal.repo_key != self.repo_key:
            raise ValueError("Evolution Write Intent namespace is inconsistent")
        if self.record.proposal_id != self.proposal.proposal_id:
            raise ValueError("Evolution Write Intent proposal is inconsistent")
        if self.record.evolution_id != self.expected_evolution_file.evolution_id:
            raise ValueError("Evolution Write Intent file is inconsistent")
        if (self.new_memory is None) != (self.expected_memory_file is None):
            raise ValueError("Evolution Write Intent memory file is inconsistent")
        if self.new_memory is not None and (
            self.new_memory.memory_id != self.record.successor_id
            or cast(ExpectedMemoryFile, self.expected_memory_file).memory_id != self.new_memory.memory_id
        ):
            raise ValueError("Evolution Write Intent restored memory is inconsistent")
        if self.operation_id != typed_id("op", evolution_commit_payload(self)):
            raise ValueError("Evolution Write Intent identity does not match its payload")

    @classmethod
    def create(
        cls,
        *,
        proposal: EvolutionProposal,
        record: EvolutionRecord,
        new_memory: CodingMemory | None,
        expected_memory_file: ExpectedMemoryFile | None,
        expected_evolution_file: ExpectedEvolutionFile,
        created_at_ms: int,
    ) -> PreparedEvolutionCommit:
        payload = _commit_payload(
            repo_key=proposal.repo_key,
            proposal=proposal,
            record=record,
            new_memory=new_memory,
            expected_memory_file=expected_memory_file,
            expected_evolution_file=expected_evolution_file,
        )
        return cls(
            operation_id=typed_id("op", payload),
            repo_key=proposal.repo_key,
            proposal=proposal,
            record=record,
            new_memory=new_memory,
            expected_memory_file=expected_memory_file,
            expected_evolution_file=expected_evolution_file,
            created_at_ms=created_at_ms,
        )


@dataclass(frozen=True, slots=True)
class ProposalResolution:
    outcome: Literal["applied", "kept_both", "rejected"]
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryHistory:
    memories: tuple[CodingMemory, ...]
    evolutions: tuple[EvolutionRecord, ...]
    statuses: tuple[tuple[str, MemoryStatus], ...]


def evaluate_proposal(
    proposal: EvolutionProposal, *, predecessor: CodingMemory | None, successor: CodingMemory, predecessor_status: MemoryStatus | None
) -> ProposalResolution:
    if successor.repo_key != proposal.repo_key or (predecessor is not None and predecessor.repo_key != proposal.repo_key):
        return ProposalResolution("rejected", "wrong_namespace")
    if proposal.successor_id != successor.memory_id:
        return ProposalResolution("rejected", "unknown_successor")
    if proposal.decision == "keep_both":
        return ProposalResolution("kept_both")
    if predecessor is None or proposal.predecessor_id != predecessor.memory_id:
        return ProposalResolution("rejected", "unknown_predecessor")
    if predecessor.memory_id == successor.memory_id:
        return ProposalResolution("rejected", "self_edge")
    if predecessor_status != "active":
        return ProposalResolution("rejected", "inactive_predecessor")
    if successor.memory_type == "task_experience":
        return ProposalResolution("rejected", "append_only_experience")
    if predecessor.memory_type != successor.memory_type:
        return ProposalResolution("kept_both", "cross_type")
    if memory_subject_key(predecessor) != memory_subject_key(successor):
        return ProposalResolution("kept_both", "different_subject")
    if proposal.relation_kind == "explicit_restore":
        return _restore_policy(predecessor, successor)
    if isinstance(successor.payload, WorkStatePayload):
        return (
            ProposalResolution("applied")
            if proposal.relation_kind == "work_state_update"
            else ProposalResolution("rejected", "wrong_relation")
        )
    if isinstance(successor.payload, UserPreferencePayload):
        if proposal.relation_kind != "preference_override":
            return ProposalResolution("rejected", "wrong_relation")
        return _newer_resolution(predecessor.source_order_key, successor.source_order_key)
    if isinstance(successor.payload, RepositoryKnowledgePayload):
        if proposal.relation_kind not in {"knowledge_obsolete", "knowledge_contradiction"}:
            return ProposalResolution("rejected", "wrong_relation")
        if proposal.proposer == "capture_model":
            return _newer_resolution(predecessor.source_order_key, successor.source_order_key)
        return ProposalResolution("applied")
    return ProposalResolution("rejected", "wrong_type")


def require_applied(resolution: ProposalResolution) -> None:
    if resolution.outcome != "applied":
        raise EvolutionRejected(
            resolution.error_code or resolution.outcome, f"Supersession was not applied: {resolution.error_code or resolution.outcome}"
        )


def evolution_identity(*, repo_key: str, relation_kind: EvolutionRelation, predecessor_id: str, successor_id: str) -> str:
    return typed_id(
        "evo",
        {
            "schema_version": SCHEMA_VERSION,
            "repo_key": repo_key,
            "relation_kind": relation_kind,
            "predecessor_id": predecessor_id,
            "successor_id": successor_id,
        },
    )


def proposal_to_dict(proposal: EvolutionProposal) -> dict[str, object]:
    return _record_to_dict(proposal)


def proposal_from_dict(value: object) -> EvolutionProposal:
    return _record_from_dict(EvolutionProposal, value)


def evolution_to_dict(record: EvolutionRecord) -> dict[str, object]:
    return _record_to_dict(record)


def evolution_from_dict(value: object) -> EvolutionRecord:
    return _record_from_dict(EvolutionRecord, value)


def evolution_commit_payload(commit: PreparedEvolutionCommit) -> dict[str, object]:
    return _commit_payload(
        repo_key=commit.repo_key,
        proposal=commit.proposal,
        record=commit.record,
        new_memory=commit.new_memory,
        expected_memory_file=commit.expected_memory_file,
        expected_evolution_file=commit.expected_evolution_file,
    )


def evolution_commit_from_payload(value: object, *, operation_id: str, created_at_ms: int) -> PreparedEvolutionCommit:
    data = _object(
        value,
        {
            "schema_version",
            "operation_kind",
            "repo_key",
            "proposal",
            "record",
            "new_memory",
            "expected_memory_file",
            "expected_evolution_file",
        },
    )
    if data["schema_version"] != SCHEMA_VERSION or data["operation_kind"] not in {"evolution", "restore"}:
        raise ValueError("Evolution Write Intent envelope is invalid")
    raw_memory = data["new_memory"]
    raw_memory_file = data["expected_memory_file"]
    return PreparedEvolutionCommit(
        operation_id=operation_id,
        repo_key=_string(data, "repo_key"),
        proposal=proposal_from_dict(data["proposal"]),
        record=evolution_from_dict(data["record"]),
        new_memory=None if raw_memory is None else coding_memory_from_dict(raw_memory),
        expected_memory_file=(None if raw_memory_file is None else _record_from_dict(ExpectedMemoryFile, raw_memory_file)),
        expected_evolution_file=_record_from_dict(ExpectedEvolutionFile, data["expected_evolution_file"]),
        created_at_ms=created_at_ms,
    )


def _newer_resolution(predecessor: SourceOrderKey | None, successor: SourceOrderKey | None) -> ProposalResolution:
    comparison = compare_source_order(predecessor, successor)
    if comparison is None:
        return ProposalResolution("kept_both", "incomparable_source_order")
    if comparison >= 0:
        return ProposalResolution("rejected", "successor_not_newer")
    return ProposalResolution("applied")


def compare_source_order(left: SourceOrderKey | None, right: SourceOrderKey | None) -> int | None:
    if left is None or right is None:
        return None
    if (left.provider, left.session_id, left.source_generation) == (right.provider, right.session_id, right.source_generation):
        return (left.event_index > right.event_index) - (left.event_index < right.event_index)
    if left.trusted_timestamp_ms is None or right.trusted_timestamp_ms is None:
        return None
    left_key = (left.trusted_timestamp_ms, left.provider, left.session_id, left.source_generation, left.event_index)
    right_key = (right.trusted_timestamp_ms, right.provider, right.session_id, right.source_generation, right.event_index)
    return (left_key > right_key) - (left_key < right_key)


def _restore_policy(predecessor: CodingMemory, successor: CodingMemory) -> ProposalResolution:
    if successor.origin != "restored" or successor.restore_predecessor_id != predecessor.memory_id or successor.restored_from is None:
        return ProposalResolution("rejected", "invalid_restore")
    return ProposalResolution("applied")


def _proposal_identity(proposal: EvolutionProposal) -> dict[str, object]:
    return _proposal_identity_fields(
        schema_version=proposal.schema_version,
        repo_key=proposal.repo_key,
        decision=proposal.decision,
        relation_kind=proposal.relation_kind,
        predecessor_id=proposal.predecessor_id,
        successor_id=proposal.successor_id,
        supporting_fact_ids=proposal.supporting_fact_ids,
        source_order_key=proposal.source_order_key,
        proposer=proposal.proposer,
        reason=proposal.reason,
    )


def _proposal_identity_fields(
    *,
    schema_version: int,
    repo_key: str,
    decision: EvolutionDecision,
    relation_kind: EvolutionRelation,
    predecessor_id: str | None,
    successor_id: str,
    supporting_fact_ids: tuple[str, ...],
    source_order_key: SourceOrderKey | None,
    proposer: EvolutionProposer,
    reason: str,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "repo_key": repo_key,
        "decision": decision,
        "relation_kind": relation_kind,
        "predecessor_id": predecessor_id,
        "successor_id": successor_id,
        "supporting_fact_ids": list(supporting_fact_ids),
        "source_order_key": (None if source_order_key is None else _record_to_dict(source_order_key)),
        "proposer": proposer,
        "reason": reason,
    }


def _evidence_order(value: EvidenceReference) -> tuple[str, str, int, int, str]:
    return (value.provider, value.session_id, value.source_generation, value.event_index, value.fact_id)


def _memory_id(value: str) -> None:
    if not value.startswith("mem_") or len(value) != 68:
        raise ValueError("Coding Memory identity is invalid")


def _commit_payload(
    *,
    repo_key: str,
    proposal: EvolutionProposal,
    record: EvolutionRecord,
    new_memory: CodingMemory | None,
    expected_memory_file: ExpectedMemoryFile | None,
    expected_evolution_file: ExpectedEvolutionFile,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation_kind": "restore" if new_memory is not None else "evolution",
        "repo_key": repo_key,
        "proposal": proposal_to_dict(proposal),
        "record": evolution_to_dict(record),
        "new_memory": None if new_memory is None else coding_memory_to_dict(new_memory),
        "expected_memory_file": (None if expected_memory_file is None else _record_to_dict(expected_memory_file)),
        "expected_evolution_file": _record_to_dict(expected_evolution_file),
    }


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Evolution object has invalid fields")
    return cast(dict[str, object], value)


def _string(data: dict[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"Evolution {key} must be a non-empty string")
    return value
