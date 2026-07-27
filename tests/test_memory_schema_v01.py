from __future__ import annotations

import pytest

from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    RepositoryKnowledgePayload,
    SchemaInvalid,
    SourceLocation,
    SourceOrderKey,
    TaskEpisode,
    TaskExperiencePayload,
    UserPreferencePayload,
    WorkStatePayload,
    coding_memory_from_dict,
    coding_memory_to_dict,
    normalize_machine_key,
    normalize_path_key,
    task_episode_from_dict,
    task_episode_to_dict,
)

_DIGEST = "0" * 64


def _episode() -> TaskEpisode:
    order = SourceOrderKey(
        trusted_timestamp_ms=1_750_000_000_000,
        provider="codex",
        session_id="session-1",
        source_generation=1,
        event_index=0,
    )
    return TaskEpisode.create(
        repo_key="acme/widgets",
        provider="codex",
        session_id="session-1",
        source_generation=1,
        start_event_index=0,
        end_event_index_exclusive=2,
        opening_event_id="event-0",
        boundary_kind="codex_stop",
        continues_episode_id=None,
        source_order_key=order,
        prefix_sha256=_DIGEST,
    )


def _user_fact(episode_id: str | None) -> EvidenceFact:
    return EvidenceFact.create(
        repo_key="acme/widgets",
        location=SourceLocation(
            provider="codex",
            session_id="session-1",
            source_generation=1,
            event_index=0,
            event_id="event-0",
            source_path_sha256=_DIGEST,
            event_sha256="1" * 64,
        ),
        fact_kind="message",
        role="user",
        value="Please run the full test suite.",
        attributes={},
        episode_id=episode_id,
    )


def test_episode_and_fact_ids_are_full_digest_and_stable() -> None:
    episode = _episode()
    before_assignment = _user_fact(None)
    after_assignment = _user_fact(episode.episode_id)

    assert len(episode.episode_id) == len("ep_") + 64
    assert before_assignment.fact_id == after_assignment.fact_id
    assert len(after_assignment.fact_id) == len("fact_") + 64


def test_capture_task_experience_has_one_episode_and_grounded_facets() -> None:
    episode = _episode()
    fact = _user_fact(episode.episode_id)
    memory = CodingMemory.create(
        repo_key=episode.repo_key,
        memory_type="task_experience",
        title="Run the full test suite",
        content="The user requested the complete repository checks.",
        category="evaluation",
        tags=("tests",),
        created_at_ms=1,
        episode_id=episode.episode_id,
        evidence=(fact.reference,),
        facts=(fact,),
        origin="capture",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=episode.source_order_key,
        payload=TaskExperiencePayload(
            goal=fact.value,
            outcome="unknown",
            actions=(),
            result="No result was observed.",
            blockers=(),
            verification_fact_ids=(),
        ),
    )

    assert len(memory.memory_id) == len("mem_") + 64
    assert memory.memory_type == "task_experience"


def test_direct_knowledge_and_work_state_do_not_synthesize_evidence() -> None:
    knowledge = CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="repository_knowledge",
        title="Checks",
        content="Run make check before committing.",
        category="command",
        tags=(),
        created_at_ms=1,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=RepositoryKnowledgePayload(
            subject_key="checks",
            claim="Run make check before committing.",
        ),
    )
    work_state = CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="work_state",
        title="Release",
        content="The release is waiting on evaluation.",
        category="task",
        tags=(),
        created_at_ms=1,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=WorkStatePayload(
            workstream_key="release",
            workstream_state="open",
            goal="Ship v0.1",
            progress="Implementation complete.",
            blockers=("Evaluation pending.",),
            next_step="Run evaluation.",
            terminal_outcome=None,
        ),
    )

    assert knowledge.evidence == ()
    assert work_state.facts == ()


def test_direct_preference_identity_uses_registry_fact_ids_without_snapshots() -> None:
    fact = _user_fact(None)
    memory = CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="user_preference",
        title="Verification preference",
        content="Run the full test suite before reporting completion.",
        category="workflow",
        tags=(),
        created_at_ms=1,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=UserPreferencePayload(
            subject_key="verification",
            preference="Run the full test suite before reporting completion.",
            source_fact_ids=(fact.fact_id,),
        ),
    )

    assert memory.payload.source_fact_ids == (fact.fact_id,)


def test_closed_payload_and_normalization_invariants_fail_early() -> None:
    with pytest.raises(SchemaInvalid):
        RepositoryKnowledgePayload(subject_key=" Build  Checks ", claim="Use make check.")
    with pytest.raises(SchemaInvalid):
        WorkStatePayload(
            workstream_key="release",
            workstream_state="open",
            goal="Ship",
            progress="Ready",
            blockers=(),
            next_step=None,
            terminal_outcome=None,
        )

    assert normalize_machine_key(" Build\u00a0 Checks ") == "build checks"
    assert normalize_path_key(r"src\codecairn\memory.py") == "src/codecairn/memory.py"
    with pytest.raises(SchemaInvalid):
        normalize_path_key("../outside")


def test_episode_and_memory_round_trip_reject_unknown_fields() -> None:
    episode = _episode()
    fact = _user_fact(episode.episode_id)
    memory = CodingMemory.create(
        repo_key=episode.repo_key,
        memory_type="task_experience",
        title="Remember the task",
        content="A deterministic task account.",
        category="other",
        tags=(),
        created_at_ms=2,
        episode_id=episode.episode_id,
        evidence=(fact.reference,),
        facts=(fact,),
        origin="capture",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=episode.source_order_key,
        payload=TaskExperiencePayload(
            goal=fact.value,
            outcome="unknown",
            actions=(),
            result="No result was observed.",
            blockers=(),
            verification_fact_ids=(),
        ),
    )

    assert task_episode_from_dict(task_episode_to_dict(episode)) == episode
    encoded = coding_memory_to_dict(memory)
    assert coding_memory_from_dict(encoded) == memory
    encoded["model_attempt_id"] = "provider-owned"
    with pytest.raises(SchemaInvalid, match="unknown"):
        coding_memory_from_dict(encoded)
