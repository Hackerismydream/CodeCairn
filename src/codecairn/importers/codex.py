from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from codecairn.importers.jsonl import JsonlScan, RawRecord, read_jsonl
from codecairn.memory.errors import TraceParseError
from codecairn.memory.models import (
    AgentTrace,
    EvidenceReference,
    FileChangeFact,
    FileChangeOperation,
    ImportCheckpoint,
    TraceEvent,
)
from codecairn.memory.trace import stable_id

_MAX_SESSION_BYTES = 64 * 1024 * 1024
_MAX_RAW_EVENTS = 100_000
_MAX_SESSION_ID_CHARS = 512
_MAX_PATCH_FILE_CHANGE_FACTS = 4_096
_MAX_SESSION_FILE_CHANGE_FACTS = 10_000
_MAX_PATCH_PATH_CHARS = 4_096
_MAX_PATCH_CHARS = 4 * 1024 * 1024
_MAX_PATCH_LINES = 100_000
_COMMAND_TOOLS = frozenset({"exec_command"})
_COMMAND_RESULT_TOOLS = frozenset({"exec_command", "write_stdin"})
_MIN_EXIT_CODE = -(2**31)
_MAX_EXIT_CODE = 2**31 - 1


@dataclass(frozen=True, slots=True)
class _PendingCall:
    event: TraceEvent
    expected_output_type: str


@dataclass(slots=True)
class _NormalizeState:
    pending_calls: dict[str, _PendingCall] = field(default_factory=dict)
    seen_call_ids: set[str] = field(default_factory=set)
    file_change_fact_count: int = 0


class CodexImporter:
    provider = "codex"

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
        return self._from_scan(scan, checkpoint=checkpoint)

    def _from_scan(
        self,
        scan: JsonlScan,
        *,
        checkpoint: ImportCheckpoint | None,
    ) -> AgentTrace:
        if checkpoint is not None:
            _validate_checkpoint(checkpoint)
        resumed_from = checkpoint.resume_raw_event_index if checkpoint is not None else 0
        if checkpoint is not None and scan.prefix_sha256 != checkpoint.resume_prefix_sha256:
            raise TraceParseError(
                f"Codex source changed before committed checkpoint: {scan.source_path}"
            )
        if (
            checkpoint is not None
            and scan.raw_event_count - 1 < checkpoint.committed_raw_event_index
        ):
            raise TraceParseError(
                f"Codex source is truncated before committed cursor: {scan.source_path}"
            )
        session_id = (
            _validated_session_id(checkpoint.session_id)
            if checkpoint is not None
            else _session_id(scan.records, fallback=scan.source_path.stem)
        )
        raw_prefix_call_ids = checkpoint.resume_call_ids if checkpoint is not None else ()
        raw_prefix_file_change_fact_count = (
            checkpoint.resume_file_change_fact_count if checkpoint is not None else 0
        )
        state = _NormalizeState(
            seen_call_ids=set(raw_prefix_call_ids),
            file_change_fact_count=raw_prefix_file_change_fact_count,
        )
        events = tuple(
            _normalize(
                raw_event=record,
                raw_event_sha256=raw_event_sha256,
                raw_event_index=index,
                source_path=scan.source_path,
                session_id=session_id,
                state=state,
            )
            for index, (record, raw_event_sha256) in enumerate(
                scan.records,
                start=resumed_from,
            )
        )
        return AgentTrace(
            trace_id=stable_id("trace", self.provider, session_id),
            provider=self.provider,
            session_id=session_id,
            source_path=str(scan.source_path),
            source_sha256=scan.source_sha256,
            raw_event_count=scan.raw_event_count,
            resumed_from_raw_event_index=resumed_from,
            raw_prefix_sha256=scan.prefix_sha256,
            raw_prefix_call_ids=raw_prefix_call_ids,
            raw_prefix_file_change_fact_count=raw_prefix_file_change_fact_count,
            raw_suffix_event_sha256s=tuple(item[1] for item in scan.records),
            events=events,
        )


def _session_id(records: tuple[RawRecord, ...], *, fallback: str) -> str:
    if records:
        record, _raw_event_sha256 = records[0]
        if record.get("type") != "session_meta":
            return _validated_session_id(fallback)
        payload = record.get("payload")
        if isinstance(payload, dict):
            value = payload.get("id")
            if isinstance(value, str):
                return _validated_session_id(value)
    return _validated_session_id(fallback)


def _validated_session_id(value: str) -> str:
    if not value or len(value) > _MAX_SESSION_ID_CHARS:
        raise TraceParseError(
            f"Codex session id must contain 1 to {_MAX_SESSION_ID_CHARS} characters"
        )
    if _contains_unsafe_text_character(value):
        raise TraceParseError("Codex session id contains an unsafe control or line separator")
    return value


def _validate_checkpoint(checkpoint: ImportCheckpoint) -> None:
    if checkpoint.provider != CodexImporter.provider:
        raise TraceParseError("Codex checkpoint provider does not match the importer")
    if checkpoint.committed_raw_event_index < -1:
        raise TraceParseError("Codex committed raw-event index is invalid")
    if not 0 <= checkpoint.resume_raw_event_index <= checkpoint.committed_raw_event_index + 1:
        raise TraceParseError("Codex resume checkpoint is outside the committed cursor")
    if not 0 <= checkpoint.resume_file_change_fact_count <= _MAX_SESSION_FILE_CHANGE_FACTS:
        raise TraceParseError("Codex checkpoint file-change count is outside the import limit")
    if len(checkpoint.resume_call_ids) != len(set(checkpoint.resume_call_ids)):
        raise TraceParseError("Codex checkpoint contains duplicate call IDs")


def _normalize(
    *,
    raw_event: dict[str, Any],
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
    state: _NormalizeState,
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
        _register_call(
            event,
            expected_output_type="function_call_output",
            raw_event_index=raw_event_index,
            state=state,
        )
        return event
    if payload_type == "custom_tool_call":
        tool_name = _string(payload.get("name"))
        tool_input = _string(payload.get("input"))
        event = TraceEvent(
            event_id=event_id,
            kind="tool_call",
            evidence=evidence,
            text=tool_input,
            tool_name=tool_name,
            call_id=call_id,
            tool_status=_string(payload.get("status")),
        )
        if tool_name == "apply_patch" and tool_input is not None:
            event = replace(
                event,
                file_changes=_parse_apply_patch(
                    tool_input,
                    event_id=event_id,
                    evidence=evidence,
                    remaining_session_facts=(
                        _MAX_SESSION_FILE_CHANGE_FACTS - state.file_change_fact_count
                    ),
                ),
            )
            state.file_change_fact_count += len(event.file_changes)
        _register_call(
            event,
            expected_output_type="custom_tool_call_output",
            raw_event_index=raw_event_index,
            state=state,
        )
        return event
    if payload_type == "function_call_output":
        raw_output = payload.get("output")
        output = _output_text(raw_output)
        paired_call = _take_call(
            call_id,
            output_type="function_call_output",
            pending_calls=state.pending_calls,
        )
        return TraceEvent(
            event_id=event_id,
            kind="tool_result",
            evidence=evidence,
            text=output,
            tool_name=paired_call.tool_name if paired_call is not None else None,
            call_id=call_id,
            command=paired_call.command if paired_call is not None else None,
            exit_code=_exit_code(raw_output),
            is_command_result=(
                paired_call is not None and paired_call.tool_name in _COMMAND_RESULT_TOOLS
            ),
        )
    if payload_type == "custom_tool_call_output":
        output = _output_text(payload.get("output"))
        paired_call = _take_call(
            call_id,
            output_type="custom_tool_call_output",
            pending_calls=state.pending_calls,
        )
        return TraceEvent(
            event_id=event_id,
            kind="tool_result",
            evidence=evidence,
            text=output,
            tool_name=paired_call.tool_name if paired_call is not None else None,
            call_id=call_id,
        )
    return TraceEvent(event_id=event_id, kind="unknown", evidence=evidence)


def _register_call(
    event: TraceEvent,
    *,
    expected_output_type: str,
    raw_event_index: int,
    state: _NormalizeState,
) -> None:
    if event.call_id is None:
        return
    if event.call_id in state.seen_call_ids:
        raise TraceParseError(
            f"Duplicate Codex call_id {event.call_id!r} at raw event {raw_event_index}"
        )
    state.seen_call_ids.add(event.call_id)
    state.pending_calls[event.call_id] = _PendingCall(
        event=event,
        expected_output_type=expected_output_type,
    )


def _take_call(
    call_id: str | None,
    *,
    output_type: str,
    pending_calls: dict[str, _PendingCall],
) -> TraceEvent | None:
    if call_id is None:
        return None
    pending = pending_calls.get(call_id)
    if pending is None or pending.expected_output_type != output_type:
        return None
    pending_calls.pop(call_id)
    return pending.event


_PATCH_FILE = re.compile(r"^\*\*\* (?P<operation>Add|Update|Delete) File: (?P<path>.+)$")
_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (?P<path>.+)$")
_PATCH_OPERATIONS: dict[str, FileChangeOperation] = {
    "Add": "add",
    "Update": "update",
    "Delete": "delete",
}


def _parse_apply_patch(
    patch: str,
    *,
    event_id: str,
    evidence: EvidenceReference,
    remaining_session_facts: int,
) -> tuple[FileChangeFact, ...]:
    if len(patch) > _MAX_PATCH_CHARS:
        raise TraceParseError(
            f"apply_patch input exceeds the {_MAX_PATCH_CHARS}-character import limit"
        )
    lines = io.StringIO(patch)
    first_line = lines.readline()
    if _patch_line(first_line) != "*** Begin Patch":
        return ()
    changes: list[FileChangeFact] = []
    previous_line: str | None = None
    line_count = 1
    for raw_line in lines:
        line_count += 1
        if line_count > _MAX_PATCH_LINES:
            raise TraceParseError(f"apply_patch exceeds the {_MAX_PATCH_LINES}-line import limit")
        if previous_line is not None:
            _record_patch_line(
                previous_line,
                changes=changes,
                event_id=event_id,
                evidence=evidence,
                remaining_session_facts=remaining_session_facts,
            )
        previous_line = _patch_line(raw_line)
    if previous_line != "*** End Patch":
        return ()
    return tuple(changes)


def _record_patch_line(
    line: str,
    *,
    changes: list[FileChangeFact],
    event_id: str,
    evidence: EvidenceReference,
    remaining_session_facts: int,
) -> None:
    file_match = _PATCH_FILE.fullmatch(line)
    if file_match is not None:
        _check_file_change_budget(
            patch_count=len(changes),
            remaining_session_facts=remaining_session_facts,
        )
        operation = _PATCH_OPERATIONS[file_match.group("operation")]
        path = _patch_path(file_match.group("path"))
        changes.append(
            FileChangeFact(
                fact_id=stable_id("fact", event_id, len(changes), operation, path),
                operation=operation,
                path=path,
                destination_path=None,
                evidence=evidence,
            )
        )
        return
    move_match = _PATCH_MOVE.fullmatch(line)
    if move_match is not None and changes and changes[-1].operation == "update":
        destination = _patch_path(move_match.group("path"))
        current = changes[-1]
        changes[-1] = replace(
            current,
            fact_id=stable_id(
                "fact",
                event_id,
                len(changes) - 1,
                "move",
                current.path,
                destination,
            ),
            operation="move",
            destination_path=destination,
        )


def _patch_line(raw_line: str) -> str:
    return raw_line.removesuffix("\n").removesuffix("\r")


def _check_file_change_budget(*, patch_count: int, remaining_session_facts: int) -> None:
    if patch_count >= _MAX_PATCH_FILE_CHANGE_FACTS:
        raise TraceParseError(
            f"apply_patch exceeds the {_MAX_PATCH_FILE_CHANGE_FACTS}-fact per-patch import limit"
        )
    if patch_count >= remaining_session_facts:
        raise TraceParseError(
            f"Codex session exceeds the {_MAX_SESSION_FILE_CHANGE_FACTS}-fact import limit"
        )


def _patch_path(value: str) -> str:
    if len(value) > _MAX_PATCH_PATH_CHARS:
        raise TraceParseError(
            f"apply_patch path evidence exceeds the {_MAX_PATCH_PATH_CHARS}-character import limit"
        )
    if not value or _contains_unsafe_text_character(value):
        raise TraceParseError(f"Invalid apply_patch path evidence: {value!r}")
    return value


def _contains_unsafe_text_character(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value)


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
