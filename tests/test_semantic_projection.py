from __future__ import annotations

from dataclasses import replace

import pytest

from codecairn.memory.models import EvidenceFact, EvidenceReference
from codecairn.memory.semantic import (
    ClauseDraft,
    GroundedClauseSemanticizer,
    InMemoryProjectionCache,
    LosslessClauseProjectionAdapter,
    ProjectionIdentity,
    ProjectionSource,
)
from codecairn.memory.trace import stable_id


def test_lossless_projection_keeps_source_fact_grounding() -> None:
    fact = _fact(
        fact_id="fact-1",
        text="I adopted a beagle named Poppy.",
        actor="Caroline",
        raw_event_index=1,
    )
    semanticizer = GroundedClauseSemanticizer(
        adapter=LosslessClauseProjectionAdapter(),
    )

    projection = semanticizer.compile((fact,), episode_id="episode-1")

    assert projection.episode_id == "episode-1"
    assert projection.source_fact_ids == ("fact-1",)
    assert len(projection.atomic_facts) == 1
    assert projection.atomic_facts[0].text == "Caroline: I adopted a beagle named Poppy."
    assert projection.atomic_facts[0].source_fact_ids == ("fact-1",)
    assert projection.narrative == projection.atomic_facts[0].text


def test_projection_normalizes_and_orders_untrusted_drafts_on_the_host() -> None:
    facts = (
        _fact(fact_id="fact-2", text="Second source", actor="Bob", raw_event_index=2),
        _fact(fact_id="fact-1", text="First source", actor="Alice", raw_event_index=1),
    )
    adapter = _StaticAdapter(
        drafts=(
            ClauseDraft(text="  Zulu   clause  ", source_fact_ids=("fact-2",)),
            ClauseDraft(
                text="  Cafe\u0301\n  clause  ",
                source_fact_ids=("fact-2", "fact-1"),
            ),
        )
    )

    projection = GroundedClauseSemanticizer(adapter=adapter).compile(
        facts,
        episode_id="episode-1",
    )

    assert [fact.text for fact in projection.atomic_facts] == ["Café clause", "Zulu clause"]
    assert projection.atomic_facts[0].source_fact_ids == ("fact-1", "fact-2")
    assert projection.atomic_facts[0].fact_id == stable_id(
        "semantic-atomic-fact",
        "episode-1",
        "fact-1",
        "fact-2",
        "Café clause",
    )


def test_projection_rejects_unknown_source_fact_references() -> None:
    adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Forged clause", source_fact_ids=("missing-fact",)),)
    )

    with pytest.raises(ValueError, match="unknown source fact"):
        GroundedClauseSemanticizer(adapter=adapter).compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_allows_meaningful_clauses_to_cover_only_relevant_sources() -> None:
    adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Only the first source", source_fact_ids=("fact-1",)),)
    )

    projection = GroundedClauseSemanticizer(adapter=adapter).compile(
        (
            _fact(fact_id="fact-1", text="First", actor="Alice", raw_event_index=1),
            _fact(fact_id="fact-2", text="Thanks!", actor="Bob", raw_event_index=2),
        ),
        episode_id="episode-1",
    )

    assert projection.source_fact_ids == ("fact-1", "fact-2")
    assert tuple(fact.source_fact_ids for fact in projection.atomic_facts) == (("fact-1",),)


def test_projection_allows_a_source_episode_without_durable_atomic_facts() -> None:
    adapter = _StaticAdapter(drafts=())

    projection = GroundedClauseSemanticizer(adapter=adapter).compile(
        (_fact(fact_id="fact-1", text="Thanks!", actor="Alice", raw_event_index=1),),
        episode_id="episode-1",
    )

    assert projection.atomic_facts == ()
    assert projection.source_fact_ids == ("fact-1",)
    assert projection.narrative == "Alice: Thanks!"


def test_projection_rejects_duplicate_source_references() -> None:
    adapter = _StaticAdapter(
        drafts=(
            ClauseDraft(
                text="Repeated citation",
                source_fact_ids=("fact-1", "fact-1"),
            ),
        )
    )

    with pytest.raises(ValueError, match="duplicate source fact"):
        GroundedClauseSemanticizer(adapter=adapter).compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_rejects_duplicate_normalized_clauses() -> None:
    adapter = _StaticAdapter(
        drafts=(
            ClauseDraft(text="Same  clause", source_fact_ids=("fact-1",)),
            ClauseDraft(text=" Same clause ", source_fact_ids=("fact-1",)),
        )
    )

    with pytest.raises(ValueError, match="duplicate semantic clause"):
        GroundedClauseSemanticizer(adapter=adapter).compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_rejects_empty_normalized_clause_text() -> None:
    adapter = _StaticAdapter(drafts=(ClauseDraft(text=" \n\t ", source_fact_ids=("fact-1",)),))

    with pytest.raises(ValueError, match="text must not be empty"):
        GroundedClauseSemanticizer(adapter=adapter).compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_rejects_clause_without_source_references() -> None:
    adapter = _StaticAdapter(drafts=(ClauseDraft(text="Ungrounded", source_fact_ids=()),))

    with pytest.raises(ValueError, match="at least one source fact"):
        GroundedClauseSemanticizer(adapter=adapter).compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_rejects_empty_source_episode() -> None:
    with pytest.raises(ValueError, match="at least one source fact"):
        GroundedClauseSemanticizer(
            adapter=LosslessClauseProjectionAdapter(),
        ).compile((), episode_id="episode-1")


def test_projection_rejects_clause_count_above_configured_limit() -> None:
    adapter = _StaticAdapter(
        drafts=(
            ClauseDraft(text="First", source_fact_ids=("fact-1",)),
            ClauseDraft(text="Second", source_fact_ids=("fact-1",)),
        )
    )
    semanticizer = GroundedClauseSemanticizer(adapter=adapter, max_clauses=1)

    with pytest.raises(ValueError, match="clause count exceeds"):
        semanticizer.compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_rejects_clause_text_above_configured_limit() -> None:
    adapter = _StaticAdapter(drafts=(ClauseDraft(text="123456", source_fact_ids=("fact-1",)),))
    semanticizer = GroundedClauseSemanticizer(adapter=adapter, max_clause_chars=5)

    with pytest.raises(ValueError, match="text exceeds"):
        semanticizer.compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )


def test_projection_rejects_source_fact_count_above_configured_limit() -> None:
    semanticizer = GroundedClauseSemanticizer(
        adapter=LosslessClauseProjectionAdapter(),
        max_source_facts=1,
    )

    with pytest.raises(ValueError, match="source fact count exceeds"):
        semanticizer.compile(
            (
                _fact(fact_id="fact-1", text="First", actor="Alice", raw_event_index=1),
                _fact(fact_id="fact-2", text="Second", actor="Bob", raw_event_index=2),
            ),
            episode_id="episode-1",
        )


def test_projection_rejects_duplicate_source_fact_ids() -> None:
    duplicate = _fact(
        fact_id="fact-1",
        text="Source",
        actor="Alice",
        raw_event_index=1,
    )

    with pytest.raises(ValueError, match="source fact IDs must be unique"):
        GroundedClauseSemanticizer(
            adapter=LosslessClauseProjectionAdapter(),
        ).compile((duplicate, duplicate), episode_id="episode-1")


def test_projection_cache_avoids_a_second_adapter_call() -> None:
    adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Grounded clause", source_fact_ids=("fact-1",)),)
    )
    semanticizer = GroundedClauseSemanticizer(
        adapter=adapter,
        cache=InMemoryProjectionCache(),
    )
    facts = (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),)

    first = semanticizer.compile(facts, episode_id="episode-1")
    second = semanticizer.compile(facts, episode_id="episode-1")

    assert second == first
    assert len(adapter.calls) == 1


def test_projection_revalidates_cache_hits_before_returning_them() -> None:
    adapter = _StaticAdapter(drafts=(ClauseDraft(text="Valid", source_fact_ids=("fact-1",)),))
    cache = _AlwaysHitCache(drafts=(ClauseDraft(text="Forged", source_fact_ids=("missing-fact",)),))

    with pytest.raises(ValueError, match="unknown source fact"):
        GroundedClauseSemanticizer(adapter=adapter, cache=cache).compile(
            (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),),
            episode_id="episode-1",
        )

    assert adapter.calls == []


def test_source_digest_and_cache_key_include_complete_evidence() -> None:
    adapter = _StaticAdapter(drafts=(ClauseDraft(text="Grounded", source_fact_ids=("fact-1",)),))
    semanticizer = GroundedClauseSemanticizer(
        adapter=adapter,
        cache=InMemoryProjectionCache(),
    )
    original = _fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1)
    changed_evidence = replace(
        original,
        evidence=(replace(original.evidence[0], call_id="call-1"),),
    )

    semanticizer.compile((original,), episode_id="episode-1")
    semanticizer.compile((changed_evidence,), episode_id="episode-1")

    assert len(adapter.calls) == 2
    assert adapter.calls[0].source_digest != adapter.calls[1].source_digest


def test_cache_key_includes_the_adapter_identity() -> None:
    cache = InMemoryProjectionCache()
    first_adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Grounded", source_fact_ids=("fact-1",)),),
        identity=ProjectionIdentity(adapter_id="test/static", revision="v1"),
    )
    second_adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Grounded", source_fact_ids=("fact-1",)),),
        identity=ProjectionIdentity(
            adapter_id="test/static",
            revision="v1",
            model_id="model-b",
        ),
    )
    facts = (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),)

    GroundedClauseSemanticizer(adapter=first_adapter, cache=cache).compile(
        facts,
        episode_id="episode-1",
    )
    GroundedClauseSemanticizer(adapter=second_adapter, cache=cache).compile(
        facts,
        episode_id="episode-1",
    )

    assert len(first_adapter.calls) == 1
    assert len(second_adapter.calls) == 1


def test_cache_key_includes_the_adapter_config_digest() -> None:
    cache = InMemoryProjectionCache()
    first_adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Grounded", source_fact_ids=("fact-1",)),),
        identity=ProjectionIdentity(
            adapter_id="test/static",
            revision="v1",
            model_id="model-a",
            config_sha256="a" * 64,
        ),
    )
    second_adapter = _StaticAdapter(
        drafts=(ClauseDraft(text="Grounded", source_fact_ids=("fact-1",)),),
        identity=ProjectionIdentity(
            adapter_id="test/static",
            revision="v1",
            model_id="model-a",
            config_sha256="b" * 64,
        ),
    )
    facts = (_fact(fact_id="fact-1", text="Source", actor="Alice", raw_event_index=1),)

    GroundedClauseSemanticizer(adapter=first_adapter, cache=cache).compile(
        facts,
        episode_id="episode-1",
    )
    GroundedClauseSemanticizer(adapter=second_adapter, cache=cache).compile(
        facts,
        episode_id="episode-1",
    )

    assert len(first_adapter.calls) == 1
    assert len(second_adapter.calls) == 1


class _StaticAdapter:
    def __init__(
        self,
        *,
        drafts: tuple[ClauseDraft, ...],
        identity: ProjectionIdentity | None = None,
    ) -> None:
        self._drafts = drafts
        self.identity = identity or ProjectionIdentity(
            adapter_id="test/static",
            revision="v1",
        )
        self.calls: list[ProjectionSource] = []

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        self.calls.append(source)
        return self._drafts


class _AlwaysHitCache:
    def __init__(self, *, drafts: tuple[ClauseDraft, ...]) -> None:
        self._drafts = drafts

    def get(self, cache_key: str) -> tuple[ClauseDraft, ...] | None:
        return self._drafts

    def put(self, cache_key: str, drafts: tuple[ClauseDraft, ...]) -> None:
        raise AssertionError("A cache hit must not be overwritten")


def _fact(
    *,
    fact_id: str,
    text: str,
    actor: str,
    raw_event_index: int,
) -> EvidenceFact:
    return EvidenceFact(
        fact_id=fact_id,
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="conversation_turn",
        text=text,
        role="participant",
        actor=actor,
        occurred_at=None,
        evidence=(
            EvidenceReference(
                provider="locomo",
                session_id="session-1",
                source_path="locomo://fixture/session-1",
                raw_event_sha256=f"{raw_event_index:064x}",
                raw_event_index=raw_event_index,
                raw_event_type="locomo_turn",
            ),
        ),
    )
