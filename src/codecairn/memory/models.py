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
TraceEpisodeOutcome = Literal["success", "failure", "unknown"]
MemoryRepairReason = Literal["missing", "truncated", "hash_mismatch", "unparsable"]
IndexOperation = Literal["upsert", "delete"]
CandidateSource = Literal["lexical", "vector"]
RecallDocumentKind = Literal["episode", "atomic_fact"]
RecallRoute = Literal["episode_first", "fact_first"]
RecallDocumentSource = Literal[
    "episode_lexical",
    "episode_entity_lexical",
    "episode_temporal_lexical",
    "episode_vector",
    "atomic_fact_lexical",
    "atomic_fact_entity_lexical",
    "atomic_fact_temporal_lexical",
    "atomic_fact_vector",
    "entity_posting",
    "provenance_posting",
]
RecallSnippetRelation = Literal["matched", "sibling", "neighbor"]
RecallStageName = Literal["candidate_recall", "fusion", "rerank", "selection", "context"]


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
class SemanticAtomicFact:
    fact_id: str
    text: str
    source_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SemanticEpisode:
    episode_id: str
    narrative: str
    atomic_facts: tuple[SemanticAtomicFact, ...]
    source_fact_ids: tuple[str, ...]
    semanticizer_id: str
    revision: str


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
class RecallDocument:
    document_id: str
    repo_key: str
    memory_id: str
    document_kind: RecallDocumentKind
    parent_document_id: str
    source_episode_id: str
    fact_id: str
    content_sha256: str
    document_sha256: str
    memory_type: MemoryType
    title: str
    summary: str
    content: str
    child_count: int


@dataclass(frozen=True, slots=True)
class RecallDocumentFingerprint:
    repo_key: str
    memory_id: str
    document_id: str
    document_kind: RecallDocumentKind
    parent_document_id: str
    fact_id: str
    document_sha256: str


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


@dataclass(frozen=True, slots=True)
class MemoryArtifact:
    memory: CodingMemory
    path: Path
    content_sha256: str


@dataclass(frozen=True, slots=True)
class IndexJob:
    job_id: int
    repo_key: str
    memory_id: str
    content_sha256: str
    operation: IndexOperation
    lease_owner: str


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


@dataclass(frozen=True, slots=True)
class TruthIssue:
    markdown_path: str
    observed_sha256: str | None
    error_type: str


@dataclass(frozen=True, slots=True)
class TruthScan:
    memories: tuple[CodingMemory, ...]
    issues: tuple[TruthIssue, ...]


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    created: int
    modified: int
    deleted: int
    corrupt: int


@dataclass(frozen=True, slots=True)
class RebuildReport:
    truth_count: int
    index_count: int
    parity: bool
    truth_document_count: int = 0
    index_document_count: int = 0
    document_parity: bool = True


@dataclass(frozen=True, slots=True)
class IndexCandidate:
    repo_key: str
    memory_id: str
    score: float
    document_id: str = ""
    document_kind: RecallDocumentKind = "episode"
    parent_document_id: str = ""
    fact_id: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""


@dataclass(frozen=True, slots=True)
class RecallMatch:
    document_id: str
    document_kind: RecallDocumentKind
    source: RecallDocumentSource
    score: float
    rank: int
    fact_id: str = ""


@dataclass(frozen=True, slots=True)
class RecallSnippet:
    relation: RecallSnippetRelation
    source_memory_id: str
    source_uri: str
    fact_id: str
    text: str
    source_title: str
    source_summary: str
    raw_event_index: int | None
    relevance_score: float | None = None
    selection_source: str | None = None
    semantic_text: str | None = None
    semantic_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RerankDocument:
    memory_id: str
    text: str
    fusion_score: float


@dataclass(frozen=True, slots=True)
class RerankScore:
    memory_id: str
    score: float


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
    reranker_score: float | None = None
    matched_documents: tuple[RecallMatch, ...] = ()
    snippets: tuple[RecallSnippet, ...] = ()
    episode_text: str = ""
    episode_fact_ids: tuple[str, ...] = ()
    episode_snippets: tuple[RecallSnippet, ...] = ()


@dataclass(frozen=True, slots=True)
class RecallStageTrace:
    stage: RecallStageName
    input_count: int
    output_count: int
    output_memory_ids: tuple[str, ...]


ContextSlotAdmissionOutcome = Literal[
    "admitted",
    "budget",
    "duplicate",
    "parent_limit",
]


@dataclass(frozen=True, slots=True)
class RecallContextSlotAttempt:
    fact_id: str
    outcome: ContextSlotAdmissionOutcome


@dataclass(frozen=True, slots=True)
class RecallContextSlotTrace:
    slot_kind: str
    max_facts: int
    attempts: tuple[RecallContextSlotAttempt, ...]


@dataclass(frozen=True, slots=True)
class RecallContextTrace:
    renderer: str
    char_count: int
    rendered_memory_ids: tuple[str, ...]
    rendered_fact_ids: tuple[str, ...]
    omitted_memory_ids: tuple[str, ...]
    omitted_snippet_count: int
    token_count: int = 0
    token_limit: int = 4_000
    tokenizer_id: str = "codecairn/utf8-two-byte-upper-bound-v1"
    omitted_fact_ids: tuple[str, ...] = ()
    admission_candidate_fact_ids: tuple[str, ...] = ()
    slot_traces: tuple[RecallContextSlotTrace, ...] = ()


@dataclass(frozen=True, slots=True)
class RecallSidecar:
    query: str
    repo_key: str
    limit: int
    latency_ms: float
    vector_candidate_count: int
    lexical_candidate_count: int
    ranked: tuple[RankedRecall, ...]
    reranker_model: str | None = None
    reranker_source: str | None = None
    reranker_revision: str | None = None
    embedding_model: str | None = None
    embedding_source: str | None = None
    embedding_revision: str | None = None
    retrieval_config_sha256: str | None = None
    recall_route: RecallRoute = "episode_first"
    episode_vector_candidate_count: int = 0
    episode_lexical_candidate_count: int = 0
    atomic_fact_vector_candidate_count: int = 0
    atomic_fact_lexical_candidate_count: int = 0
    episode_temporal_lexical_candidate_count: int = 0
    atomic_fact_temporal_lexical_candidate_count: int = 0
    episode_entity_lexical_candidate_count: int = 0
    atomic_fact_entity_lexical_candidate_count: int = 0
    neighbor_expansion_count: int = 0
    entity_posting_candidate_count: int = 0
    rerank_bundle_count: int = 0
    query_anchors: tuple[str, ...] = ()
    query_temporal_prefixes: tuple[str, ...] = ()
    query_sketcher_id: str = "codecairn/deterministic-query-sketch-v1"
    covered_slots: tuple[str, ...] = ()
    missing_slots: tuple[str, ...] = ()
    covered_requirements: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    expansion_fact_count: int = 0
    expansion_fact_limit: int = 0
    provenance_expansion_count: int = 0
    completion: Literal["complete", "partial"] = "complete"
    degraded_stages: tuple[str, ...] = ()
    query_vector_sha256: str | None = None
    neighbor_window: int = 0
    hydrated_episode_count: int = 0
    hydrated_episode_ids: tuple[str, ...] = ()
    partial_episode_ids: tuple[str, ...] = ()
    dropped_episode_ids: tuple[str, ...] = ()
    stage_trace: tuple[RecallStageTrace, ...] = ()
    context_trace: RecallContextTrace | None = None


@dataclass(frozen=True, slots=True)
class RecallResult:
    markdown: str
    sidecar: RecallSidecar
