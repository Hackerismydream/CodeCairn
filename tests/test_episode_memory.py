from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.memory.episode import AttributedEpisode, AttributedTurn
from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    SemanticAtomicFact,
    SemanticEpisode,
)
from codecairn.memory.semantic import ClauseDraft, ProjectionIdentity, ProjectionSource


def test_attributed_episode_round_trips_exact_truth_and_semantic_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    episode = _episode()

    decision = runtime.write_episode(episode)

    assert decision.accepted is True
    assert decision.memory is not None
    assert decision.memory.memory_type == "conversation_episode"
    assert [fact.text for fact in decision.memory.facts] == [
        "I adopted a beagle named Poppy.",
        "Poppy sounds wonderful.",
        "We finished the charity race.",
    ]
    assert [fact.actor for fact in decision.memory.facts] == [
        "Caroline",
        "Melanie",
        "Caroline",
    ]
    assert {fact.occurred_at for fact in decision.memory.facts} == {"2023-05-08T13:56:00+00:00"}
    assert decision.memory.semantic_episode is not None
    assert decision.memory.semantic_episode.source_fact_ids == decision.memory.fact_ids
    assert "Melanie: Poppy sounds wonderful." in decision.memory.semantic_episode.narrative

    restored = create_runtime(root).list_memories(repo_key=episode.repo_key)

    assert restored == (decision.memory,)


def test_reconcile_completes_an_episode_write_interrupted_after_markdown(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    original_store = runtime._markdown

    class CrashAfterWriteStore:
        def prepare(self, memory: CodingMemory) -> CodingMemory:
            return original_store.prepare(memory)

        def write(self, memory: CodingMemory) -> CodingMemory:
            original_store.write(memory)
            raise RuntimeError("simulated process interruption")

    runtime._markdown = CrashAfterWriteStore()

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        runtime.write_episode(_episode())

    assert runtime.list_memories(repo_key="locomo/conv-test") == ()
    assert runtime.list_gate_audits(repo_key="locomo/conv-test") == ()
    assert runtime._state.operational_counts().pending_recovery_count == 1

    report = create_cascade(root).reconcile()

    assert report.created == 1
    recovered = create_runtime(root)
    memories = recovered.list_memories(repo_key="locomo/conv-test")
    assert len(memories) == 1
    assert memories[0].semantic_episode is not None
    assert len(recovered.list_gate_audits(repo_key="locomo/conv-test")) == 1
    assert recovered._state.operational_counts().pending_recovery_count == 0

    repeated = recovered.write_episode(_episode())

    assert repeated.accepted is True
    assert repeated.memory == memories[0]


def test_attributed_episode_canonicalizes_turns_by_source_event_order(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    episode = _episode()
    reversed_episode = AttributedEpisode(
        repo_key=episode.repo_key,
        source_episode_id=episode.source_episode_id,
        title=episode.title,
        turns=tuple(reversed(episode.turns)),
    )

    decision = create_runtime(root).write_episode(reversed_episode)

    assert decision.accepted is True
    assert decision.memory is not None
    assert [fact.text for fact in decision.memory.facts] == [
        "I adopted a beagle named Poppy.",
        "Poppy sounds wonderful.",
        "We finished the charity race.",
    ]
    assert decision.memory.semantic_episode is not None
    assert decision.memory.semantic_episode.source_fact_ids == decision.memory.fact_ids


def test_disconnected_episode_rejects_before_semantic_projection(tmp_path: Path) -> None:
    episode = _episode()
    second = episode.turns[1]
    disconnected = replace(
        episode,
        turns=(
            episode.turns[0],
            replace(
                second,
                evidence=replace(
                    second.evidence,
                    session_id="conv-test/session-2",
                    source_path="locomo://fixture/conv-test/session-2",
                ),
            ),
            episode.turns[2],
        ),
    )
    adapter = _CountingClauseAdapter()

    decision = create_runtime(
        tmp_path / "runtime",
        clause_adapter=adapter,
    ).write_episode(disconnected)

    assert decision.accepted is False
    assert decision.reason == "conversation_episode_facts_are_disconnected"
    assert adapter.calls == []


def test_corrected_episode_source_evidence_uses_a_new_auditable_attempt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    episode = _episode()
    second = episode.turns[1]
    disconnected = replace(
        episode,
        turns=(
            episode.turns[0],
            replace(
                second,
                evidence=replace(
                    second.evidence,
                    session_id="conv-test/session-2",
                    source_path="locomo://fixture/conv-test/session-2",
                ),
            ),
            episode.turns[2],
        ),
    )
    runtime = create_runtime(root)

    rejected = runtime.write_episode(disconnected)
    accepted = runtime.write_episode(episode)

    assert rejected.accepted is False
    assert accepted.accepted is True
    assert accepted.memory is not None
    audits = runtime.list_gate_audits(repo_key=episode.repo_key)
    assert [(audit.accepted, audit.reason) for audit in audits] == [
        (False, "conversation_episode_facts_are_disconnected"),
        (True, "accepted"),
    ]
    assert len({audit.proposal_id for audit in audits}) == 2
    assert create_cascade(root).reconcile().created == 0
    assert runtime.list_memories(repo_key=episode.repo_key) == (accepted.memory,)


def test_episode_keeps_source_truth_when_projection_has_no_durable_atomic_facts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    episode = _single_turn_episode(
        source_episode_id="small-talk",
        actor="Alice",
        text="Thanks!",
        event_index=1,
    )

    decision = create_runtime(
        root,
        clause_adapter=_NoDurableClauseAdapter(),
    ).write_episode(episode)

    assert decision.accepted is True
    assert decision.memory is not None
    assert tuple(fact.text for fact in decision.memory.facts) == ("Thanks!",)
    assert decision.memory.semantic_episode is not None
    assert decision.memory.semantic_episode.atomic_facts == ()
    assert decision.memory.semantic_episode.source_fact_ids == decision.memory.fact_ids
    assert create_runtime(root).list_memories(repo_key=episode.repo_key) == (decision.memory,)
    assert create_cascade(root).rebuild().parity is True


def test_fact_hit_compiles_all_grounded_excerpts_before_parent_hydration(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    decision = runtime.write_episode(_episode())
    assert decision.accepted is True
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall("Poppy sounds wonderful", repo_key="locomo/conv-test", limit=1)

    assert "Caroline: I adopted a beagle named Poppy." in recalled.markdown
    assert "Melanie: Poppy sounds wonderful." in recalled.markdown
    assert "Caroline: We finished the charity race." in recalled.markdown
    assert recalled.sidecar.hydrated_episode_count == 0
    assert recalled.sidecar.partial_episode_ids == (recalled.sidecar.ranked[0].memory_id,)
    assert recalled.sidecar.dropped_episode_ids == ()
    assert recalled.sidecar.context_trace is not None
    assert recalled.sidecar.context_trace.renderer == "facts-first-round-robin-v4"
    assert len(recalled.sidecar.context_trace.rendered_fact_ids) == 3
    assert recalled.sidecar.ranked[0].episode_text == ""


def test_procedure_query_uses_remaining_budget_for_complete_parent_episode(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    assert runtime.write_episode(_episode()).accepted is True
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall(
        "How did Caroline finish the charity race?",
        repo_key="locomo/conv-test",
        limit=1,
    )

    assert "Evidence excerpts:" in recalled.markdown
    assert "Complete parent episode:" in recalled.markdown
    assert recalled.sidecar.hydrated_episode_count == 1
    assert recalled.sidecar.partial_episode_ids == ()
    assert recalled.sidecar.dropped_episode_ids == ()
    assert recalled.sidecar.context_trace is not None
    assert set(recalled.sidecar.context_trace.rendered_fact_ids) == set(
        recalled.sidecar.ranked[0].episode_fact_ids
    )
    assert all(
        f"[{fact_id}]" in recalled.markdown
        for fact_id in recalled.sidecar.context_trace.rendered_fact_ids
    )
    assert {snippet.fact_id for snippet in recalled.sidecar.ranked[0].snippets} == set(
        recalled.sidecar.ranked[0].episode_fact_ids
    )


def test_multi_anchor_query_keeps_distinct_evidence_parents(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    alice = _single_turn_episode(
        source_episode_id="alice-session",
        actor="Alice",
        text="I adopted a beagle named Poppy.",
        event_index=1,
    )
    bob = _single_turn_episode(
        source_episode_id="bob-session",
        actor="Bob",
        text="I finished the charity marathon.",
        event_index=2,
    )
    assert runtime.write_episode(alice).accepted is True
    assert runtime.write_episode(bob).accepted is True
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall(
        "What did Alice adopt and Bob finish?",
        repo_key="locomo/conv-test",
        limit=2,
    )

    assert set(recalled.sidecar.covered_slots) == {"alice", "bob"}
    assert recalled.sidecar.missing_slots == ()
    assert "Alice: I adopted a beagle named Poppy." in recalled.markdown
    assert "Bob: I finished the charity marathon." in recalled.markdown
    assert recalled.sidecar.hydrated_episode_count == 0
    assert recalled.sidecar.context_trace is not None
    assert set(recalled.sidecar.context_trace.rendered_memory_ids) == {
        item.memory_id for item in recalled.sidecar.ranked
    }


def test_explicit_session_adjacency_expands_neighbor_episodes(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    episodes = (
        replace(
            _single_turn_episode(
                source_episode_id="session-1",
                actor="Alice",
                text="Alice booked the venue on Monday.",
                event_index=1,
            ),
            adjacency_group_id="conv-test",
            adjacency_index=0,
        ),
        replace(
            _single_turn_episode(
                source_episode_id="session-2",
                actor="Bob",
                text="Bob chose blue flowers on Tuesday.",
                event_index=2,
            ),
            adjacency_group_id="conv-test",
            adjacency_index=1,
        ),
        replace(
            _single_turn_episode(
                source_episode_id="session-3",
                actor="Carol",
                text="Carol ordered the cake on Wednesday.",
                event_index=3,
            ),
            adjacency_group_id="conv-test",
            adjacency_index=2,
        ),
        replace(
            _single_turn_episode(
                source_episode_id="session-5",
                actor="Dana",
                text="Dana confirmed the guest list on Friday.",
                event_index=5,
            ),
            adjacency_group_id="conv-test",
            adjacency_index=4,
        ),
    )
    memories = []
    for episode in episodes:
        decision = runtime.write_episode(episode)
        assert decision.accepted is True
        assert decision.memory is not None
        memories.append(decision.memory)
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall(
        "When did Bob choose blue flowers?",
        repo_key="locomo/conv-test",
        limit=1,
    )

    assert recalled.sidecar.ranked[0].memory_id == memories[1].memory_id
    assert recalled.sidecar.neighbor_expansion_count == 2
    assert "Alice booked the venue on Monday." in recalled.markdown
    assert "Carol ordered the cake on Wednesday." in recalled.markdown
    assert "Dana confirmed the guest list on Friday." not in recalled.markdown


def test_recall_context_honestly_omits_source_facts_larger_than_the_budget(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    alice = _single_turn_episode(
        source_episode_id="alice-large-session",
        actor="Alice",
        text="Alice adopted Poppy the beagle. " + "alice-filler " * 1_000,
        event_index=1,
    )
    bob = _single_turn_episode(
        source_episode_id="bob-large-session",
        actor="Bob",
        text="Bob finished the charity marathon. " + "bob-filler " * 1_000,
        event_index=2,
    )
    alice_decision = runtime.write_episode(alice)
    bob_decision = runtime.write_episode(bob)
    assert alice_decision.accepted is True
    assert bob_decision.accepted is True
    assert alice_decision.memory is not None
    assert bob_decision.memory is not None
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall(
        "What did Alice adopt and Bob finish?",
        repo_key="locomo/conv-test",
        limit=2,
    )

    assert "Alice adopted Poppy the beagle." not in recalled.markdown
    assert "Bob finished the charity marathon." not in recalled.markdown
    assert set(recalled.sidecar.dropped_episode_ids) == {
        item.memory_id for item in recalled.sidecar.ranked
    }
    assert {fact_id for item in recalled.sidecar.ranked for fact_id in item.episode_fact_ids} == {
        fact.fact_id
        for decision in (alice_decision, bob_decision)
        for fact in decision.memory.facts
    }
    assert recalled.sidecar.context_trace is not None
    assert recalled.sidecar.context_trace.rendered_memory_ids == ()
    assert len(recalled.sidecar.context_trace.omitted_fact_ids) == 2
    assert recalled.sidecar.context_trace.token_count <= 4_000
    assert recalled.sidecar.context_trace.char_count == len(recalled.markdown)


def test_semantic_annotation_cannot_reference_unknown_evidence(tmp_path: Path) -> None:
    class ForgedSemanticizer:
        semanticizer_id = "test/forged"
        revision = "v1"

        def compile(
            self,
            facts: tuple[EvidenceFact, ...],
            *,
            episode_id: str,
        ) -> SemanticEpisode:
            return SemanticEpisode(
                episode_id=episode_id,
                narrative="A forged annotation.",
                atomic_facts=(
                    SemanticAtomicFact(
                        fact_id="forged",
                        text="A forged fact.",
                        source_fact_ids=("missing-source-fact",),
                    ),
                ),
                source_fact_ids=tuple(fact.fact_id for fact in facts),
                semanticizer_id=self.semanticizer_id,
                revision=self.revision,
            )

    root = tmp_path / "runtime"
    runtime = create_runtime(
        root,
        episode_semanticizer=ForgedSemanticizer(),
    )

    decision = runtime.write_episode(_episode())

    assert decision.accepted is False
    assert decision.reason == "semantic_episode_invalid"
    assert runtime.list_memories(repo_key="locomo/conv-test") == ()

    retried = create_runtime(root).write_episode(_episode())

    assert retried.accepted is True
    assert retried.memory is not None
    audits = runtime.list_gate_audits(repo_key="locomo/conv-test")
    assert [(audit.accepted, audit.reason) for audit in audits] == [
        (False, "semantic_episode_invalid"),
        (True, "accepted"),
    ]
    assert len({audit.proposal_id for audit in audits}) == 2


def _episode() -> AttributedEpisode:
    occurred_at = "2023-05-08T13:56:00+00:00"
    turns = tuple(
        AttributedTurn(
            turn_id=f"turn-{position}",
            actor=actor,
            role="user",
            text=text,
            occurred_at=occurred_at,
            evidence=EvidenceReference(
                provider="locomo",
                session_id="conv-test/session-1",
                source_path="locomo://fixture/conv-test/session-1",
                raw_event_sha256=f"{position:064x}",
                raw_event_index=position,
                raw_event_type="locomo_turn",
            ),
        )
        for position, (actor, text) in enumerate(
            (
                ("Caroline", "I adopted a beagle named Poppy."),
                ("Melanie", "Poppy sounds wonderful."),
                ("Caroline", "We finished the charity race."),
            )
        )
    )
    return AttributedEpisode(
        repo_key="locomo/conv-test",
        source_episode_id="session-1",
        title="Conversation session 1 on 2023-05-08",
        turns=turns,
    )


def _single_turn_episode(
    *,
    source_episode_id: str,
    actor: str,
    text: str,
    event_index: int,
) -> AttributedEpisode:
    return AttributedEpisode(
        repo_key="locomo/conv-test",
        source_episode_id=source_episode_id,
        title=f"Conversation {source_episode_id}",
        turns=(
            AttributedTurn(
                turn_id=f"turn-{event_index}",
                actor=actor,
                role="participant",
                text=text,
                occurred_at="2023-05-08T13:56:00+00:00",
                evidence=EvidenceReference(
                    provider="locomo",
                    session_id=f"conv-test/{source_episode_id}",
                    source_path=f"locomo://fixture/conv-test/{source_episode_id}",
                    raw_event_sha256=f"{event_index:064x}",
                    raw_event_index=event_index,
                    raw_event_type="locomo_turn",
                ),
            ),
        ),
    )


class _NoDurableClauseAdapter:
    identity = ProjectionIdentity(
        adapter_id="test/no-durable-clauses",
        revision="v1",
    )

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        del source
        return ()


class _CountingClauseAdapter:
    identity = ProjectionIdentity(
        adapter_id="test/counting-clauses",
        revision="v1",
    )

    def __init__(self) -> None:
        self.calls: list[ProjectionSource] = []

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        self.calls.append(source)
        return tuple(
            ClauseDraft(text=fact.text, source_fact_ids=(fact.fact_id,)) for fact in source.facts
        )
