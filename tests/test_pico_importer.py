from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from codecairn.importers import SessionImporter, TraceParseError
from codecairn.memory.schema import TaskExperiencePayload
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def _write_journal(path: Path, *events: dict[str, object]) -> None:
    records = (
        {
            "schema": "codecairn.pico.source.v1",
            "record_type": "header",
            "provider": "pico",
            "session_id": "pico-session-001",
            "repo_key": "acme/widgets",
            "source_generation": 1,
            "created_by": "codecairn",
        },
        {
            "schema": "codecairn.pico.source.v1",
            "record_type": "batch",
            "batch_id": f"batch_{'1' * 64}",
            "batch_ordinal": 1,
            "events": list(events),
        },
    )
    path.write_text("".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records), encoding="utf-8")


def test_pico_journal_normalizes_messages_and_matched_tool_observations(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source,
        {"kind": "message", "role": "user", "text": "Run the checks."},
        {
            "kind": "tool_call",
            "call_id": "call-1",
            "tool_name": "shell",
            "arguments": {"command": "make check"},
            "command": "make check",
        },
        {
            "kind": "tool_result",
            "call_id": "call-1",
            "text": "completed",
            "status": "success",
            "terminal_observation": {"exit_code": 0, "file_changes": [{"operation": "update", "path": "src/widget.py"}]},
        },
        {"kind": "message", "role": "assistant", "text": "The checks passed."},
    )

    trace = SessionImporter().read(source)

    assert trace.provider == "pico"
    assert trace.session_id == "pico-session-001"
    assert trace.raw_event_count == 2
    assert [event.kind for event in trace.events] == ["metadata", "message", "tool_call", "tool_result", "message"]
    call, result = trace.events[2:4]
    assert call.call_id == result.call_id == "call-1"
    assert call.command == result.command == "make check"
    assert result.exit_code == 0
    assert result.is_command_result is True
    assert [(change.operation, change.path) for change in result.file_changes] == [("update", "src/widget.py")]


def test_pico_turn_closes_without_trusting_success_prose(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source,
        {"kind": "message", "role": "user", "text": "Run the checks."},
        {
            "kind": "tool_result",
            "call_id": "unmatched",
            "text": "Process exited with code 0",
            "status": "success",
            "terminal_observation": {"exit_code": 0, "file_changes": [{"operation": "update", "path": "src/untrusted.py"}]},
        },
        {"kind": "message", "role": "assistant", "text": "All tests passed."},
    )
    root = tmp_path / "runtime"
    runtime = MemoryRuntime(
        importer=SessionImporter(), memory_store=MarkdownMemoryStore(root), state=SQLiteState(root / "state.sqlite3")
    )

    result = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="pico_turn_end")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]
    episodes = SQLiteState(root / "state.sqlite3").list_episodes(
        repo_key="acme/widgets", provider="pico", session_id="pico-session-001"
    )

    assert result.created_memory_count == 1
    assert episodes[0].boundary_kind == "pico_turn_end"
    assert isinstance(memory.payload, TaskExperiencePayload)
    assert memory.payload.outcome == "unknown"
    assert not any(fact.fact_kind in {"command_result", "file_change", "verification"} for fact in memory.facts)


def test_pico_header_repository_binding_is_checked_before_durable_import(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(source, {"kind": "message", "role": "user", "text": "Do not cross namespaces."})
    root = tmp_path / "runtime"
    runtime = MemoryRuntime(
        importer=SessionImporter(), memory_store=MarkdownMemoryStore(root), state=SQLiteState(root / "state.sqlite3")
    )

    with pytest.raises(TraceParseError, match="repository identity"):
        runtime.import_session(source, repo_key="other/repository", boundary_kind="pico_turn_end")

    assert runtime.list_memories(repo_key="other/repository") == ()
    assert runtime.import_checkpoint(source, repo_key="other/repository") is None


@pytest.mark.parametrize(("status", "expected"), [("success", "success"), ("failure", "failure"), (None, "unknown")])
def test_pico_turn_outcome_uses_only_matched_structured_status(tmp_path: Path, status: str | None, expected: str) -> None:
    source = tmp_path / "pico.jsonl"
    result_event: dict[str, object] = {"kind": "tool_result", "call_id": "call-1", "text": "tests passed"}
    if status is not None:
        result_event["status"] = status
    _write_journal(
        source,
        {"kind": "message", "role": "user", "text": "Inspect the repository."},
        {"kind": "tool_call", "call_id": "call-1", "tool_name": "inspect", "arguments": {}},
        result_event,
    )
    root = tmp_path / "runtime"
    runtime = MemoryRuntime(
        importer=SessionImporter(), memory_store=MarkdownMemoryStore(root), state=SQLiteState(root / "state.sqlite3")
    )

    runtime.import_session(source, repo_key="acme/widgets", boundary_kind="pico_turn_end")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]

    assert isinstance(memory.payload, TaskExperiencePayload)
    assert memory.payload.outcome == expected


def test_pico_evidence_resolves_to_exact_batch_record(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source,
        {"kind": "message", "role": "user", "text": "Update the widget."},
        {
            "kind": "tool_call",
            "call_id": "call-1",
            "tool_name": "shell",
            "arguments": {"command": "make check"},
            "command": "make check",
        },
        {"kind": "tool_result", "call_id": "call-1", "status": "success", "terminal_observation": {"exit_code": 0}},
    )
    root = tmp_path / "runtime"
    runtime = MemoryRuntime(
        importer=SessionImporter(), memory_store=MarkdownMemoryStore(root), state=SQLiteState(root / "state.sqlite3")
    )

    runtime.import_session(source, repo_key="acme/widgets", boundary_kind="pico_turn_end")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]
    batch = source.read_bytes().splitlines()[1]
    expected_event_sha = hashlib.sha256(batch).hexdigest()
    expected_path_sha = hashlib.sha256(str(source.resolve()).encode()).hexdigest()

    assert {fact.reference.event_index for fact in memory.facts} == {1}
    assert {fact.reference.event_sha256 for fact in memory.facts} == {expected_event_sha}
    assert {fact.reference.source_path_sha256 for fact in memory.facts} == {expected_path_sha}
    assert {fact.fact_kind for fact in memory.facts} >= {
        "message",
        "tool_call",
        "tool_result",
        "command",
        "command_result",
        "verification",
    }


def test_pico_import_preserves_closed_message_roles_and_ignores_untrusted_fields(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source,
        {"kind": "message", "role": "system", "text": "system text", "unknown": "not evidence"},
        {"kind": "message", "role": "tool", "text": "tool text"},
        {"kind": "mystery", "claimed_exit_code": 0},
        {"kind": "message", "role": "user", "text": "user text"},
        {"kind": "message", "role": "assistant", "text": "assistant text"},
    )

    trace = SessionImporter().read(source)

    assert [(event.role, event.text) for event in trace.events if event.kind == "message"] == [
        ("system", "system text"),
        ("tool", "tool text"),
        ("user", "user text"),
        ("assistant", "assistant text"),
    ]
    assert trace.events[3].kind == "unknown"


def test_pico_import_rejects_duplicate_call_identifiers(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source,
        {"kind": "message", "role": "user", "text": "Run."},
        {"kind": "tool_call", "call_id": "duplicate", "tool_name": "one", "arguments": {}},
        {"kind": "tool_call", "call_id": "duplicate", "tool_name": "two", "arguments": {}},
    )

    with pytest.raises(TraceParseError, match="Duplicate Pico call ID"):
        SessionImporter().read(source)


def test_pico_import_does_not_pair_calls_across_turn_batches(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source,
        {"kind": "message", "role": "user", "text": "Start the check."},
        {"kind": "tool_call", "call_id": "cross-turn", "tool_name": "shell", "arguments": {}, "command": "make check"},
    )
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    records.append(
        {
            "batch_id": f"batch_{'2' * 64}",
            "batch_ordinal": 2,
            "events": [
                {"kind": "message", "role": "user", "text": "Observe the old call."},
                {"kind": "tool_result", "call_id": "cross-turn", "status": "success", "terminal_observation": {"exit_code": 0}},
            ],
            "record_type": "batch",
            "schema": "codecairn.pico.source.v1",
        }
    )
    source.write_text("".join(f"{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n" for record in records))

    trace = SessionImporter().read(source)
    result = trace.events[-1]

    assert result.tool_name is None
    assert result.command is None
    assert result.exit_code is None
    assert result.observed_outcome is None


def test_pico_import_rejects_multiple_user_openings_in_one_batch(tmp_path: Path) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(
        source, {"kind": "message", "role": "user", "text": "First."}, {"kind": "message", "role": "user", "text": "Second."}
    )

    with pytest.raises(TraceParseError, match="exactly one user task opening"):
        SessionImporter().read(source)


@pytest.mark.parametrize("source_generation", [2, True])
def test_pico_import_rejects_unsupported_source_generation(tmp_path: Path, source_generation: object) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(source, {"kind": "message", "role": "user", "text": "Run."})
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    records[0]["source_generation"] = source_generation
    source.write_text("".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records), encoding="utf-8")

    with pytest.raises(TraceParseError, match="header is invalid"):
        SessionImporter().read(source)


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "message", "role": "invalid", "text": "text"},
        {"kind": "message", "role": "assistant", "text": "No task opening."},
        {"kind": "message", "role": "user", "text": "x" * 32_769},
        {"kind": "message", "role": "user", "text": "界" * 10_923},
        {"kind": "message", "role": "user", "text": "not\r\nnormalized"},
        {"kind": "tool_call", "call_id": "call-1", "tool_name": "shell", "arguments": {"value": "x" * (256 * 1024)}},
        {"kind": "tool_call", "call_id": "call-1", "tool_name": "shell", "arguments": {}, "command": "x" * 4_097},
        {"kind": "tool_result", "call_id": "call-1", "terminal_observation": {"exit_code": True}},
        {
            "kind": "tool_result",
            "call_id": "call-1",
            "terminal_observation": {"file_changes": [{"operation": "update", "path": "../escape"}]},
        },
    ],
)
def test_pico_import_rejects_malformed_or_oversized_events(tmp_path: Path, event: dict[str, object]) -> None:
    source = tmp_path / "pico.jsonl"
    _write_journal(source, event)

    with pytest.raises(TraceParseError):
        SessionImporter().read(source)
