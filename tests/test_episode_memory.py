from __future__ import annotations

from pathlib import Path

import pytest

from codecairn.bootstrap import create_runtime
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
    coding_memory_from_dict,
    coding_memory_to_dict,
    typed_id,
)
from codecairn.storage.markdown import MarkdownMemoryStore


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
    order = SourceOrderKey(trusted_timestamp_ms=None, provider="codex", session_id="session-1", source_generation=1, event_index=0)
    common = {"repo_key": "acme/widgets", "tags": (), "created_at_ms": 1, "restored_from": None, "restore_predecessor_id": None}
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
                goal=fact.value, outcome="unknown", actions=(), result="No result was observed.", blockers=(), verification_fact_ids=()
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
            payload=RepositoryKnowledgePayload(subject_key="checks", claim="Run make check."),
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
                subject_key="answer-style", preference="Keep answers concise.", source_fact_ids=(fact.fact_id,)
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
    fixture = Path(__file__).parent / "fixtures/codex/failed_command.jsonl"
    runtime = create_runtime(tmp_path)
    runtime.import_session(fixture, repo_key="acme/widgets", boundary_kind="manual_finalize")
    captured = runtime.list_memories(repo_key="acme/widgets")[0]
    user_fact = next(fact for fact in captured.facts if fact.role == "user")
    direct = _memories()[1:]
    preference = direct[1]
    preference = CodingMemory.create(
        repo_key=preference.repo_key,
        memory_type="user_preference",
        title=preference.title,
        content=preference.content,
        category=preference.category,
        tags=preference.tags,
        created_at_ms=preference.created_at_ms,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=UserPreferencePayload(
            subject_key="answer-style", preference="Keep answers concise.", source_fact_ids=(user_fact.fact_id,)
        ),
    )
    for memory in (direct[0], preference, direct[2]):
        assert runtime.store_memory(memory) == memory

    restored = runtime.list_memories(repo_key="acme/widgets")
    truth = MarkdownMemoryStore(tmp_path)
    scanned = tuple(sorted((artifact.memory for artifact in truth.scan().memories), key=lambda memory: memory.memory_id))
    assert scanned == tuple(sorted(restored, key=lambda memory: memory.memory_id))
    assert {memory.memory_type for memory in restored} == {"task_experience", "repository_knowledge", "user_preference", "work_state"}


def test_unknown_memory_type_and_field_are_rejected() -> None:
    encoded = coding_memory_to_dict(_memories()[1])
    encoded["memory_type"] = "verified_fix"
    with pytest.raises(SchemaInvalid, match="Unknown memory type"):
        coding_memory_from_dict(encoded)

    encoded = coding_memory_to_dict(_memories()[1])
    encoded["provider_attempt_id"] = "untrusted"
    with pytest.raises(SchemaInvalid, match="unknown"):
        coding_memory_from_dict(encoded)
