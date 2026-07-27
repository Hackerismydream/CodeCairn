from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codecairn.importers import SessionImporter
from codecairn.memory.schema import LegacyRootUnsupported, TaskExperiencePayload
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState

FIXTURES = Path(__file__).parent / "fixtures"


def _runtime(root: Path) -> MemoryRuntime:
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(root),
        state=SQLiteState(root / "state.sqlite3"),
    )


@pytest.mark.parametrize(
    ("fixture", "provider"),
    [
        ("codex/failed_command.jsonl", "codex"),
        ("claude/failed_command.jsonl", "claude"),
    ],
)
def test_import_creates_one_auditable_task_experience(
    tmp_path: Path,
    fixture: str,
    provider: str,
) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)

    result = runtime.import_session(
        FIXTURES / fixture,
        repo_key="acme/widgets",
    )
    memories = runtime.list_memories(repo_key="acme/widgets")

    assert result.provider == provider
    assert result.created_memory_count == 1
    assert len(memories) == 1
    memory = memories[0]
    assert memory.memory_type == "task_experience"
    assert isinstance(memory.payload, TaskExperiencePayload)
    assert memory.payload.outcome == "failure"
    assert {fact.fact_kind for fact in memory.facts} >= {
        "message",
        "command",
        "command_result",
        "verification",
    }
    assert all(reference.fact_id.startswith("fact_") for reference in memory.evidence)
    artifact = MarkdownMemoryStore(root).read(MarkdownMemoryStore(root).path_for(memory))
    assert artifact.memory == memory


def test_repeat_import_is_idempotent(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "runtime")
    source = FIXTURES / "codex/failed_command.jsonl"

    first = runtime.import_session(source, repo_key="acme/widgets")
    second = runtime.import_session(source, repo_key="acme/widgets")

    assert first.created_memory_count == 1
    assert second.created_memory_count == 0
    assert second.skipped_memory_count == 1
    assert len(runtime.list_memories(repo_key="acme/widgets")) == 1


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
