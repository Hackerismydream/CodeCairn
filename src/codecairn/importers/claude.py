from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
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
_MAX_SESSION_FILE_CHANGE_FACTS = 10_000
_MAX_PATH_CHARS = 4_096
_MIN_EXIT_CODE = -(2**31)
_MAX_EXIT_CODE = 2**31 - 1
_COMMAND_TOOLS = frozenset({"Bash"})
_FILE_TOOLS = frozenset({"Edit", "MultiEdit", "Write"})
_EXIT_CODE = re.compile(r"^Exit code (?P<code>-?\d+)[ \t]*$", flags=re.MULTILINE)


@dataclass(frozen=True, slots=True)
class _PendingCall:
    event: TraceEvent
    tool_input: dict[str, Any]


@dataclass(slots=True)
class _NormalizeState:
    pending_calls: dict[str, _PendingCall] = field(default_factory=dict)
    seen_call_ids: set[str] = field(default_factory=set)
    file_change_fact_count: int = 0


class ClaudeImporter:
    provider = "claude"

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
                f"Claude source changed before committed checkpoint: {scan.source_path}"
            )
        if (
            checkpoint is not None
            and scan.raw_event_count - 1 < checkpoint.committed_raw_event_index
        ):
            raise TraceParseError(
                f"Claude source is truncated before committed cursor: {scan.source_path}"
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
        events: list[TraceEvent] = []
        for index, (record, raw_event_sha256) in enumerate(
            scan.records,
            start=resumed_from,
        ):
            events.extend(
                _normalize_record(
                    raw_event=record,
                    raw_event_sha256=raw_event_sha256,
                    raw_event_index=index,
                    source_path=scan.source_path,
                    session_id=session_id,
                    state=state,
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
            events=tuple(events),
        )


def _session_id(records: tuple[RawRecord, ...], *, fallback: str) -> str:
    for record, _raw_event_sha256 in records:
        value = record.get("sessionId")
        if isinstance(value, str):
            return _validated_session_id(value)
    return _validated_session_id(fallback)


def _validated_session_id(value: str) -> str:
    if not value or len(value) > _MAX_SESSION_ID_CHARS:
        raise TraceParseError(
            f"Claude session id must contain 1 to {_MAX_SESSION_ID_CHARS} characters"
        )
    if _contains_unsafe_text_character(value):
        raise TraceParseError("Claude session id contains an unsafe control or line separator")
    return value


def _validate_checkpoint(checkpoint: ImportCheckpoint) -> None:
    if checkpoint.provider != ClaudeImporter.provider:
        raise TraceParseError("Claude checkpoint provider does not match the importer")
    if checkpoint.committed_raw_event_index < -1:
        raise TraceParseError("Claude committed raw-event index is invalid")
    if not 0 <= checkpoint.resume_raw_event_index <= checkpoint.committed_raw_event_index + 1:
        raise TraceParseError("Claude resume checkpoint is outside the committed cursor")
    if not 0 <= checkpoint.resume_file_change_fact_count <= _MAX_SESSION_FILE_CHANGE_FACTS:
        raise TraceParseError("Claude checkpoint file-change count is outside the import limit")
    if len(checkpoint.resume_call_ids) != len(set(checkpoint.resume_call_ids)):
        raise TraceParseError("Claude checkpoint contains duplicate call IDs")


def _normalize_record(
    *,
    raw_event: dict[str, Any],
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
    state: _NormalizeState,
) -> tuple[TraceEvent, ...]:
    raw_event_type = _string(raw_event.get("type")) or "unknown"
    message = raw_event.get("message")
    if not isinstance(message, dict):
        return (
            _unknown_event(
                raw_event_type=raw_event_type,
                raw_event_sha256=raw_event_sha256,
                raw_event_index=raw_event_index,
                source_path=source_path,
                session_id=session_id,
            ),
        )
    role = _string(message.get("role"))
    content = message.get("content")
    if isinstance(content, str):
        return (
            _message_event(
                text=content,
                role=role,
                block_index=0,
                raw_event_type=raw_event_type,
                raw_event_sha256=raw_event_sha256,
                raw_event_index=raw_event_index,
                source_path=source_path,
                session_id=session_id,
            ),
        )
    if not isinstance(content, list):
        return ()

    events: list[TraceEvent] = []
    for block_index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        block_type = _string(block.get("type"))
        if block_type == "text":
            text = _string(block.get("text"))
            if text is not None:
                events.append(
                    _message_event(
                        text=text,
                        role=role,
                        block_index=block_index,
                        raw_event_type=raw_event_type,
                        raw_event_sha256=raw_event_sha256,
                        raw_event_index=raw_event_index,
                        source_path=source_path,
                        session_id=session_id,
                    )
                )
        elif block_type == "tool_use":
            events.append(
                _tool_call_event(
                    block=block,
                    block_index=block_index,
                    raw_event_type=raw_event_type,
                    raw_event_sha256=raw_event_sha256,
                    raw_event_index=raw_event_index,
                    source_path=source_path,
                    session_id=session_id,
                    state=state,
                )
            )
        elif block_type == "tool_result":
            events.append(
                _tool_result_event(
                    block=block,
                    tool_use_result=raw_event.get("toolUseResult"),
                    block_index=block_index,
                    raw_event_type=raw_event_type,
                    raw_event_sha256=raw_event_sha256,
                    raw_event_index=raw_event_index,
                    source_path=source_path,
                    session_id=session_id,
                    state=state,
                )
            )
    return tuple(events)


def _message_event(
    *,
    text: str,
    role: str | None,
    block_index: int,
    raw_event_type: str,
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
) -> TraceEvent:
    evidence = _evidence(
        call_id=None,
        raw_event_type=raw_event_type,
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        source_path=source_path,
        session_id=session_id,
    )
    return TraceEvent(
        event_id=_event_id(
            session_id,
            raw_event_index,
            raw_event_sha256,
            raw_event_type,
            block_index,
            "message",
        ),
        kind="message",
        evidence=evidence,
        role=role,
        text=text,
    )


def _tool_call_event(
    *,
    block: dict[str, Any],
    block_index: int,
    raw_event_type: str,
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
    state: _NormalizeState,
) -> TraceEvent:
    call_id = _string(block.get("id"))
    tool_name = _string(block.get("name"))
    raw_input = block.get("input")
    tool_input = raw_input if isinstance(raw_input, dict) else {}
    command_value = tool_input.get("command")
    command = (
        command_value if tool_name in _COMMAND_TOOLS and isinstance(command_value, str) else None
    )
    evidence = _evidence(
        call_id=call_id,
        raw_event_type=raw_event_type,
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        source_path=source_path,
        session_id=session_id,
    )
    event = TraceEvent(
        event_id=_event_id(
            session_id,
            raw_event_index,
            raw_event_sha256,
            raw_event_type,
            block_index,
            "tool_call",
        ),
        kind="tool_call",
        evidence=evidence,
        tool_name=tool_name,
        call_id=call_id,
        command=command,
    )
    if call_id is not None:
        if call_id in state.seen_call_ids:
            raise TraceParseError(
                f"Duplicate Claude call_id {call_id!r} at raw event {raw_event_index}"
            )
        state.seen_call_ids.add(call_id)
        state.pending_calls[call_id] = _PendingCall(event=event, tool_input=tool_input)
    return event


def _tool_result_event(
    *,
    block: dict[str, Any],
    tool_use_result: object,
    block_index: int,
    raw_event_type: str,
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
    state: _NormalizeState,
) -> TraceEvent:
    call_id = _string(block.get("tool_use_id"))
    pending = state.pending_calls.pop(call_id, None) if call_id is not None else None
    evidence = _evidence(
        call_id=call_id,
        raw_event_type=raw_event_type,
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        source_path=source_path,
        session_id=session_id,
    )
    event_id = _event_id(
        session_id,
        raw_event_index,
        raw_event_sha256,
        raw_event_type,
        block_index,
        "tool_result",
    )
    content = _result_text(block.get("content"))
    is_error = block.get("is_error") is True
    paired_call = pending.event if pending is not None else None
    is_command_result = paired_call is not None and paired_call.tool_name in _COMMAND_TOOLS
    file_changes = _file_changes(
        event_id=event_id,
        evidence=evidence,
        pending=pending,
        tool_use_result=tool_use_result,
        is_error=is_error,
        state=state,
    )
    return TraceEvent(
        event_id=event_id,
        kind="tool_result",
        evidence=evidence,
        text=content,
        tool_name=paired_call.tool_name if paired_call is not None else None,
        call_id=call_id,
        command=paired_call.command if paired_call is not None else None,
        exit_code=_exit_code(content, is_error=is_error) if is_command_result else None,
        file_changes=file_changes,
        is_command_result=is_command_result,
    )


def _file_changes(
    *,
    event_id: str,
    evidence: EvidenceReference,
    pending: _PendingCall | None,
    tool_use_result: object,
    is_error: bool,
    state: _NormalizeState,
) -> tuple[FileChangeFact, ...]:
    if pending is None or pending.event.tool_name not in _FILE_TOOLS or is_error:
        return ()
    structured = tool_use_result if isinstance(tool_use_result, dict) else {}
    path_value = structured.get("filePath", pending.tool_input.get("file_path"))
    if not isinstance(path_value, str):
        return ()
    path = _validated_path(path_value)
    result_type = structured.get("type")
    if result_type == "create" or (
        pending.event.tool_name == "Write" and structured.get("originalFile") is None
    ):
        operation: FileChangeOperation = "add"
    elif result_type == "delete":
        operation = "delete"
    else:
        operation = "update"
    if state.file_change_fact_count >= _MAX_SESSION_FILE_CHANGE_FACTS:
        raise TraceParseError(
            f"Claude session exceeds the {_MAX_SESSION_FILE_CHANGE_FACTS}-fact import limit"
        )
    state.file_change_fact_count += 1
    return (
        FileChangeFact(
            fact_id=stable_id("fact", event_id, operation, path),
            operation=operation,
            path=path,
            destination_path=None,
            evidence=evidence,
        ),
    )


def _unknown_event(
    *,
    raw_event_type: str,
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
) -> TraceEvent:
    evidence = _evidence(
        call_id=None,
        raw_event_type=raw_event_type,
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        source_path=source_path,
        session_id=session_id,
    )
    return TraceEvent(
        event_id=_event_id(
            session_id,
            raw_event_index,
            raw_event_sha256,
            raw_event_type,
            0,
            "unknown",
        ),
        kind="unknown",
        evidence=evidence,
    )


def _evidence(
    *,
    call_id: str | None,
    raw_event_type: str,
    raw_event_sha256: str,
    raw_event_index: int,
    source_path: Path,
    session_id: str,
) -> EvidenceReference:
    return EvidenceReference(
        provider=ClaudeImporter.provider,
        session_id=session_id,
        source_path=str(source_path),
        raw_event_sha256=raw_event_sha256,
        raw_event_index=raw_event_index,
        raw_event_type=raw_event_type,
        call_id=call_id,
    )


def _event_id(
    session_id: str,
    raw_event_index: int,
    raw_event_sha256: str,
    raw_event_type: str,
    block_index: int,
    normalized_type: str,
) -> str:
    return stable_id(
        "event",
        ClaudeImporter.provider,
        session_id,
        raw_event_index,
        raw_event_sha256,
        raw_event_type,
        block_index,
        normalized_type,
    )


def _exit_code(content: str | None, *, is_error: bool) -> int:
    if content is not None:
        match = _EXIT_CODE.search(content)
        if match is not None:
            value = int(match.group("code"))
            if not _MIN_EXIT_CODE <= value <= _MAX_EXIT_CODE:
                raise TraceParseError(f"Claude exit code is outside signed 32-bit range: {value}")
            return value
    return 1 if is_error else 0


def _result_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    texts = [
        item["text"]
        for item in value
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    return "\n".join(texts) if texts else json.dumps(value, sort_keys=True)


def _validated_path(value: str) -> str:
    if not value or len(value) > _MAX_PATH_CHARS or _contains_unsafe_text_character(value):
        raise TraceParseError(f"Invalid Claude file-change path evidence: {value!r}")
    return value


def _contains_unsafe_text_character(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None
