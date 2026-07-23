from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from codecairn.memory.evidence_selector import (
    FACT_SELECTOR_ID,
    EvidenceSelector,
    _weighted_parent_limits,
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
    ]
    assert selected[0].snippets[0].relevance_score == 10.0
    assert selected[0].snippets[0].selection_source == FACT_SELECTOR_ID


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
            episode_fact_ids=(question.fact_id, answer.fact_id),
        ),
    )
    reranker = CapturingReranker()

    selected = EvidenceSelector(
        reranker=reranker,
        max_candidates=2,
        max_candidates_per_parent=2,
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
    assert {snippet.fact_id for snippet in selected[0].snippets} == {
        "fact-festival",
        "fact-attitude",
    }


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


def test_weighted_parent_limits_move_bounded_work_to_higher_ranked_parents() -> None:
    limits = _weighted_parent_limits(
        20,
        max_candidates=256,
        max_candidates_per_parent=24,
    )

    assert sum(limits) == 256
    assert limits[:4] == (24, 24, 24, 24)
    assert all(limit >= later for limit, later in pairwise(limits))
