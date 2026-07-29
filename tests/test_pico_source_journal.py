from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from codecairn.importers import SessionImporter
from codecairn.importers import jsonl as jsonl_importer
from codecairn.importers import pico as pico_importer
from codecairn.integrations.pico import journal as pico_journal
from codecairn.integrations.pico.journal import PicoJournalError, PicoSourceJournal
from codecairn.memory.errors import SourceRewritten
from codecairn.memory.models import ImportCheckpoint
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def _runtime(root: Path) -> MemoryRuntime:
    return MemoryRuntime(importer=SessionImporter(), memory_store=MarkdownMemoryStore(root), state=SQLiteState(root / "state.sqlite3"))


def test_commit_writes_hashed_fsynced_source_and_imports_one_turn(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session/../../one")

    result = journal.commit(
        (
            {"kind": "message", "role": "user", "text": "Remember this repository rule."},
            {"kind": "message", "role": "assistant", "text": "Recorded."},
        ),
        importer=runtime,
    )

    records = tuple(json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines())
    assert journal.path.parent.parent == root / "sources" / "pico"
    assert "acme" not in str(journal.path.relative_to(root))
    assert "session" not in journal.path.name
    assert records[0]["schema"] == "codecairn.pico.source.v1"
    assert records[1]["batch_ordinal"] == 1
    assert result.created_memory_count == 1
    assert journal.staged_path.exists() is False
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 1


def test_sessions_and_repositories_have_independent_journals_and_memories(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    repo_a_session_1 = PicoSourceJournal(root, repo_key="acme/one", session_id="same-session")
    repo_a_session_2 = PicoSourceJournal(root, repo_key="acme/one", session_id="other-session")
    repo_b_session_1 = PicoSourceJournal(root, repo_key="acme/two", session_id="same-session")

    repo_a_session_1.commit(({"kind": "message", "role": "user", "text": "A1"},), importer=runtime)
    repo_a_session_2.commit(({"kind": "message", "role": "user", "text": "A2"},), importer=runtime)
    repo_b_session_1.commit(({"kind": "message", "role": "user", "text": "B1"},), importer=runtime)

    assert len({repo_a_session_1.path, repo_a_session_2.path, repo_b_session_1.path}) == 3
    assert len(runtime.list_memories(repo_key="acme/one")) == 2
    assert len(runtime.list_memories(repo_key="acme/two")) == 1


def test_repeat_prefix_is_idempotent_but_a_second_append_is_a_new_batch(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    events = (
        {"kind": "message", "role": "user", "text": "Remember this."},
        {"kind": "message", "role": "assistant", "text": "Recorded."},
    )

    first = journal.commit(events, importer=runtime)
    replay = runtime.import_session(journal.path, repo_key="acme/widgets", boundary_kind="pico_turn_end")
    second = journal.commit(events, importer=runtime)

    records = tuple(json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines())
    assert first.created_memory_count == 1
    assert replay.created_memory_count == 0
    assert second.created_memory_count == 1
    assert [record["batch_ordinal"] for record in records[1:]] == [1, 2]
    assert records[1]["batch_id"] != records[2]["batch_id"]
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 2


class _FailingImporter:
    def __init__(self, checkpoint: ImportCheckpoint | None = None) -> None:
        self._checkpoint = checkpoint

    def import_checkpoint(self, source_path: Path, *, repo_key: str) -> ImportCheckpoint | None:
        return self._checkpoint

    def import_session(self, source_path: Path, **_kwargs: Any) -> object:
        raise RuntimeError("injected import failure")


def test_recovery_reuses_staged_identity_and_repairs_unterminated_tail(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    events = ({"kind": "message", "role": "user", "text": "Recover this turn."},)

    with pytest.raises(RuntimeError, match="injected import failure"):
        journal.commit(events, importer=_FailingImporter())
    staged = journal.staged_path.read_bytes()
    batch_id = json.loads(staged)["batch_id"]
    with journal.path.open("r+b") as handle:
        handle.truncate(journal.path.stat().st_size - len(staged) // 2)
        handle.flush()
        os.fsync(handle.fileno())

    runtime = _runtime(root)
    recovered = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1").recover(importer=runtime)
    records = tuple(json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines())

    assert recovered is not None
    assert recovered.created_memory_count == 1
    assert records[-1]["batch_id"] == batch_id
    assert journal.staged_path.exists() is False


def test_recovery_rejects_uncommitted_complete_batch_before_staged_fragment(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    journal.commit(({"kind": "message", "role": "user", "text": "Committed."},), importer=runtime)
    checkpoint = runtime.import_checkpoint(journal.path, repo_key="acme/widgets")

    with pytest.raises(RuntimeError, match="injected import failure"):
        journal.commit(({"kind": "message", "role": "user", "text": "Uncommitted complete."},), importer=_FailingImporter(checkpoint))
    uncommitted = journal.staged_path.read_bytes()
    staged_record = json.loads(uncommitted)
    staged_record["batch_id"] = f"batch_{'3' * 64}"
    staged_record["batch_ordinal"] = 3
    staged_record["events"][0]["text"] = "Staged fragment."
    staged = f"{json.dumps(staged_record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n".encode()
    journal.staged_path.write_bytes(staged)
    with journal.path.open("ab") as handle:
        handle.write(staged[: len(staged) // 2])
        handle.flush()
        os.fsync(handle.fileno())
    before = journal.path.read_bytes()

    with pytest.raises(PicoJournalError, match="uncommitted complete batch"):
        journal.recover(importer=runtime)

    assert journal.path.read_bytes() == before
    assert journal.staged_path.read_bytes() == staged
    assert runtime.import_checkpoint(journal.path, repo_key="acme/widgets") == checkpoint
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 1


def test_recovery_rejects_oversized_stage_before_creating_journal(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    event = {"arguments": {"payload": "x" * (240 * 1024)}, "call_id": "call", "kind": "tool_call", "tool_name": "tool"}
    batch = {
        "batch_id": f"batch_{'4' * 64}",
        "batch_ordinal": 1,
        "events": [event | {"call_id": f"call-{index}"} for index in range(18)],
        "record_type": "batch",
        "schema": "codecairn.pico.source.v1",
    }
    journal.staged_path.parent.mkdir(parents=True)
    journal.staged_path.write_bytes(f"{json.dumps(batch, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n".encode())
    assert journal.staged_path.stat().st_size > 4 * 1024 * 1024

    with pytest.raises(PicoJournalError, match="exceeds"):
        journal.recover(importer=_runtime(root))

    assert journal.path.exists() is False


def test_recovery_rejects_conflicting_complete_bytes_for_staged_identity(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    with pytest.raises(RuntimeError, match="injected import failure"):
        journal.commit(({"kind": "message", "role": "user", "text": "Original."},), importer=_FailingImporter())
    records = journal.path.read_text(encoding="utf-8").splitlines()
    batch = json.loads(records[-1])
    batch["events"][0]["text"] = "Conflicting."
    records[-1] = json.dumps(batch, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    journal.path.write_text("\n".join(records) + "\n", encoding="utf-8")

    with pytest.raises(PicoJournalError, match="identity conflicts"):
        journal.recover(importer=_runtime(root))

    assert journal.staged_path.exists() is True


def test_unterminated_tail_without_stage_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    journal.commit(({"kind": "message", "role": "user", "text": "Committed."},), importer=runtime)
    with journal.path.open("ab") as handle:
        handle.write(b'{"partial":')
        handle.flush()
        os.fsync(handle.fileno())

    with pytest.raises(PicoJournalError, match="unterminated record"):
        journal.commit(({"kind": "message", "role": "user", "text": "New."},), importer=runtime)


def test_stage_is_published_atomically_and_orphan_temp_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    with journal._locked_directory():
        pass
    temporary = journal.staged_path.with_name(f"{journal.staged_path.name}.tmp")
    temporary.write_bytes(b'{"partial":')

    with pytest.raises(RuntimeError, match="injected import failure"):
        journal.commit(({"kind": "message", "role": "user", "text": "Recoverable."},), importer=_FailingImporter())

    assert json.loads(journal.staged_path.read_text())["events"][0]["text"] == "Recoverable."
    assert temporary.exists() is False


def test_partial_header_is_atomically_replaced_during_recovery(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    with pytest.raises(RuntimeError, match="injected import failure"):
        journal.commit(({"kind": "message", "role": "user", "text": "Recoverable."},), importer=_FailingImporter())
    journal.path.write_bytes(b'{"partial":')

    result = journal.recover(importer=_runtime(root))

    assert result is not None
    assert result.created_memory_count == 1
    assert json.loads(journal.path.read_text().splitlines()[0])["record_type"] == "header"


def test_namespace_symlink_is_rejected_before_external_files_are_created(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "pico").symlink_to(outside, target_is_directory=True)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")

    with pytest.raises(PicoJournalError, match="symbolic links"):
        journal.commit(({"kind": "message", "role": "user", "text": "Stay inside."},), importer=_FailingImporter())

    assert tuple(outside.iterdir()) == ()


def test_namespace_swap_after_open_cannot_redirect_journal_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    original = pico_journal._open_directory

    def swap_after_open(runtime_root: Path, components: tuple[str, ...]) -> int:
        descriptor = original(runtime_root, components)
        namespace = journal.path.parent
        moved = namespace.with_name(f"{namespace.name}.moved")
        namespace.rename(moved)
        namespace.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(pico_journal, "_open_directory", swap_after_open)
    with pytest.raises(RuntimeError, match="injected import failure"):
        journal.commit(({"kind": "message", "role": "user", "text": "Pinned."},), importer=_FailingImporter())

    assert tuple(outside.iterdir()) == ()


def test_writer_rejects_importer_bounds_before_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    journal.commit(({"kind": "message", "role": "user", "text": "Committed."},), importer=runtime)
    current_size = journal.path.stat().st_size
    monkeypatch.setattr(pico_journal, "MAX_SESSION_BYTES", current_size + 64)
    monkeypatch.setattr(jsonl_importer, "MAX_SESSION_BYTES", current_size + 64)

    with pytest.raises(PicoJournalError, match="byte limit"):
        journal.commit(({"kind": "message", "role": "user", "text": "x" * 128},), importer=runtime)

    assert journal.staged_path.exists() is False
    assert journal.path.stat().st_size == current_size


def test_writer_rejects_cumulative_file_changes_before_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    change = {"operation": "update", "path": "one.py"}
    event = {"kind": "tool_result", "call_id": "call", "terminal_observation": {"file_changes": [change]}}
    monkeypatch.setattr(pico_journal, "MAX_PICO_SESSION_FILE_CHANGES", 1)
    monkeypatch.setattr(pico_importer, "MAX_PICO_SESSION_FILE_CHANGES", 1)
    journal.commit(
        (
            {"kind": "message", "role": "user", "text": "First."},
            {"kind": "tool_call", "call_id": "call", "tool_name": "edit", "arguments": {}},
            event,
        ),
        importer=runtime,
    )

    with pytest.raises(PicoJournalError, match="file changes"):
        journal.commit(
            (
                {"kind": "message", "role": "user", "text": "Second."},
                {"kind": "tool_call", "call_id": "call-2", "tool_name": "edit", "arguments": {}},
                event | {"call_id": "call-2"},
            ),
            importer=runtime,
        )

    assert journal.staged_path.exists() is False
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 1


def test_writer_rejects_multiple_user_openings_before_staging(tmp_path: Path) -> None:
    journal = PicoSourceJournal(tmp_path / "runtime", repo_key="acme/widgets", session_id="session-1")

    with pytest.raises(PicoJournalError, match="exactly one user task opening"):
        journal.commit(
            ({"kind": "message", "role": "user", "text": "First."}, {"kind": "message", "role": "user", "text": "Second."}),
            importer=_FailingImporter(),
        )

    assert journal.path.exists() is False
    assert journal.staged_path.exists() is False


@pytest.mark.parametrize(
    "events",
    [
        (),
        ({"kind": "message", "role": "assistant", "text": "No task opening."},),
        ({"kind": "message", "role": "invalid", "text": "text"},),
        ({"kind": "message", "role": "user", "text": "x" * 32_769},),
        ({"kind": "message", "role": "user", "text": "界" * 10_923},),
        ({"kind": "message", "role": "user", "text": "not\r\nnormalized"},),
        ({"kind": "message", "role": "user", "text": "text", "claimed_exit_code": 0},),
        ({"kind": "tool_call", "call_id": "call-1", "tool_name": "shell", "arguments": {}, "command": "x" * 4_097},),
        (
            {
                "kind": "tool_result",
                "call_id": "call-1",
                "terminal_observation": {"file_changes": [{"operation": "update", "path": "../escape"}]},
            },
        ),
        (
            {
                "kind": "tool_result",
                "call_id": "call-1",
                "terminal_observation": {"file_changes": [{"operation": "move", "path": "one"}]},
            },
        ),
    ],
)
def test_journal_rejects_unbounded_or_malformed_batches(tmp_path: Path, events: tuple[dict[str, object], ...]) -> None:
    journal = PicoSourceJournal(tmp_path / "runtime", repo_key="acme/widgets", session_id="session-1")

    with pytest.raises(PicoJournalError):
        journal.commit(events, importer=_FailingImporter())

    assert journal.path.exists() is False
    assert journal.staged_path.exists() is False


@pytest.mark.parametrize("session_id", ["x" * 257, "unsafe\u007f", "unsafe\u2028"])
def test_journal_rejects_session_identity_before_writing(tmp_path: Path, session_id: str) -> None:
    root = tmp_path / "runtime"

    with pytest.raises(PicoJournalError, match="session_id"):
        PicoSourceJournal(root, repo_key="acme/widgets", session_id=session_id)

    assert root.exists() is False


@pytest.mark.parametrize("source_generation", [2, True])
def test_journal_rejects_unsupported_source_generation_before_writing(tmp_path: Path, source_generation: int) -> None:
    root = tmp_path / "runtime"

    with pytest.raises(PicoJournalError, match="source_generation"):
        PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1", source_generation=source_generation)

    assert root.exists() is False


def test_rejected_batch_without_user_opening_does_not_poison_follow_up(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")

    with pytest.raises(PicoJournalError, match="user task opening"):
        journal.commit(({"kind": "message", "role": "assistant", "text": "No task."},), importer=runtime)
    result = journal.commit(({"kind": "message", "role": "user", "text": "Valid task."},), importer=runtime)

    assert result.created_memory_count == 1
    assert journal.staged_path.exists() is False


@pytest.mark.parametrize("rewrite", ["mutate", "truncate"])
def test_committed_prefix_conflict_fails_before_a_new_batch(tmp_path: Path, rewrite: str) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    journal = PicoSourceJournal(root, repo_key="acme/widgets", session_id="session-1")
    journal.commit(({"kind": "message", "role": "user", "text": "Committed."},), importer=runtime)
    before = runtime.import_checkpoint(journal.path, repo_key="acme/widgets")
    source = journal.path.read_bytes()
    if rewrite == "mutate":
        journal.path.write_bytes(source.replace(b"Committed.", b"Rewritten."))
    else:
        journal.path.write_bytes(source.splitlines(keepends=True)[0])

    with pytest.raises(SourceRewritten):
        journal.commit(({"kind": "message", "role": "user", "text": "New turn."},), importer=runtime)

    assert runtime.import_checkpoint(journal.path, repo_key="acme/widgets") == before
    assert journal.staged_path.exists() is False
