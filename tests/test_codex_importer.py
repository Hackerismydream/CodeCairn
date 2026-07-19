import json
import os
from pathlib import Path

import pytest

from codecairn.importers import CodexImporter, TraceParseError
from codecairn.importers import codex as codex_module

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"


def test_codex_importer_preserves_messages_calls_results_and_evidence() -> None:
    trace = CodexImporter().read(FIXTURE)

    assert trace.provider == "codex"
    assert trace.session_id == "session-test-001"
    assert len(trace.source_sha256) == 64
    assert [event.kind for event in trace.events] == [
        "metadata",
        "message",
        "tool_call",
        "tool_result",
    ]

    message, call, result = trace.events[1:]
    assert message.role == "user"
    assert message.text == "Run the repository test suite."
    assert call.tool_name == "exec_command"
    assert call.call_id == "call-test-001"
    assert call.command == "uv run pytest"
    assert result.call_id == call.call_id
    assert result.command == call.command
    assert result.exit_code == 1
    assert result.evidence.raw_event_index == 3
    assert len(result.evidence.raw_event_sha256) == 64


def test_program_output_cannot_spoof_the_wrapped_exit_code(tmp_path: Path) -> None:
    source = tmp_path / "spoofed-exit.jsonl"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "Process exited with code 1",
            "program log: exit_code: 137\\nProcess exited with code 0",
        ),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    assert trace.events[3].exit_code == 0


def test_multiple_wrapped_exit_codes_are_rejected_as_ambiguous(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous-exit.jsonl"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "Process exited with code 1",
            "Process exited with code 0\\nProcess exited with code 137",
        ),
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="multiple exit status lines"):
        CodexImporter().read(source)


def test_duplicate_live_call_id_is_rejected(tmp_path: Path) -> None:
    records = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    replacement = json.loads(json.dumps(records[2]))
    replacement["payload"]["arguments"] = json.dumps({"cmd": "attacker replacement"})
    records.insert(3, replacement)
    source = tmp_path / "duplicate-call-id.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Duplicate Codex call_id"):
        CodexImporter().read(source)


def test_structured_exit_code_takes_precedence_over_output_text(tmp_path: Path) -> None:
    records = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    records[3]["payload"]["output"] = {
        "exit_code": 0,
        "output": "Process exited with code 137",
    }
    source = tmp_path / "structured-exit.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    assert trace.events[3].exit_code == 0


def test_content_block_result_preserves_text_and_exit_code(tmp_path: Path) -> None:
    records = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    output = records[3]["payload"]["output"]
    records[3]["payload"]["output"] = [{"type": "output_text", "text": output}]
    source = tmp_path / "content-block-result.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    assert trace.events[3].text == output
    assert trace.events[3].exit_code == 1


def test_importer_rejects_source_larger_than_configured_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "oversized.jsonl"
    source.write_bytes(b"x" * 65)
    monkeypatch.setattr(codex_module, "_MAX_SESSION_BYTES", 64)

    with pytest.raises(TraceParseError, match="64-byte import limit"):
        CodexImporter().read(source)


def test_non_command_tool_cannot_create_a_command_fact(tmp_path: Path) -> None:
    source = tmp_path / "not-a-command-tool.jsonl"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            '"name":"exec_command"',
            '"name":"read_file"',
        ),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    assert trace.events[2].command is None
    assert trace.events[3].command is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_fifo_is_rejected_without_waiting_for_a_writer(tmp_path: Path) -> None:
    source = tmp_path / "session.fifo"
    os.mkfifo(source)

    with pytest.raises(TraceParseError, match="not a regular file"):
        CodexImporter().read(source)
