from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from codecairn.importers import SessionImporter, SourceRewritten
from codecairn.memory.capture import PreparedMemoryCommit
from codecairn.memory.schema import IdentityConflict, LegacyRootUnsupported, TaskExperiencePayload
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SCHEMA_REVISION, SQLiteImportProgress, SQLiteState

FIXTURES = Path(__file__).parent / "fixtures"


def _runtime(root: Path, *, fault_injector: Callable[[str], None] | None = None) -> MemoryRuntime:
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(root),
        state=SQLiteState(root / "state.sqlite3"),
        fault_injector=fault_injector,
    )


def _write_codex_session(path: Path, *, exit_codes: tuple[int, ...]) -> None:
    records: list[dict[str, object]] = [
        {"type": "session_meta", "payload": {"id": "outcome-session"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run all checks."}]},
        },
    ]
    for index, exit_code in enumerate(exit_codes):
        call_id = f"call-{index}"
        records.extend(
            (
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": f"check-{index}"}),
                        "call_id": call_id,
                    },
                },
                {
                    "type": "response_item",
                    "payload": {"type": "function_call_output", "call_id": call_id, "output": f"Process exited with code {exit_code}"},
                },
            )
        )
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")


@pytest.mark.parametrize(("fixture", "provider"), [("codex/failed_command.jsonl", "codex"), ("claude/failed_command.jsonl", "claude")])
def test_import_creates_one_auditable_task_experience(tmp_path: Path, fixture: str, provider: str) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    boundary = "codex_stop" if provider == "codex" else "claude_session_end"

    result = runtime.import_session(FIXTURES / fixture, repo_key="acme/widgets", boundary_kind=boundary)
    memories = runtime.list_memories(repo_key="acme/widgets")
    episodes = SQLiteState(root / "state.sqlite3").list_episodes(
        repo_key="acme/widgets", provider=provider, session_id=result.session_id
    )

    assert result.provider == provider
    assert result.created_memory_count == 1
    assert episodes[0].boundary_kind == boundary
    assert len(memories) == 1
    memory = memories[0]
    assert memory.memory_type == "task_experience"
    assert isinstance(memory.payload, TaskExperiencePayload)
    assert memory.payload.outcome == "failure"
    assert {fact.fact_kind for fact in memory.facts} >= {"message", "command", "command_result", "verification"}
    assert all(reference.fact_id.startswith("fact_") for reference in memory.evidence)
    artifact = MarkdownMemoryStore(root).read(MarkdownMemoryStore(root).path_for(memory))
    assert artifact.memory == memory


def test_repeat_import_is_idempotent(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "runtime")
    source = FIXTURES / "codex/failed_command.jsonl"

    first = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")
    second = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")

    assert first.created_memory_count == 1
    assert second.created_memory_count == 0
    assert second.skipped_memory_count == 0
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 1


@pytest.mark.parametrize(("exit_codes", "expected"), [((), "unknown"), ((0,), "success"), ((1,), "failure"), ((0, 1), "partial")])
def test_task_experience_uses_observed_outcome(tmp_path: Path, exit_codes: tuple[int, ...], expected: str) -> None:
    source = tmp_path / "outcome.jsonl"
    _write_codex_session(source, exit_codes=exit_codes)
    runtime = _runtime(tmp_path / "runtime")

    runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]

    assert isinstance(memory.payload, TaskExperiencePayload)
    assert memory.payload.outcome == expected


def test_large_episode_uses_bounded_deterministic_evidence_selection(tmp_path: Path) -> None:
    source = tmp_path / "large.jsonl"
    _write_codex_session(source, exit_codes=(*([0] * 70), 1))
    runtime = _runtime(tmp_path / "runtime")

    runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]

    assert isinstance(memory.payload, TaskExperiencePayload)
    assert memory.payload.outcome == "partial"
    assert len(memory.facts) == 128
    assert memory.payload.blockers
    assert any(fact.role == "user" for fact in memory.facts)


def test_unclosed_suffix_waits_for_explicit_finalize(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "runtime")
    source = tmp_path / "session.jsonl"
    source.write_text((FIXTURES / "codex/failed_command.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

    observed = runtime.import_session(source, repo_key="acme/widgets")
    finalized = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")

    assert observed.created_memory_count == 0
    assert finalized.created_memory_count == 1
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 1

    source.write_text(
        source.read_text(encoding="utf-8").replace("Run the repository test suite.", "Rewrite the finalized task."), encoding="utf-8"
    )
    with pytest.raises(SourceRewritten):
        runtime.import_session(source, repo_key="acme/widgets")


def test_next_user_closes_previous_task_but_leaves_new_task_open(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    source = tmp_path / "session.jsonl"
    source.write_text((FIXTURES / "codex/failed_command.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    second_user = {
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Now update the docs."}]},
    }
    with source.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(second_user)}\n")
    runtime = _runtime(root)

    observed = runtime.import_session(source, repo_key="acme/widgets")
    episodes = SQLiteState(root / "state.sqlite3").list_episodes(
        repo_key="acme/widgets", provider="codex", session_id="session-test-001"
    )
    finalized = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")

    assert observed.created_memory_count == 1
    assert len(episodes) == 1
    assert episodes[0].boundary_kind == "next_user"
    assert finalized.created_memory_count == 1
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 2


def test_append_after_committed_boundary_creates_linked_continuation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "runtime")
    source = tmp_path / "session.jsonl"
    source.write_text((FIXTURES / "codex/failed_command.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    first = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="codex_stop")
    record = {
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I documented the failure."}]},
    }
    with source.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(record)}\n")

    second = runtime.import_session(source, repo_key="acme/widgets", boundary_kind="codex_stop")
    memories = runtime.list_memories(repo_key="acme/widgets")
    episodes = SQLiteState(tmp_path / "runtime" / "state.sqlite3").list_episodes(
        repo_key="acme/widgets", provider="codex", session_id="session-test-001"
    )

    assert first.created_memory_count == 1
    assert second.created_memory_count == 1
    assert len(memories) == 2
    assert len(episodes) == 2
    assert episodes[1].continues_episode_id == episodes[0].episode_id
    assert episodes[0].end_event_index_exclusive == 4
    assert episodes[1].start_event_index == 4
    assert episodes[1].end_event_index_exclusive == 5


@pytest.mark.parametrize(
    "crash_stage",
    [
        "capture_after_intent_prepared",
        "capture_after_temp_write",
        "capture_after_file_fsync",
        "capture_after_atomic_create",
        "capture_after_directory_fsync",
        "capture_transaction_b_start",
        "capture_before_commit",
        "capture_after_complete",
    ],
)
def test_capture_recovers_each_documented_write_intent_boundary(tmp_path: Path, crash_stage: str) -> None:
    root = tmp_path / "runtime"
    crashed = False

    def fail_once(stage: str) -> None:
        nonlocal crashed
        if stage == crash_stage and not crashed:
            crashed = True
            raise RuntimeError(f"injected crash at {stage}")

    with pytest.raises(RuntimeError, match="injected crash"):
        _runtime(root, fault_injector=fail_once).import_session(
            FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize"
        )

    recovered = _runtime(root).import_session(
        FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize"
    )
    state = SQLiteState(root / "state.sqlite3")

    assert crashed is True
    assert len(state.list_memories(repo_key="acme/widgets")) == 1
    assert len(state.list_episodes(repo_key="acme/widgets", provider="codex", session_id="session-test-001")) == 1
    assert state.operational_counts().pending_recovery_count == 0
    assert recovered.created_memory_count == 0


def test_committed_source_rewrite_is_typed_and_does_not_advance_cursor(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    source = tmp_path / "session.jsonl"
    source.write_text((FIXTURES / "codex/failed_command.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    runtime = _runtime(root)
    runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")
    state = SQLiteState(root / "state.sqlite3")
    before = state.get_checkpoint(repo_key="acme/widgets", source_path=str(source.resolve()))
    source.write_text(
        source.read_text(encoding="utf-8").replace("Run the repository test suite.", "Rewrite committed history."), encoding="utf-8"
    )

    with pytest.raises(SourceRewritten) as captured:
        runtime.import_session(source, repo_key="acme/widgets", boundary_kind="manual_finalize")

    assert captured.value.code == "source_rewritten"
    assert state.get_checkpoint(repo_key="acme/widgets", source_path=str(source.resolve())) == before


def test_recovery_marks_conflicting_markdown_intent_conflicted(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    def crash_after_intent(stage: str) -> None:
        if stage == "capture_after_intent_prepared":
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        _runtime(root, fault_injector=crash_after_intent).import_session(
            FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize"
        )
    state = SQLiteState(root / "state.sqlite3")
    capture = state.list_prepared_memory_commits()[0]
    conflicting = MarkdownMemoryStore(root).path_for(capture.memories[0])
    conflicting.parent.mkdir(parents=True, exist_ok=True)
    conflicting.write_text("conflicting immutable bytes\n", encoding="utf-8")

    with pytest.raises(IdentityConflict):
        _runtime(root).import_session(FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize")

    assert state.list_prepared_memory_commits() == ()
    assert state.operational_counts().pending_recovery_count == 0
    assert state.operational_counts().conflicted_recovery_count == 1
    assert state.list_memories(repo_key="acme/widgets") == ()


def test_write_intent_reserves_episode_closure_before_markdown(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    def crash_after_intent(stage: str) -> None:
        if stage == "capture_after_intent_prepared":
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        _runtime(root, fault_injector=crash_after_intent).import_session(
            FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize"
        )
    state = SQLiteState(root / "state.sqlite3")
    winner = state.list_prepared_memory_commits()[0]
    losing_episode = replace(winner.episodes[0], prefix_sha256="0" * 64)
    loser = PreparedMemoryCommit.create(
        repo_key=winner.repo_key,
        episodes=(losing_episode,),
        facts=winner.facts,
        memories=winner.memories,
        expected_files=winner.expected_files,
        checkpoint=winner.checkpoint,
        created_at_ms=winner.created_at_ms,
    )

    assert state.prepare_memory_commit(loser) == "closure_lost"
    assert state.list_prepared_memory_commits() == (winner,)


def test_legacy_sqlite_root_is_rejected_before_mutation(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    database = root / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE gate_audit (audit_id INTEGER PRIMARY KEY)")
    before = database.read_bytes()

    with pytest.raises(LegacyRootUnsupported):
        SQLiteState(database)

    assert database.read_bytes() == before


def test_v01_sqlite_root_migrates_in_place_without_losing_memories(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    runtime.import_session(FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize")
    memory_id = runtime.list_memories(repo_key="acme/widgets")[0].memory_id
    database = root / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE codecairn_meta SET value = 'codecairn-v01-5' WHERE key = 'schema_revision'")

    assert (
        SQLiteImportProgress(path=database, repo_key="acme/widgets")(
            source_path=tmp_path / "unseen.jsonl", raw_event_count=1, source_fingerprint="a" * 64, raw_event_sha256s=("b" * 64,)
        )
        == "new"
    )

    migrated = SQLiteState(database)

    assert migrated.get_memory(repo_key="acme/widgets", memory_id=memory_id) is not None
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT value FROM codecairn_meta WHERE key = 'schema_revision'").fetchone()
    assert revision == (SCHEMA_REVISION,)


def test_unknown_versioned_sqlite_root_is_rejected_before_ddl(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    database = root / "state.sqlite3"
    SQLiteState(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE codecairn_meta SET value = 'codecairn-v99' WHERE key = 'schema_revision'")
    before = database.read_bytes()

    with pytest.raises(LegacyRootUnsupported):
        SQLiteState(database)

    assert database.read_bytes() == before
