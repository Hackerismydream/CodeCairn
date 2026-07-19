from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codecairn.importers.claude import ClaudeImporter
from codecairn.importers.codex import CodexImporter
from codecairn.importers.jsonl import JsonlScan, read_jsonl
from codecairn.memory.errors import TraceParseError
from codecairn.memory.models import AgentTrace, ImportCheckpoint

_MAX_SESSION_BYTES = 64 * 1024 * 1024
_MAX_RAW_EVENTS = 100_000


class _JsonlAdapter(Protocol):
    provider: str

    def _from_scan(
        self,
        scan: JsonlScan,
        *,
        checkpoint: ImportCheckpoint | None,
    ) -> AgentTrace: ...


class SessionImporter:
    """Detect a supported JSONL provider and emit one shared Agent Trace."""

    def __init__(self) -> None:
        self._adapters: dict[str, _JsonlAdapter] = {
            ClaudeImporter.provider: ClaudeImporter(),
            CodexImporter.provider: CodexImporter(),
        }

    def read(
        self,
        source_path: Path,
        *,
        source_root: Path | None = None,
        checkpoint: ImportCheckpoint | None = None,
    ) -> AgentTrace:
        resumed_from = checkpoint.resume_raw_event_index if checkpoint is not None else 0
        scan = read_jsonl(
            source_path,
            source_root=source_root,
            start_raw_event_index=resumed_from,
            max_session_bytes=_MAX_SESSION_BYTES,
            max_raw_events=_MAX_RAW_EVENTS,
        )
        provider = checkpoint.provider if checkpoint is not None else _detect_provider(scan)
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise TraceParseError(f"Unsupported trace provider in checkpoint: {provider!r}")
        return adapter._from_scan(scan, checkpoint=checkpoint)


def _detect_provider(scan: JsonlScan) -> str:
    for record, _raw_event_sha256 in scan.records:
        if isinstance(record.get("sessionId"), str):
            return ClaudeImporter.provider
        if record.get("type") == "session_meta":
            return CodexImporter.provider
        payload = record.get("payload")
        if record.get("type") in {"event_msg", "response_item"} and isinstance(payload, dict):
            return CodexImporter.provider
    raise TraceParseError(f"Unsupported trace JSONL format: {scan.source_path}")
