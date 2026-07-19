from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codecairn.memory.errors import TraceParseError
from codecairn.memory.trace import EMPTY_RAW_PREFIX_SHA256, extend_raw_prefix_sha256

RawRecord = tuple[dict[str, Any], str]


@dataclass(frozen=True, slots=True)
class JsonlScan:
    source_path: Path
    source_sha256: str
    records: tuple[RawRecord, ...]
    raw_event_count: int
    prefix_sha256: str


def read_jsonl(
    source_path: Path,
    *,
    source_root: Path | None,
    start_raw_event_index: int,
    max_session_bytes: int,
    max_raw_events: int,
) -> JsonlScan:
    observed_path = Path(os.path.abspath(source_path))
    if source_root is None:
        source = _read_source_bytes(observed_path, max_session_bytes=max_session_bytes)
    else:
        source = _read_source_beneath_root(
            observed_path,
            source_root=source_root,
            max_session_bytes=max_session_bytes,
        )
    records, raw_event_count, prefix_sha256 = _scan_records(
        source,
        source_path=observed_path,
        start_raw_event_index=start_raw_event_index,
        max_raw_events=max_raw_events,
    )
    return JsonlScan(
        source_path=observed_path,
        source_sha256=hashlib.sha256(source).hexdigest(),
        records=records,
        raw_event_count=raw_event_count,
        prefix_sha256=prefix_sha256,
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
    return _read_regular_descriptor(
        descriptor,
        source_path=source_path,
        max_session_bytes=max_session_bytes,
    )


def _read_source_beneath_root(
    source_path: Path,
    *,
    source_root: Path,
    max_session_bytes: int,
) -> bytes:
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
    return _read_regular_descriptor(
        file_descriptor,
        source_path=source_path,
        max_session_bytes=max_session_bytes,
    )


def _read_regular_descriptor(
    descriptor: int,
    *,
    source_path: Path,
    max_session_bytes: int,
) -> bytes:
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TraceParseError(f"Trace source is not a regular file: {source_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            source = handle.read(max_session_bytes + 1)
    finally:
        os.close(descriptor)
    if len(source) > max_session_bytes:
        raise TraceParseError(
            f"Trace source exceeds the {max_session_bytes}-byte import limit: {source_path}"
        )
    return source


def _safe_open_error(source_path: Path, error: OSError) -> Exception:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return TraceParseError(f"Trace source path must not traverse symbolic links: {source_path}")
    return error


def _scan_records(
    source: bytes,
    *,
    source_path: Path,
    start_raw_event_index: int,
    max_raw_events: int,
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
            raise TraceParseError(
                f"Trace source exceeds the {max_raw_events}-event import limit: {source_path}"
            )
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
        raise TraceParseError(
            f"Trace source is truncated before committed checkpoint: {source_path}"
        )
    return tuple(records), raw_event_count, prefix_sha256
