from __future__ import annotations

from dataclasses import replace

from codecairn.memory.evidence_selector import (
    FACT_SELECTOR_ID,
    EvidenceSelector,
    _allocate_parent_limits,
)
from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    RankedRecall,
    RecallSnippet,
    RerankDocument,
    RerankScore,
    SemanticAtomicFact,
    SemanticEpisode,
)


class AnswerFirstReranker:
    model_id = "test/answer-first"
    source_id = "test/answer-first"
    revision = "test-v1"
    batch_size = 8

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]:
        assert query == "What was Melanie's favorite book from her childhood?"
        return tuple(
            RerankScore(
                memory_id=document.memory_id,
                score=10.0 if "Charlotte's Web" in document.text else -1.0,
            )
            for document in documents
        )


class CapturingReranker:
    model_id = "test/capturing"
    source_id = "test/capturing"
    revision = "test-v1"
    batch_size = 8

    def __init__(self) -> None:
        self.documents: tuple[RerankDocument, ...] = ()

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]:
        self.documents = documents
        return tuple(RerankScore(memory_id=document.memory_id, score=0.0) for document in documents)


def test_evidence_selector_reranks_all_authoritative_facts_inside_a_parent() -> None:
    reference = EvidenceReference(
        provider="locomo",
        session_id="conv-test/session-1",
        source_path="locomo://fixture/conv-test/session-1",
        raw_event_sha256="a" * 64,
        raw_event_index=1,
        raw_event_type="locomo_turn",
    )
    question_fact = EvidenceFact(
        fact_id="fact-question",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="user_quote",
        text="What favorite book do you remember from your childhood?",
        role="participant",
        actor="Caroline",
        evidence=(reference,),
    )
    answer_fact = EvidenceFact(
        fact_id="fact-answer",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="user_quote",
        text="I loved reading Charlotte's Web as a kid.",
        role="participant",
        actor="Melanie",
        evidence=(replace(reference, raw_event_index=2),),
    )
    memory = CodingMemory(
        memory_id="memory-parent",
        repo_key="locomo/conv-test",
        memory_type="conversation_episode",
        title="Conversation",
        summary="Attributed conversation",
        episode_id="episode-1",
        command=None,
        exit_code=None,
        evidence=(reference,),
        facts=(question_fact, answer_fact),
        content_sha256="b" * 64,
        markdown_path="/runtime/memory-parent.md",
    )
    ranked = (
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri="codecairn://memory/memory-parent",
            content_sha256=memory.content_sha256 or "",
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
                    source_memory_id=memory.memory_id,
                    source_uri="codecairn://memory/memory-parent",
                    fact_id=question_fact.fact_id,
                    text=question_fact.text,
                    source_title=memory.title,
                    source_summary=memory.summary,
                    raw_event_index=1,
                ),
                RecallSnippet(
                    relation="neighbor",
                    source_memory_id="memory-neighbor",
                    source_uri="codecairn://memory/memory-neighbor",
                    fact_id="fact-neighbor",
                    text="A bounded neighbor hint.",
                    source_title="Neighbor",
                    source_summary="Neighbor episode",
                    raw_event_index=3,
                ),
            ),
            episode_fact_ids=(question_fact.fact_id, answer_fact.fact_id),
        ),
    )

    selected = EvidenceSelector(reranker=AnswerFirstReranker()).select(
        "What was Melanie's favorite book from her childhood?",
        ranked=ranked,
        memories={memory.memory_id: memory},
    )

    assert [snippet.fact_id for snippet in selected[0].snippets] == [
        "fact-answer",
        "fact-question",
        "fact-neighbor",
    ]
    assert selected[0].snippets[0].relevance_score == 10.0
    assert selected[0].snippets[0].selection_source == FACT_SELECTOR_ID
    assert selected[0].snippets[-1].relevance_score is None
    assert selected[0].snippets[-1].selection_source is None


def test_evidence_selector_bounds_each_local_reranker_document() -> None:
    reference = EvidenceReference(
        provider="locomo",
        session_id="conv-test/session-1",
        source_path="locomo://fixture/conv-test/session-1",
        raw_event_sha256="a" * 64,
        raw_event_index=1,
        raw_event_type="locomo_turn",
    )
    fact = EvidenceFact(
        fact_id="fact-long",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="user_quote",
        text="HEAD-" + "x" * 2_000 + "-TAIL",
        role="participant",
        actor="Caroline",
        evidence=(reference,),
    )
    memory = CodingMemory(
        memory_id="memory-parent",
        repo_key="locomo/conv-test",
        memory_type="conversation_episode",
        title="Conversation",
        summary="Attributed conversation",
        episode_id="episode-1",
        command=None,
        exit_code=None,
        evidence=(reference,),
        facts=(fact,),
        content_sha256="b" * 64,
        markdown_path="/runtime/memory-parent.md",
    )
    ranked = (
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri="codecairn://memory/memory-parent",
            content_sha256=memory.content_sha256 or "",
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=1.0,
            lexical_rank=1,
            final_score=1.0,
            evidence=(),
            snippets=(),
            episode_fact_ids=(fact.fact_id,),
        ),
    )
    reranker = CapturingReranker()

    EvidenceSelector(reranker=reranker, max_document_chars=256).select(
        "What happened?",
        ranked=ranked,
        memories={memory.memory_id: memory},
    )

    assert len(reranker.documents) == 1
    assert len(reranker.documents[0].text) == 256
    assert "\n…\n" in reranker.documents[0].text
    assert "HEAD-" in reranker.documents[0].text
    assert "-TAIL" in reranker.documents[0].text


def test_evidence_selector_preserves_a_short_answer_and_reranks_it_with_previous_turn() -> None:
    reference = EvidenceReference(
        provider="locomo",
        session_id="conv-test/session-1",
        source_path="locomo://fixture/conv-test/session-1",
        raw_event_sha256="a" * 64,
        raw_event_index=1,
        raw_event_type="locomo_turn",
    )
    question = EvidenceFact(
        fact_id="fact-festival",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="conversation_turn",
        text="Are you glad that your dancers will perform at the festival?",
        role="participant",
        actor="Gina",
        evidence=(reference,),
    )
    answer = EvidenceFact(
        fact_id="fact-attitude",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="conversation_turn",
        text="Yeah, awesome! Glad to be part of it.",
        role="participant",
        actor="Jon",
        evidence=(replace(reference, raw_event_index=2),),
    )
    self_contained = EvidenceFact(
        fact_id="fact-self-contained",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="conversation_turn",
        text=(
            "I independently organized a detailed community workshop about sustainable "
            "dance costumes for the autumn festival."
        ),
        role="participant",
        actor="Gina",
        evidence=(replace(reference, raw_event_index=3),),
    )
    memory = CodingMemory(
        memory_id="memory-parent",
        repo_key="locomo/conv-test",
        memory_type="conversation_episode",
        title="Conversation",
        summary="Attributed conversation",
        episode_id="episode-1",
        command=None,
        exit_code=None,
        evidence=(reference,),
        facts=(question, answer, self_contained),
        content_sha256="b" * 64,
        markdown_path="/runtime/memory-parent.md",
    )
    ranked = (
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri="codecairn://memory/memory-parent",
            content_sha256=memory.content_sha256 or "",
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
                    source_memory_id=memory.memory_id,
                    source_uri="codecairn://memory/memory-parent",
                    fact_id=question.fact_id,
                    text=question.text,
                    source_title=memory.title,
                    source_summary=memory.summary,
                    raw_event_index=1,
                ),
                RecallSnippet(
                    relation="sibling",
                    source_memory_id=memory.memory_id,
                    source_uri="codecairn://memory/memory-parent",
                    fact_id=answer.fact_id,
                    text=answer.text,
                    source_title=memory.title,
                    source_summary=memory.summary,
                    raw_event_index=2,
                ),
            ),
            episode_fact_ids=(question.fact_id, answer.fact_id, self_contained.fact_id),
        ),
    )
    reranker = CapturingReranker()

    selected = EvidenceSelector(
        reranker=reranker,
        max_candidates=3,
        max_candidates_per_parent=3,
        max_selected_per_parent=2,
    ).select(
        "What is Jon's attitude towards being part of the dance festival?",
        ranked=ranked,
        memories={memory.memory_id: memory},
    )

    answer_document = next(
        document
        for document in reranker.documents
        if "Target turn:\nJon: Yeah, awesome!" in document.text
    )
    assert "Previous turn:\nGina: Are you glad" in answer_document.text
    self_contained_document = next(
        document
        for document in reranker.documents
        if "Target turn:\nGina: I independently organized" in document.text
    )
    assert "Previous turn:" not in self_contained_document.text
    assert {snippet.fact_id for snippet in selected[0].snippets} == {
        "fact-festival",
        "fact-attitude",
    }


def test_evidence_selector_reranks_a_long_answer_with_its_question_context() -> None:
    reference = EvidenceReference(
        provider="locomo",
        session_id="conv-test/session-1",
        source_path="locomo://fixture/conv-test/session-1",
        raw_event_sha256="a" * 64,
        raw_event_index=1,
        raw_event_type="locomo_turn",
    )
    question = EvidenceFact(
        fact_id="fact-career-question",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="conversation_turn",
        text="Did the accident change your career plans?",
        role="participant",
        actor="Melanie",
        evidence=(reference,),
    )
    answer = EvidenceFact(
        fact_id="fact-career-answer",
        repo_key=question.repo_key,
        episode_id=question.episode_id,
        kind="conversation_turn",
        text=(
            "The experience completely changed my career plans, so I decided to study "
            "physical therapy after graduation and work with injured athletes."
        ),
        role="participant",
        actor="Caroline",
        evidence=(replace(reference, raw_event_index=2),),
    )
    memory = CodingMemory(
        memory_id="memory-parent",
        repo_key=question.repo_key,
        memory_type="conversation_episode",
        title="Conversation",
        summary="Attributed conversation",
        episode_id=question.episode_id,
        command=None,
        exit_code=None,
        evidence=question.evidence + answer.evidence,
        facts=(question, answer),
        content_sha256="b" * 64,
        markdown_path="/runtime/memory-parent.md",
    )
    ranked = (
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri="codecairn://memory/memory-parent",
            content_sha256=memory.content_sha256 or "",
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=1.0,
            lexical_rank=1,
            final_score=1.0,
            evidence=(),
            snippets=(),
            episode_fact_ids=(question.fact_id, answer.fact_id),
        ),
    )
    reranker = CapturingReranker()

    EvidenceSelector(reranker=reranker).select(
        "How did the accident affect Caroline's career?",
        ranked=ranked,
        memories={memory.memory_id: memory},
    )

    answer_document = next(
        document
        for document in reranker.documents
        if "Target turn:\nCaroline: The experience completely changed" in document.text
    )
    assert (
        "Previous turn:\nMelanie: Did the accident change your career plans?"
        in answer_document.text
    )


def test_evidence_selector_keeps_exact_text_beside_single_source_semantic_text() -> None:
    reference = EvidenceReference(
        provider="locomo",
        session_id="conv-test/session-1",
        source_path="locomo://fixture/conv-test/session-1",
        raw_event_sha256="a" * 64,
        raw_event_index=1,
        raw_event_type="locomo_turn",
    )
    fact = EvidenceFact(
        fact_id="fact-answer",
        repo_key="locomo/conv-test",
        episode_id="episode-1",
        kind="conversation_turn",
        text="A long exact authoritative answer.",
        role="participant",
        actor="Melanie",
        evidence=(reference,),
    )
    semantic = SemanticAtomicFact(
        fact_id="semantic-answer",
        text="Melanie gave the concise answer.",
        source_fact_ids=(fact.fact_id,),
    )
    memory = CodingMemory(
        memory_id="memory-parent",
        repo_key="locomo/conv-test",
        memory_type="conversation_episode",
        title="Conversation",
        summary="Attributed conversation",
        episode_id="episode-1",
        command=None,
        exit_code=None,
        evidence=(reference,),
        facts=(fact,),
        content_sha256="b" * 64,
        markdown_path="/runtime/memory-parent.md",
        semantic_episode=SemanticEpisode(
            episode_id="episode-1",
            narrative=semantic.text,
            atomic_facts=(semantic,),
            source_fact_ids=(fact.fact_id,),
            semanticizer_id="test/semanticizer",
            revision="test-v1",
        ),
    )
    ranked = (
        RankedRecall(
            rank=1,
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            title=memory.title,
            summary=memory.summary,
            source_uri="codecairn://memory/memory-parent",
            content_sha256=memory.content_sha256 or "",
            candidate_sources=("lexical",),
            vector_score=None,
            vector_rank=None,
            lexical_score=1.0,
            lexical_rank=1,
            final_score=1.0,
            evidence=(),
            episode_fact_ids=(fact.fact_id,),
        ),
    )

    selected = EvidenceSelector(reranker=CapturingReranker()).select(
        "What was the answer?",
        ranked=ranked,
        memories={memory.memory_id: memory},
    )

    snippet = selected[0].snippets[0]
    assert snippet.text == "Melanie: A long exact authoritative answer."
    assert snippet.semantic_text == semantic.text
    assert snippet.semantic_fact_ids == (semantic.fact_id,)


def test_parent_limits_preserve_breadth_then_follow_direct_evidence_and_capacity() -> None:
    limits = _allocate_parent_limits(
        (24, 20, 8),
        (1, 2, 3),
        max_candidates=32,
        coverage_floor=4,
    )

    assert limits == (4, 20, 8)


def test_parent_limits_never_exceed_a_small_global_budget() -> None:
    limits = _allocate_parent_limits(
        (24, 24, 24),
        (3, 2, 1),
        max_candidates=2,
        coverage_floor=12,
    )

    assert limits == (1, 1, 0)
