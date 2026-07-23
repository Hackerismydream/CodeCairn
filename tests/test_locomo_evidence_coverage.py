from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from codecairn.evaluation.locomo import (
    ConversationIngestResult,
    LoCoMoConversation,
    LoCoMoRunConfig,
    run_locomo,
)
from codecairn.evaluation.locomo_evidence import (
    LoCoMoEvidenceCoverageConfig,
    classify_evidence_coverage,
    report_locomo_evidence_coverage,
)
from codecairn.evaluation.locomo_oracle import compile_locomo_source_facts
from codecairn.memory.context import count_context_tokens
from codecairn.memory.models import (
    RankedRecall,
    RecallContextTrace,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
)

FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"


class _UnrelatedMemory:
    semantic_projection: ClassVar[dict[str, object]] = {
        "adapter": "codecairn/lossless-clause",
        "model": None,
        "revision": "v1",
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        del dataset_sha256
        return ConversationIngestResult(
            session_count=len(conversation.sessions),
            turn_count=sum(len(session.turns) for session in conversation.sessions),
            accepted_memory_count=len(conversation.sessions),
            rejected_memory_count=0,
            semantic_source_fact_count=sum(len(session.turns) for session in conversation.sessions),
            semantic_referenced_source_fact_count=sum(
                len(session.turns) for session in conversation.sessions
            ),
            semantic_atomic_fact_count=sum(len(session.turns) for session in conversation.sessions),
            semantic_empty_episode_count=0,
        )

    def recall(self, question: str, *, limit: int) -> RecallResult:
        markdown = "# Recall Context\n\n- [unrelated-fact] Unrelated evidence.\n"
        snippet = RecallSnippet(
            relation="matched",
            source_memory_id="unrelated-memory",
            source_uri="codecairn://memory/unrelated-memory",
            fact_id="unrelated-fact",
            text="Unrelated evidence.",
            source_title="Unrelated",
            source_summary="Unrelated",
            raw_event_index=0,
        )
        ranked = RankedRecall(
            rank=1,
            memory_id="unrelated-memory",
            memory_type="conversation_episode",
            title="Unrelated",
            summary="Unrelated",
            source_uri="codecairn://memory/unrelated-memory",
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
        return RecallResult(
            markdown=markdown,
            sidecar=RecallSidecar(
                query=question,
                repo_key=f"locomo/{self._root.name}",
                limit=limit,
                latency_ms=1.0,
                vector_candidate_count=0,
                lexical_candidate_count=1,
                ranked=(ranked,),
                context_trace=RecallContextTrace(
                    renderer="facts-first-round-robin-v4",
                    char_count=len(markdown),
                    rendered_memory_ids=("unrelated-memory",),
                    rendered_fact_ids=("unrelated-fact",),
                    omitted_memory_ids=(),
                    omitted_snippet_count=0,
                    token_count=count_context_tokens(markdown),
                ),
            ),
        )

    def corpus_snapshot(self) -> dict[str, object]:
        return {"adapter": "unrelated"}


class _ParentOnlyGoldMemory(_UnrelatedMemory):
    _fact_ids_by_root: ClassVar[dict[Path, tuple[str, ...]]] = {}

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._parent_fact_ids = self._fact_ids_by_root.get(root.resolve(), ())

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        self._parent_fact_ids = tuple(
            item.fact.fact_id
            for item in compile_locomo_source_facts(
                conversation,
                dataset_sha256=dataset_sha256,
            )
        )
        self._fact_ids_by_root[self._root.resolve()] = self._parent_fact_ids
        return super().ingest(conversation, dataset_sha256=dataset_sha256)

    def recall(self, question: str, *, limit: int) -> RecallResult:
        result = super().recall(question, limit=limit)
        ranked = replace(
            result.sidecar.ranked[0],
            episode_fact_ids=self._parent_fact_ids,
        )
        return replace(
            result,
            sidecar=replace(result.sidecar, ranked=(ranked,)),
        )


def test_classify_evidence_coverage_distinguishes_retrieval_failure_stages() -> None:
    gold = ("fact-a", "fact-b")

    assert (
        classify_evidence_coverage(
            gold_fact_ids=gold,
            observed_fact_ids={"fact-a", "fact-b", "fact-c"},
        ).status
        == "all"
    )
    assert (
        classify_evidence_coverage(
            gold_fact_ids=gold,
            observed_fact_ids={"fact-a"},
        ).status
        == "partial"
    )
    assert (
        classify_evidence_coverage(
            gold_fact_ids=gold,
            observed_fact_ids={"fact-c"},
        ).status
        == "none"
    )


def test_classify_evidence_coverage_keeps_dataset_failures_out_of_denominator() -> None:
    assert (
        classify_evidence_coverage(
            gold_fact_ids=(),
            observed_fact_ids=set(),
        ).status
        == "no_gold"
    )
    assert (
        classify_evidence_coverage(
            gold_fact_ids=("fact-a",),
            observed_fact_ids={"fact-a"},
            has_unknown_gold=True,
        ).status
        == "unknown_gold"
    )


def test_evidence_coverage_report_is_provider_free_and_writes_an_artifact(
    tmp_path: Path,
) -> None:
    run = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="unrelated-retrieval",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
        ),
        memory_factory=_UnrelatedMemory,
        answer_model=None,
        judge_model=None,
    )
    output_path = tmp_path / "evidence-coverage.json"

    report = report_locomo_evidence_coverage(
        LoCoMoEvidenceCoverageConfig(
            run_dir=run.run_dir,
            dataset_path=FIXTURE,
            output_path=output_path,
        )
    )

    overall = report["overall"]
    assert isinstance(overall, dict)
    assert overall["resolvable_question_count"] == 4
    assert overall["ranked_all_coverage"] == 0.0
    assert overall["context_all_coverage"] == 0.0
    assert overall["oracle_context_buildable_rate"] == 1.0
    assert output_path.is_file()


def test_evidence_report_separates_ranked_parent_and_candidate_snippet_coverage(
    tmp_path: Path,
) -> None:
    run = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="parent-only-gold",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
        ),
        memory_factory=_ParentOnlyGoldMemory,
        answer_model=None,
        judge_model=None,
    )

    report = report_locomo_evidence_coverage(
        LoCoMoEvidenceCoverageConfig(
            run_dir=run.run_dir,
            dataset_path=FIXTURE,
        )
    )

    assert report["schema_version"] == 2
    overall = report["overall"]
    assert isinstance(overall, dict)
    assert overall["ranked_all_coverage"] == 1.0
    assert overall["candidate_snippet_all_coverage"] == 0.0
    assert overall["context_all_coverage"] == 0.0
    questions = report["questions"]
    assert isinstance(questions, list)
    assert {
        item["candidate_snippet_coverage"]
        for item in questions
        if isinstance(item, dict) and item["ranked_coverage"] != "no_gold"
    } == {"none"}
