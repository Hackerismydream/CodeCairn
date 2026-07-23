from __future__ import annotations

import json
from dataclasses import replace
from typing import ClassVar

import pytest

from codecairn.evaluation.locomo import (
    EvidenceAnswerSynthesisFailure,
    EvidenceAnswerSynthesizer,
    LoCoMoQuery,
)
from codecairn.evaluation.model import ModelResponse
from codecairn.memory.context import count_context_tokens
from codecairn.memory.models import (
    RankedRecall,
    RecallContextTrace,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
)


def test_locomo_answer_is_structured_and_cites_rendered_source_facts() -> None:
    model = _AnswerModel(
        {
            "answer": "A beagle named Poppy.",
            "supporting_evidence_ids": ["fact-1"],
            "insufficient": False,
        }
    )

    synthesis = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(question_id="q-1", text="What did Caroline adopt?"),
        speakers=("Caroline", "Melanie"),
        recall=_recall(),
        model=model,
        seed=7,
    )

    assert synthesis.response.text == "A beagle named Poppy."
    assert synthesis.evidence_ids == ("fact-1",)
    assert synthesis.invalid_evidence_ids == ()
    assert synthesis.format == "structured-v1"
    assert model.calls[0]["response_format"] == "json"
    request = json.loads(str(model.calls[0]["user"]))
    assert request["rendered_evidence"] == [
        {
            "source_fact_id": "fact-1",
            "source_uri": "codecairn://memory/memory-1",
        }
    ]


def test_answer_payload_has_one_budgeted_evidence_channel() -> None:
    fact_text = "Caroline: " + "Poppy is a beagle. " * 200
    recall = _recall()
    ranked = replace(
        recall.sidecar.ranked[0],
        snippets=(replace(recall.sidecar.ranked[0].snippets[0], text=fact_text),),
    )
    markdown = f"# Recall Context\n\n- [fact-1] {fact_text}\n"
    recall = replace(
        recall,
        markdown=markdown,
        sidecar=replace(
            recall.sidecar,
            ranked=(ranked,),
            context_trace=replace(
                recall.sidecar.context_trace,
                char_count=len(markdown),
                token_count=count_context_tokens(markdown),
            ),
        ),
    )
    model = _AnswerModel(
        {
            "answer": "A beagle named Poppy.",
            "supporting_evidence_ids": ["fact-1"],
            "insufficient": False,
        }
    )

    EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(question_id="q-1", text="What did Caroline adopt?"),
        speakers=("Caroline", "Melanie"),
        recall=recall,
        model=model,
        seed=7,
    )

    raw_request = str(model.calls[0]["user"])
    request = json.loads(raw_request)
    assert raw_request.count(fact_text) == 1
    assert request["memory_context"] == markdown
    assert request["rendered_evidence"] == [
        {
            "source_fact_id": "fact-1",
            "source_uri": "codecairn://memory/memory-1",
        }
    ]
    assert count_context_tokens(request["memory_context"]) <= 4_000


def test_answer_rejects_trace_fact_missing_from_markdown() -> None:
    recall = _recall()
    markdown = "# Recall Context\n\n- No source fact marker is present.\n"
    recall = replace(
        recall,
        markdown=markdown,
        sidecar=replace(
            recall.sidecar,
            context_trace=replace(
                recall.sidecar.context_trace,
                char_count=len(markdown),
                token_count=count_context_tokens(markdown),
            ),
        ),
    )
    model = _AnswerModel(
        {
            "answer": "A beagle named Poppy.",
            "supporting_evidence_ids": ["fact-1"],
            "insufficient": False,
        }
    )

    with pytest.raises(ValueError, match="missing from its Markdown"):
        EvidenceAnswerSynthesizer().synthesize(
            LoCoMoQuery(question_id="q-1", text="What did Caroline adopt?"),
            speakers=("Caroline", "Melanie"),
            recall=recall,
            model=model,
            seed=7,
        )

    assert model.calls == []


def test_locomo_answer_rejects_an_omitted_or_unknown_citation() -> None:
    model = _AnswerModel(
        {
            "answer": "A beagle named Poppy.",
            "supporting_evidence_ids": ["fact-2"],
            "insufficient": False,
        }
    )

    with pytest.raises(EvidenceAnswerSynthesisFailure) as captured:
        EvidenceAnswerSynthesizer().synthesize(
            LoCoMoQuery(question_id="q-1", text="What did Caroline adopt?"),
            speakers=("Caroline", "Melanie"),
            recall=_recall(),
            model=model,
            seed=7,
        )
    assert captured.value.status.value == "contract_exhausted"
    assert captured.value.receipt["attempt_count"] == 2


class _AnswerModel:
    model_id = "fixture-answer"
    public_config: ClassVar[dict[str, object]] = {"adapter": "fixture"}

    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "seed": seed,
                "response_format": response_format,
            }
        )
        return ModelResponse(
            text=json.dumps(self._response),
            model=self.model_id,
            input_tokens=20,
            output_tokens=10,
        )


def _recall() -> RecallResult:
    snippet = RecallSnippet(
        relation="matched",
        source_memory_id="memory-1",
        source_uri="codecairn://memory/memory-1",
        fact_id="fact-1",
        text="Caroline: I adopted a beagle named Poppy.",
        source_title="Conversation",
        source_summary="One source turn.",
        raw_event_index=1,
    )
    ranked = RankedRecall(
        rank=1,
        memory_id="memory-1",
        memory_type="conversation_episode",
        title="Conversation",
        summary="One source turn.",
        source_uri="codecairn://memory/memory-1",
        content_sha256="a" * 64,
        candidate_sources=("lexical",),
        vector_score=None,
        vector_rank=None,
        lexical_score=1.0,
        lexical_rank=1,
        final_score=1.0,
        evidence=(),
        snippets=(snippet,),
    )
    markdown = "# Recall Context\n\n- [fact-1] Caroline adopted Poppy.\n"
    sidecar = RecallSidecar(
        query="What did Caroline adopt?",
        repo_key="locomo/conv-test",
        limit=1,
        latency_ms=1.0,
        vector_candidate_count=0,
        lexical_candidate_count=1,
        ranked=(ranked,),
        context_trace=RecallContextTrace(
            renderer="facts-first-round-robin-v4",
            char_count=len(markdown),
            rendered_memory_ids=("memory-1",),
            rendered_fact_ids=("fact-1",),
            omitted_memory_ids=(),
            omitted_snippet_count=0,
            token_count=count_context_tokens(markdown),
        ),
    )
    return RecallResult(
        markdown=markdown,
        sidecar=sidecar,
    )
