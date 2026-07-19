from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codecairn.memory.models import AgentTrace, CodingMemory, ImportResult
from codecairn.memory.trace import extract_failed_commands, segment_tasks


class TraceImporter(Protocol):
    provider: str

    def read(self, source_path: Path, *, source_root: Path | None = None) -> AgentTrace: ...


class MemoryStore(Protocol):
    def write(self, memory: CodingMemory) -> CodingMemory: ...


class ImportState(Protocol):
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
        memories: tuple[CodingMemory, ...],
    ) -> int: ...

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]: ...


class MemoryRuntime:
    """Deep module for importing and inspecting durable coding memory."""

    def __init__(
        self,
        *,
        importer: TraceImporter,
        memory_store: MemoryStore,
        state: ImportState,
    ) -> None:
        self._state = state
        self._markdown = memory_store
        self._importer = importer

    def import_session(
        self,
        source_path: Path,
        *,
        repo_key: str,
        source_root: Path | None = None,
    ) -> ImportResult:
        if not repo_key.strip():
            raise ValueError("repo_key must not be empty")
        trace = self._importer.read(source_path, source_root=source_root)
        episodes = segment_tasks(trace, repo_key=repo_key)
        candidates = extract_failed_commands(episodes, repo_key=repo_key)
        persisted = tuple(self._markdown.write(candidate) for candidate in candidates)

        committed_raw_event_index = trace.raw_event_count - 1
        created_count = self._state.commit_import(
            repo_key=repo_key,
            provider=trace.provider,
            session_id=trace.session_id,
            source_path=trace.source_path,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            memories=persisted,
        )
        return ImportResult(
            provider=trace.provider,
            session_id=trace.session_id,
            source_sha256=trace.source_sha256,
            raw_event_count=trace.raw_event_count,
            committed_raw_event_index=committed_raw_event_index,
            created_memory_count=created_count,
            skipped_memory_count=len(persisted) - created_count,
        )

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._state.list_memories(repo_key=repo_key)
