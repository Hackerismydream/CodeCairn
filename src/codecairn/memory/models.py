from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codecairn.memory.schema import CodingMemory as CodingMemory
from codecairn.memory.schema import EvidenceFact as EvidenceFact
from codecairn.memory.schema import EvidenceReference as EvidenceReference
from codecairn.memory.schema import MemoryType as MemoryType
from codecairn.memory.schema import Provider

TraceEventKind = Literal["message", "tool_call", "tool_result", "metadata", "unknown"]
FileChangeOperation = Literal["add", "update", "delete", "move"]
TraceEpisodeOutcome = Literal["success", "failure", "partial", "unknown"]
CandidateSource = Literal["lexical", "vector"]
DocumentKind = Literal["memory", "fact", "snippet"]
MemoryStatus = Literal["active", "superseded"]
HookOutcome = Literal["imported", "noop", "failed", "unsupported"]


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
    observed_outcome: TraceEpisodeOutcome | None = None


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
    source_repo_key: str | None = None


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
class HookReceipt:
    schema_version: int
    receipt_id: str
    repo_key: str | None
    client: Literal["codex", "claude"]
    event: Literal["stop", "session_end"]
    client_version: str
    session_identity_sha256: str
    source_identity_sha256: str | None
    outcome: HookOutcome
    error_code: str | None
    retry_command: str | None
    started_at_ms: int
    duration_ms: int


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
class IndexJob:
    job_id: str
    repo_key: str
    memory_id: str
    target_status: MemoryStatus
    attempt_count: int


@dataclass(frozen=True, slots=True)
class RecallDocument:
    document_id: str
    document_kind: DocumentKind
    repo_key: str
    memory_id: str
    memory_type: MemoryType
    status: MemoryStatus
    title: str
    content: str
    content_sha256: str
    created_at_ms: int
    workstream_key: str | None


@dataclass(frozen=True, slots=True)
class IndexCandidate:
    memory_id: str
    document_id: str = ""
    content: str = ""
    relevance_score: float | None = None


@dataclass(frozen=True, slots=True)
class RecallSnippet:
    document_id: str
    text: str
    final_score: float


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
    final_score: float
    evidence: tuple[EvidenceReference, ...]
    status: MemoryStatus = "active"
    pinned: bool = False
    snippets: tuple[RecallSnippet, ...] = ()


@dataclass(frozen=True, slots=True)
class RecallContextTrace:
    renderer: str
    rendered_memory_ids: tuple[str, ...]
    rendered_fact_ids: tuple[str, ...]
    omitted_snippet_count: int
    type_caps: tuple[tuple[MemoryType, int], ...]
    tokenizer: str = "codecairn/utf8-two-byte-upper-bound-v1"
    token_count: int = 0
    token_limit: int = 8_192


@dataclass(frozen=True, slots=True)
class RecallAdmissionTrace:
    policy: str
    outcome: Literal["admitted", "abstained"]
    reason: Literal["relevant_candidate", "pinned_work_state", "no_candidates", "below_threshold"]
    vector_threshold: float
    max_vector_score: float | None


@dataclass(frozen=True, slots=True)
class RecallOmission:
    memory_id: str
    reason: Literal["historical_filter", "relevance", "type_cap", "limit", "token_budget"]


@dataclass(frozen=True, slots=True)
class RecallSidecar:
    query: str
    repo_key: str
    limit: int
    latency_ms: float
    vector_candidate_count: int
    lexical_candidate_count: int
    ranked: tuple[RankedRecall, ...]
    context_trace: RecallContextTrace | None = None
    admission_trace: RecallAdmissionTrace | None = None
    retrieval_profile: str = "unconfigured"
    include_superseded: bool = False
    workstream_key: str | None = None
    omissions: tuple[RecallOmission, ...] = ()
    source_cursor: int = -1
    index_cursor: int = -1
    semantic_state: str = "complete"
    freshness: Literal["fresh", "semantic_pending"] = "fresh"


@dataclass(frozen=True, slots=True)
class RecallResult:
    markdown: str
    sidecar: RecallSidecar
