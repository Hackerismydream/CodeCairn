from __future__ import annotations

from pathlib import Path

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.memory.episode import AttributedEpisode, AttributedTurn
from codecairn.memory.models import (
    EvidenceFact,
    EvidenceReference,
    SemanticAtomicFact,
    SemanticEpisode,
)


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


def test_fact_hit_hydrates_the_complete_parent_episode(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    decision = runtime.write_episode(_episode())
    assert decision.accepted is True
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall("Poppy sounds wonderful", repo_key="locomo/conv-test", limit=1)

    assert "Caroline: I adopted a beagle named Poppy." in recalled.markdown
    assert "Melanie: Poppy sounds wonderful." in recalled.markdown
    assert "Caroline: We finished the charity race." in recalled.markdown
    assert recalled.sidecar.hydrated_episode_count == 1
    assert recalled.sidecar.partial_episode_ids == ()
    assert recalled.sidecar.dropped_episode_ids == ()
    assert recalled.sidecar.ranked[0].episode_text == ""


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
    assert recalled.sidecar.hydrated_episode_count == 2


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

    runtime = create_runtime(
        tmp_path / "runtime",
        episode_semanticizer=ForgedSemanticizer(),
    )

    decision = runtime.write_episode(_episode())

    assert decision.accepted is False
    assert decision.reason == "semantic_episode_invalid"
    assert runtime.list_memories(repo_key="locomo/conv-test") == ()


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
