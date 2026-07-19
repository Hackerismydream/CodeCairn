from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TraceEventKind = Literal["message", "tool_call", "tool_result", "metadata", "unknown"]
MemoryType = Literal[
    "debug_episode",
    "repository_convention",
    "failed_command",
    "verified_fix",
    "user_preference",
]
EpisodeOutcome = Literal["success", "failed", "unknown"]


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


@dataclass(frozen=True, slots=True)
class AgentTrace:
    trace_id: str
    provider: str
    session_id: str
    source_path: str
    source_sha256: str
    raw_event_count: int
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
class ImportResult:
    provider: str
    session_id: str
    source_sha256: str
    raw_event_count: int
    committed_raw_event_index: int
    created_memory_count: int
    skipped_memory_count: int
