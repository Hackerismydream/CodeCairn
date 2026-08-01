from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codecairn.importers.claude import ClaudeImporter
from codecairn.importers.codex import CodexImporter
from codecairn.importers.jsonl import JsonlScan, read_import_scan
from codecairn.importers.pico import PICO_SOURCE_SCHEMA, PicoImporter
from codecairn.memory.errors import TraceParseError
from codecairn.memory.models import AgentTrace, ImportCheckpoint
from codecairn.memory.schema import Provider


class _JsonlAdapter(Protocol):
    provider: Provider

    def _from_scan(self, scan: JsonlScan, *, checkpoint: ImportCheckpoint | None) -> AgentTrace: ...


class SessionImporter:
    """Detect a supported JSONL provider and emit one shared Agent Trace."""

    def __init__(self) -> None:
        self._adapters: dict[str, _JsonlAdapter] = {
            ClaudeImporter.provider: ClaudeImporter(),
            CodexImporter.provider: CodexImporter(),
            PicoImporter.provider: PicoImporter(),
        }

    def read(self, source_path: Path, *, source_root: Path | None = None, checkpoint: ImportCheckpoint | None = None) -> AgentTrace:
        scan = read_import_scan(source_path, source_root=source_root, checkpoint=checkpoint)
        return self.from_scan(scan, checkpoint=checkpoint)

    def from_scan(self, scan: JsonlScan, *, checkpoint: ImportCheckpoint | None = None) -> AgentTrace:
        """Normalize one already-safe scan without reading its source again."""
        provider = checkpoint.provider if checkpoint is not None else _detect_provider(scan)
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise TraceParseError(f"Unsupported trace provider in checkpoint: {provider!r}")
        return adapter._from_scan(scan, checkpoint=checkpoint)


def _detect_provider(scan: JsonlScan) -> Provider:
    for record, _raw_event_sha256 in scan.records:
        if record.get("schema") == PICO_SOURCE_SCHEMA and record.get("record_type") == "header":
            return PicoImporter.provider
        if isinstance(record.get("sessionId"), str):
            return ClaudeImporter.provider
        if record.get("type") == "session_meta":
            return CodexImporter.provider
        payload = record.get("payload")
        if record.get("type") in {"event_msg", "response_item"} and isinstance(payload, dict):
            return CodexImporter.provider
    raise TraceParseError(f"Unsupported trace JSONL format: {scan.source_path}")
