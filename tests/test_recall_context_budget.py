from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.memory.context import (
    CONTEXT_RENDERER_ID,
    CONTEXT_TOKENIZER_ID,
    LEGACY_CONTEXT_EVIDENCE_SLOT_POLICY_ID,
    count_context_tokens,
)
from codecairn.memory.episode import AttributedEpisode, AttributedTurn
from codecairn.memory.evidence_selector import FACT_SELECTOR_ID
from codecairn.memory.models import (
    EvidenceReference,
    RankedRecall,
    RecallMatch,
    RecallSnippet,
    RecallSnippetRelation,
)
from codecairn.memory.recall_planner import ContextEvidenceSlot, RecallPlannerConfig
from codecairn.service.recall import _compile_context as compile_context
from codecairn.service.recall import _context_effective_relevance
from codecairn.service.recall import _context_slot_candidates as context_slot_candidates
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
            context_max_tokens=max(256, budget_template.trace.token_count),
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


def test_context_direct_match_prior_is_bounded_and_keeps_raw_scores() -> None:
    parent_memory_id = "memory-parent"
    matched = replace(
        _snippet(
            fact_id="fact-matched",
            text="Direct retrieval match.".ljust(600, "M"),
            raw_event_index=1,
        ),
        relation="matched",
        source_memory_id=parent_memory_id,
        relevance_score=0.0,
        selection_source=FACT_SELECTOR_ID,
    )
    sibling = replace(
        _snippet(
            fact_id="fact-sibling",
            text="Higher raw sibling.".ljust(600, "S"),
            raw_event_index=2,
        ),
        relation="sibling",
        source_memory_id=parent_memory_id,
        relevance_score=1.9,
        selection_source=FACT_SELECTOR_ID,
    )
    parent = replace(
        _ranked_parent(snippet_text="unused"),
        memory_id=parent_memory_id,
        source_uri=f"codecairn://memory/{parent_memory_id}",
        snippets=(matched,),
    )
    template = compile_context(
        "What was directly stated?",
        repo_key="locomo/conv-test",
        ranked=(parent,),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )
    assert template.trace is not None

    prioritized = compile_context(
        "What was directly stated?",
        repo_key="locomo/conv-test",
        ranked=(replace(parent, snippets=(matched, sibling)),),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(context_max_tokens=template.trace.token_count),
    )

    assert prioritized.trace is not None
    assert prioritized.trace.rendered_fact_ids == ("fact-matched",)
    assert matched.relevance_score == 0.0
    assert sibling.relevance_score == 1.9

    stronger_sibling = replace(sibling, relevance_score=2.1)
    unprioritized = compile_context(
        "What was directly stated?",
        repo_key="locomo/conv-test",
        ranked=(replace(parent, snippets=(matched, stronger_sibling)),),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(context_max_tokens=template.trace.token_count),
    )

    assert unprioritized.trace is not None
    assert unprioritized.trace.rendered_fact_ids == ("fact-sibling",)


def test_context_direct_match_prior_never_activates_unscored_or_external_facts() -> None:
    unscored = replace(
        _snippet(fact_id="fact-unscored", text="Unscored.", raw_event_index=1),
        relation="matched",
        relevance_score=None,
    )
    external = replace(
        _snippet(fact_id="fact-external", text="External.", raw_event_index=2),
        relation="matched",
        source_memory_id="memory-external",
        relevance_score=1.0,
    )
    sibling = replace(
        _snippet(fact_id="fact-sibling", text="Sibling.", raw_event_index=3),
        relation="sibling",
        relevance_score=1.0,
    )

    assert _context_effective_relevance(
        unscored,
        parent_memory_id="memory-source",
    ) == float("-inf")
    assert (
        _context_effective_relevance(
            external,
            parent_memory_id="memory-source",
        )
        == 1.0
    )
    assert (
        _context_effective_relevance(
            sibling,
            parent_memory_id="memory-source",
        )
        == 1.0
    )


def test_context_never_lets_a_semantic_projection_replace_exact_source_evidence() -> None:
    exact = _snippet(
        fact_id="fact-source",
        text="2023-07-15T13:51:00+00:00 — Melanie: A much longer exact source turn.",
        raw_event_index=1,
    )
    projected = replace(
        exact,
        semantic_text="Melanie gave a concise grounded answer.",
        semantic_fact_ids=("semantic-fact",),
        relevance_score=1.0,
        selection_source="bounded-dialogue-aware-cross-encoder-v4",
    )

    compiled = compile_context(
        "What did Melanie answer?",
        repo_key="locomo/conv-test",
        ranked=(replace(_ranked_parent(snippet_text="unused"), snippets=(projected,)),),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert "2023-07-15T13:51:00+00:00 — Melanie:" in compiled.markdown
    assert "A much longer exact source turn." in compiled.markdown
    assert "Melanie gave a concise grounded answer." not in compiled.markdown
    assert projected.text.endswith("A much longer exact source turn.")
    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-source",)


def test_context_keeps_temporal_source_data_when_projection_is_unreliable() -> None:
    exact = _snippet(
        fact_id="fact-source",
        text="2023-07-15T13:51:00+00:00 — Melanie: The appointment is next Tuesday.",
        raw_event_index=1,
    )
    projected = replace(
        exact,
        semantic_text="Melanie discussed an appointment.",
        semantic_fact_ids=("semantic-fact",),
        relevance_score=1.0,
        selection_source="bounded-dialogue-aware-cross-encoder-v4",
    )

    compiled = compile_context(
        "What did Melanie answer?",
        repo_key="locomo/conv-test",
        ranked=(replace(_ranked_parent(snippet_text="unused"), snippets=(projected,)),),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert "2023-07-15T13:51:00+00:00" in compiled.markdown
    assert "The appointment is next Tuesday." in compiled.markdown
    assert "Melanie discussed an appointment." not in compiled.markdown
    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-source",)


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


def test_context_protects_a_semantic_child_hit_from_raw_score_starvation() -> None:
    supported_memory_id = "memory-supported"
    supported = replace(
        _snippet(
            fact_id="fact-supported",
            text=(
                "2023-07-11T10:05:00+00:00 — Andrew: "
                "I miss hiking after stressful work. " + "S" * 500
            ),
            raw_event_index=2,
        ),
        relation="matched",
        source_memory_id=supported_memory_id,
        source_uri=f"codecairn://memory/{supported_memory_id}",
        relevance_score=-10.0,
        semantic_fact_ids=("semantic-supported",),
    )
    supported_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=2,
        memory_id=supported_memory_id,
        title="Supported conversation",
        source_uri=f"codecairn://memory/{supported_memory_id}",
        snippets=(supported,),
        matched_documents=(
            RecallMatch(
                document_id="document-supported",
                document_kind="atomic_fact",
                source="atomic_fact_vector",
                score=0.5,
                rank=1,
                fact_id="semantic-supported",
            ),
        ),
    )
    high_score = replace(
        _snippet(
            fact_id="fact-high-score",
            text=("2023-07-12T10:05:00+00:00 — Andrew: An unrelated high-score turn. " + "H" * 500),
            raw_event_index=3,
        ),
        relation="matched",
        relevance_score=10.0,
    )
    high_parent = replace(
        _ranked_parent(snippet_text="unused"),
        snippets=(high_score,),
    )
    template = compile_context(
        "What could Andrew do?",
        repo_key="locomo/conv-test",
        ranked=(supported_parent,),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )
    assert template.trace is not None

    compiled = compile_context(
        "What could Andrew do?",
        repo_key="locomo/conv-test",
        ranked=(high_parent, supported_parent),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(context_max_tokens=template.trace.token_count),
        evidence_slots=(ContextEvidenceSlot(kind="semantic_child_support", max_facts=1),),
    )

    assert compiled.trace is not None
    assert "fact-supported" in compiled.trace.rendered_fact_ids
    assert "fact-high-score" not in compiled.trace.rendered_fact_ids


def test_quantity_slot_keeps_state_transitions_and_the_following_answer() -> None:
    high_score_facts = tuple(
        replace(
            _snippet(
                fact_id=f"fact-high-{index}",
                text=f"2022-01-0{index}T10:00:00+00:00 — Joanna: Unrelated detail {index}.",
                raw_event_index=index,
            ),
            relation="matched",
            relevance_score=20.0 - index,
        )
        for index in range(1, 4)
    )
    transition_facts = (
        replace(
            _snippet(
                fact_id="fact-first",
                text="2022-01-04T10:00:00+00:00 — Joanna: I finished my first screenplay.",
                raw_event_index=4,
            ),
            relation="matched",
            relevance_score=-5.0,
        ),
        replace(
            _snippet(
                fact_id="fact-another",
                text="2022-01-05T10:00:00+00:00 — Joanna: I started another screenplay.",
                raw_event_index=5,
            ),
            relation="matched",
            relevance_score=-6.0,
        ),
        replace(
            _snippet(
                fact_id="fact-second",
                text="2022-01-06T10:00:00+00:00 — Joanna: I finished my second script.",
                raw_event_index=6,
            ),
            relation="matched",
            relevance_score=-7.0,
            semantic_text="Joanna completed her second screenplay.",
        ),
        replace(
            _snippet(
                fact_id="fact-third-question",
                text="2022-01-07T10:00:00+00:00 — Nate: Joanna, is that your third one?",
                raw_event_index=7,
            ),
            relevance_score=-8.0,
        ),
        replace(
            _snippet(
                fact_id="fact-third-answer",
                text="2022-01-07T10:00:00+00:00 — Joanna: Yes, I am proud of it.",
                raw_event_index=8,
            ),
            relevance_score=-9.0,
        ),
    )
    parent = replace(
        _ranked_parent(snippet_text="unused"),
        snippets=(*high_score_facts, *transition_facts),
    )

    compiled = compile_context(
        "How many screenplays has Joanna written?",
        repo_key="locomo/conv-test",
        ranked=(parent,),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(
            context_snippets_per_memory=8,
            context_temporal_snippets_per_memory=8,
        ),
        evidence_slots=(
            ContextEvidenceSlot(
                kind="quantity_transition",
                max_facts=12,
                anchors=("joanna",),
                topic_terms=("screenplay", "written"),
            ),
        ),
    )

    assert compiled.trace is not None
    assert {
        "fact-first",
        "fact-another",
        "fact-second",
        "fact-third-question",
        "fact-third-answer",
    }.issubset(compiled.trace.rendered_fact_ids)
    assert len(compiled.trace.slot_traces) == 1
    slot_trace = compiled.trace.slot_traces[0]
    assert slot_trace.slot_kind == "quantity_transition"
    assert slot_trace.max_facts == 12
    assert {
        attempt.fact_id for attempt in slot_trace.attempts if attempt.outcome == "admitted"
    } == {
        "fact-first",
        "fact-another",
        "fact-second",
        "fact-third-question",
        "fact-third-answer",
    }


def test_quantity_slot_prefers_topic_evidence_across_unordered_parents() -> None:
    relevant = replace(
        _snippet(
            fact_id="fact-book",
            text="2022-02-01T10:00:00+00:00 — Alice: My first book is nearly done.",
            raw_event_index=100,
        ),
        source_memory_id="memory-book",
        source_uri="codecairn://memory/memory-book",
        relevance_score=-5.0,
    )
    distractor = replace(
        _snippet(
            fact_id="fact-dog",
            text="2021-01-01T10:00:00+00:00 — Alice: My first dog was a beagle.",
            raw_event_index=1,
        ),
        source_memory_id="memory-dog",
        source_uri="codecairn://memory/memory-dog",
        relevance_score=5.0,
    )
    book_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=1,
        memory_id="memory-book",
        source_uri="codecairn://memory/memory-book",
        snippets=(relevant,),
    )
    dog_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=5,
        memory_id="memory-dog",
        source_uri="codecairn://memory/memory-dog",
        snippets=(distractor,),
    )
    ranked = (book_parent, dog_parent)
    candidates = ((0, 0, relevant), (1, 0, distractor))

    selected = context_slot_candidates(
        ContextEvidenceSlot(
            kind="quantity_transition",
            max_facts=1,
            anchors=("alice",),
            topic_terms=("book",),
        ),
        ranked=ranked,
        snippet_values=((relevant,), (distractor,)),
        candidates=candidates,
    )

    assert tuple(candidate[2].fact_id for candidate in selected) == ("fact-book",)


def test_quantity_slot_keeps_anchored_anaphoric_pair_for_the_same_ordinal() -> None:
    third_occurrence = replace(
        _snippet(
            fact_id="fact-third-occurrence",
            text=("2022-10-25T20:16:00+00:00 — Joanna: This is the third time it has happened."),
            raw_event_index=493,
        ),
        source_memory_id="memory-occurrence",
        source_uri="codecairn://memory/memory-occurrence",
        relevance_score=5.0,
        semantic_text="This was the third time Joanna's movie script was shown.",
        semantic_fact_ids=("semantic-third-occurrence",),
    )
    third_question = replace(
        _snippet(
            fact_id="fact-third-question",
            text=(
                "2022-05-20T19:49:00+00:00 — Nate: Wow, that looks great "
                "Joanna! Is that your third one?"
            ),
            raw_event_index=230,
        ),
        source_memory_id="memory-question-answer",
        source_uri="codecairn://memory/memory-question-answer",
        relevance_score=-8.0,
    )
    third_answer = replace(
        _snippet(
            fact_id="fact-third-answer",
            text=("2022-05-20T19:49:00+00:00 — Joanna: Yep! It is personal, and I am proud of it."),
            raw_event_index=231,
        ),
        source_memory_id="memory-question-answer",
        source_uri="codecairn://memory/memory-question-answer",
        relevance_score=-9.0,
    )
    occurrence_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=1,
        memory_id="memory-occurrence",
        source_uri="codecairn://memory/memory-occurrence",
        snippets=(third_occurrence,),
        matched_documents=(
            RecallMatch(
                document_id="document-third-occurrence",
                document_kind="atomic_fact",
                source="atomic_fact_vector",
                score=0.5,
                rank=1,
                fact_id="semantic-third-occurrence",
            ),
        ),
    )
    question_answer_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=2,
        memory_id="memory-question-answer",
        source_uri="codecairn://memory/memory-question-answer",
        snippets=(third_question, third_answer),
    )
    ranked = (occurrence_parent, question_answer_parent)

    selected = context_slot_candidates(
        ContextEvidenceSlot(
            kind="quantity_transition",
            max_facts=12,
            anchors=("joanna",),
            topic_terms=("screenplay", "written"),
        ),
        ranked=ranked,
        snippet_values=((third_occurrence,), (third_question, third_answer)),
        candidates=(
            (0, 0, third_occurrence),
            (1, 0, third_question),
            (1, 1, third_answer),
        ),
    )

    assert tuple(candidate[2].fact_id for candidate in selected) == (
        "fact-third-occurrence",
        "fact-third-question",
        "fact-third-answer",
    )


def test_quantity_slot_prioritizes_high_ordinals_without_splitting_support_pairs() -> None:
    ordinals = ("first", "second", "third", "fourth", "fifth")
    snippets: list[RecallSnippet] = []
    matches: list[RecallMatch] = []
    for ordinal_index, ordinal in enumerate(ordinals, start=1):
        semantic_fact_id = f"semantic-{ordinal}"
        snippets.extend(
            (
                replace(
                    _snippet(
                        fact_id=f"fact-{ordinal}-occurrence",
                        text=(
                            f"2022-01-{ordinal_index:02d}T10:00:00+00:00 — Joanna: "
                            f"This is the {ordinal} time it has happened."
                        ),
                        raw_event_index=ordinal_index * 10,
                    ),
                    relevance_score=float(ordinal_index),
                    semantic_text=(f"This was the {ordinal} time Joanna's movie script was shown."),
                    semantic_fact_ids=(semantic_fact_id,),
                ),
                replace(
                    _snippet(
                        fact_id=f"fact-{ordinal}-question",
                        text=(
                            f"2022-01-{ordinal_index:02d}T10:01:00+00:00 — Nate: "
                            f"Joanna, is that your {ordinal} one?"
                        ),
                        raw_event_index=ordinal_index * 10 + 1,
                    ),
                    relevance_score=-float(ordinal_index),
                ),
                replace(
                    _snippet(
                        fact_id=f"fact-{ordinal}-answer",
                        text=(f"2022-01-{ordinal_index:02d}T10:02:00+00:00 — Joanna: Yes, it is."),
                        raw_event_index=ordinal_index * 10 + 2,
                    ),
                    relevance_score=-float(ordinal_index) - 0.5,
                ),
            )
        )
        matches.append(
            RecallMatch(
                document_id=f"document-{ordinal}",
                document_kind="atomic_fact",
                source="atomic_fact_vector",
                score=1.0,
                rank=ordinal_index,
                fact_id=semantic_fact_id,
            )
        )
    parent = replace(
        _ranked_parent(snippet_text="unused"),
        snippets=tuple(snippets),
        matched_documents=tuple(matches),
    )
    selected = context_slot_candidates(
        ContextEvidenceSlot(
            kind="quantity_transition",
            max_facts=12,
            anchors=("joanna",),
            topic_terms=("screenplay", "written"),
        ),
        ranked=(parent,),
        snippet_values=(tuple(snippets),),
        candidates=tuple(
            (0, snippet_index, snippet) for snippet_index, snippet in enumerate(snippets)
        ),
    )
    selected_fact_ids = tuple(candidate[2].fact_id for candidate in selected)

    assert selected_fact_ids[:5] == tuple(
        f"fact-{ordinal}-occurrence" for ordinal in reversed(ordinals)
    )
    assert selected_fact_ids[5:] == (
        "fact-fifth-question",
        "fact-fifth-answer",
        "fact-fourth-question",
        "fact-fourth-answer",
        "fact-third-question",
        "fact-third-answer",
    )
    assert len(selected_fact_ids) == 11
    for ordinal in ordinals:
        pair = {
            f"fact-{ordinal}-question",
            f"fact-{ordinal}-answer",
        }
        assert len(pair & set(selected_fact_ids)) in {0, 2}


def test_quantity_slot_can_replay_the_frozen_v1_candidate_policy() -> None:
    occurrence = replace(
        _snippet(
            fact_id="fact-third-occurrence",
            text="2022-10-25T20:16:00+00:00 — Joanna: This is the third time.",
            raw_event_index=10,
        ),
        relevance_score=5.0,
        semantic_text="This was the third time Joanna's movie script was shown.",
        semantic_fact_ids=("semantic-third-occurrence",),
    )
    question = replace(
        _snippet(
            fact_id="fact-third-question",
            text="2022-05-20T19:49:00+00:00 — Nate: Joanna, is that your third one?",
            raw_event_index=20,
        ),
        relevance_score=-8.0,
    )
    answer = replace(
        _snippet(
            fact_id="fact-third-answer",
            text="2022-05-20T19:50:00+00:00 — Joanna: Yes, it is.",
            raw_event_index=21,
        ),
        relevance_score=-9.0,
    )
    parent = replace(
        _ranked_parent(snippet_text="unused"),
        snippets=(occurrence, question, answer),
        matched_documents=(
            RecallMatch(
                document_id="document-third-occurrence",
                document_kind="atomic_fact",
                source="atomic_fact_vector",
                score=1.0,
                rank=1,
                fact_id="semantic-third-occurrence",
            ),
        ),
    )
    candidates = ((0, 0, occurrence), (0, 1, question), (0, 2, answer))
    slot = ContextEvidenceSlot(
        kind="quantity_transition",
        max_facts=12,
        anchors=("joanna",),
        topic_terms=("screenplay", "written"),
    )

    legacy = context_slot_candidates(
        slot,
        ranked=(parent,),
        snippet_values=((occurrence, question, answer),),
        candidates=candidates,
        evidence_slot_policy=LEGACY_CONTEXT_EVIDENCE_SLOT_POLICY_ID,
    )
    current = context_slot_candidates(
        slot,
        ranked=(parent,),
        snippet_values=((occurrence, question, answer),),
        candidates=candidates,
    )

    assert tuple(candidate[2].fact_id for candidate in legacy) == ("fact-third-occurrence",)
    assert tuple(candidate[2].fact_id for candidate in current) == (
        "fact-third-occurrence",
        "fact-third-question",
        "fact-third-answer",
    )


def test_vocative_alias_slot_protects_a_shortened_name() -> None:
    alias = replace(
        _snippet(
            fact_id="fact-alias",
            text="2022-04-15T19:37:00+00:00 — Nate: Hey Jo, come see!",
            raw_event_index=2,
        ),
        relevance_score=-10.0,
    )
    distractor = replace(
        _snippet(
            fact_id="fact-distractor",
            text="2022-04-15T19:37:00+00:00 — Nate: An unrelated statement.",
            raw_event_index=1,
        ),
        relation="matched",
        relevance_score=10.0,
    )

    compiled = compile_context(
        "What nickname does Nate use for Joanna?",
        repo_key="locomo/conv-test",
        ranked=(replace(_ranked_parent(snippet_text="unused"), snippets=(distractor, alias)),),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(
            context_snippets_per_memory=1,
            context_temporal_snippets_per_memory=1,
        ),
        evidence_slots=(
            ContextEvidenceSlot(
                kind="vocative_alias",
                max_facts=2,
                anchors=("nate", "joanna"),
            ),
        ),
    )

    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-alias",)


def test_prior_state_slot_protects_exclusive_affect_evidence() -> None:
    prior_state = replace(
        _snippet(
            fact_id="fact-prior-state",
            text=(
                "2022-05-04T19:01:00+00:00 — James: My pets and games are all "
                "that bring me happiness."
            ),
            raw_event_index=2,
        ),
        relation="matched",
        relevance_score=-10.0,
    )
    distractor = replace(
        _snippet(
            fact_id="fact-after",
            text="2022-10-31T00:37:00+00:00 — James: Samantha and I moved in together.",
            raw_event_index=3,
        ),
        relation="matched",
        relevance_score=10.0,
    )

    compiled = compile_context(
        "Was James lonely before meeting Samantha?",
        repo_key="locomo/conv-test",
        ranked=(
            replace(
                _ranked_parent(snippet_text="unused"),
                snippets=(distractor, prior_state),
            ),
        ),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(
            context_snippets_per_memory=1,
            context_temporal_snippets_per_memory=1,
        ),
        evidence_slots=(
            ContextEvidenceSlot(
                kind="prior_state",
                max_facts=4,
                anchors=("james",),
            ),
        ),
    )

    assert compiled.trace is not None
    assert compiled.trace.rendered_fact_ids == ("fact-prior-state",)


def test_prior_state_slot_does_not_compare_raw_indexes_across_parents() -> None:
    relevant = replace(
        _snippet(
            fact_id="fact-relevant-state",
            text=(
                "2021-04-01T10:00:00+00:00 — James: My work friends were the "
                "only people who brought me happiness."
            ),
            raw_event_index=50,
        ),
        source_memory_id="memory-relevant",
        source_uri="codecairn://memory/memory-relevant",
        relevance_score=5.0,
    )
    distractor = replace(
        _snippet(
            fact_id="fact-low-rank-state",
            text=(
                "2022-01-01T10:00:00+00:00 — James: My cat was my only friend when I felt lonely."
            ),
            raw_event_index=1,
        ),
        source_memory_id="memory-distractor",
        source_uri="codecairn://memory/memory-distractor",
        relevance_score=-5.0,
    )
    relevant_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=1,
        memory_id="memory-relevant",
        source_uri="codecairn://memory/memory-relevant",
        snippets=(relevant,),
    )
    distractor_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=5,
        memory_id="memory-distractor",
        source_uri="codecairn://memory/memory-distractor",
        snippets=(distractor,),
    )

    selected = context_slot_candidates(
        ContextEvidenceSlot(
            kind="prior_state",
            max_facts=1,
            anchors=("james",),
        ),
        ranked=(relevant_parent, distractor_parent),
        snippet_values=((relevant,), (distractor,)),
        candidates=((0, 0, relevant), (1, 0, distractor)),
    )

    assert tuple(candidate[2].fact_id for candidate in selected) == ("fact-relevant-state",)


def test_context_renders_flat_source_facts_without_repeating_parent_chrome() -> None:
    ranked = (
        replace(
            _ranked_parent(snippet_text="first complete source fact"),
            title="A deliberately verbose conversation title",
            snippets=(
                _snippet(
                    fact_id="fact-first",
                    text="first complete source fact",
                    raw_event_index=1,
                ),
                _snippet(
                    fact_id="fact-second",
                    text="second complete source fact",
                    raw_event_index=2,
                ),
            ),
        ),
    )

    compiled = compile_context(
        "What complete source facts are relevant?",
        repo_key="locomo/conv-test",
        ranked=ranked,
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(),
    )

    assert "## " not in compiled.markdown
    assert "Evidence excerpts:" not in compiled.markdown
    assert "[fact-first]" in compiled.markdown
    assert "[fact-second]" in compiled.markdown


def test_high_confidence_parent_slot_reserves_bounded_parent_breadth() -> None:
    top_parent = replace(
        _ranked_parent(snippet_text="unused"),
        final_score=6.0,
        snippets=tuple(
            replace(
                _snippet(
                    fact_id=f"fact-top-{index}",
                    text=(f"top parent fact {index} " + "T" * 180),
                    raw_event_index=index,
                ),
                relevance_score=(10.0 - index if index != 4 else -10.0),
                selection_source=FACT_SELECTOR_ID,
            )
            for index in range(1, 7)
        ),
    )
    distractor_parent = replace(
        _ranked_parent(snippet_text="unused"),
        rank=2,
        memory_id="memory-distractor",
        source_uri="codecairn://memory/memory-distractor",
        final_score=5.0,
        snippets=tuple(
            replace(
                _snippet(
                    fact_id=f"fact-distractor-{index}",
                    text=(f"distractor fact {index} " + "D" * 180),
                    raw_event_index=index,
                ),
                source_memory_id="memory-distractor",
                source_uri="codecairn://memory/memory-distractor",
                relevance_score=20.0 - index,
                selection_source=FACT_SELECTOR_ID,
            )
            for index in range(1, 7)
        ),
    )

    compiled = compile_context(
        "What does the top result establish?",
        repo_key="locomo/conv-test",
        ranked=(top_parent, distractor_parent),
        temporal_priority_memory_ids=set(),
        config=RecallPlannerConfig(context_max_tokens=900),
        evidence_slots=(
            ContextEvidenceSlot(
                kind="high_confidence_parent",
                max_facts=4,
                minimum_parent_score=5.5,
            ),
        ),
    )

    assert compiled.trace is not None
    assert "fact-top-4" in compiled.trace.rendered_fact_ids
    assert len(compiled.trace.slot_traces) == 1
    slot_trace = compiled.trace.slot_traces[0]
    assert slot_trace.slot_kind == "high_confidence_parent"
    assert {
        attempt.fact_id for attempt in slot_trace.attempts if attempt.outcome == "admitted"
    } == {
        "fact-top-1",
        "fact-top-2",
        "fact-top-3",
        "fact-top-4",
    }


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
