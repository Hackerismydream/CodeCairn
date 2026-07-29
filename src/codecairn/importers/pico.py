from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codecairn.importers.jsonl import JsonlScan, agent_trace, checkpoint_context, read_import_scan, validated_session_id
from codecairn.memory.errors import TraceParseError
from codecairn.memory.models import AgentTrace, FileChangeFact, ImportCheckpoint, TraceEpisodeOutcome, TraceEvent, TraceReference
from codecairn.memory.schema import Provider, SchemaInvalid, normalize_path_key, normalize_text
from codecairn.memory.trace import stable_id

PICO_SOURCE_SCHEMA = "codecairn.pico.source.v1"
MAX_PICO_SESSION_FILE_CHANGES = 10_000
MAX_PICO_BATCH_EVENTS = 2_048
MAX_PICO_BATCH_BYTES = 4 * 1024 * 1024
_MAX_TEXT_CHARS = 32_768
_MAX_ARGUMENT_BYTES = 256 * 1024
_MAX_PATH_CHARS = 4_096
_MAX_ATTRIBUTE_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class _PendingCall:
    tool_name: str
    command: str | None


@dataclass(slots=True)
class _NormalizeState:
    pending_calls: dict[str, _PendingCall] = field(default_factory=dict)
    seen_call_ids: set[str] = field(default_factory=set)
    file_change_fact_count: int = 0


class PicoImporter:
    provider: Provider = "pico"

    def read(self, source_path: Path, *, source_root: Path | None = None, checkpoint: ImportCheckpoint | None = None) -> AgentTrace:
        scan = read_import_scan(source_path, source_root=source_root, checkpoint=checkpoint)
        return self._from_scan(scan, checkpoint=checkpoint)

    def _from_scan(self, scan: JsonlScan, *, checkpoint: ImportCheckpoint | None) -> AgentTrace:
        context = checkpoint_context(
            scan, checkpoint, provider=self.provider, label="Pico", max_file_changes=MAX_PICO_SESSION_FILE_CHANGES
        )
        resumed_from, raw_prefix_call_ids, raw_prefix_file_change_fact_count = context
        records = scan.records
        source_repo_key = None
        if checkpoint is None:
            if not records:
                raise TraceParseError("Pico source journal is empty")
            session_id, source_repo_key = _header_identity(records[0][0])
        else:
            session_id = validated_session_id(checkpoint.session_id, label="Pico")
        state = _NormalizeState(seen_call_ids=set(raw_prefix_call_ids), file_change_fact_count=raw_prefix_file_change_fact_count)
        events: list[TraceEvent] = []
        for raw_event_index, (record, raw_event_sha256) in enumerate(records, start=resumed_from):
            if checkpoint is None and raw_event_index == 0:
                events.append(
                    _metadata_event(
                        record,
                        source_path=scan.source_path,
                        session_id=session_id,
                        raw_event_index=raw_event_index,
                        raw_event_sha256=raw_event_sha256,
                    )
                )
                continue
            events.extend(
                _batch_events(
                    record,
                    source_path=scan.source_path,
                    session_id=session_id,
                    raw_event_index=raw_event_index,
                    raw_event_sha256=raw_event_sha256,
                    state=state,
                )
            )
        return agent_trace(
            scan, provider=self.provider, session_id=session_id, context=context, events=tuple(events), source_repo_key=source_repo_key
        )


def _header_identity(record: dict[str, Any]) -> tuple[str, str]:
    if (
        set(record) != {"schema", "record_type", "provider", "session_id", "repo_key", "source_generation", "created_by"}
        or record.get("schema") != PICO_SOURCE_SCHEMA
        or record.get("record_type") != "header"
        or record.get("provider") != PicoImporter.provider
        or record.get("created_by") != "codecairn"
        or type(record.get("source_generation")) is not int
        or record.get("source_generation") != 1
    ):
        raise TraceParseError("Pico source journal header is invalid")
    repo_key = _bounded_string(record.get("repo_key"), field="repo_key", maximum=512)
    session_id = validated_session_id(_required_string(record.get("session_id"), field="session_id"), label="Pico")
    return _bounded_string(session_id, field="session_id", maximum=256), repo_key


def _metadata_event(
    record: dict[str, Any], *, source_path: Path, session_id: str, raw_event_index: int, raw_event_sha256: str
) -> TraceEvent:
    evidence = _reference(
        source_path=source_path,
        session_id=session_id,
        raw_event_index=raw_event_index,
        raw_event_sha256=raw_event_sha256,
        raw_event_type="pico_header",
        call_id=None,
    )
    return TraceEvent(
        event_id=stable_id("event", PicoImporter.provider, session_id, raw_event_index, raw_event_sha256, "header"),
        kind="metadata",
        evidence=evidence,
    )


def _batch_events(
    record: dict[str, Any], *, source_path: Path, session_id: str, raw_event_index: int, raw_event_sha256: str, state: _NormalizeState
) -> tuple[TraceEvent, ...]:
    state.pending_calls.clear()
    if (
        set(record) != {"schema", "record_type", "batch_id", "batch_ordinal", "events"}
        or record.get("schema") != PICO_SOURCE_SCHEMA
        or record.get("record_type") != "batch"
        or type(record.get("batch_ordinal")) is not int
        or record.get("batch_ordinal") != raw_event_index
        or not _valid_batch_id(record.get("batch_id"))
    ):
        raise TraceParseError("Pico source journal batch is invalid")
    source_events = record.get("events")
    if not isinstance(source_events, list) or not 1 <= len(source_events) <= MAX_PICO_BATCH_EVENTS:
        raise TraceParseError(f"Pico source journal batch must contain 1 to {MAX_PICO_BATCH_EVENTS} events")
    openings = sum(event.get("kind") == "message" and event.get("role") == "user" for event in source_events if isinstance(event, dict))
    if openings != 1:
        raise TraceParseError("Pico source journal batch must contain exactly one user task opening")
    if len(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) > MAX_PICO_BATCH_BYTES:
        raise TraceParseError(f"Pico source journal batch exceeds the {MAX_PICO_BATCH_BYTES}-byte limit")
    events: list[TraceEvent] = []
    for event_ordinal, source_event in enumerate(source_events):
        if not isinstance(source_event, dict):
            raise TraceParseError("Pico source event must be an object")
        events.append(
            _normalize_event(
                source_event,
                source_path=source_path,
                session_id=session_id,
                raw_event_index=raw_event_index,
                raw_event_sha256=raw_event_sha256,
                event_ordinal=event_ordinal,
                state=state,
            )
        )
    return tuple(events)


def _normalize_event(
    source_event: dict[str, Any],
    *,
    source_path: Path,
    session_id: str,
    raw_event_index: int,
    raw_event_sha256: str,
    event_ordinal: int,
    state: _NormalizeState,
) -> TraceEvent:
    kind = _required_string(source_event.get("kind"), field="event kind")
    call_id = _optional_string(source_event.get("call_id"), field="call_id")
    evidence = _reference(
        source_path=source_path,
        session_id=session_id,
        raw_event_index=raw_event_index,
        raw_event_sha256=raw_event_sha256,
        raw_event_type=f"pico_batch:{event_ordinal}:{kind}",
        call_id=call_id,
    )
    event_id = stable_id("event", PicoImporter.provider, session_id, raw_event_index, raw_event_sha256, event_ordinal, kind)
    if kind == "message":
        role = _optional_string(source_event.get("role"), field="role")
        if role not in {"user", "assistant", "system", "tool"}:
            raise TraceParseError(f"Unsupported Pico message role: {role!r}")
        return TraceEvent(
            event_id=event_id,
            kind="message",
            evidence=evidence,
            role=role,
            text=_optional_string(source_event.get("text"), field="text"),
        )
    if kind == "tool_call":
        tool_name = _optional_bounded_string(source_event.get("tool_name"), field="tool_name", maximum=512)
        call_id = _optional_bounded_string(call_id, field="call_id", maximum=512)
        command = _optional_bounded_string(source_event.get("command"), field="command", maximum=_MAX_ATTRIBUTE_BYTES)
        if call_id is not None:
            if call_id in state.seen_call_ids:
                raise TraceParseError(f"Duplicate Pico call ID: {call_id}")
            state.seen_call_ids.add(call_id)
            if tool_name is not None:
                state.pending_calls[call_id] = _PendingCall(tool_name=tool_name, command=command)
        return TraceEvent(
            event_id=event_id,
            kind="tool_call",
            evidence=evidence,
            text=_arguments_text(source_event.get("arguments")),
            tool_name=tool_name,
            call_id=call_id,
            command=command,
        )
    if kind == "tool_result":
        call_id = _optional_bounded_string(call_id, field="call_id", maximum=512)
        pending = state.pending_calls.pop(call_id, None) if call_id is not None else None
        terminal = source_event.get("terminal_observation")
        if terminal is not None and not isinstance(terminal, dict):
            raise TraceParseError("Pico terminal_observation must be an object")
        observed_exit_code = _exit_code(terminal.get("exit_code")) if isinstance(terminal, dict) else None
        tool_status = _optional_bounded_string(source_event.get("status"), field="status", maximum=64)
        observed_file_changes = (
            _file_changes(
                terminal.get("file_changes"),
                event_id=event_id,
                evidence=evidence,
                remaining=MAX_PICO_SESSION_FILE_CHANGES - state.file_change_fact_count,
            )
            if isinstance(terminal, dict)
            else ()
        )
        exit_code = observed_exit_code if pending is not None else None
        file_changes = observed_file_changes if pending is not None else ()
        state.file_change_fact_count += len(file_changes)
        return TraceEvent(
            event_id=event_id,
            kind="tool_result",
            evidence=evidence,
            text=_optional_string(source_event.get("text"), field="text"),
            tool_name=pending.tool_name if pending is not None else None,
            call_id=call_id,
            command=pending.command if pending is not None else None,
            exit_code=exit_code,
            tool_status=tool_status,
            file_changes=file_changes,
            is_command_result=pending is not None and pending.command is not None,
            observed_outcome=_observed_outcome(exit_code=exit_code, status=tool_status) if pending is not None else None,
        )
    return TraceEvent(event_id=event_id, kind="unknown", evidence=evidence)


def _reference(
    *, source_path: Path, session_id: str, raw_event_index: int, raw_event_sha256: str, raw_event_type: str, call_id: str | None
) -> TraceReference:
    return TraceReference(
        provider=PicoImporter.provider,
        session_id=session_id,
        source_path=str(source_path),
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        raw_event_type=raw_event_type,
        call_id=call_id,
    )


def _required_string(value: object, *, field: str) -> str:
    result = _optional_string(value, field=field)
    if result is None:
        raise TraceParseError(f"Pico {field} must be a non-empty string")
    return result


def _optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, field=field, maximum=_MAX_TEXT_CHARS)


def _arguments_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        result = value
    else:
        try:
            result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise TraceParseError("Pico tool arguments are not canonical JSON") from exc
    if len(result.encode()) > _MAX_ARGUMENT_BYTES:
        raise TraceParseError(f"Pico tool arguments exceed the {_MAX_ARGUMENT_BYTES}-byte limit")
    return result


def _exit_code(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not -(2**31) <= value <= 2**31 - 1:
        raise TraceParseError("Pico terminal exit_code is invalid")
    return value


def _observed_outcome(*, exit_code: int | None, status: str | None) -> TraceEpisodeOutcome:
    if exit_code is not None:
        return "success" if exit_code == 0 else "failure"
    if status in {"success", "completed"}:
        return "success"
    if status in {"failure", "failed", "error"}:
        return "failure"
    return "unknown"


def _bounded_string(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise TraceParseError(f"Pico {field} must contain 1 to {maximum} UTF-8 bytes")
    if value != normalize_text(value):
        raise TraceParseError(f"Pico {field} must use NFC text and LF newlines")
    return value


def _optional_bounded_string(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, field=field, maximum=maximum)


def _valid_batch_id(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value.startswith("batch_")
        and len(value) == 70
        and all(character in "0123456789abcdef" for character in value[6:])
    )


def _file_changes(value: object, *, event_id: str, evidence: TraceReference, remaining: int) -> tuple[FileChangeFact, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > remaining:
        raise TraceParseError("Pico terminal file changes exceed the session limit")
    changes: list[FileChangeFact] = []
    for ordinal, raw in enumerate(value):
        if not isinstance(raw, dict) or set(raw) - {"operation", "path", "destination_path"}:
            raise TraceParseError("Pico terminal file change is invalid")
        operation = raw.get("operation")
        if operation not in {"add", "update", "delete", "move"}:
            raise TraceParseError("Pico terminal file change operation is invalid")
        path = _path(raw.get("path"), field="file change path")
        destination = raw.get("destination_path")
        if destination is not None:
            destination = _path(destination, field="file change destination")
        if operation == "move" and destination is None:
            raise TraceParseError("Pico move file change requires destination_path")
        if operation != "move" and destination is not None:
            raise TraceParseError("Only Pico move file changes may have destination_path")
        changes.append(
            FileChangeFact(
                fact_id=stable_id("file_change", event_id, ordinal, operation, path, destination),
                operation=operation,
                path=path,
                destination_path=destination,
                evidence=evidence,
            )
        )
    return tuple(changes)


def _path(value: object, *, field: str) -> str:
    path = _bounded_string(value, field=field, maximum=_MAX_PATH_CHARS)
    try:
        if path != normalize_path_key(path):
            raise TraceParseError(f"Pico {field} must be repository-relative and normalized")
    except SchemaInvalid as exc:
        raise TraceParseError(f"Pico {field} must be repository-relative and normalized") from exc
    return path
