import json
from pathlib import Path

import pytest

from codecairn.importers import ClaudeImporter, SessionImporter, TraceParseError

FIXTURE = Path(__file__).parent / "fixtures" / "claude" / "failed_command.jsonl"


def test_claude_importer_preserves_messages_calls_results_changes_and_evidence() -> None:
    trace = ClaudeImporter().read(FIXTURE)

    assert trace.provider == "claude"
    assert trace.session_id == "claude-session-test-001"
    assert [event.kind for event in trace.events] == [
        "message",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "message",
    ]

    task, command, failure, write, written, final = trace.events
    assert task.role == "user"
    assert task.text == "Run the repository test suite and add the missing module."
    assert command.tool_name == "Bash"
    assert command.call_id == "tool-call-001"
    assert command.command == "uv run pytest"
    assert failure.call_id == command.call_id
    assert failure.command == command.command
    assert failure.exit_code == 1
    assert failure.is_command_result is True
    assert write.tool_name == "Write"
    assert written.call_id == write.call_id == "tool-call-002"
    assert [(fact.operation, fact.path) for fact in written.file_changes] == [
        ("add", "src/new_module.py")
    ]
    assert final.role == "assistant"
    assert failure.evidence.raw_event_index == 2
    assert len(failure.evidence.raw_event_sha256) == 64


def test_failed_claude_file_tool_cannot_emit_a_file_change(tmp_path: Path) -> None:
    source = tmp_path / "failed-write.jsonl"
    records = [
        {
            "type": "assistant",
            "sessionId": "claude-session-failed-write",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "write-001",
                        "name": "Write",
                        "input": {"file_path": "src/unsafe.py", "content": "unsafe"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "sessionId": "claude-session-failed-write",
            "toolUseResult": {
                "type": "create",
                "filePath": "src/unsafe.py",
                "originalFile": None,
            },
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "write-001",
                        "content": "Permission denied",
                        "is_error": True,
                    }
                ],
            },
        },
    ]
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = ClaudeImporter().read(source)

    assert trace.events[-1].file_changes == ()


def test_unmatched_claude_result_cannot_claim_a_file_change(tmp_path: Path) -> None:
    source = tmp_path / "unmatched-write.jsonl"
    record = {
        "type": "user",
        "sessionId": "claude-session-unmatched-write",
        "toolUseResult": {
            "type": "create",
            "filePath": "src/unsafe.py",
            "originalFile": None,
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "missing-write-call",
                    "content": "Created src/unsafe.py",
                }
            ],
        },
    }
    source.write_text(f"{json.dumps(record)}\n", encoding="utf-8")

    trace = ClaudeImporter().read(source)

    assert trace.events[-1].file_changes == ()


def test_shared_importer_rejects_an_unknown_jsonl_envelope(tmp_path: Path) -> None:
    source = tmp_path / "unknown.jsonl"
    source.write_text('{"type":"message","payload":{}}\n', encoding="utf-8")

    with pytest.raises(TraceParseError, match="Unsupported trace JSONL format"):
        SessionImporter().read(source)
