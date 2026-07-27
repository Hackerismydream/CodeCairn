from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codecairn.memory.schema import (
    CodingMemory as CodingMemory,
)
from codecairn.memory.schema import (
    EvidenceFact as EvidenceFact,
)
from codecairn.memory.schema import (
    MemoryType as MemoryType,
)
from codecairn.memory.schema import (
    Provider,
)

TraceEventKind = Literal["message", "tool_call", "tool_result", "metadata", "unknown"]
FileChangeOperation = Literal["add", "update", "delete", "move"]
TraceEpisodeOutcome = Literal["success", "failure", "partial", "unknown"]
CandidateSource = Literal["lexical", "vector"]


@dataclass(frozen=True, slots=True)
class TraceReference:
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
    evidence: TraceReference


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    kind: TraceEventKind
    evidence: TraceReference
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
    provider: Provider
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
class TraceEpisode:
    episode_id: str
    trace_id: str
    opening_event_id: str
    events: tuple[TraceEvent, ...]
    outcome: TraceEpisodeOutcome


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


@dataclass(frozen=True, slots=True)
class MemoryArtifact:
    memory: CodingMemory
    path: Path
    content_sha256: str


@dataclass(frozen=True, slots=True)
class IndexHealth:
    pending: int
    leased: int
    indexed: int
    failed: int
    stale: int


@dataclass(frozen=True, slots=True)
class OperationalCounts:
    import_count: int
    observed_event_count: int
    memory_count: int
    pending_recovery_count: int
    conflicted_recovery_count: int


@dataclass(frozen=True, slots=True)
class RebuildReport:
    truth_count: int
    index_count: int
    parity: bool
    truth_document_count: int = 0
    index_document_count: int = 0
    document_parity: bool = True


@dataclass(frozen=True, slots=True)
class RecallEvidence:
    provider: str
    session_id: str
    raw_event_sha256: str
    raw_event_index: int
    raw_event_type: str
    call_id: str | None


@dataclass(frozen=True, slots=True)
class RankedRecall:
    rank: int
    memory_id: str
    memory_type: MemoryType
    title: str
    summary: str
    source_uri: str
    content_sha256: str
    candidate_sources: tuple[CandidateSource, ...]
    vector_score: float | None
    vector_rank: int | None
    lexical_score: float | None
    lexical_rank: int | None
    final_score: float
    evidence: tuple[RecallEvidence, ...]
    episode_text: str = ""
    episode_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecallContextTrace:
    renderer: str
    char_count: int
    rendered_memory_ids: tuple[str, ...]
    rendered_fact_ids: tuple[str, ...]
    omitted_memory_ids: tuple[str, ...]
    omitted_snippet_count: int


@dataclass(frozen=True, slots=True)
class RecallSidecar:
    query: str
    repo_key: str
    limit: int
    latency_ms: float
    vector_candidate_count: int
    lexical_candidate_count: int
    ranked: tuple[RankedRecall, ...]
    completion: Literal["complete", "partial"] = "complete"
    degraded_stages: tuple[str, ...] = ()
    context_trace: RecallContextTrace | None = None


@dataclass(frozen=True, slots=True)
class RecallResult:
    markdown: str
    sidecar: RecallSidecar
