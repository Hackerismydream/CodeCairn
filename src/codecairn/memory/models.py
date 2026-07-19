from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TraceEventKind = Literal["message", "tool_call", "tool_result", "metadata", "unknown"]
FileChangeOperation = Literal["add", "update", "delete", "move"]
MemoryType = Literal[
    "debug_episode",
    "repository_convention",
    "failed_command",
    "verified_fix",
    "user_preference",
]
EpisodeOutcome = Literal["success", "failed", "unknown"]
MemoryRepairReason = Literal["missing", "truncated", "hash_mismatch", "unparsable"]


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    provider: str
    session_id: str
    source_path: str
    raw_event_sha256: str
    raw_event_index: int
    raw_event_type: str
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class FileChangeFact:
    fact_id: str
    operation: FileChangeOperation
    path: str
    destination_path: str | None
    evidence: EvidenceReference


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    kind: TraceEventKind
    evidence: EvidenceReference
    role: str | None = None
    text: str | None = None
    tool_name: str | None = None
    call_id: str | None = None
    command: str | None = None
    exit_code: int | None = None
    tool_status: str | None = None
    file_changes: tuple[FileChangeFact, ...] = ()
    is_command_result: bool = False


@dataclass(frozen=True, slots=True)
class AgentTrace:
    trace_id: str
    provider: str
    session_id: str
    source_path: str
    source_sha256: str
    raw_event_count: int
    resumed_from_raw_event_index: int
    raw_prefix_sha256: str
    raw_prefix_call_ids: tuple[str, ...]
    raw_prefix_file_change_fact_count: int
    raw_suffix_event_sha256s: tuple[str, ...]
    events: tuple[TraceEvent, ...]


@dataclass(frozen=True, slots=True)
class TaskEpisode:
    episode_id: str
    trace_id: str
    opening_event_id: str
    events: tuple[TraceEvent, ...]
    outcome: EpisodeOutcome


@dataclass(frozen=True, slots=True)
class CodingMemory:
    memory_id: str
    repo_key: str
    memory_type: MemoryType
    title: str
    summary: str
    episode_id: str
    command: str | None
    exit_code: int | None
    evidence: tuple[EvidenceReference, ...]
    markdown_path: str | None = None
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ImportCheckpoint:
    provider: str
    session_id: str
    committed_raw_event_index: int
    resume_raw_event_index: int
    resume_prefix_sha256: str
    resume_call_ids: tuple[str, ...]
    resume_file_change_fact_count: int


@dataclass(frozen=True, slots=True)
class MemoryRepairPlan:
    repo_key: str
    memory_id: str
    reason: MemoryRepairReason
    observed_sha256: str | None
    expected_sha256: str


@dataclass(frozen=True, slots=True)
class PendingRecoveryAudit:
    audit_id: int
    plan: MemoryRepairPlan


@dataclass(frozen=True, slots=True)
class ImportResult:
    provider: str
    session_id: str
    source_sha256: str
    raw_event_count: int
    committed_raw_event_index: int
    resumed_from_raw_event_index: int
    processed_raw_event_count: int
    created_memory_count: int
    skipped_memory_count: int
    repaired_memory_count: int
