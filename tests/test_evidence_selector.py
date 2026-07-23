from __future__ import annotations

from dataclasses import replace

from codecairn.memory.evidence_selector import FACT_SELECTOR_ID, EvidenceSelector
from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    RankedRecall,
    RecallSnippet,
    RerankDocument,
    RerankScore,
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
