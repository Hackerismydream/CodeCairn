from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    RepositoryKnowledgePayload,
    SchemaInvalid,
    SourceLocation,
    SourceOrderKey,
    TaskExperiencePayload,
    UserPreferencePayload,
    WorkStatePayload,
    canonical_json,
    coding_memory_from_dict,
    coding_memory_to_dict,
    typed_id,
)
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def _fact() -> EvidenceFact:
    return EvidenceFact.create(
        repo_key="acme/widgets",
        location=SourceLocation(
            provider="codex",
            session_id="session-1",
            source_generation=1,
            event_index=0,
            event_id="event-0",
            source_path_sha256="0" * 64,
            event_sha256="1" * 64,
        ),
        fact_kind="message",
        role="user",
        value="Keep answers concise.",
        attributes={},
        episode_id=typed_id("ep", {"test": "episode"}),
    )


def _memories() -> tuple[CodingMemory, ...]:
    fact = _fact()
    order = SourceOrderKey(
        trusted_timestamp_ms=None,
        provider="codex",
        session_id="session-1",
        source_generation=1,
        event_index=0,
    )
    common = {
        "repo_key": "acme/widgets",
        "tags": (),
        "created_at_ms": 1,
        "restored_from": None,
        "restore_predecessor_id": None,
    }
    return (
        CodingMemory.create(
            **common,
            memory_type="task_experience",
            title="Keep answers concise",
            content="The user asked for concise answers.",
            category="other",
            episode_id=fact.episode_id,
            evidence=(fact.reference,),
            facts=(fact,),
            origin="capture",
            source_order_key=order,
            payload=TaskExperiencePayload(
                goal=fact.value,
                outcome="unknown",
                actions=(),
                result="No result was observed.",
                blockers=(),
                verification_fact_ids=(),
            ),
        ),
        CodingMemory.create(
            **common,
            memory_type="repository_knowledge",
            title="Checks",
            content="Run make check.",
            category="command",
            episode_id=None,
            evidence=(),
            facts=(),
            origin="agent_asserted",
            source_order_key=None,
            payload=RepositoryKnowledgePayload(
                subject_key="checks",
                claim="Run make check.",
            ),
        ),
        CodingMemory.create(
            **common,
            memory_type="user_preference",
            title="Answer style",
            content="Keep answers concise.",
            category="style",
            episode_id=None,
            evidence=(),
            facts=(),
            origin="agent_asserted",
            source_order_key=None,
            payload=UserPreferencePayload(
                subject_key="answer-style",
                preference="Keep answers concise.",
                source_fact_ids=(fact.fact_id,),
            ),
        ),
        CodingMemory.create(
            **common,
            memory_type="work_state",
            title="Release",
            content="The release evaluation is pending.",
            category="task",
            episode_id=None,
            evidence=(),
            facts=(),
            origin="agent_asserted",
            source_order_key=None,
            payload=WorkStatePayload(
                workstream_key="release",
                workstream_state="open",
                goal="Ship v0.1",
                progress="Implementation is ready.",
                blockers=(),
                next_step="Run evaluation.",
                terminal_outcome=None,
            ),
        ),
    )


def test_all_four_memory_types_round_trip_markdown_and_sqlite(tmp_path: Path) -> None:
    truth = MarkdownMemoryStore(tmp_path)
    state = SQLiteState(tmp_path / "state.sqlite3")
    state.store_source_facts((_fact(),))

    for memory in _memories():
        artifact = truth.write(memory)
        assert state.store_memory(artifact) is True
        assert state.store_memory(artifact) is False
        assert truth.read(artifact.path).memory == memory

    restored = state.list_memories(repo_key="acme/widgets")
    assert {memory.memory_type for memory in restored} == {
        "task_experience",
        "repository_knowledge",
        "user_preference",
        "work_state",
    }


def test_unknown_memory_type_and_field_are_rejected() -> None:
    encoded = coding_memory_to_dict(_memories()[1])
    encoded["memory_type"] = "verified_fix"
    with pytest.raises(SchemaInvalid, match="Unknown memory type"):
        coding_memory_from_dict(encoded)

    encoded = coding_memory_to_dict(_memories()[1])
    encoded["provider_attempt_id"] = "untrusted"
    with pytest.raises(SchemaInvalid, match="unknown"):
        coding_memory_from_dict(encoded)


def test_v01_1_memory_state_adds_episode_projection_column(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    memory = _memories()[1]
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE codecairn_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO codecairn_meta VALUES ('schema_revision', 'codecairn-v01-1');
            CREATE TABLE memories (
                repo_key TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                canonical_memory_json TEXT NOT NULL,
                markdown_path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                PRIMARY KEY (repo_key, memory_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory.repo_key,
                memory.memory_id,
                memory.memory_type,
                canonical_json(coding_memory_to_dict(memory)),
                "/old/memory.md",
                "0" * 64,
            ),
        )

    state = SQLiteState(database)

    assert state.list_memories(repo_key="acme/widgets") == (memory,)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
    assert "episode_id" in columns


def test_v01_3_state_upgrades_to_current_schema(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    SQLiteState(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE codecairn_meta SET value = 'codecairn-v01-3' WHERE key = 'schema_revision'"
        )

    SQLiteState(database)

    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT value FROM codecairn_meta WHERE key = 'schema_revision'"
        ).fetchone()
    assert revision == ("codecairn-v01-4",)
