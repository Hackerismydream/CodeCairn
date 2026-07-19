from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from codecairn.memory.errors import TraceImportError
from codecairn.memory.models import AgentTrace, EvidenceReference, TraceEvent
from codecairn.memory.trace import stable_id


class TraceParseError(TraceImportError):
    """Raised when a provider trace cannot be parsed safely."""


_MAX_SESSION_BYTES = 64 * 1024 * 1024
_COMMAND_TOOLS = frozenset({"exec_command"})
_MIN_EXIT_CODE = -(2**31)
_MAX_EXIT_CODE = 2**31 - 1


class CodexImporter:
    provider = "codex"

    def read(self, source_path: Path, *, source_root: Path | None = None) -> AgentTrace:
        observed_path = Path(os.path.abspath(source_path))
        if source_root is None:
            source_bytes = _read_session_bytes(observed_path)
        else:
            source_bytes = _read_session_beneath_root(
                observed_path,
                source_root=source_root,
            )
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        records = _parse_jsonl(source_bytes, source_path=observed_path)
        session_id = _session_id(records, fallback=observed_path.stem)
        pending_calls: dict[str, TraceEvent] = {}
        seen_call_ids: set[str] = set()
        events = tuple(
            _normalize(
                raw_event=record,
                raw_event_sha256=raw_event_sha256,
                raw_event_index=index,
                source_path=observed_path,
                session_id=session_id,
                pending_calls=pending_calls,
                seen_call_ids=seen_call_ids,
            )
            for index, (record, raw_event_sha256) in enumerate(records)
        )
        return AgentTrace(
            trace_id=stable_id("trace", self.provider, session_id),
            provider=self.provider,
            session_id=session_id,
            source_path=str(observed_path),
            source_sha256=source_sha256,
            raw_event_count=len(records),
            events=events,
        )


RawRecord = tuple[dict[str, Any], str]


def _read_session_bytes(source_path: Path) -> bytes:
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
    return _read_regular_descriptor(descriptor, source_path=source_path)


def _read_session_beneath_root(source_path: Path, *, source_root: Path) -> bytes:
    root = Path(os.path.abspath(source_root))
    try:
        relative = source_path.relative_to(root)
    except ValueError as exc:
        raise TraceParseError(f"Codex source is outside configured root: {source_path}") from exc
    if not relative.parts:
        raise TraceParseError(f"Codex source is not a file: {source_path}")
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
        file_descriptor = os.open(
            relative.parts[-1],
            file_flags,
            dir_fd=descriptors[-1],
        )
    except OSError as exc:
        translated = _safe_open_error(source_path, exc)
        if translated is exc:
            raise
        raise translated from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return _read_regular_descriptor(file_descriptor, source_path=source_path)


def _read_regular_descriptor(descriptor: int, *, source_path: Path) -> bytes:
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TraceParseError(f"Codex source is not a regular file: {source_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            source = handle.read(_MAX_SESSION_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(source) > _MAX_SESSION_BYTES:
        raise TraceParseError(
            f"Codex source exceeds the {_MAX_SESSION_BYTES}-byte import limit: {source_path}"
        )
    return source


def _safe_open_error(source_path: Path, error: OSError) -> Exception:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return TraceParseError(f"Codex source path must not traverse symbolic links: {source_path}")
    return error


def _parse_jsonl(source: bytes, *, source_path: Path) -> list[RawRecord]:
    records: list[RawRecord] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TraceParseError(f"Invalid Codex JSONL at {source_path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise TraceParseError(f"Codex record at {source_path}:{line_number} is not an object")
        records.append((value, hashlib.sha256(line).hexdigest()))
    return records


def _session_id(records: list[RawRecord], *, fallback: str) -> str:
    for record, _raw_event_sha256 in records:
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if isinstance(payload, dict):
            value = payload.get("id")
            if isinstance(value, str):
                return value
    return fallback


def _normalize(
    *,
    raw_event: dict[str, Any],
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
    pending_calls: dict[str, TraceEvent],
    seen_call_ids: set[str],
) -> TraceEvent:
    raw_event_type = _string(raw_event.get("type")) or "unknown"
    payload = raw_event.get("payload")
    payload_type = _string(payload.get("type")) if isinstance(payload, dict) else None
    call_id = _string(payload.get("call_id")) if isinstance(payload, dict) else None
    evidence = EvidenceReference(
        provider=CodexImporter.provider,
        session_id=session_id,
        source_path=str(source_path),
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        raw_event_type=raw_event_type,
        call_id=call_id,
    )
    event_id = stable_id(
        "event",
        CodexImporter.provider,
        session_id,
        raw_event_index,
        raw_event_sha256,
        raw_event_type,
        payload_type,
    )

    if raw_event_type == "session_meta":
        return TraceEvent(event_id=event_id, kind="metadata", evidence=evidence)
    if raw_event_type != "response_item" or not isinstance(payload, dict):
        return TraceEvent(event_id=event_id, kind="unknown", evidence=evidence)
    if payload_type == "message":
        return TraceEvent(
            event_id=event_id,
            kind="message",
            evidence=evidence,
            role=_string(payload.get("role")),
            text=_message_text(payload.get("content")),
        )
    if payload_type == "function_call":
        tool_name = _string(payload.get("name"))
        command = _command(payload.get("arguments")) if tool_name in _COMMAND_TOOLS else None
        event = TraceEvent(
            event_id=event_id,
            kind="tool_call",
            evidence=evidence,
            tool_name=tool_name,
            call_id=call_id,
            command=command,
        )
        if call_id is not None:
            if call_id in seen_call_ids:
                raise TraceParseError(
                    f"Duplicate Codex call_id {call_id!r} at raw event {raw_event_index}"
                )
            seen_call_ids.add(call_id)
            pending_calls[call_id] = event
        return event
    if payload_type == "function_call_output":
        raw_output = payload.get("output")
        output = _output_text(raw_output)
        paired_call = pending_calls.pop(call_id, None) if call_id is not None else None
        return TraceEvent(
            event_id=event_id,
            kind="tool_result",
            evidence=evidence,
            text=output,
            tool_name=paired_call.tool_name if paired_call is not None else None,
            call_id=call_id,
            command=paired_call.command if paired_call is not None else None,
            exit_code=_exit_code(raw_output),
        )
    return TraceEvent(event_id=event_id, kind="unknown", evidence=evidence)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _message_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = [
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    return "\n".join(parts) if parts else None


def _command(arguments: object) -> str | None:
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    for key in ("cmd", "command"):
        value = parsed.get(key)
        if isinstance(value, str):
            return value
    return None


def _output_text(output: object) -> str | None:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _message_text(output)
    if isinstance(output, dict):
        text = output.get("output")
        return text if isinstance(text, str) else json.dumps(output, sort_keys=True)
    return None


_WRAPPED_EXIT_CODE = re.compile(
    r"^Process exited with code (?P<code>-?\d+)[ \t]*$",
    flags=re.MULTILINE,
)


def _exit_code(output: object) -> int | None:
    if isinstance(output, dict):
        structured = output.get("exit_code")
        if isinstance(structured, int) and not isinstance(structured, bool):
            return _validated_exit_code(structured)
        output = output.get("output")
    if isinstance(output, list):
        output = _output_text(output)
    if not isinstance(output, str):
        return None
    envelope = output.partition("\nFinal output:")[0]
    matches = tuple(_WRAPPED_EXIT_CODE.finditer(envelope))
    if len(matches) > 1:
        raise TraceParseError("Ambiguous Codex result contains multiple exit status lines")
    return _validated_exit_code(int(matches[0].group("code"))) if matches else None


def _validated_exit_code(value: int) -> int:
    if not _MIN_EXIT_CODE <= value <= _MAX_EXIT_CODE:
        raise TraceParseError(f"Codex exit code is outside signed 32-bit range: {value}")
    return value
