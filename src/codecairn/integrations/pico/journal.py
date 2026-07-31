from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from codecairn.importers.jsonl import MAX_RAW_EVENTS, MAX_SESSION_BYTES
from codecairn.importers.pico import MAX_PICO_BATCH_BYTES, MAX_PICO_BATCH_EVENTS, MAX_PICO_SESSION_FILE_CHANGES, PICO_SOURCE_SCHEMA
from codecairn.memory.errors import SourceRewritten
from codecairn.memory.models import ImportCheckpoint
from codecairn.memory.schema import SchemaInvalid, canonical_json, normalize_path_key, normalize_text
from codecairn.memory.trace import EMPTY_RAW_PREFIX_SHA256, extend_raw_prefix_sha256

_T = TypeVar("_T", covariant=True)

_MAX_IDENTITY_CHARS = 512
_MAX_TEXT_CHARS = 32_768
_MAX_ARGUMENT_BYTES = 256 * 1024
_MAX_UNTRUSTED_BYTES = 256 * 1024
_MAX_FILE_CHANGES = 4_096
_MAX_PATH_CHARS = 4_096
_MAX_CALL_ID_CHARS = 512
_MAX_TOOL_NAME_CHARS = 512
_MAX_ATTRIBUTE_BYTES = 4_096
_BATCH_ID_PREFIX = "batch_"


class PicoJournalError(ValueError):
    """A Pico source batch cannot be committed without weakening durability."""

    code = "pico_journal_invalid"


class PicoJournalImporter(Protocol[_T]):
    def import_checkpoint(self, source_path: Path, *, repo_key: str) -> ImportCheckpoint | None: ...

    def import_session(
        self, source_path: Path, *, repo_key: str, source_root: Path | None = None, boundary_kind: str | None = None
    ) -> _T: ...


class PicoSourceJournal:
    """Commit bounded Pico after-Turn batches behind one recovery interface."""

    def __init__(self, runtime_root: Path, *, repo_key: str, session_id: str, source_generation: int = 1) -> None:
        self._runtime_root = Path(os.path.abspath(runtime_root)).resolve()
        self.repo_key = _identity(repo_key, field="repo_key", maximum=_MAX_IDENTITY_CHARS)
        self.session_id = _identity(session_id, field="session_id", maximum=256)
        if type(source_generation) is not int or source_generation != 1:
            raise PicoJournalError("source_generation must be 1")
        self.source_generation = source_generation
        namespace_hash = hashlib.sha256(self.repo_key.encode()).hexdigest()
        session_hash = hashlib.sha256(self.session_id.encode()).hexdigest()
        self.path = self._runtime_root / "sources" / "pico" / namespace_hash / f"{session_hash}.jsonl"
        self.staged_path = self.path.with_name(f".{session_hash}.stage.jsonl")
        self._lock_name = f".{session_hash}.lock"

    def commit(
        self, events: Sequence[Mapping[str, object]], *, importer: PicoJournalImporter[_T], idempotency_key: str | None = None
    ) -> _T:
        normalized_events = _canonical_events(events)
        stable_batch_id = None
        if idempotency_key is not None:
            normalized_key = _identity(idempotency_key, field="idempotency_key", maximum=_MAX_IDENTITY_CHARS)
            if normalized_key != self.session_id:
                raise PicoJournalError("Pico stable batch key must match the journal session")
            stable_batch_id = f"{_BATCH_ID_PREFIX}{hashlib.sha256(normalized_key.encode()).hexdigest()}"
        with self._locked_directory() as directory:
            if _exists(directory, self.staged_path.name):
                if stable_batch_id is not None:
                    expected = self._batch_line(normalized_events, batch_id=stable_batch_id, batch_ordinal=1)
                    staged = _read_regular(directory, self.staged_path.name, maximum=MAX_PICO_BATCH_BYTES)
                    if staged != expected:
                        raise PicoJournalError("Pico stable batch identity conflicts with staged bytes")
                    return self._recover_locked(importer, directory)
                self._recover_locked(importer, directory)
            state = self._read_journal(directory)
            checkpoint = importer.import_checkpoint(self.path, repo_key=self.repo_key)
            self._verify_committed_prefix(state, checkpoint)
            if not state.records:
                _create_atomic(directory, self.path.name, self._header_line())
                state = self._read_journal(directory)
            if state.trailing:
                raise PicoJournalError("Pico journal has an unterminated record without a staged batch")
            self._validate_header(state.records[0])
            if stable_batch_id is not None:
                expected = self._batch_line(normalized_events, batch_id=stable_batch_id, batch_ordinal=1)
                if len(state.records) == 2 and state.records[1] == expected:
                    return self._import_committed(importer, directory)
                if len(state.records) != 1:
                    raise PicoJournalError("Pico stable batch identity conflicts with committed bytes")
            committed_changes = checkpoint.resume_file_change_fact_count if checkpoint is not None else 0
            if committed_changes + _file_change_count(normalized_events) > MAX_PICO_SESSION_FILE_CHANGES:
                raise PicoJournalError("Pico session file changes exceed the import limit")
            if len(state.records) >= MAX_RAW_EVENTS:
                raise PicoJournalError("Pico journal exceeds the import event limit")
            batch_id = stable_batch_id or f"{_BATCH_ID_PREFIX}{secrets.token_hex(32)}"
            staged = self._batch_line(normalized_events, batch_id=batch_id, batch_ordinal=max(1, len(state.records)))
            if len(staged) > MAX_PICO_BATCH_BYTES:
                raise PicoJournalError(f"Pico batch exceeds the {MAX_PICO_BATCH_BYTES}-byte limit")
            if state.complete_bytes + len(staged) > MAX_SESSION_BYTES:
                raise PicoJournalError("Pico journal exceeds the import byte limit")
            _create_atomic(directory, self.staged_path.name, staged)
            return self._recover_locked(importer, directory)

    @classmethod
    def recover_pending(cls, runtime_root: Path, *, repo_key: str, importer: PicoJournalImporter[Any]) -> int:
        """Recover every staged journal bound to one repository namespace."""
        root = Path(os.path.abspath(runtime_root)).resolve()
        normalized_key = _identity(repo_key, field="repo_key", maximum=_MAX_IDENTITY_CHARS)
        namespace_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
        directory = _open_directory(root, ("sources", "pico", namespace_hash))
        pending: list[tuple[str, str]] = []
        try:
            names = set(os.listdir(directory))
            for staged_name in sorted(name for name in names if name.startswith(".") and name.endswith(".stage.jsonl")):
                journal_name = staged_name[1 : -len(".stage.jsonl")] + ".jsonl"
                if journal_name not in names:
                    raise PicoJournalError("Pico staged batch has no bound journal header")
                source = _read_regular(directory, journal_name, maximum=MAX_SESSION_BYTES)
                header = source.splitlines(keepends=True)[0] if source else b""
                record = _decode_canonical_line(header, label="Pico journal header")
                raw_session_id = record.get("session_id")
                if not isinstance(raw_session_id, str):
                    raise PicoJournalError("Pico journal header session identity is invalid")
                session_id = _identity(raw_session_id, field="session_id", maximum=256)
                journal = cls(root, repo_key=normalized_key, session_id=session_id)
                if journal.path.name != journal_name or journal.staged_path.name != staged_name:
                    raise PicoJournalError("Pico staged batch path does not match its journal identity")
                journal._validate_header(header)
                pending.append((session_id, staged_name))
        finally:
            os.close(directory)
        for session_id, _staged_name in pending:
            cls(root, repo_key=normalized_key, session_id=session_id).recover(importer=importer)
        return len(pending)

    def recover(self, *, importer: PicoJournalImporter[_T]) -> _T | None:
        with self._locked_directory() as directory:
            if not _exists(directory, self.staged_path.name):
                return None
            return self._recover_locked(importer, directory)

    def _recover_locked(self, importer: PicoJournalImporter[_T], directory: int) -> _T:
        staged = _read_regular(directory, self.staged_path.name, maximum=MAX_PICO_BATCH_BYTES)
        staged_record = _decode_canonical_line(staged, label="staged Pico batch")
        _validate_batch_record(staged_record)
        state = self._read_journal(directory)
        checkpoint = importer.import_checkpoint(self.path, repo_key=self.repo_key)
        self._verify_committed_prefix(state, checkpoint)
        if not state.records:
            _create_atomic(directory, self.path.name, self._header_line())
            state = self._read_journal(directory)
        self._validate_header(state.records[0])
        self._install_staged_batch(directory, state, staged=staged, staged_record=staged_record, checkpoint=checkpoint)
        result = self._import_committed(importer, directory)
        os.unlink(self.staged_path.name, dir_fd=directory)
        os.fsync(directory)
        return result

    def _import_committed(self, importer: PicoJournalImporter[_T], directory: int) -> _T:
        result = importer.import_session(
            self.path, repo_key=self.repo_key, source_root=self._runtime_root, boundary_kind="pico_turn_end"
        )
        committed = importer.import_checkpoint(self.path, repo_key=self.repo_key)
        current = self._read_journal(directory)
        if committed is None or committed.committed_raw_event_index != len(current.records) - 1 or current.trailing:
            raise PicoJournalError("Pico import did not commit the complete journal")
        return result

    @staticmethod
    def _batch_line(events: list[dict[str, object]], *, batch_id: str, batch_ordinal: int) -> bytes:
        return _canonical_line(
            {
                "batch_id": batch_id,
                "batch_ordinal": batch_ordinal,
                "events": events,
                "record_type": "batch",
                "schema": PICO_SOURCE_SCHEMA,
            }
        )

    def _read_journal(self, directory: int) -> _JournalState:
        if not _exists(directory, self.path.name):
            return _JournalState((), b"", 0)
        source = _read_regular(directory, self.path.name, maximum=MAX_SESSION_BYTES)
        records: list[bytes] = []
        cursor = 0
        while True:
            newline = source.find(b"\n", cursor)
            if newline < 0:
                break
            line = source[cursor : newline + 1]
            if not line[:-1].strip():
                raise PicoJournalError("Pico journal contains an empty record")
            _decode_canonical_line(line, label="Pico journal record")
            records.append(line)
            cursor = newline + 1
        return _JournalState(tuple(records), source[cursor:], cursor)

    def _verify_committed_prefix(self, state: _JournalState, checkpoint: ImportCheckpoint | None) -> None:
        if checkpoint is None:
            return
        if checkpoint.provider != "pico" or checkpoint.session_id != self.session_id:
            raise SourceRewritten("Pico journal checkpoint identity does not match the journal")
        if checkpoint.resume_raw_event_index != checkpoint.committed_raw_event_index + 1:
            raise SourceRewritten("Pico journal checkpoint retains an unexpected active suffix")
        if checkpoint.committed_raw_event_index >= len(state.records):
            raise SourceRewritten(f"Pico source is truncated before checkpoint: {self.path}")
        prefix = EMPTY_RAW_PREFIX_SHA256
        for line in state.records[: checkpoint.resume_raw_event_index]:
            prefix = extend_raw_prefix_sha256(prefix, hashlib.sha256(line[:-1]).hexdigest())
        if prefix != checkpoint.resume_prefix_sha256:
            raise SourceRewritten(f"Pico source changed before checkpoint: {self.path}")

    def _install_staged_batch(
        self, directory: int, state: _JournalState, *, staged: bytes, staged_record: dict[str, Any], checkpoint: ImportCheckpoint | None
    ) -> None:
        committed_count = 0 if checkpoint is None else checkpoint.committed_raw_event_index + 1
        complete_records = state.records
        if state.trailing:
            if not staged.startswith(state.trailing):
                raise PicoJournalError("Pico journal final fragment conflicts with the staged batch")
            if len(complete_records) < max(1, committed_count):
                raise SourceRewritten(f"Pico source is truncated before checkpoint: {self.path}")
            if len(complete_records) > max(1, committed_count):
                raise PicoJournalError("Pico journal has an uncommitted complete batch before the staged fragment")
            if staged_record["batch_ordinal"] != len(complete_records):
                raise PicoJournalError("Pico staged batch ordinal does not follow the journal")
            _truncate_fsynced(directory, self.path.name, state.complete_bytes)
            _append_fsynced(directory, self.path.name, staged)
            return
        decoded = tuple(_decode_canonical_line(line, label="Pico journal record") for line in complete_records)
        matching = tuple(index for index, record in enumerate(decoded) if record.get("batch_id") == staged_record["batch_id"])
        if matching:
            if matching != (len(decoded) - 1,) or complete_records[-1] != staged:
                raise PicoJournalError("Pico staged batch identity conflicts with committed bytes")
            return
        if len(complete_records) > max(1, committed_count):
            raise PicoJournalError("Pico journal has an uncommitted complete batch that does not match the stage")
        expected_ordinal = len(complete_records)
        if staged_record["batch_ordinal"] != expected_ordinal:
            raise PicoJournalError("Pico staged batch ordinal does not follow the journal")
        _append_fsynced(directory, self.path.name, staged)

    def _header_line(self) -> bytes:
        return _canonical_line(
            {
                "created_by": "codecairn",
                "provider": "pico",
                "record_type": "header",
                "repo_key": self.repo_key,
                "schema": PICO_SOURCE_SCHEMA,
                "session_id": self.session_id,
                "source_generation": self.source_generation,
            }
        )

    def _validate_header(self, line: bytes) -> None:
        if line != self._header_line():
            raise PicoJournalError("Pico journal header does not match its bound repository and session")

    @contextmanager
    def _locked_directory(self) -> Iterator[int]:
        directory = _open_directory(self._runtime_root, self.path.parent.relative_to(self._runtime_root).parts)
        lock: int | None = None
        try:
            lock = os.open(
                self._lock_name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory,
            )
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield directory
        finally:
            try:
                if lock is not None:
                    os.close(lock)
            finally:
                os.close(directory)


class _JournalState:
    __slots__ = ("complete_bytes", "records", "trailing")

    def __init__(self, records: tuple[bytes, ...], trailing: bytes, complete_bytes: int) -> None:
        self.records = records
        self.trailing = trailing
        self.complete_bytes = complete_bytes


def _canonical_events(events: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    if isinstance(events, (str, bytes)) or not 1 <= len(events) <= MAX_PICO_BATCH_EVENTS:
        raise PicoJournalError(f"Pico batch must contain 1 to {MAX_PICO_BATCH_EVENTS} events")
    normalized = [_canonical_event(dict(event)) for event in events]
    openings = sum(event.get("kind") == "message" and event.get("role") == "user" for event in normalized)
    if openings != 1:
        raise PicoJournalError("Pico batch must contain exactly one user task opening")
    if _file_change_count(normalized) > MAX_PICO_SESSION_FILE_CHANGES:
        raise PicoJournalError("Pico batch file changes exceed the import limit")
    return normalized


def _file_change_count(events: Sequence[Mapping[str, object]]) -> int:
    return sum(
        len(terminal.get("file_changes") or ())
        for event in events
        if isinstance((terminal := event.get("terminal_observation")), Mapping)
    )


def _canonical_event(event: dict[str, object]) -> dict[str, object]:
    kind = _bounded_string(event.get("kind"), field="event kind", maximum=64)
    allowed: set[str]
    if kind == "message":
        allowed = {"kind", "role", "text", "untrusted_payload"}
        role = _bounded_string(event.get("role"), field="message role", maximum=32)
        if role not in {"user", "assistant", "system", "tool"}:
            raise PicoJournalError(f"Unsupported Pico message role: {role!r}")
        _bounded_string(event.get("text"), field="message text", maximum=_MAX_TEXT_CHARS)
    elif kind == "tool_call":
        allowed = {"kind", "call_id", "tool_name", "arguments", "command", "untrusted_payload"}
        _bounded_string(event.get("call_id"), field="call_id", maximum=_MAX_CALL_ID_CHARS)
        _bounded_string(event.get("tool_name"), field="tool_name", maximum=_MAX_TOOL_NAME_CHARS)
        _optional_bounded_string(event.get("command"), field="command", maximum=_MAX_ATTRIBUTE_BYTES)
        _bounded_json(event.get("arguments"), field="tool arguments", maximum=_MAX_ARGUMENT_BYTES)
    elif kind == "tool_result":
        allowed = {"kind", "call_id", "text", "status", "terminal_observation", "untrusted_payload"}
        _bounded_string(event.get("call_id"), field="call_id", maximum=_MAX_CALL_ID_CHARS)
        _optional_bounded_string(event.get("text"), field="tool result text", maximum=_MAX_TEXT_CHARS)
        _optional_bounded_string(event.get("status"), field="tool result status", maximum=64)
        terminal = event.get("terminal_observation")
        if terminal is not None:
            if not isinstance(terminal, Mapping):
                raise PicoJournalError("terminal_observation must be an object")
            _validate_terminal(dict(terminal))
    else:
        allowed = {"kind", "untrusted_payload"}
    unknown = set(event) - allowed
    if unknown:
        raise PicoJournalError(f"Unknown Pico event fields must be nested under untrusted_payload: {sorted(unknown)!r}")
    if "untrusted_payload" in event:
        _bounded_json(event["untrusted_payload"], field="untrusted_payload", maximum=_MAX_UNTRUSTED_BYTES)
    _bounded_json(event, field="event", maximum=MAX_PICO_BATCH_BYTES)
    return cast(dict[str, object], json.loads(canonical_json(event)))


def _validate_terminal(terminal: dict[str, object]) -> None:
    allowed = {"exit_code", "file_changes"}
    if set(terminal) - allowed:
        raise PicoJournalError("Unknown terminal observation fields")
    exit_code = terminal.get("exit_code")
    if exit_code is not None and (type(exit_code) is not int or not -(2**31) <= exit_code <= 2**31 - 1):
        raise PicoJournalError("terminal exit_code is invalid")
    changes = terminal.get("file_changes")
    if changes is None:
        return
    if not isinstance(changes, list) or len(changes) > _MAX_FILE_CHANGES:
        raise PicoJournalError("terminal file_changes are invalid")
    for change in changes:
        if not isinstance(change, Mapping):
            raise PicoJournalError("terminal file change must be an object")
        value = dict(change)
        if set(value) - {"operation", "path", "destination_path"}:
            raise PicoJournalError("Unknown terminal file change fields")
        operation = _bounded_string(value.get("operation"), field="file change operation", maximum=16)
        if operation not in {"add", "update", "delete", "move"}:
            raise PicoJournalError("terminal file change operation is invalid")
        _path(value.get("path"), field="file change path")
        destination = value.get("destination_path")
        if destination is not None:
            destination = _path(destination, field="file change destination")
        if operation == "move" and destination is None:
            raise PicoJournalError("terminal move file change requires destination_path")
        if operation != "move" and destination is not None:
            raise PicoJournalError("Only terminal move file changes may have destination_path")


def _validate_batch_record(record: dict[str, Any]) -> None:
    if set(record) != {"batch_id", "batch_ordinal", "events", "record_type", "schema"}:
        raise PicoJournalError("Staged Pico batch fields are invalid")
    if record["schema"] != PICO_SOURCE_SCHEMA or record["record_type"] != "batch":
        raise PicoJournalError("Staged Pico batch schema is invalid")
    batch_id = record["batch_id"]
    if (
        not isinstance(batch_id, str)
        or not batch_id.startswith(_BATCH_ID_PREFIX)
        or len(batch_id) != len(_BATCH_ID_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in batch_id[len(_BATCH_ID_PREFIX) :])
    ):
        raise PicoJournalError("Staged Pico batch identity is invalid")
    if type(record["batch_ordinal"]) is not int or record["batch_ordinal"] < 1:
        raise PicoJournalError("Staged Pico batch ordinal is invalid")
    events = record["events"]
    if not isinstance(events, list):
        raise PicoJournalError("Staged Pico batch events are invalid")
    _canonical_events(events)
    if len(_canonical_line(record)) > MAX_PICO_BATCH_BYTES:
        raise PicoJournalError(f"Pico batch exceeds the {MAX_PICO_BATCH_BYTES}-byte limit")


def _identity(value: str, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value)
        or value != normalize_text(value)
    ):
        raise PicoJournalError(f"{field} must contain 1 to {maximum} safe UTF-8 bytes")
    return value


def _bounded_string(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise PicoJournalError(f"{field} must contain 1 to {maximum} UTF-8 bytes")
    if value != normalize_text(value):
        raise PicoJournalError(f"{field} must use NFC text and LF newlines")
    return value


def _optional_bounded_string(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, field=field, maximum=maximum)


def _path(value: object, *, field: str) -> str:
    path = _bounded_string(value, field=field, maximum=_MAX_PATH_CHARS)
    try:
        if path != normalize_path_key(path):
            raise PicoJournalError(f"{field} must be repository-relative and normalized")
    except SchemaInvalid as exc:
        raise PicoJournalError(f"{field} must be repository-relative and normalized") from exc
    return path


def _bounded_json(value: object, *, field: str, maximum: int) -> None:
    try:
        encoded = canonical_json(value).encode()
    except (TypeError, ValueError) as exc:
        raise PicoJournalError(f"{field} must be canonical JSON") from exc
    if len(encoded) > maximum:
        raise PicoJournalError(f"{field} exceeds the {maximum}-byte limit")


def _canonical_line(value: object) -> bytes:
    return f"{canonical_json(value)}\n".encode()


def _decode_canonical_line(line: bytes, *, label: str) -> dict[str, Any]:
    if not line.endswith(b"\n"):
        raise PicoJournalError(f"{label} is not newline terminated")
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PicoJournalError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict) or _canonical_line(value) != line:
        raise PicoJournalError(f"{label} is not canonical JSON")
    return cast(dict[str, Any], value)


def _open_directory(root: Path, components: tuple[str, ...]) -> int:
    root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(root, flags)
    try:
        for component in components:
            created = False
            try:
                os.mkdir(component, 0o700, dir_fd=directory)
                created = True
            except FileExistsError:
                pass
            if created:
                os.fsync(directory)
            try:
                child = os.open(component, flags, dir_fd=directory)
            except OSError as exc:
                raise PicoJournalError("Pico journal directory must not traverse symbolic links") from exc
            os.close(directory)
            directory = child
    except BaseException:
        os.close(directory)
        raise
    return directory


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _read_regular(directory: int, name: str, *, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PicoJournalError(f"Pico journal path is not a regular file: {name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            source = handle.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(source) > maximum:
        raise PicoJournalError(f"Pico journal file exceeds the {maximum}-byte limit: {name}")
    return source


def _create_fsynced(directory: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.fsync(directory)


def _create_atomic(directory: int, name: str, content: bytes) -> None:
    temporary = f"{name}.tmp"
    with suppress(FileNotFoundError):
        os.unlink(temporary, dir_fd=directory)
    _create_fsynced(directory, temporary, content)
    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    os.fsync(directory)


def _append_fsynced(directory: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        if os.fstat(descriptor).st_size + len(content) > MAX_SESSION_BYTES:
            raise PicoJournalError("Pico journal exceeds the import byte limit")
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _truncate_fsynced(directory: int, name: str, length: int) -> None:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        os.ftruncate(descriptor, length)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
