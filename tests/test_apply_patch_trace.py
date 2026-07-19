import json
from pathlib import Path

import pytest

from codecairn.importers import CodexImporter, TraceParseError
from codecairn.importers import codex as codex_module
from codecairn.memory.trace import segment_tasks

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "apply_patch_session.jsonl"


def test_custom_apply_patch_calls_emit_evidence_backed_file_changes(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    trace = CodexImporter().read(source)

    first_call = trace.events[2]
    first_result = trace.events[3]
    second_call = trace.events[4]
    second_result = trace.events[5]
    assert first_call.kind == "tool_call"
    assert first_call.tool_name == "apply_patch"
    assert first_call.call_id == "patch-call-001"
    assert first_call.tool_status == "completed"
    assert first_call.text is not None and first_call.text.startswith("*** Begin Patch")
    assert [
        (change.operation, change.path, change.destination_path)
        for change in first_call.file_changes
    ] == [
        ("add", "created_by_patch.txt", None),
        ("move", "src/example.py", "src/renamed.py"),
    ]
    assert [change.evidence.raw_event_index for change in first_call.file_changes] == [2, 2]
    assert first_result.kind == "tool_result"
    assert first_result.tool_name == "apply_patch"
    assert first_result.call_id == first_call.call_id
    assert [
        (change.operation, change.path, change.destination_path)
        for change in second_call.file_changes
    ] == [
        ("delete", "docs/obsolete.md", None),
        ("update", "README.md", None),
    ]
    assert second_result.tool_name == "apply_patch"
    assert second_result.call_id == second_call.call_id
    assert not (tmp_path / "created_by_patch.txt").exists()


def test_apply_patch_with_real_shape_terminal_newline_emits_file_changes(
    tmp_path: Path,
) -> None:
    records = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    patch_call = records[2]["payload"]
    patch_call["input"] = f"{patch_call['input']}\n"
    source = tmp_path / "terminal-newline.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    assert [change.operation for change in trace.events[2].file_changes] == ["add", "move"]


def test_appended_task_preserves_earlier_episode_identity_and_outcome(
    tmp_path: Path,
) -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    source = tmp_path / "session.jsonl"
    source.write_text("\n".join(lines[:8]) + "\n", encoding="utf-8")
    importer = CodexImporter()

    before = segment_tasks(importer.read(source), repo_key="acme/widgets")
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    after = segment_tasks(importer.read(source), repo_key="acme/widgets")

    assert len(before) == 1
    assert len(after) == 2
    assert before[0].episode_id == after[0].episode_id
    assert before[0].opening_event_id == after[0].opening_event_id
    assert [change.fact_id for change in before[0].events[1].file_changes] == [
        change.fact_id for change in after[0].events[1].file_changes
    ]
    assert before[0].outcome == "success"
    assert after[0].outcome == "success"
    assert after[1].outcome == "failed"


def test_unmatched_custom_output_does_not_steal_the_real_pair(tmp_path: Path) -> None:
    records = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    records.insert(
        3,
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "missing-call",
                "output": "Done!",
            },
        },
    )
    source = tmp_path / "unmatched-output.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    unmatched = trace.events[3]
    matched = trace.events[4]
    assert unmatched.kind == "tool_result"
    assert unmatched.call_id == "missing-call"
    assert unmatched.tool_name is None
    assert matched.call_id == "patch-call-001"
    assert matched.tool_name == "apply_patch"


def test_function_output_cannot_claim_custom_call_or_episode_outcome(tmp_path: Path) -> None:
    records = [
        {"type": "session_meta", "payload": {"id": "protocol-mismatch-custom"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "Patch it."},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "call_id": "shared-call",
                "input": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "shared-call",
                "output": {"exit_code": 1, "output": "failed"},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "shared-call",
                "output": "Done!",
            },
        },
    ]
    source = tmp_path / "function-output-for-custom-call.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)
    episode = segment_tasks(trace, repo_key="acme/widgets")[0]

    assert trace.events[3].tool_name is None
    assert trace.events[4].tool_name == "apply_patch"
    assert episode.outcome == "unknown"


def test_custom_output_cannot_steal_function_call_pair(tmp_path: Path) -> None:
    records = [
        {"type": "session_meta", "payload": {"id": "protocol-mismatch-function"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "Verify it."},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "shared-call",
                "arguments": '{"cmd":"pytest -q"}',
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "shared-call",
                "output": "not a command result",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "shared-call",
                "output": {"exit_code": 0, "output": "passed"},
            },
        },
    ]
    source = tmp_path / "custom-output-for-function-call.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)
    episode = segment_tasks(trace, repo_key="acme/widgets")[0]

    assert trace.events[3].tool_name is None
    assert trace.events[4].tool_name == "exec_command"
    assert episode.outcome == "success"


def test_non_command_function_output_cannot_author_episode_outcome(tmp_path: Path) -> None:
    records = [
        {"type": "session_meta", "payload": {"id": "non-command-outcome"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "Read it."},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "read_file",
                "call_id": "read-call",
                "arguments": '{"path":"README.md"}',
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "read-call",
                "output": {"exit_code": 1, "output": "not a command status"},
            },
        },
    ]
    source = tmp_path / "non-command-outcome.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)
    episode = segment_tasks(trace, repo_key="acme/widgets")[0]

    assert trace.events[3].tool_name == "read_file"
    assert trace.events[3].command is None
    assert not trace.events[3].is_command_result
    assert episode.outcome == "unknown"


def test_write_stdin_result_can_author_long_running_command_outcome(tmp_path: Path) -> None:
    records = [
        {"type": "session_meta", "payload": {"id": "write-stdin-outcome"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "Wait for tests."},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "write_stdin",
                "call_id": "wait-call",
                "arguments": '{"session_id":123,"chars":""}',
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "wait-call",
                "output": {"exit_code": 1, "output": "tests failed"},
            },
        },
    ]
    source = tmp_path / "write-stdin-outcome.jsonl"
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)
    episode = segment_tasks(trace, repo_key="acme/widgets")[0]

    assert trace.events[3].command is None
    assert trace.events[3].is_command_result
    assert episode.outcome == "failed"


def test_appending_a_result_extends_only_the_active_episode(tmp_path: Path) -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    source = tmp_path / "session.jsonl"
    source.write_text("\n".join(lines[:7]) + "\n", encoding="utf-8")
    importer = CodexImporter()

    before = segment_tasks(importer.read(source), repo_key="acme/widgets")
    source.write_text("\n".join(lines[:8]) + "\n", encoding="utf-8")
    after = segment_tasks(importer.read(source), repo_key="acme/widgets")

    assert len(before) == len(after) == 1
    assert before[0].episode_id == after[0].episode_id
    assert before[0].outcome == "unknown"
    assert after[0].outcome == "success"


def test_apply_patch_path_is_preserved_as_data_without_filesystem_access(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsafe-patch.jsonl"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "created_by_patch.txt",
            "../outside.txt",
        ),
        encoding="utf-8",
    )

    trace = CodexImporter().read(source)

    assert trace.events[2].file_changes[0].path == "../outside.txt"
    assert not (tmp_path.parent / "outside.txt").exists()


def test_late_session_metadata_does_not_rename_existing_evidence(tmp_path: Path) -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()[1:]
    source = tmp_path / "fallback-session.jsonl"
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    importer = CodexImporter()

    before = importer.read(source)
    before_episodes = segment_tasks(before, repo_key="acme/widgets")
    source.write_text(
        "\n".join([*lines, '{"type":"session_meta","payload":{"id":"late-id"}}']) + "\n",
        encoding="utf-8",
    )
    after = importer.read(source)
    after_episodes = segment_tasks(after, repo_key="acme/widgets")

    assert before.session_id == after.session_id == "fallback-session"
    assert before_episodes[0].episode_id == after_episodes[0].episode_id
    assert before.events[1].file_changes[0].fact_id == after.events[1].file_changes[0].fact_id


def test_unicode_line_separator_in_patch_path_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "unicode-separator.jsonl"
    source.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "created_by_patch.txt",
            "src/line\u2028break.py",
        ),
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Invalid apply_patch path evidence"):
        CodexImporter().read(source)


def test_apply_patch_fact_count_has_per_patch_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "patch-fact-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_PATCH_FILE_CHANGE_FACTS", 1)

    with pytest.raises(TraceParseError, match="per-patch import limit"):
        CodexImporter().read(source)


def test_apply_patch_fact_count_has_per_session_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "session-fact-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_SESSION_FILE_CHANGE_FACTS", 3)

    with pytest.raises(TraceParseError, match="session exceeds"):
        CodexImporter().read(source)


def test_apply_patch_path_has_length_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "patch-path-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_PATCH_PATH_CHARS", 8)

    with pytest.raises(TraceParseError, match="path evidence exceeds"):
        CodexImporter().read(source)


def test_apply_patch_has_input_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "patch-input-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_PATCH_CHARS", 64)

    with pytest.raises(TraceParseError, match="input exceeds"):
        CodexImporter().read(source)


def test_apply_patch_has_line_count_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "patch-line-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_PATCH_LINES", 2)

    with pytest.raises(TraceParseError, match="line import limit"):
        CodexImporter().read(source)


def test_codex_import_has_raw_event_count_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "event-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_RAW_EVENTS", 2)

    with pytest.raises(TraceParseError, match="event import limit"):
        CodexImporter().read(source)


def test_codex_import_has_session_id_length_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "session-id-limit.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(codex_module, "_MAX_SESSION_ID_CHARS", 8)

    with pytest.raises(TraceParseError, match="session id must contain"):
        CodexImporter().read(source)
