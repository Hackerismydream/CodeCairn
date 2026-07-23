from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.memory.context import (
    CONTEXT_RENDERER_ID,
    CONTEXT_TOKENIZER_ID,
    count_context_tokens,
)
from codecairn.memory.episode import AttributedEpisode, AttributedTurn
from codecairn.memory.models import (
    EvidenceReference,
    RankedRecall,
    RecallSnippet,
    RecallSnippetRelation,
)
from codecairn.memory.recall_planner import RecallPlannerConfig
from codecairn.service.recall import _compile_context as compile_context
from codecairn.service.recall import _render_context as render_context


def test_recall_context_obeys_the_pinned_four_thousand_token_budget(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    for index, actor in enumerate(("Alice", "Bob", "Caroline", "Melanie"), start=1):
        assert runtime.write_episode(_large_episode(index=index, actor=actor)).accepted is True
    assert create_cascade(root).rebuild().parity is True

    recalled = runtime.recall(
        "What did Alice and Bob do before Caroline and Melanie?",
        repo_key="locomo/conv-test",
        limit=4,
    )

    trace = recalled.sidecar.context_trace
    assert trace is not None
    assert trace.tokenizer_id == CONTEXT_TOKENIZER_ID
    assert trace.token_limit == 4_000
    assert trace.token_count == count_context_tokens(recalled.markdown)
    assert trace.token_count <= trace.token_limit
    assert trace.renderer == CONTEXT_RENDERER_ID
    assert all(f"[{fact_id}]" in recalled.markdown for fact_id in trace.rendered_fact_ids)


def test_empty_recall_context_still_obeys_a_small_legal_token_budget() -> None:
    rendered = render_context(
        "Q" * 8_000,
        repo_key="repository-" + "R" * 500,
        ranked=(),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(context_max_tokens=256),
    )

    assert count_context_tokens(rendered) <= 256
    assert "No evidence-backed memory matched this task." in rendered


def test_context_renders_a_complete_source_fact_instead_of_counting_a_truncated_prefix() -> None:
    text = "prefix " * 40 + "TAIL-GOLD-ANSWER"
    ranked = (_ranked_parent(snippet_text=text),)

    compiled = compile_context(
        "What was the answer?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert "TAIL-GOLD-ANSWER" in compiled.markdown
    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-source",)
    assert "[fact-source]" in compiled.markdown


def test_context_does_not_claim_an_oversized_source_fact_that_cannot_fit() -> None:
    ranked = (_ranked_parent(snippet_text="X" * 20_000),)

    compiled = compile_context(
        "What was the answer?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ()
    assert compiled.trace.omitted_fact_ids == ("fact-source",)
    assert "[fact-source]" not in compiled.markdown


def test_context_skips_an_oversized_middle_fact_and_keeps_a_short_tail_fact() -> None:
    ranked = (
        replace(
            _ranked_parent(snippet_text="unused"),
            snippets=(
                _snippet(fact_id="fact-first", text="First fact.", raw_event_index=1),
                _snippet(fact_id="fact-middle", text="X" * 9_000, raw_event_index=2),
                _snippet(
                    fact_id="fact-tail",
                    text="GOLD-SMALL-FACT",
                    raw_event_index=3,
                ),
            ),
        ),
    )

    compiled = compile_context(
        "What was the answer?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert "GOLD-SMALL-FACT" in compiled.markdown
    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-first", "fact-tail")
    assert compiled.trace.omitted_fact_ids == ("fact-middle",)


def test_temporal_context_packs_all_matched_facts_before_siblings() -> None:
    matched_first = replace(
        _snippet(fact_id="fact-alice", text="Alice chose the venue.", raw_event_index=1),
        relation="matched",
    )
    matched_second = replace(
        _snippet(fact_id="fact-bob", text="Bob chose the music.", raw_event_index=2),
        relation="matched",
    )
    siblings = tuple(
        _snippet(
            fact_id=f"fact-sibling-{index}",
            text=f"sibling-{index}-" + "X" * 1_520,
            raw_event_index=index + 3,
        )
        for index in range(5)
    )
    ranked = (
        replace(
            _ranked_parent(snippet_text="unused"),
            snippets=(matched_first, matched_second, *siblings),
        ),
    )

    compiled = compile_context(
        "What did Alice and Bob choose before the event?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids={"memory-source"},
        config=RecallPlannerConfig(),
    )

    assert compiled.trace is not None
    assert {"fact-alice", "fact-bob"}.issubset(compiled.trace.rendered_fact_ids)
    assert compiled.markdown.index("Bob chose the music") < compiled.markdown.index("sibling-0")


def test_context_packs_all_matched_facts_across_parents_before_any_sibling() -> None:
    def parent(
        *,
        rank: int,
        memory_id: str,
        snippets: tuple[RecallSnippet, ...],
    ) -> RankedRecall:
        return replace(
            _ranked_parent(snippet_text="unused"),
            rank=rank,
            memory_id=memory_id,
            title=f"Conversation {rank}",
            source_uri=f"codecairn://memory/{memory_id}",
            snippets=snippets,
        )

    def snippet(
        *,
        memory_id: str,
        fact_id: str,
        relation: RecallSnippetRelation,
        raw_event_index: int,
    ) -> RecallSnippet:
        return replace(
            _snippet(
                fact_id=fact_id,
                text="same-sized-evidence-" + "X" * 80,
                raw_event_index=raw_event_index,
            ),
            relation=relation,
            source_memory_id=memory_id,
            source_uri=f"codecairn://memory/{memory_id}",
        )

    first_matched = snippet(
        memory_id="memory-a",
        fact_id="fact-a-first",
        relation="matched",
        raw_event_index=1,
    )
    high_rank_sibling = snippet(
        memory_id="memory-a",
        fact_id="fact-a-second",
        relation="sibling",
        raw_event_index=2,
    )
    second_parent_first = snippet(
        memory_id="memory-b",
        fact_id="fact-b-first",
        relation="matched",
        raw_event_index=1,
    )
    lower_rank_second_matched = snippet(
        memory_id="memory-b",
        fact_id="fact-b-second",
        relation="matched",
        raw_event_index=2,
    )
    budget_template = compile_context(
        "What did both people choose?",
        repo_key="locomo/conv-test",
        ranked=(
            parent(rank=1, memory_id="memory-a", snippets=(first_matched,)),
            parent(
                rank=2,
                memory_id="memory-b",
                snippets=(second_parent_first, lower_rank_second_matched),
            ),
        ),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )
    assert budget_template.trace is not None

    compiled = compile_context(
        "What did both people choose?",
        repo_key="locomo/conv-test",
        ranked=(
            parent(
                rank=1,
                memory_id="memory-a",
                snippets=(first_matched, high_rank_sibling),
            ),
            parent(
                rank=2,
                memory_id="memory-b",
                snippets=(second_parent_first, lower_rank_second_matched),
            ),
        ),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(
            context_max_tokens=budget_template.trace.token_count,
        ),
    )

    assert compiled.trace is not None
    assert compiled.trace.rendered_memory_ids == ("memory-a", "memory-b")
    assert "fact-b-second" in compiled.trace.rendered_fact_ids
    assert "fact-a-second" not in compiled.trace.rendered_fact_ids


def test_context_admission_prefers_the_query_entity_answer_over_a_restatement() -> None:
    def parent(
        *,
        rank: int,
        memory_id: str,
        snippets: tuple[RecallSnippet, ...],
    ) -> RankedRecall:
        return replace(
            _ranked_parent(snippet_text="unused"),
            rank=rank,
            memory_id=memory_id,
            title=f"Conversation {rank}",
            source_uri=f"codecairn://memory/{memory_id}",
            snippets=snippets,
        )

    def matched(
        *,
        memory_id: str,
        fact_id: str,
        text: str,
        raw_event_index: int,
        relevance_score: float,
    ) -> RecallSnippet:
        return replace(
            _snippet(
                fact_id=fact_id,
                text=text.ljust(600, "X"),
                raw_event_index=raw_event_index,
            ),
            relation="matched",
            source_memory_id=memory_id,
            source_uri=f"codecairn://memory/{memory_id}",
            relevance_score=relevance_score,
            selection_source="bounded-authoritative-cross-encoder-v1",
        )

    answer = matched(
        memory_id="memory-top",
        fact_id="fact-0000000000000002",
        text='Melanie: I loved reading "Charlotte\'s Web" as a child.',
        raw_event_index=2,
        relevance_score=1.0,
    )
    budget_template = compile_context(
        "What was Melanie's favorite book from her childhood?",
        repo_key="locomo/conv-test",
        ranked=(parent(rank=1, memory_id="memory-top", snippets=(answer,)),),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )
    assert budget_template.trace is not None

    compiled = compile_context(
        "What was Melanie's favorite book from her childhood?",
        repo_key="locomo/conv-test",
        ranked=(
            parent(
                rank=1,
                memory_id="memory-top",
                snippets=(
                    matched(
                        memory_id="memory-top",
                        fact_id="fact-0000000000000001",
                        text=("Caroline: What favorite book do you remember from your childhood?"),
                        raw_event_index=1,
                        relevance_score=0.0,
                    ),
                    answer,
                ),
            ),
        ),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(
            context_max_tokens=budget_template.trace.token_count,
        ),
    )

    assert compiled.trace is not None
    assert "fact-0000000000000002" in compiled.trace.rendered_fact_ids
    assert "fact-0000000000000001" not in compiled.trace.rendered_fact_ids


def test_context_keeps_a_parent_when_a_later_fact_fits_after_an_oversized_first_fact() -> None:
    ranked = (
        replace(
            _ranked_parent(snippet_text="unused"),
            snippets=(
                _snippet(fact_id="fact-first", text="X" * 9_000, raw_event_index=1),
                _snippet(
                    fact_id="fact-second",
                    text="GOLD-SMALL-FACT",
                    raw_event_index=2,
                ),
            ),
        ),
    )

    compiled = compile_context(
        "What was the answer?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert "GOLD-SMALL-FACT" in compiled.markdown
    assert compiled.trace is not None
    assert compiled.trace.rendered_memory_ids == ("memory-source",)
    assert compiled.trace.rendered_fact_ids == ("fact-second",)
    assert compiled.trace.omitted_memory_ids == ()
    assert compiled.trace.omitted_fact_ids == ("fact-first",)


def test_parent_hydration_marks_every_authoritative_source_fact() -> None:
    ranked = (
        _ranked_parent(
            snippet_text="The explicitly cited source fact.",
            episode_snippets=(
                _snippet(
                    fact_id="fact-source",
                    text="Alice: The explicitly cited source fact.",
                    raw_event_index=1,
                ),
                _snippet(
                    fact_id="fact-hydrated",
                    text="Bob: A second authoritative parent fact.",
                    raw_event_index=2,
                ),
            ),
            episode_fact_ids=("fact-source", "fact-hydrated"),
        ),
    )

    compiled = compile_context(
        "How did they complete the procedure?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
        wants_procedure=True,
    )

    assert "Complete parent episode:" in compiled.markdown
    assert "A second authoritative parent fact" in compiled.markdown
    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-source", "fact-hydrated")
    assert "[fact-source]" in compiled.markdown
    assert "[fact-hydrated]" in compiled.markdown


def test_parent_hydration_spends_budget_only_on_facts_not_already_rendered() -> None:
    ranked = (
        _ranked_parent(
            snippet_text="A" * 3_000,
            episode_snippets=(
                _snippet(fact_id="fact-source", text="A" * 3_000, raw_event_index=1),
                _snippet(fact_id="fact-hydrated", text="B" * 3_000, raw_event_index=2),
            ),
            episode_fact_ids=("fact-source", "fact-hydrated"),
        ),
    )

    compiled = compile_context(
        "How did they complete the procedure?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
        wants_procedure=True,
    )

    assert "Complete parent episode:" in compiled.markdown
    assert compiled.markdown.count("[fact-source]") == 1
    assert "[fact-hydrated]" in compiled.markdown
    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-source", "fact-hydrated")
    assert compiled.trace.token_count <= compiled.trace.token_limit == 4_000


def _ranked_parent(
    *,
    snippet_text: str,
    episode_fact_ids: tuple[str, ...] = (),
    episode_snippets: tuple[RecallSnippet, ...] = (),
) -> RankedRecall:
    return RankedRecall(
        rank=1,
        memory_id="memory-source",
        memory_type="conversation_episode",
        title="Conversation",
        summary="Conversation episode",
        source_uri="codecairn://memory/memory-source",
        content_sha256="a" * 64,
        candidate_sources=("lexical",),
        vector_score=None,
        vector_rank=None,
        lexical_score=1.0,
        lexical_rank=1,
        final_score=1.0,
        evidence=(),
        snippets=(
            RecallSnippet(
                relation="matched",
                source_memory_id="memory-source",
                source_uri="codecairn://memory/memory-source",
                fact_id="fact-source",
                text=snippet_text,
                source_title="Conversation",
                source_summary="Conversation episode",
                raw_event_index=1,
            ),
        ),
        episode_fact_ids=episode_fact_ids,
        episode_snippets=episode_snippets,
    )


def _snippet(*, fact_id: str, text: str, raw_event_index: int) -> RecallSnippet:
    return RecallSnippet(
        relation="sibling",
        source_memory_id="memory-source",
        source_uri="codecairn://memory/memory-source",
        fact_id=fact_id,
        text=text,
        source_title="Conversation",
        source_summary="Conversation episode",
        raw_event_index=raw_event_index,
    )


def _large_episode(*, index: int, actor: str) -> AttributedEpisode:
    return AttributedEpisode(
        repo_key="locomo/conv-test",
        source_episode_id=f"session-{index}",
        title=f"Conversation session {index}",
        turns=(
            AttributedTurn(
                turn_id=f"turn-{index}",
                actor=actor,
                role="participant",
                text=f"{actor} completed milestone {index}. " + f"detail-{index} " * 1_000,
                occurred_at=f"2023-05-{index:02d}T13:56:00+00:00",
                evidence=EvidenceReference(
                    provider="locomo",
                    session_id=f"conv-test/session-{index}",
                    source_path=f"locomo://fixture/conv-test/session-{index}",
                    raw_event_sha256=f"{index:064x}",
                    raw_event_index=index,
                    raw_event_type="locomo_turn",
                ),
            ),
        ),
    )
