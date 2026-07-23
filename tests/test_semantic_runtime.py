from __future__ import annotations

from pathlib import Path

import pytest

from codecairn.bootstrap import create_clause_projection_adapter, create_runtime
from codecairn.memory.episode import (
    AttributedEpisode,
    AttributedTurn,
    LosslessEpisodeSemanticizer,
)
from codecairn.memory.models import EvidenceReference
from codecairn.memory.semantic import ClauseDraft, ProjectionIdentity, ProjectionSource


def test_runtime_projection_cache_survives_runtime_recreation(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first_adapter = _CountingAdapter()
    first = create_runtime(root, clause_adapter=first_adapter).write_episode(_episode())

    assert first.accepted is True
    assert len(first_adapter.calls) == 1

    second_adapter = _CountingAdapter()
    second = create_runtime(root, clause_adapter=second_adapter).write_episode(_episode())

    assert second.accepted is True
    assert second_adapter.calls == []
    assert second.memory == first.memory


def test_default_runtime_replays_historical_lossless_episode_without_conflict(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    historical_default = LosslessEpisodeSemanticizer()
    assert historical_default.semanticizer_id == "codecairn/lossless-episode"

    first = create_runtime(
        root,
        episode_semanticizer=historical_default,
    ).write_episode(_episode())
    replay = create_runtime(root).write_episode(_episode())

    assert first.accepted is True
    assert replay.accepted is True
    assert replay.memory == first.memory
    assert create_runtime(root).list_memories(repo_key=_episode().repo_key) == (first.memory,)


def test_runtime_keeps_the_answer_without_indexing_the_question_as_an_atomic_fact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    episode = _question_answer_episode()

    decision = create_runtime(
        root,
        clause_adapter=_AnswerOnlyAdapter(),
    ).write_episode(episode)

    assert decision.accepted is True
    assert decision.memory is not None
    assert [fact.text for fact in decision.memory.facts] == [
        "What kind of jobs are you thinking of?",
        "I am considering counseling or working in mental health.",
    ]
    semantic_episode = decision.memory.semantic_episode
    assert semantic_episode is not None
    assert semantic_episode.source_fact_ids == decision.memory.fact_ids
    assert len(semantic_episode.atomic_facts) == 1
    answer = semantic_episode.atomic_facts[0]
    assert answer.text == "Caroline is considering counseling or mental-health work."
    assert answer.source_fact_ids == (decision.memory.facts[1].fact_id,)
    assert decision.memory.facts[0].fact_id not in answer.source_fact_ids


def test_runtime_persists_a_filler_episode_without_atomic_facts(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    episode = _filler_episode()

    decision = create_runtime(
        root,
        clause_adapter=_NoDurableClauseAdapter(),
    ).write_episode(episode)

    assert decision.accepted is True
    assert decision.memory is not None
    assert [fact.text for fact in decision.memory.facts] == [
        "Hey, good to see you!",
        "Thanks, talk to you soon!",
    ]
    semantic_episode = decision.memory.semantic_episode
    assert semantic_episode is not None
    assert semantic_episode.atomic_facts == ()
    assert semantic_episode.source_fact_ids == decision.memory.fact_ids
    assert semantic_episode.narrative == (
        "2023-05-08T13:56:00+00:00 — Melanie: Hey, good to see you!\n"
        "2023-05-08T13:57:00+00:00 — Caroline: Thanks, talk to you soon!"
    )

    restored = create_runtime(root).list_memories(repo_key=episode.repo_key)

    assert restored == (decision.memory,)


def test_runtime_rejects_two_semantic_projection_strategies(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only one semantic projection strategy"):
        create_runtime(
            tmp_path / "runtime",
            episode_semanticizer=LosslessEpisodeSemanticizer(),
            clause_adapter=_CountingAdapter(),
        )


def test_structured_projection_profile_resolves_without_exposing_its_key() -> None:
    adapter = create_clause_projection_adapter(
        environment={
            "CODECAIRN_SEMANTICIZER_PROFILE": "structured",
            "DEEPSEEK_API_KEY": "semantic-secret",
        }
    )

    assert adapter.identity.adapter_id == "codecairn/structured-model-clause"
    assert adapter.identity.model_id == "deepseek-v4-flash"
    assert hasattr(adapter, "public_config")
    assert "semantic-secret" not in str(adapter.public_config)


def test_explicit_lossless_clause_profile_keeps_the_grounded_clause_identity() -> None:
    adapter = create_clause_projection_adapter(
        environment={"CODECAIRN_SEMANTICIZER_PROFILE": "lossless"}
    )

    assert adapter.identity.adapter_id == "codecairn/lossless-clause"
    assert adapter.identity.revision == "v1"


class _CountingAdapter:
    identity = ProjectionIdentity(
        adapter_id="test/counting-clause",
        revision="v1",
    )

    def __init__(self) -> None:
        self.calls: list[ProjectionSource] = []

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        self.calls.append(source)
        return tuple(
            ClauseDraft(
                text=(f"{fact.actor}: {fact.text}" if fact.actor is not None else fact.text),
                source_fact_ids=(fact.fact_id,),
            )
            for fact in source.facts
        )


class _AnswerOnlyAdapter:
    identity = ProjectionIdentity(
        adapter_id="test/answer-only-clause",
        revision="v1",
    )

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        answer = source.facts[1]
        return (
            ClauseDraft(
                text="Caroline is considering counseling or mental-health work.",
                source_fact_ids=(answer.fact_id,),
            ),
        )


class _NoDurableClauseAdapter:
    identity = ProjectionIdentity(
        adapter_id="test/no-durable-clause",
        revision="v1",
    )

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        return ()


def _episode() -> AttributedEpisode:
    return AttributedEpisode(
        repo_key="locomo/conv-test",
        source_episode_id="session-1",
        title="Conversation session 1",
        turns=(
            AttributedTurn(
                turn_id="turn-1",
                actor="Caroline",
                role="participant",
                text="I adopted a beagle named Poppy.",
                occurred_at="2023-05-08T13:56:00+00:00",
                evidence=EvidenceReference(
                    provider="locomo",
                    session_id="conv-test/session-1",
                    source_path="locomo://fixture/conv-test/session-1",
                    raw_event_sha256="1" * 64,
                    raw_event_index=1,
                    raw_event_type="locomo_turn",
                ),
            ),
        ),
    )


def _question_answer_episode() -> AttributedEpisode:
    return _episode_with_turns(
        source_episode_id="question-answer-session",
        turns=(
            (
                "question",
                "Melanie",
                "What kind of jobs are you thinking of?",
                "2023-05-08T13:56:00+00:00",
            ),
            (
                "answer",
                "Caroline",
                "I am considering counseling or working in mental health.",
                "2023-05-08T13:57:00+00:00",
            ),
        ),
    )


def _filler_episode() -> AttributedEpisode:
    return _episode_with_turns(
        source_episode_id="filler-session",
        turns=(
            (
                "greeting",
                "Melanie",
                "Hey, good to see you!",
                "2023-05-08T13:56:00+00:00",
            ),
            (
                "goodbye",
                "Caroline",
                "Thanks, talk to you soon!",
                "2023-05-08T13:57:00+00:00",
            ),
        ),
    )


def _episode_with_turns(
    *,
    source_episode_id: str,
    turns: tuple[tuple[str, str, str, str], ...],
) -> AttributedEpisode:
    return AttributedEpisode(
        repo_key="locomo/conv-test",
        source_episode_id=source_episode_id,
        title=f"Conversation {source_episode_id}",
        turns=tuple(
            AttributedTurn(
                turn_id=turn_id,
                actor=actor,
                role="participant",
                text=text,
                occurred_at=occurred_at,
                evidence=EvidenceReference(
                    provider="locomo",
                    session_id=f"conv-test/{source_episode_id}",
                    source_path=f"locomo://fixture/conv-test/{source_episode_id}",
                    raw_event_sha256=f"{position:064x}",
                    raw_event_index=position,
                    raw_event_type="locomo_turn",
                ),
            )
            for position, (turn_id, actor, text, occurred_at) in enumerate(turns, start=1)
        ),
    )
