from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codecairn.memory.errors import SourceRewritten, TraceParseError
from codecairn.memory.models import AgentTrace, ImportCheckpoint, TraceEvent
from codecairn.memory.schema import Provider
from codecairn.memory.trace import EMPTY_RAW_PREFIX_SHA256, extend_raw_prefix_sha256, stable_id

RawRecord = tuple[dict[str, Any], str]
MAX_SESSION_BYTES = 64 * 1024 * 1024
MAX_RAW_EVENTS = 100_000
MAX_SESSION_ID_CHARS = 512


@dataclass(frozen=True, slots=True)
class JsonlScan:
    source_path: Path
    source_sha256: str
    records: tuple[RawRecord, ...]
    raw_event_count: int
    prefix_sha256: str


def read_import_scan(source_path: Path, *, source_root: Path | None, checkpoint: ImportCheckpoint | None) -> JsonlScan:
    return read_jsonl(
        source_path,
        source_root=source_root,
        start_raw_event_index=checkpoint.resume_raw_event_index if checkpoint is not None else 0,
        max_session_bytes=MAX_SESSION_BYTES,
        max_raw_events=MAX_RAW_EVENTS,
    )


def validated_session_id(value: str, *, label: str) -> str:
    if not value or len(value) > MAX_SESSION_ID_CHARS:
        raise TraceParseError(f"{label} session id must contain 1 to {MAX_SESSION_ID_CHARS} characters")
    if any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value):
        raise TraceParseError(f"{label} session id contains an unsafe control or line separator")
    return value


def text_content(value: object, *, json_fallback: bool = False) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    texts = [item["text"] for item in value if isinstance(item, dict) and isinstance(item.get("text"), str)]
    return "\n".join(texts) if texts else json.dumps(value, sort_keys=True) if json_fallback else None


def read_jsonl(
    source_path: Path, *, source_root: Path | None, start_raw_event_index: int, max_session_bytes: int, max_raw_events: int
) -> JsonlScan:
    observed_path = Path(os.path.abspath(source_path))
    if source_root is None:
        source = _read_source_bytes(observed_path, max_session_bytes=max_session_bytes)
    else:
        source = _read_source_beneath_root(observed_path, source_root=source_root, max_session_bytes=max_session_bytes)
    records, raw_event_count, prefix_sha256 = _scan_records(
        source, source_path=observed_path, start_raw_event_index=start_raw_event_index, max_raw_events=max_raw_events
    )
    return JsonlScan(
        source_path=observed_path,
        source_sha256=hashlib.sha256(source).hexdigest(),
        records=records,
        raw_event_count=raw_event_count,
        prefix_sha256=prefix_sha256,
    )


def checkpoint_context(
    scan: JsonlScan, checkpoint: ImportCheckpoint | None, *, provider: Provider, label: str, max_file_changes: int
) -> tuple[int, tuple[str, ...], int]:
    if checkpoint is None:
        return 0, (), 0
    if checkpoint.provider != provider:
        raise TraceParseError(f"{label} checkpoint provider does not match the importer")
    if checkpoint.committed_raw_event_index < -1:
        raise TraceParseError(f"{label} committed raw-event index is invalid")
    if not 0 <= checkpoint.resume_raw_event_index <= checkpoint.committed_raw_event_index + 1:
        raise TraceParseError(f"{label} resume checkpoint is outside the committed cursor")
    if not 0 <= checkpoint.resume_file_change_fact_count <= max_file_changes:
        raise TraceParseError(f"{label} checkpoint file-change count is outside the import limit")
    if len(checkpoint.resume_call_ids) != len(set(checkpoint.resume_call_ids)):
        raise TraceParseError(f"{label} checkpoint contains duplicate call IDs")
    if scan.prefix_sha256 != checkpoint.resume_prefix_sha256:
        raise SourceRewritten(f"{label} source changed before checkpoint: {scan.source_path}")
    if scan.raw_event_count - 1 < checkpoint.committed_raw_event_index:
        raise SourceRewritten(f"{label} source is truncated before checkpoint: {scan.source_path}")
    return (checkpoint.resume_raw_event_index, checkpoint.resume_call_ids, checkpoint.resume_file_change_fact_count)


def agent_trace(
    scan: JsonlScan, *, provider: Provider, session_id: str, context: tuple[int, tuple[str, ...], int], events: tuple[TraceEvent, ...]
) -> AgentTrace:
    resumed_from, call_ids, file_change_count = context
    return AgentTrace(
        trace_id=stable_id("trace", provider, session_id),
        provider=provider,
        session_id=session_id,
        source_path=str(scan.source_path),
        source_sha256=scan.source_sha256,
        raw_event_count=scan.raw_event_count,
        resumed_from_raw_event_index=resumed_from,
        raw_prefix_sha256=scan.prefix_sha256,
        raw_prefix_call_ids=call_ids,
        raw_prefix_file_change_fact_count=file_change_count,
        raw_suffix_event_sha256s=tuple(item[1] for item in scan.records),
        events=events,
    )


def _read_source_bytes(source_path: Path, *, max_session_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source_path, flags)
    except OSError as exc:
        translated = _safe_open_error(source_path, exc)
        if translated is exc:
            raise
        raise translated from exc
    return _read_regular_descriptor(descriptor, source_path=source_path, max_session_bytes=max_session_bytes)


def _read_source_beneath_root(source_path: Path, *, source_root: Path, max_session_bytes: int) -> bytes:
    root = Path(os.path.abspath(source_root))
    try:
        relative = source_path.relative_to(root)
    except ValueError as exc:
        raise TraceParseError(f"Trace source is outside configured root: {source_path}") from exc
    if not relative.parts:
        raise TraceParseError(f"Trace source is not a file: {source_path}")
    if os.open not in os.supports_dir_fd:
        raise TraceParseError("Secure source-root traversal is unsupported on this platform")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    file_flags |= getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NOFOLLOW", 0)

    descriptors: list[int] = []
    try:
        descriptors.append(os.open(root, directory_flags))
        for component in relative.parts[:-1]:
            descriptors.append(os.open(component, directory_flags, dir_fd=descriptors[-1]))
        file_descriptor = os.open(relative.parts[-1], file_flags, dir_fd=descriptors[-1])
    except OSError as exc:
        translated = _safe_open_error(source_path, exc)
        if translated is exc:
            raise
        raise translated from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return _read_regular_descriptor(file_descriptor, source_path=source_path, max_session_bytes=max_session_bytes)


def _read_regular_descriptor(descriptor: int, *, source_path: Path, max_session_bytes: int) -> bytes:
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TraceParseError(f"Trace source is not a regular file: {source_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            source = handle.read(max_session_bytes + 1)
    finally:
        os.close(descriptor)
    if len(source) > max_session_bytes:
        raise TraceParseError(f"Trace source exceeds the {max_session_bytes}-byte import limit: {source_path}")
    return source


def _safe_open_error(source_path: Path, error: OSError) -> Exception:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return TraceParseError(f"Trace source path must not traverse symbolic links: {source_path}")
    return error


def _scan_records(
    source: bytes, *, source_path: Path, start_raw_event_index: int, max_raw_events: int
) -> tuple[tuple[RawRecord, ...], int, str]:
    if start_raw_event_index < 0:
        raise TraceParseError("Trace checkpoint raw-event index must not be negative")
    records: list[RawRecord] = []
    raw_event_count = 0
    prefix_sha256 = EMPTY_RAW_PREFIX_SHA256
    for line_number, line in enumerate(io.BytesIO(source), start=1):
        line = line.removesuffix(b"\n").removesuffix(b"\r")
        if not line.strip():
            continue
        if raw_event_count >= max_raw_events:
            raise TraceParseError(f"Trace source exceeds the {max_raw_events}-event import limit: {source_path}")
        raw_event_sha256 = hashlib.sha256(line).hexdigest()
        if raw_event_count < start_raw_event_index:
            prefix_sha256 = extend_raw_prefix_sha256(prefix_sha256, raw_event_sha256)
            raw_event_count += 1
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TraceParseError(f"Invalid trace JSONL at {source_path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise TraceParseError(f"Trace record at {source_path}:{line_number} is not an object")
        records.append((value, raw_event_sha256))
        raw_event_count += 1
    if raw_event_count < start_raw_event_index:
        raise SourceRewritten(f"Trace source is truncated before committed checkpoint: {source_path}")
    return tuple(records), raw_event_count, prefix_sha256
