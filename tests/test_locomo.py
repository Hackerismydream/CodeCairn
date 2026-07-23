from __future__ import annotations

import hashlib
import json
import shutil
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.evaluation.artifacts import canonical_json, read_json, write_json_exclusive
from codecairn.evaluation.attempt_journal import ModelAttemptJournal
from codecairn.evaluation.locomo import (
    CodeCairnConversationMemory,
    ConversationIngestResult,
    EvidenceAnswerSynthesizer,
    FrozenQueryEmbeddingAdapter,
    LoCoMoConversation,
    LoCoMoConversationWork,
    LoCoMoCorpusConfig,
    LoCoMoQuery,
    LoCoMoQueryVectorConfig,
    LoCoMoQuestionSet,
    LoCoMoRunConfig,
    build_locomo_corpus,
    build_locomo_query_vectors,
    load_locomo_dataset,
    load_locomo_question_set,
    report_locomo,
    run_locomo,
    run_locomo_conversation_questions,
    validate_locomo_corpus_conversation,
    validate_locomo_corpus_preflight,
)
from codecairn.evaluation.locomo import _recall_question as recall_question
from codecairn.evaluation.locomo import _run_question as run_question
from codecairn.evaluation.locomo import _validate_run_protocol as validate_run_protocol
from codecairn.evaluation.locomo import (
    _validate_scored_fact_selection as validate_scored_fact_selection,
)
from codecairn.evaluation.locomo_ablation import (
    LoCoMoAblationConfig,
    build_locomo_ablation_report,
)
from codecairn.evaluation.model import ModelResponse
from codecairn.evaluation.providers import OpenAICompatibleTextModel
from codecairn.memory.context import CONTEXT_RENDERER_ID
from codecairn.memory.evidence_selector import FACT_SELECTOR_ID
from codecairn.memory.models import (
    RankedRecall,
    RecallContextTrace,
    RecallResult,
    RecallSidecar,
    RecallSnippet,
)
from codecairn.memory.recall_planner import RecallPlannerConfig
from codecairn.memory.retrieval import RetrievalProviders, retrieval_config_sha256
from codecairn.storage.sqlite import SQLiteState

FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"
FAKE_RETRIEVAL_CONFIG: dict[str, object] = {
    "method": "hybrid-rrf-cross-encoder",
    "inference_threads": 2,
    "tokenizer_parallelism": False,
    "tokenizer_threads": 1,
    "embedding": {
        "model": "test/embedding",
        "source": "test/embedding-source",
        "revision": "a" * 40,
        "dimension": 3,
    },
    "reranker": {
        "model": "test/reranker",
        "source": "test/reranker-source",
        "revision": "b" * 40,
    },
}
LOSSLESS_SEMANTIC_PROJECTION: dict[str, object] = {
    "adapter": "codecairn/lossless-clause",
    "model": None,
    "revision": "v1",
}


def _write_corpus_protocol_question_set(
    tmp_path: Path,
    *,
    reranker_batch_size: int,
) -> tuple[Path, dict[str, object]]:
    dataset = load_locomo_dataset(FIXTURE)
    selected = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 2, 3, 4}
    )
    definition = json.loads(
        (Path(__file__).parents[1] / "benchmarks/locomo/diagnostic-200-v15.json").read_text(
            encoding="utf-8"
        )
    )
    protocol = definition["protocol"]
    protocol.update(
        {
            "embedding_adapter": "fake-embedding",
            "embedding_model": "test/embedding",
            "embedding_dimension": 3,
            "reranker_model": "test/reranker",
            "reranker_batch_size": reranker_batch_size,
        }
    )
    question_set_path = tmp_path / f"frozen-corpus-protocol-{reranker_batch_size}.json"
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "synthetic-corpus-protocol",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {str(category): 1 for category in range(1, 5)},
            "selection_sha256": hashlib.sha256(
                json.dumps(sorted(selected), separators=(",", ":")).encode()
            ).hexdigest(),
            "protocol": protocol,
        },
    )
    retrieval_config: dict[str, object] = {
        "method": "hybrid-rrf-cross-encoder",
        "inference_threads": 2,
        "tokenizer_parallelism": False,
        "tokenizer_threads": 1,
        "embedding": {
            "adapter": "fake-embedding",
            "model": "test/embedding",
            "source": "test/embedding-source",
            "revision": "a" * 40,
            "dimension": 3,
        },
        "reranker": {
            "model": "test/reranker",
            "source": "test/reranker-source",
            "revision": "b" * 40,
            "batch_size": 8,
        },
        "planner": RecallPlannerConfig().public_config,
    }
    return question_set_path, retrieval_config


def _write_paid_embedding_corpus_contract(
    tmp_path: Path,
) -> tuple[Path, dict[str, object]]:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    definition = read_json(question_set_path)
    protocol = definition["protocol"]
    assert isinstance(protocol, dict)
    protocol.update(
        {
            "embedding_adapter": "dashscope-openai-compatible",
            "embedding_model": "text-embedding-v4",
            "embedding_dimension": 3,
        }
    )
    question_set_path.unlink()
    write_json_exclusive(question_set_path, definition)
    retrieval_config["embedding"] = {
        "adapter": "dashscope-openai-compatible",
        "adapter_version": "1",
        "model": "text-embedding-v4",
        "source": "https://dashscope.example/v1",
        "revision": "provider-managed",
        "dimension": 3,
        "pricing": {
            "currency": "CNY",
            "input_per_million": 0.5,
        },
    }
    return question_set_path, retrieval_config


class FakeMemory:
    semantic_projection = LOSSLESS_SEMANTIC_PROJECTION

    def __init__(self, root: Path) -> None:
        self.root = root

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        turn_count = sum(len(session.turns) for session in conversation.sessions)
        result = ConversationIngestResult(
            session_count=len(conversation.sessions),
            turn_count=turn_count,
            accepted_memory_count=sum(bool(session.turns) for session in conversation.sessions),
            rejected_memory_count=0,
            semantic_source_fact_count=turn_count,
            semantic_referenced_source_fact_count=turn_count,
            semantic_atomic_fact_count=turn_count,
            semantic_empty_episode_count=0,
        )
        write_json_exclusive(
            self.root / "fake-semantic-counts.json",
            {
                field: getattr(result, field)
                for field in (
                    "semantic_source_fact_count",
                    "semantic_referenced_source_fact_count",
                    "semantic_atomic_fact_count",
                    "semantic_empty_episode_count",
                )
            },
        )
        return result

    def recall(self, question: str, *, limit: int) -> RecallResult:
        snippet = RecallSnippet(
            relation="matched",
            source_memory_id="fixture-memory",
            source_uri="codecairn://memory/fixture-memory",
            fact_id="fixture-evidence",
            text="A relevant attributed memory.",
            source_title="Fixture memory",
            source_summary="A relevant attributed memory.",
            raw_event_index=1,
            relevance_score=1.0,
            selection_source=FACT_SELECTOR_ID,
        )
        ranked = RankedRecall(
            rank=1,
            memory_id="fixture-memory",
            memory_type="conversation_episode",
            title="Fixture memory",
            summary="A relevant attributed memory.",
            source_uri="codecairn://memory/fixture-memory",
            content_sha256="f" * 64,
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
            markdown=("# Recall Context\n\n- [fixture-evidence] A relevant attributed memory.\n"),
            sidecar=RecallSidecar(
                query=question,
                repo_key=f"locomo/{self.root.name}",
                limit=limit,
                latency_ms=1.25,
                vector_candidate_count=1,
                lexical_candidate_count=1,
                ranked=(ranked,),
                reranker_model="test/reranker",
                reranker_source="test/reranker-source",
                reranker_revision="b" * 40,
                embedding_model="test/embedding",
                embedding_source="test/embedding-source",
                embedding_revision="a" * 40,
                retrieval_config_sha256=retrieval_config_sha256(FAKE_RETRIEVAL_CONFIG),
                query_sketcher_id="codecairn/deterministic-query-sketch-v2",
                expansion_fact_limit=24,
                context_trace=RecallContextTrace(
                    renderer=CONTEXT_RENDERER_ID,
                    char_count=69,
                    rendered_memory_ids=("fixture-memory",),
                    rendered_fact_ids=("fixture-evidence",),
                    omitted_memory_ids=(),
                    omitted_snippet_count=0,
                    token_count=35,
                ),
            ),
        )

    def corpus_snapshot(self) -> dict[str, object]:
        return {
            "adapter": "fake",
            "sample_id": self.root.name,
            "semantic_counts": json.loads(
                (self.root / "fake-semantic-counts.json").read_text(encoding="utf-8")
            ),
        }


@dataclass
class FakeAnswerModel:
    calls: int = 0

    @property
    def model_id(self) -> str:
        return "fake-answer"

    @property
    def public_config(self) -> dict[str, object]:
        return {"adapter": "fake", "model": self.model_id}

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=json.dumps(
                {
                    "answer": "A concise answer",
                    "supporting_evidence_ids": ["fixture-evidence"],
                    "insufficient": False,
                }
            ),
            model=self.model_id,
            input_tokens=10,
            output_tokens=3,
            cost_usd=0.001,
        )


@dataclass
class PricedMissingCostAnswerModel(FakeAnswerModel):
    @property
    def public_config(self) -> dict[str, object]:
        return {
            "adapter": "fake",
            "model": self.model_id,
            "pricing": {
                "currency": "CNY",
                "cached_input_per_million": 0.02,
                "uncached_input_per_million": 1.0,
                "output_per_million": 2.0,
            },
        }

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        del system, user, seed, response_format
        self.calls += 1
        return ModelResponse(
            text=json.dumps(
                {
                    "answer": "A concise answer",
                    "supporting_evidence_ids": ["fixture-evidence"],
                    "insufficient": False,
                }
            ),
            model=self.model_id,
            input_tokens=10,
            output_tokens=3,
            cached_input_tokens=6,
            uncached_input_tokens=4,
        )


@dataclass
class AlternatingJudgeModel:
    calls: int = 0

    @property
    def model_id(self) -> str:
        return "fake-judge"

    @property
    def public_config(self) -> dict[str, object]:
        return {"adapter": "fake", "model": self.model_id}

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        label = "WRONG" if self.calls % 3 == 1 else "CORRECT"
        self.calls += 1
        return ModelResponse(
            text=f'{{"label": "{label}"}}',
            model=self.model_id,
            input_tokens=8,
            output_tokens=2,
        )


@dataclass
class MalformedThenValidJudgeModel:
    calls: int = 0
    seeds: list[int] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    first_response: str = "not-json"
    cost_usd: float | None = 0.1
    cost_cny: float | None = None

    @property
    def model_id(self) -> str:
        return "fake-judge"

    @property
    def public_config(self) -> dict[str, object]:
        return {"adapter": "fake", "model": self.model_id}

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        self.seeds.append(seed)
        self.systems.append(system)
        text = self.first_response if self.calls == 1 else '{"label": "CORRECT"}'
        return ModelResponse(
            text=text,
            model=self.model_id,
            input_tokens=8,
            output_tokens=2,
            cost_usd=self.cost_usd,
            cost_cny=self.cost_cny,
        )


@dataclass
class AlwaysMalformedJudgeModel(MalformedThenValidJudgeModel):
    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text="not-json",
            model=self.model_id,
            input_tokens=8,
            output_tokens=2,
        )


@dataclass
class FailingAnswerModel(FakeAnswerModel):
    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        raise RuntimeError("provider unavailable")


@dataclass
class MalformedThenValidAnswerModel(FakeAnswerModel):
    seeds: list[int] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        self.seeds.append(seed)
        self.systems.append(system)
        text = (
            "not-json"
            if self.calls == 1
            else json.dumps(
                {
                    "answer": "A concise answer",
                    "supporting_evidence_ids": ["fixture-evidence"],
                    "insufficient": False,
                }
            )
        )
        return ModelResponse(
            text=text,
            model=self.model_id,
            input_tokens=10,
            output_tokens=3,
            cost_cny=0.001,
        )


@dataclass
class AlwaysUnknownCitationAnswerModel(FakeAnswerModel):
    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=json.dumps(
                {
                    "answer": "A forged answer",
                    "supporting_evidence_ids": ["forged-evidence"],
                    "insufficient": False,
                }
            ),
            model=self.model_id,
            input_tokens=10,
            output_tokens=3,
            cost_cny=0.001,
        )


@dataclass
class CnyAnswerModel(FakeAnswerModel):
    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=json.dumps(
                {
                    "answer": "A concise answer",
                    "supporting_evidence_ids": ["fixture-evidence"],
                    "insufficient": False,
                }
            ),
            model=self.model_id,
            input_tokens=10,
            output_tokens=3,
            cached_input_tokens=6,
            uncached_input_tokens=4,
            reasoning_tokens=2,
            cost_cny=0.001,
        )


@dataclass
class CapturingTextModel(FakeAnswerModel):
    requests: list[tuple[str, str, str]] = field(default_factory=list)

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self.calls += 1
        self.requests.append((system, user, response_format))
        text = (
            json.dumps(
                {
                    "answer": "A concise answer",
                    "supporting_evidence_ids": ["fixture-evidence"],
                    "insufficient": False,
                }
            )
            if "supporting_evidence_ids" in system
            else '{"label":"CORRECT"}'
        )
        return ModelResponse(text=text, model=self.model_id)


class StagedConcurrencyMemory(FakeMemory):
    lock = threading.Lock()
    ingest_barrier = threading.Barrier(2)
    recall_barrier = threading.Barrier(2)
    active_ingests = 0
    max_active_ingests = 0
    active_recalls = 0
    max_active_recalls = 0
    recall_thread_ids: ClassVar[set[int]] = set()

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        with self.lock:
            type(self).active_ingests += 1
            type(self).max_active_ingests = max(
                type(self).max_active_ingests,
                type(self).active_ingests,
            )
        try:
            with suppress(threading.BrokenBarrierError):
                self.ingest_barrier.wait(timeout=0.2)
            return super().ingest(conversation, dataset_sha256=dataset_sha256)
        finally:
            with self.lock:
                type(self).active_ingests -= 1

    def recall(self, question: str, *, limit: int) -> RecallResult:
        with self.lock:
            type(self).active_recalls += 1
            type(self).recall_thread_ids.add(threading.get_ident())
            type(self).max_active_recalls = max(
                type(self).max_active_recalls,
                type(self).active_recalls,
            )
        try:
            with suppress(threading.BrokenBarrierError):
                self.recall_barrier.wait(timeout=0.2)
            return super().recall(question, limit=limit)
        finally:
            with self.lock:
                type(self).active_recalls -= 1


class ConcurrentAnswerModel(FakeAnswerModel):
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    active_calls = 0
    max_active_calls = 0

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        with self.lock:
            type(self).active_calls += 1
            type(self).max_active_calls = max(
                type(self).max_active_calls,
                type(self).active_calls,
            )
        try:
            self.barrier.wait(timeout=2)
            return super().generate(
                system=system,
                user=user,
                seed=seed,
                response_format=response_format,
            )
        finally:
            with self.lock:
                type(self).active_calls -= 1


def test_loader_preserves_sessions_speakers_timestamps_and_all_categories() -> None:
    dataset = load_locomo_dataset(FIXTURE)

    assert len(dataset.conversations) == 2
    first = dataset.conversations[0]
    assert first.speaker_a == "Caroline"
    assert first.speaker_b == "Melanie"
    assert [session.session_id for session in first.sessions] == ["session_1", "session_2"]
    assert first.sessions[0].timestamp == "1:56 pm on 8 May, 2023"
    assert first.sessions[0].turns[0].speaker == "Caroline"
    assert first.sessions[0].turns[0].timestamp_iso == "2023-05-08T13:56:00+00:00"
    assert "Image caption" in first.sessions[1].turns[0].text
    assert [question.category for question in first.questions] == [1, 4, 5]
    assert first.questions[2].golden_answer is None
    assert first.questions[2].adversarial_answer == "No medal was mentioned"
    assert dataset.conversations[1].questions[1].golden_answer == "2023"


def test_locomo_turns_ingest_as_facts_of_session_episodes(
    tmp_path: Path,
) -> None:
    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    root = tmp_path / "memory"
    adapter = CodeCairnConversationMemory(
        runtime=create_runtime(root),
        cascade=create_cascade(root),
        repo_key=f"locomo/{conversation.sample_id}",
        semantic_projection=LOSSLESS_SEMANTIC_PROJECTION,
    )

    ingested = adapter.ingest(conversation, dataset_sha256="f" * 64)
    recalled = adapter.recall("What breed is Caroline's dog?", limit=5)
    snapshot = adapter.corpus_snapshot()

    assert ingested.session_count == 2
    assert ingested.turn_count == 3
    assert ingested.accepted_memory_count == 2
    assert ingested.rejected_memory_count == 0
    assert ingested.semantic_source_fact_count == 3
    assert ingested.semantic_referenced_source_fact_count == 3
    assert ingested.semantic_atomic_fact_count == 3
    assert ingested.semantic_empty_episode_count == 0
    assert snapshot["semantic_counts"] == {
        "semantic_source_fact_count": 3,
        "semantic_referenced_source_fact_count": 3,
        "semantic_atomic_fact_count": 3,
        "semantic_empty_episode_count": 0,
    }
    assert recalled.sidecar.repo_key == "locomo/conv-test-1"
    assert recalled.sidecar.ranked[0].memory_type == "conversation_episode"
    assert recalled.sidecar.neighbor_expansion_count > 0
    assert "beagle" in recalled.markdown
    memories = create_runtime(root).list_memories(repo_key="locomo/conv-test-1")
    assert len(memories) == 2
    assert {len(item.facts) for item in memories} == {1, 2}
    assert {item.adjacency_group_id for item in memories} == {"locomo/conv-test-1"}
    assert {item.adjacency_index for item in memories} == {0, 1}
    episode_sizes = Counter(fact.episode_id for item in memories for fact in item.facts)
    assert sorted(episode_sizes.values()) == [1, 2]
    assert {fact.text for item in memories for fact in item.facts} == {
        turn.text for session in conversation.sessions for turn in session.turns
    }
    assert {(fact.actor, fact.occurred_at) for item in memories for fact in item.facts} == {
        (turn.speaker, turn.timestamp_iso)
        for session in conversation.sessions
        for turn in session.turns
    }
    assert all(item.semantic_episode is not None for item in memories)
    assert all(" — attributed episode with " in item.summary for item in memories)
    assert all(
        item.semantic_episode is not None and ": " in item.semantic_episode.narrative
        for item in memories
    )
    entity_hits = SQLiteState(root / "state.sqlite3").find_entity_memories(
        repo_key="locomo/conv-test-1",
        entity_keys=("caroline",),
        limit=10,
    )
    assert entity_hits
    assert all(any(fact.actor == "Caroline" for fact in item.facts) for item in entity_hits)
    assert {item.evidence[0].provider for item in memories} == {"locomo"}


def test_evidence_answer_synthesizer_requires_source_fact_citations() -> None:
    class CitedAnswerModel(FakeAnswerModel):
        def generate(
            self,
            *,
            system: str,
            user: str,
            seed: int,
            response_format: str = "text",
        ) -> ModelResponse:
            assert response_format == "json"
            return ModelResponse(
                text=json.dumps(
                    {
                        "answer": "A beagle.",
                        "supporting_evidence_ids": ["fact-beagle"],
                        "insufficient": False,
                    }
                ),
                model=self.model_id,
            )

    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    ranked = RankedRecall(
        rank=1,
        memory_id="memory-session",
        memory_type="user_preference",
        title="Session",
        summary="Caroline has a beagle.",
        source_uri="codecairn://memory/memory-session",
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
                source_memory_id="memory-session",
                source_uri="codecairn://memory/memory-session",
                fact_id="fact-beagle",
                text="2023-05-08T13:56:00+00:00 — Caroline: I have a beagle.",
                source_title="Session",
                source_summary="Caroline has a beagle.",
                raw_event_index=1,
            ),
        ),
    )
    recall = RecallResult(
        markdown="# Recall Context\n",
        sidecar=RecallSidecar(
            query=conversation.questions[0].question,
            repo_key="locomo/conv-test-1",
            limit=1,
            latency_ms=1.0,
            vector_candidate_count=0,
            lexical_candidate_count=1,
            ranked=(ranked,),
        ),
    )

    answer = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(
            question_id=conversation.questions[0].question_id,
            text=conversation.questions[0].question,
        ),
        speakers=(conversation.speaker_a, conversation.speaker_b),
        recall=recall,
        model=CitedAnswerModel(),
        seed=7,
    )

    assert answer.response.text == "A beagle."
    assert answer.evidence_ids == ("fact-beagle",)
    assert answer.invalid_evidence_ids == ()
    assert answer.format == "structured-v1"


def test_evidence_answer_synthesizer_uses_bounded_attributed_markdown() -> None:
    @dataclass
    class CapturingAnswerModel:
        user_payload: dict[str, object] | None = None
        system_prompt: str | None = None
        response_format: str | None = None

        @property
        def model_id(self) -> str:
            return "capturing-answer"

        @property
        def public_config(self) -> dict[str, object]:
            return {"adapter": "fake", "model": self.model_id}

        def generate(
            self,
            *,
            system: str,
            user: str,
            seed: int,
            response_format: str = "text",
        ) -> ModelResponse:
            self.system_prompt = system
            self.user_payload = json.loads(user)
            self.response_format = response_format
            return ModelResponse(
                text=json.dumps(
                    {
                        "answer": "The context is insufficient.",
                        "supporting_evidence_ids": [],
                        "insufficient": True,
                    }
                ),
                model=self.model_id,
            )

    recall = RecallResult(
        markdown="# Recall Context\n" + "X" * 30_000,
        sidecar=RecallSidecar(
            query="What music?",
            repo_key="locomo/test",
            limit=20,
            latency_ms=1.0,
            vector_candidate_count=0,
            lexical_candidate_count=20,
            ranked=(),
        ),
    )
    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    model = CapturingAnswerModel()

    question = replace(conversation.questions[0], question="What music?")
    answer = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(question_id=question.question_id, text=question.question),
        speakers=(conversation.speaker_a, conversation.speaker_b),
        recall=recall,
        model=model,
        seed=7,
    )

    assert model.user_payload is not None
    assert set(model.user_payload) == {
        "memory_context",
        "question",
        "rendered_evidence",
        "speakers",
    }
    assert len(model.user_payload["memory_context"]) == 24_000
    assert model.response_format == "json"
    assert model.system_prompt is not None
    assert "ordinary common-sense inferences" not in model.system_prompt.casefold()
    assert "preserving action, negation, and qualifiers" in model.system_prompt.casefold()
    assert answer.plan.route == "direct"
    assert answer.response.text == "The context is insufficient."
    assert answer.evidence_ids == ()
    assert answer.invalid_evidence_ids == ()
    assert answer.format == "structured-v1"

    relabeled = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(question_id=question.question_id, text=question.question),
        speakers=(conversation.speaker_a, conversation.speaker_b),
        recall=recall,
        model=model,
        seed=7,
    )
    assert relabeled.plan.route == "direct"

    inferred = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(
            question_id=question.question_id,
            text="Would they likely enjoy a live concert?",
        ),
        speakers=(conversation.speaker_a, conversation.speaker_b),
        recall=recall,
        model=model,
        seed=7,
    )
    assert model.system_prompt is not None
    assert "ordinary common-sense inferences" in model.system_prompt.casefold()
    assert "preserve uncertainty and logical alternatives" in model.system_prompt.casefold()
    assert inferred.plan.route == "inference"

    temporal_item = RankedRecall(
        rank=1,
        memory_id="memory-temporal",
        memory_type="user_preference",
        title="Tim and John",
        summary="2023-12-06T17:34:00+00:00 — Tim and John conversation",
        source_uri="codecairn://memory/memory-temporal",
        content_sha256="b" * 64,
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
                source_memory_id="memory-temporal",
                source_uri="codecairn://memory/memory-temporal",
                fact_id="fact-violin",
                text="Tim has been playing the violin for about four months now.",
                source_title="Tim and John",
                source_summary="2023-12-06T17:34:00+00:00 — Tim and John conversation",
                raw_event_index=1,
            ),
            RecallSnippet(
                relation="sibling",
                source_memory_id="memory-temporal",
                source_uri="codecairn://memory/memory-temporal",
                fact_id="fact-omitted",
                text="Tim started playing the violin yesterday.",
                source_title="Tim and John",
                source_summary="2023-12-06T17:34:00+00:00 — Tim and John conversation",
                raw_event_index=2,
            ),
        ),
    )
    temporal_markdown = (
        "# Recall Context\n\n"
        "- [fact-violin] Tim has been playing the violin for about four months now.\n"
    )
    temporal_recall = replace(
        recall,
        markdown=temporal_markdown,
        sidecar=replace(
            recall.sidecar,
            ranked=(temporal_item,),
            context_trace=RecallContextTrace(
                renderer="facts-first-round-robin-v1",
                char_count=len(temporal_markdown),
                rendered_memory_ids=("memory-temporal",),
                rendered_fact_ids=("fact-violin",),
                omitted_memory_ids=(),
                omitted_snippet_count=1,
            ),
        ),
    )
    temporal = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(
            question_id=question.question_id,
            text="When did Tim start playing the violin?",
        ),
        speakers=(conversation.speaker_a, conversation.speaker_b),
        recall=temporal_recall,
        model=model,
        seed=7,
    )
    assert model.system_prompt is not None
    assert "resolve relative expressions" in model.system_prompt.casefold()
    assert "closest matching event" in model.system_prompt.casefold()
    assert "adjacent exchange" in model.system_prompt.casefold()
    assert "unrelated qualifier" in model.system_prompt.casefold()
    assert "resolved_time" in model.system_prompt
    assert "either state" in model.system_prompt.casefold()
    assert model.user_payload is not None
    hints = model.user_payload["temporal_hints"]
    assert isinstance(hints, list)
    assert len(hints) == 1
    assert hints[0]["resolved_time"] == "2023-08"
    assert temporal.plan.route == "temporal"

    listed = EvidenceAnswerSynthesizer().synthesize(
        LoCoMoQuery(
            question_id=question.question_id,
            text="What activities have they done together?",
        ),
        speakers=(conversation.speaker_a, conversation.speaker_b),
        recall=recall,
        model=model,
        seed=7,
    )
    assert model.system_prompt is not None
    assert "all distinct supported items" in model.system_prompt.casefold()
    assert listed.plan.route == "list"


def test_retrieval_and_answer_requests_exclude_evaluation_labels(tmp_path: Path) -> None:
    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    question = conversation.questions[0]
    relabeled = replace(
        question,
        golden_answer="a deliberately unrelated label",
        adversarial_answer="another hidden label",
        category=4,
        evidence=("hidden-evidence-id",),
    )

    class RecordingMemory(FakeMemory):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.queries: list[tuple[str, int]] = []

        def recall(self, question: str, *, limit: int) -> RecallResult:
            self.queries.append((question, limit))
            return super().recall(question, limit=limit)

    memory = RecordingMemory(tmp_path / conversation.sample_id)
    query = LoCoMoQuery(question_id=question.question_id, text=question.question)
    first_recall = recall_question(
        conversation.sample_id,
        query,
        memory=memory,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
        top_k=20,
    )
    second_recall = recall_question(
        conversation.sample_id,
        query,
        memory=memory,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
        top_k=20,
    )
    assert not isinstance(first_recall, dict)
    assert not isinstance(second_recall, dict)
    assert memory.queries == [(question.question, 20), (question.question, 20)]

    @dataclass
    class RecordingAnswerModel:
        requests: list[tuple[str, str, int, str]] = field(default_factory=list)

        @property
        def model_id(self) -> str:
            return "recording-answer"

        @property
        def public_config(self) -> dict[str, object]:
            return {"adapter": "fake", "model": self.model_id}

        def generate(
            self,
            *,
            system: str,
            user: str,
            seed: int,
            response_format: str = "text",
        ) -> ModelResponse:
            self.requests.append((system, user, seed, response_format))
            return ModelResponse(
                text=json.dumps(
                    {
                        "answer": "answer",
                        "supporting_evidence_ids": ["fixture-evidence"],
                        "insufficient": False,
                    }
                ),
                model=self.model_id,
            )

    model = RecordingAnswerModel()
    records = [
        run_question(
            conversation,
            candidate,
            recall=first_recall,
            answer_model=model,
            judge_model=None,
            judge_votes=0,
            judge_response_max_attempts=1,
            judge_response_max_chars=128,
            seed=7,
        )
        for candidate in (question, relabeled)
    ]
    assert model.requests[0] == model.requests[1]
    assert records[0]["category"] != records[1]["category"]
    assert records[0]["golden_answer"] != records[1]["golden_answer"]


def test_answer_transport_unknown_spend_stops_application_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise httpx.ReadTimeout("provider may have completed the answer")

    monkeypatch.setattr(httpx, "post", post)
    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    question = conversation.questions[0]
    journal_root = tmp_path / "answer-journal"
    journal = ModelAttemptJournal(journal_root, question_id=question.question_id)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="fixture-secret",
        model="fixture-answer",
        max_attempts=3,
        retry_backoff_seconds=0,
    )

    record = run_question(
        conversation,
        question,
        recall=FakeMemory(tmp_path / "memory").recall(question.question, limit=20),
        answer_model=model,
        judge_model=None,
        answer_response_max_attempts=2,
        judge_votes=0,
        judge_response_max_attempts=3,
        judge_response_max_chars=128,
        seed=17,
        attempt_journal=journal,
    )

    assert calls == 1
    assert record["status"] == "infrastructure_failed"
    assert record["phase"] == "answer"
    assert record["error_type"] == "UnknownProviderSpend"
    receipt = record["answer_attempt_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["attempt_count"] == 1
    attempts = receipt["attempts"]
    assert isinstance(attempts, list)
    assert attempts[0]["error_type"] == "UnknownProviderSpend"
    assert not (journal_root / "answer.app-002.start.json").exists()
    snapshot = journal.snapshot()
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    assert [entry["status"] for entry in entries] == ["unknown_spend"]


def test_judge_transport_unknown_spend_stops_votes_and_application_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise httpx.ReadTimeout("provider may have completed the judge vote")

    monkeypatch.setattr(httpx, "post", post)
    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    question = conversation.questions[0]
    journal_root = tmp_path / "judge-journal"
    journal = ModelAttemptJournal(journal_root, question_id=question.question_id)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="fixture-secret",
        model="fixture-judge",
        max_attempts=3,
        retry_backoff_seconds=0,
    )

    record = run_question(
        conversation,
        question,
        recall=FakeMemory(tmp_path / "memory").recall(question.question, limit=20),
        answer_model=FakeAnswerModel(),
        judge_model=model,
        answer_response_max_attempts=2,
        judge_votes=3,
        judge_response_max_attempts=3,
        judge_response_max_chars=128,
        seed=17,
        attempt_journal=journal,
    )

    assert calls == 1
    assert record["status"] == "infrastructure_failed"
    assert record["phase"] == "judge"
    assert record["error_type"] == "UnknownProviderSpend"
    votes = record["judge_votes"]
    assert isinstance(votes, list)
    assert len(votes) == 1
    assert votes[0]["error_type"] == "UnknownProviderSpend"
    assert votes[0]["attempt_count"] == 1
    assert not (journal_root / "judge-vote-000.app-002.start.json").exists()
    assert not (journal_root / "judge-vote-001.app-001.start.json").exists()
    snapshot = journal.snapshot()
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    assert [entry["status"] for entry in entries] == ["responded", "unknown_spend"]


def test_full_run_keeps_isolated_roots_raw_votes_and_read_only_reporting(
    tmp_path: Path,
) -> None:
    roots: list[Path] = []

    def memory_factory(root: Path) -> FakeMemory:
        roots.append(root)
        return FakeMemory(root)

    answer = FakeAnswerModel()
    judge = AlternatingJudgeModel()
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="locomo-full-test",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
    )

    artifact = run_locomo(
        config,
        memory_factory=memory_factory,
        answer_model=answer,
        judge_model=judge,
    )

    assert len(roots) == 4
    assert len(set(roots)) == 2
    assert all(roots.count(root) == 2 for root in set(roots))
    assert all(root.is_relative_to(artifact.run_dir / "runtime") for root in roots)
    assert artifact.summary["scored"] is True
    assert artifact.summary["question_artifact_count"] == 4
    assert artifact.summary["scored_question_count"] == 4
    assert artifact.summary["accuracy"] == 1.0
    assert artifact.summary["by_category"] == {
        "1": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "multi-hop"},
        "2": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "temporal"},
        "3": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "open-domain"},
        "4": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "single-hop"},
    }
    manifest = json.loads((artifact.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["retrieval"] == {**FAKE_RETRIEVAL_CONFIG, "top_k": 20}
    assert manifest["judge_contract"] == "locomo-generous-semantic-equivalence-v1"
    question_files = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    first_payload = question_files[0].read_text(encoding="utf-8")
    assert '"judge_votes"' in first_payload
    assert first_payload.count('"label"') == 3
    assert '"is_correct"' not in first_payload

    before = _tree_fingerprints(artifact.run_dir / "runtime")
    first_report = report_locomo(artifact.run_dir)
    second_report = report_locomo(artifact.run_dir)
    after = _tree_fingerprints(artifact.run_dir / "runtime")

    assert first_report == second_report == artifact.summary
    assert after == before
    with pytest.raises(FileExistsError):
        run_locomo(
            config,
            memory_factory=memory_factory,
            answer_model=answer,
            judge_model=judge,
        )

    tampered = json.loads(question_files[0].read_text(encoding="utf-8"))
    tampered["retrieval"]["retrieval_config_sha256"] = "0" * 64
    question_files[0].write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="configuration hash"):
        report_locomo(artifact.run_dir)


def test_locomo_supports_process_isolated_ingest_and_question_phases(tmp_path: Path) -> None:
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="locomo-phase-isolation-test",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
        execution_phase="ingest",
    )

    ingest = run_locomo(
        config,
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )

    assert ingest.summary == {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": config.run_id,
        "execution_phase": "ingest",
        "ingest_checkpoint_count": 2,
        "question_artifact_count": 0,
        "complete": False,
    }
    assert not (ingest.run_dir / "summary.json").exists()

    questions = run_locomo(
        replace(config, execution_phase="questions", resume=True),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )

    assert questions.summary["completed_question_count"] == 4
    assert questions.summary["question_artifact_count"] == 4
    assert (questions.run_dir / "summary.json").is_file()


def test_shared_corpus_is_built_once_and_reused_by_independent_runs(tmp_path: Path) -> None:
    class CountingMemory(FakeMemory):
        ingest_calls = 0
        snapshot_calls = 0

        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            type(self).ingest_calls += 1
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

        def corpus_snapshot(self) -> dict[str, object]:
            type(self).snapshot_calls += 1
            return super().corpus_snapshot()

        def recall(self, question: str, *, limit: int) -> RecallResult:
            for name in (".index.lancedb.lock", "state.sqlite3-shm", "state.sqlite3-wal"):
                (self.root / name).touch()
            return super().recall(question, limit=limit)

    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="synthetic-corpus",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=CountingMemory,
    )

    assert CountingMemory.ingest_calls == 2
    assert CountingMemory.snapshot_calls == 2
    assert corpus.manifest["build_contract"]["projection_contract"] == (
        "locomo-grounded-clause-projection-v7"
    )
    assert corpus.manifest["build_contract"]["repository_commit"] == "abc123"
    assert corpus.manifest["build_contract"]["semantic_projection"] == {
        "adapter": "codecairn/lossless-clause",
        "model": None,
        "revision": "v1",
    }
    assert corpus.manifest["counts"]["semantic_source_fact_count"] == 5
    assert corpus.manifest["counts"]["semantic_referenced_source_fact_count"] == 5
    assert corpus.manifest["counts"]["semantic_atomic_fact_count"] == 5
    assert corpus.manifest["counts"]["semantic_empty_episode_count"] == 0
    run_dirs: list[Path] = []
    for run_id in ("shared-corpus-first", "shared-corpus-second"):
        run = run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "runs",
                run_id=run_id,
                repository_commit="abc123",
                mode="smoke",
                expected_dataset_sha256=None,
                retrieval_config=FAKE_RETRIEVAL_CONFIG,
                corpus_path=corpus.corpus_dir,
                execution_phase="questions",
            ),
            memory_factory=CountingMemory,
            answer_model=FakeAnswerModel(),
            judge_model=None,
        )
        run_dirs.append(run.run_dir)

    assert CountingMemory.ingest_calls == 2
    assert CountingMemory.snapshot_calls == 6
    assert all(not (run_dir / "runtime").exists() for run_dir in run_dirs)
    manifests = [
        json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")) for run_dir in run_dirs
    ]
    assert {manifest["corpus"]["content_sha256"] for manifest in manifests} == {
        corpus.content_sha256
    }


def test_corpus_protocol_drift_fails_before_memory_or_output_side_effects(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=7,
    )
    memory_factory_calls = 0

    def forbidden_memory_factory(root: Path) -> FakeMemory:
        del root
        nonlocal memory_factory_calls
        memory_factory_calls += 1
        raise AssertionError("protocol preflight must run before memory construction")

    output_root = tmp_path / "corpora"
    with pytest.raises(ValueError, match="reranker_batch_size"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="protocol-drift",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                question_set_path=question_set_path,
            ),
            memory_factory=forbidden_memory_factory,
        )

    assert memory_factory_calls == 0
    assert not output_root.exists()


def test_corpus_build_contract_records_verified_question_set_digests(tmp_path: Path) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )

    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="protocol-verified",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
            question_set_path=question_set_path,
        ),
        memory_factory=FakeMemory,
    )

    question_set = load_locomo_question_set(
        question_set_path,
        dataset=load_locomo_dataset(FIXTURE),
    )
    assert corpus.manifest["build_contract"]["question_set"] == question_set.public_manifest
    assert corpus.manifest["build_contract"]["question_set"]["definition_sha256"] == (
        hashlib.sha256(question_set_path.read_bytes()).hexdigest()
    )
    assert corpus.manifest["build_contract"]["question_set"]["protocol_sha256"] == (
        hashlib.sha256(canonical_json(question_set.protocol).encode()).hexdigest()
    )


def test_corpus_resume_rejects_semantic_contract_drift_before_ingestion(
    tmp_path: Path,
) -> None:
    class InterruptedMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            if conversation.sample_id == "conv-test-2":
                raise RuntimeError("simulated corpus interruption")
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    output_root = tmp_path / "corpora"
    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
        "prompt_sha256": "a" * 64,
    }
    InterruptedMemory.semantic_projection = semantic_projection
    with pytest.raises(RuntimeError, match="simulated corpus interruption"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="resume-contract",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                semantic_projection=semantic_projection,
                semantic_projection_usage=lambda: {
                    "call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "cost_cny": 0.0,
                },
                question_set_path=question_set_path,
            ),
            memory_factory=InterruptedMemory,
        )
    building_dir = output_root / ".building-resume-contract"
    contract_path = building_dir / "build-contract.json"
    assert contract_path.is_file()
    contract_receipt = json.loads(contract_path.read_text(encoding="utf-8"))
    assert (
        contract_receipt["build_contract"]["semantic_projection_sha256"]
        == hashlib.sha256(canonical_json(semantic_projection).encode()).hexdigest()
    )

    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("resume drift must fail before ingestion")

    with pytest.raises(ValueError, match="resume build contract does not match"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="resume-contract",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                semantic_projection={**semantic_projection, "revision": "v2"},
                semantic_projection_usage=lambda: {
                    "call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "cost_cny": 0.0,
                },
                resume=True,
                question_set_path=question_set_path,
            ),
            memory_factory=forbidden_memory_factory,
        )


def test_corpus_resume_rejects_repository_commit_drift_before_ingestion(
    tmp_path: Path,
) -> None:
    class InterruptedMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            if conversation.sample_id == "conv-test-2":
                raise RuntimeError("simulated corpus interruption")
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    output_root = tmp_path / "corpora"
    config = LoCoMoCorpusConfig(
        dataset_path=FIXTURE,
        output_root=output_root,
        corpus_id="resume-commit-contract",
        repository_commit="first-commit",
        expected_dataset_sha256=None,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
    )
    with pytest.raises(RuntimeError, match="simulated corpus interruption"):
        build_locomo_corpus(config, memory_factory=InterruptedMemory)

    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("commit drift must fail before ingestion")

    with pytest.raises(ValueError, match="resume build contract does not match"):
        build_locomo_corpus(
            replace(config, repository_commit="second-commit", resume=True),
            memory_factory=forbidden_memory_factory,
        )


def test_corpus_rejects_observed_semantic_projection_mismatch_before_ingestion(
    tmp_path: Path,
) -> None:
    class MismatchedProjectionMemory(FakeMemory):
        semantic_projection: ClassVar[dict[str, object]] = {
            "adapter": "test/structured-semantic-projection",
            "model": "test/semantic-model",
            "revision": "v1",
        }

        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            raise AssertionError("projection mismatch must fail before ingestion")

    with pytest.raises(ValueError, match="observed semantic projection"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "corpora",
                corpus_id="mismatched-observed-projection",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=FAKE_RETRIEVAL_CONFIG,
            ),
            memory_factory=MismatchedProjectionMemory,
        )


def test_corpus_checkpoint_records_observed_semantic_projection(tmp_path: Path) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="observed-projection-receipt",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )

    checkpoints = sorted((corpus.corpus_dir / "checkpoints" / "ingest").glob("*.json"))
    for checkpoint_path in checkpoints:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        receipt = checkpoint["semantic_projection_receipt"]
        assert receipt["observed_semantic_projection"] == LOSSLESS_SEMANTIC_PROJECTION
        assert (
            receipt["observed_semantic_projection_sha256"]
            == hashlib.sha256(canonical_json(LOSSLESS_SEMANTIC_PROJECTION).encode()).hexdigest()
        )


def test_corpus_rejects_semantic_counts_not_derived_from_runtime_snapshot(
    tmp_path: Path,
) -> None:
    class DriftedSemanticSnapshotMemory(FakeMemory):
        def corpus_snapshot(self) -> dict[str, object]:
            return {
                **super().corpus_snapshot(),
                "semantic_counts": {
                    "semantic_source_fact_count": 0,
                    "semantic_referenced_source_fact_count": 0,
                    "semantic_atomic_fact_count": 0,
                    "semantic_empty_episode_count": 0,
                },
            }

    with pytest.raises(ValueError, match="runtime semantic counts"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "corpora",
                corpus_id="drifted-runtime-semantic-counts",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=FAKE_RETRIEVAL_CONFIG,
            ),
            memory_factory=DriftedSemanticSnapshotMemory,
        )


def test_corpus_preflight_rejects_rehashed_semantic_count_tampering(tmp_path: Path) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
    }

    class StructuredMemory(FakeMemory):
        pass

    StructuredMemory.semantic_projection = semantic_projection
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="rehashed-semantic-count-tamper",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
            semantic_projection=semantic_projection,
            semantic_projection_usage=lambda: {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
            },
            question_set_path=question_set_path,
        ),
        memory_factory=StructuredMemory,
    )
    manifest_path = corpus.corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered_ingests: list[dict[str, object]] = []
    for checkpoint_path in sorted((corpus.corpus_dir / "checkpoints/ingest").glob("*.json")):
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["semantic_referenced_source_fact_count"] = 0
        checkpoint["semantic_atomic_fact_count"] = 0
        checkpoint["semantic_empty_episode_count"] = checkpoint["accepted_memory_count"]
        checkpoint_path.unlink()
        write_json_exclusive(checkpoint_path, checkpoint)
        tampered_ingests.append(checkpoint)
    manifest["content"]["ingest_checkpoints"] = tampered_ingests
    manifest["counts"]["semantic_referenced_source_fact_count"] = 0
    manifest["counts"]["semantic_atomic_fact_count"] = 0
    manifest["counts"]["semantic_empty_episode_count"] = 3
    content_sha256 = hashlib.sha256(canonical_json(manifest["content"]).encode()).hexdigest()
    manifest["content_sha256"] = content_sha256
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)
    tampered_dir = corpus.corpus_dir.with_name(f"corpus-{content_sha256[:16]}")
    corpus.corpus_dir.rename(tampered_dir)

    with pytest.raises(ValueError, match="runtime semantic counts"):
        validate_locomo_corpus_preflight(
            tampered_dir,
            dataset=load_locomo_dataset(FIXTURE),
            expected_content_sha256=content_sha256,
            retrieval_config=retrieval_config,
        )


def test_corpus_rejects_unmetered_semantic_projection_before_ingestion(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )

    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("unmetered semantic projection must fail before ingestion")

    with pytest.raises(ValueError, match="usage reader is required"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "corpora",
                corpus_id="unmetered-semantic-projection",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                semantic_projection={
                    "adapter": "test/semantic-projection",
                    "model": "test/semantic-model",
                    "revision": "v1",
                },
                question_set_path=question_set_path,
            ),
            memory_factory=forbidden_memory_factory,
        )


def test_corpus_rejects_paid_embedding_without_frozen_question_set(tmp_path: Path) -> None:
    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("paid embedding preflight must fail before ingestion")

    paid_retrieval_config = {
        **FAKE_RETRIEVAL_CONFIG,
        "embedding": {
            "adapter": "dashscope-openai-compatible",
            "model": "test/embedding",
            "source": "test/embedding-source",
            "revision": "a" * 40,
            "dimension": 3,
        },
    }
    output_root = tmp_path / "corpora"
    with pytest.raises(ValueError, match="Paid LoCoMo corpus builds"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="paid-embedding-without-question-set",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=paid_retrieval_config,
            ),
            memory_factory=forbidden_memory_factory,
        )
    assert not output_root.exists()


def test_corpus_rejects_paid_embedding_without_usage_or_pricing(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_paid_embedding_corpus_contract(tmp_path)

    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("paid embedding preflight must fail before ingestion")

    base_config = LoCoMoCorpusConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "corpora",
        corpus_id="paid-embedding-contract",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=retrieval_config,
        question_set_path=question_set_path,
    )
    with pytest.raises(ValueError, match="embedding usage reader"):
        build_locomo_corpus(base_config, memory_factory=forbidden_memory_factory)

    embedding = dict(retrieval_config["embedding"])
    embedding.pop("pricing")
    retrieval_without_pricing = {**retrieval_config, "embedding": embedding}
    with pytest.raises(ValueError, match="configured CNY pricing"):
        build_locomo_corpus(
            replace(
                base_config,
                retrieval_config=retrieval_without_pricing,
                embedding_usage=lambda: {
                    "call_count": 0,
                    "provider_attempt_count": 0,
                    "unobserved_provider_attempt_count": 0,
                    "input_tokens": 0,
                    "cost_cny": 0.0,
                    "known_input_tokens_count": 0,
                    "known_cost_cny_count": 0,
                },
            ),
            memory_factory=forbidden_memory_factory,
        )


def test_corpus_audits_document_embedding_usage_per_conversation(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_paid_embedding_corpus_contract(tmp_path)
    usage: dict[str, object] = {
        "call_count": 0,
        "provider_attempt_count": 0,
        "unobserved_provider_attempt_count": 0,
        "input_tokens": 0,
        "cost_cny": 0.0,
        "known_input_tokens_count": 0,
        "known_cost_cny_count": 0,
    }

    class MeteredEmbeddingMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            usage["call_count"] = int(usage["call_count"]) + 1
            usage["provider_attempt_count"] = int(usage["provider_attempt_count"]) + 1
            usage["input_tokens"] = int(usage["input_tokens"]) + 10
            usage["cost_cny"] = float(usage["cost_cny"]) + 0.000005
            usage["known_input_tokens_count"] = int(usage["known_input_tokens_count"]) + 1
            usage["known_cost_cny_count"] = int(usage["known_cost_cny_count"]) + 1
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="metered-document-embedding",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
            embedding_usage=lambda: dict(usage),
            question_set_path=question_set_path,
        ),
        memory_factory=MeteredEmbeddingMemory,
    )

    expected_usage = {
        "call_count": 2,
        "provider_attempt_count": 2,
        "unobserved_provider_attempt_count": 0,
        "input_tokens": 20,
        "cost_cny": pytest.approx(0.00001),
        "known_input_tokens_count": 2,
        "known_cost_cny_count": 2,
    }
    assert corpus.manifest["embedding_usage"] == expected_usage
    assert corpus.manifest["content"]["embedding_usage"] == expected_usage
    assert corpus.manifest["content"]["embedding_receipt"]["usage"] == expected_usage
    checkpoints = sorted((corpus.corpus_dir / "checkpoints" / "ingest").glob("*.json"))
    attempts = sorted((corpus.corpus_dir / "checkpoints" / "ingest-attempts").glob("*.json"))
    assert len(checkpoints) == len(attempts) == 2
    for checkpoint_path, attempt_path in zip(checkpoints, attempts, strict=True):
        checkpoint = read_json(checkpoint_path)
        attempt = read_json(attempt_path)
        assert (
            checkpoint["embedding_receipt"]["attempt_receipt_sha256"] == (attempt["receipt_sha256"])
        )
        assert checkpoint["embedding_receipt"]["usage_delta"]["call_count"] == 1

    validate_locomo_corpus_preflight(
        corpus.corpus_dir,
        dataset=load_locomo_dataset(FIXTURE),
        expected_content_sha256=corpus.content_sha256,
        retrieval_config=retrieval_config,
    )


def test_corpus_embedding_interruption_records_unknown_spend_and_blocks_resume(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_paid_embedding_corpus_contract(tmp_path)
    usage: dict[str, object] = {
        "call_count": 0,
        "provider_attempt_count": 0,
        "unobserved_provider_attempt_count": 0,
        "input_tokens": 0,
        "cost_cny": 0.0,
        "known_input_tokens_count": 0,
        "known_cost_cny_count": 0,
    }

    class InterruptedEmbeddingMemory(FakeMemory):
        ingest_calls = 0

        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            type(self).ingest_calls += 1
            usage.update(
                call_count=1,
                provider_attempt_count=1,
                unobserved_provider_attempt_count=1,
                input_tokens=None,
                cost_cny=None,
            )
            raise RuntimeError("simulated embedding transport interruption")

    output_root = tmp_path / "corpora"
    config = LoCoMoCorpusConfig(
        dataset_path=FIXTURE,
        output_root=output_root,
        corpus_id="unknown-document-embedding-spend",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=retrieval_config,
        embedding_usage=lambda: dict(usage),
        question_set_path=question_set_path,
    )
    with pytest.raises(RuntimeError, match="transport interruption"):
        build_locomo_corpus(config, memory_factory=InterruptedEmbeddingMemory)

    failure = read_json(
        output_root
        / ".building-unknown-document-embedding-spend"
        / "checkpoints"
        / "ingest-failures"
        / "conv-test-1.json"
    )
    assert failure["embedding_usage_delta"]["provider_attempt_count"] == 1
    assert failure["embedding_usage_delta"]["unobserved_provider_attempt_count"] == 1
    assert failure["embedding_usage_delta"]["input_tokens"] is None

    with pytest.raises(ValueError, match="incomplete semantic projection ingest attempt"):
        build_locomo_corpus(
            replace(config, resume=True),
            memory_factory=InterruptedEmbeddingMemory,
        )
    assert InterruptedEmbeddingMemory.ingest_calls == 1


def test_corpus_resume_aggregates_semantic_usage_from_ingest_receipts(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )

    def empty_usage() -> dict[str, object]:
        return {
            "call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "uncached_input_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": 0.0,
            "cost_cny": 0.0,
        }

    def add_usage(usage: dict[str, object], *, multiplier: int) -> None:
        usage["call_count"] = int(usage["call_count"]) + multiplier
        usage["input_tokens"] = int(usage["input_tokens"]) + 10 * multiplier
        usage["output_tokens"] = int(usage["output_tokens"]) + 3 * multiplier
        usage["cached_input_tokens"] = int(usage["cached_input_tokens"]) + 2 * multiplier
        usage["uncached_input_tokens"] = int(usage["uncached_input_tokens"]) + 8 * multiplier
        usage["reasoning_tokens"] = int(usage["reasoning_tokens"]) + multiplier
        usage["cost_usd"] = float(usage["cost_usd"]) + 0.01 * multiplier
        usage["cost_cny"] = float(usage["cost_cny"]) + 0.1 * multiplier

    first_process_usage = empty_usage()

    class FirstProcessMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            add_usage(first_process_usage, multiplier=1)
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    def interrupted_memory_factory(root: Path) -> FakeMemory:
        if root.name == "conv-test-2":
            raise RuntimeError("simulated corpus interruption")
        return FirstProcessMemory(root)

    output_root = tmp_path / "corpora"
    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
    }
    FirstProcessMemory.semantic_projection = semantic_projection
    with pytest.raises(RuntimeError, match="simulated corpus interruption"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="resume-usage",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                semantic_projection=semantic_projection,
                semantic_projection_usage=lambda: dict(first_process_usage),
                question_set_path=question_set_path,
            ),
            memory_factory=interrupted_memory_factory,
        )

    resumed_process_usage = empty_usage()

    class ResumedMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            assert conversation.sample_id == "conv-test-2"
            add_usage(resumed_process_usage, multiplier=2)
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    ResumedMemory.semantic_projection = semantic_projection

    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=output_root,
            corpus_id="resume-usage",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
            semantic_projection=semantic_projection,
            semantic_projection_usage=lambda: dict(resumed_process_usage),
            resume=True,
            question_set_path=question_set_path,
        ),
        memory_factory=ResumedMemory,
    )

    expected_usage = {
        "call_count": 3,
        "input_tokens": 30,
        "output_tokens": 9,
        "cached_input_tokens": 6,
        "uncached_input_tokens": 24,
        "reasoning_tokens": 3,
        "cost_usd": 0.03,
        "cost_cny": 0.3,
        "known_input_tokens_count": 3,
        "known_output_tokens_count": 3,
        "known_cached_input_tokens_count": 3,
        "known_uncached_input_tokens_count": 3,
        "known_reasoning_tokens_count": 3,
        "known_cost_count": 3,
        "known_cost_cny_count": 3,
    }
    assert corpus.manifest["semantic_projection_usage"] == expected_usage
    assert corpus.manifest["content"]["semantic_projection_usage"] == expected_usage
    aggregate_receipt = corpus.manifest["content"]["semantic_projection_receipt"]
    assert aggregate_receipt["usage"] == expected_usage
    checkpoints = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((corpus.corpus_dir / "checkpoints" / "ingest").glob("*.json"))
    ]
    assert [
        checkpoint["semantic_projection_receipt"]["usage_delta"]["call_count"]
        for checkpoint in checkpoints
    ] == [1, 2]
    assert {
        checkpoint["semantic_projection_receipt"]["semantic_projection_sha256"]
        for checkpoint in checkpoints
    } == {corpus.manifest["build_contract"]["semantic_projection_sha256"]}


def test_corpus_usage_receipts_preserve_unknown_provider_metrics(tmp_path: Path) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
    }
    usage: dict[str, object] = {
        "call_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "reasoning_tokens": None,
        "cost_usd": None,
        "cost_cny": None,
        "known_input_tokens_count": 0,
        "known_output_tokens_count": 0,
        "known_cached_input_tokens_count": 0,
        "known_uncached_input_tokens_count": 0,
        "known_reasoning_tokens_count": 0,
        "known_cost_count": 0,
        "known_cost_cny_count": 0,
    }

    class PartiallyObservedUsageMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            usage["call_count"] = int(usage["call_count"]) + 1
            usage["input_tokens"] = int(usage["input_tokens"] or 0) + 10
            usage["known_input_tokens_count"] = int(usage["known_input_tokens_count"]) + 1
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    PartiallyObservedUsageMemory.semantic_projection = semantic_projection

    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="partially-observed-usage",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
            semantic_projection=semantic_projection,
            semantic_projection_usage=lambda: dict(usage),
            question_set_path=question_set_path,
        ),
        memory_factory=PartiallyObservedUsageMemory,
    )

    aggregate = corpus.manifest["semantic_projection_usage"]
    assert aggregate["call_count"] == 2
    assert aggregate["input_tokens"] == 20
    assert aggregate["known_input_tokens_count"] == 2
    assert aggregate["output_tokens"] is None
    assert aggregate["known_output_tokens_count"] == 0
    assert aggregate["cost_cny"] is None
    assert aggregate["known_cost_cny_count"] == 0


def test_corpus_resume_rejects_tampered_usage_receipt_before_ingestion(
    tmp_path: Path,
) -> None:
    class InterruptedMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            if conversation.sample_id == "conv-test-2":
                raise RuntimeError("simulated corpus interruption")
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    output_root = tmp_path / "corpora"
    config = LoCoMoCorpusConfig(
        dataset_path=FIXTURE,
        output_root=output_root,
        corpus_id="resume-tampered-receipt",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
    )
    with pytest.raises(RuntimeError, match="simulated corpus interruption"):
        build_locomo_corpus(config, memory_factory=InterruptedMemory)

    checkpoint_path = (
        output_root
        / ".building-resume-tampered-receipt"
        / "checkpoints"
        / "ingest"
        / "conv-test-1.json"
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["semantic_projection_receipt"]["usage_delta"]["call_count"] = 1
    checkpoint_path.unlink()
    write_json_exclusive(checkpoint_path, checkpoint)

    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("tampered receipts must fail before ingestion")

    with pytest.raises(ValueError, match="receipt digest does not match"):
        build_locomo_corpus(
            replace(config, resume=True),
            memory_factory=forbidden_memory_factory,
        )


def test_structured_corpus_failure_persists_known_semantic_usage(tmp_path: Path) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
    }
    usage: dict[str, object] = {
        "call_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "reasoning_tokens": None,
        "cost_usd": None,
        "cost_cny": None,
        "known_input_tokens_count": 0,
        "known_output_tokens_count": 0,
        "known_cached_input_tokens_count": 0,
        "known_uncached_input_tokens_count": 0,
        "known_reasoning_tokens_count": 0,
        "known_cost_count": 0,
        "known_cost_cny_count": 0,
    }

    class FailedStructuredMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            usage.update(
                call_count=1,
                input_tokens=37,
                cost_cny=0.125,
                known_input_tokens_count=1,
                known_cost_cny_count=1,
            )
            raise RuntimeError("simulated post-response projection failure")

    FailedStructuredMemory.semantic_projection = semantic_projection

    output_root = tmp_path / "corpora"
    with pytest.raises(RuntimeError, match="post-response projection failure"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="persisted-semantic-failure",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                semantic_projection=semantic_projection,
                semantic_projection_usage=lambda: dict(usage),
                question_set_path=question_set_path,
            ),
            memory_factory=FailedStructuredMemory,
        )

    building_dir = output_root / ".building-persisted-semantic-failure"
    failure = json.loads(
        (building_dir / "checkpoints" / "ingest-failures" / "conv-test-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["status"] == "failed"
    assert failure["usage_delta"]["call_count"] == 1
    assert failure["usage_delta"]["input_tokens"] == 37
    assert failure["usage_delta"]["known_input_tokens_count"] == 1
    assert failure["usage_delta"]["cost_cny"] == pytest.approx(0.125)
    assert failure["usage_delta"]["known_cost_cny_count"] == 1
    assert (
        failure["receipt_sha256"]
        == hashlib.sha256(
            canonical_json(
                {key: value for key, value in failure.items() if key != "receipt_sha256"}
            ).encode()
        ).hexdigest()
    )


def test_structured_corpus_failure_preserves_unknown_semantic_cost(tmp_path: Path) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
    }
    usage: dict[str, object] = {
        "call_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "reasoning_tokens": None,
        "cost_usd": None,
        "cost_cny": None,
        "known_input_tokens_count": 0,
        "known_output_tokens_count": 0,
        "known_cached_input_tokens_count": 0,
        "known_uncached_input_tokens_count": 0,
        "known_reasoning_tokens_count": 0,
        "known_cost_count": 0,
        "known_cost_cny_count": 0,
    }

    class FailedStructuredMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            usage["call_count"] = 1
            raise RuntimeError("simulated provider failure")

    FailedStructuredMemory.semantic_projection = semantic_projection
    output_root = tmp_path / "corpora"
    with pytest.raises(RuntimeError, match="provider failure"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="unknown-semantic-failure-cost",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                semantic_projection=semantic_projection,
                semantic_projection_usage=lambda: dict(usage),
                question_set_path=question_set_path,
            ),
            memory_factory=FailedStructuredMemory,
        )

    failure = json.loads(
        (
            output_root
            / ".building-unknown-semantic-failure-cost"
            / "checkpoints"
            / "ingest-failures"
            / "conv-test-1.json"
        ).read_text(encoding="utf-8")
    )
    assert failure["call_start_count"] == 1
    assert failure["usage_delta"]["input_tokens"] is None
    assert failure["usage_delta"]["known_input_tokens_count"] == 0
    assert failure["usage_delta"]["cost_cny"] is None
    assert failure["usage_delta"]["known_cost_cny_count"] == 0


def test_corpus_resume_rejects_uncheckpointed_semantic_attempt_with_zero_observed_usage(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_corpus_protocol_question_set(
        tmp_path,
        reranker_batch_size=8,
    )
    usage: dict[str, object] = {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "cost_cny": 0.0,
    }

    class UnobservedInterruptedMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            raise RuntimeError("simulated unobserved provider interruption")

    semantic_projection = {
        "adapter": "test/semantic-projection",
        "model": "test/semantic-model",
        "revision": "v1",
    }
    UnobservedInterruptedMemory.semantic_projection = semantic_projection
    output_root = tmp_path / "corpora"
    config = LoCoMoCorpusConfig(
        dataset_path=FIXTURE,
        output_root=output_root,
        corpus_id="unobserved-interrupted-ingest",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=retrieval_config,
        semantic_projection=semantic_projection,
        semantic_projection_usage=lambda: dict(usage),
        question_set_path=question_set_path,
    )
    with pytest.raises(RuntimeError, match="simulated unobserved provider interruption"):
        build_locomo_corpus(config, memory_factory=UnobservedInterruptedMemory)

    attempt_path = (
        output_root
        / ".building-unobserved-interrupted-ingest"
        / "checkpoints"
        / "ingest-attempts"
        / "conv-test-1.json"
    )
    assert attempt_path.is_file()

    def forbidden_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("uncheckpointed semantic attempt must fail before ingestion")

    with pytest.raises(ValueError, match="incomplete semantic projection ingest attempt"):
        build_locomo_corpus(
            replace(config, resume=True),
            memory_factory=forbidden_memory_factory,
        )


def test_corpus_preflight_rejects_manifest_usage_not_derived_from_checkpoints(
    tmp_path: Path,
) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="tampered-aggregate-usage",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )
    manifest_path = corpus.corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["semantic_projection_usage"]["call_count"] = 1
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="semantic projection usage"):
        validate_locomo_corpus_preflight(
            corpus.corpus_dir,
            dataset=load_locomo_dataset(FIXTURE),
            expected_content_sha256=corpus.content_sha256,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        )


def test_corpus_preflight_rejects_embedding_usage_not_derived_from_checkpoints(
    tmp_path: Path,
) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="tampered-embedding-aggregate",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )
    manifest_path = corpus.corpus_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["embedding_usage"]["call_count"] = 1
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="document embedding usage"):
        validate_locomo_corpus_preflight(
            corpus.corpus_dir,
            dataset=load_locomo_dataset(FIXTURE),
            expected_content_sha256=corpus.content_sha256,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        )


def test_corpus_exact_contract_is_locked_and_reused_without_embedding_spend(
    tmp_path: Path,
) -> None:
    question_set_path, retrieval_config = _write_paid_embedding_corpus_contract(tmp_path)
    usage: dict[str, object] = {
        "call_count": 0,
        "provider_attempt_count": 0,
        "unobserved_provider_attempt_count": 0,
        "input_tokens": 0,
        "cost_cny": 0.0,
        "known_input_tokens_count": 0,
        "known_cost_cny_count": 0,
    }
    usage_lock = threading.Lock()

    class ConcurrentMeteredMemory(FakeMemory):
        ingest_calls = 0

        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            with usage_lock:
                type(self).ingest_calls += 1
                usage["call_count"] = int(usage["call_count"]) + 1
                usage["provider_attempt_count"] = int(usage["provider_attempt_count"]) + 1
                usage["input_tokens"] = int(usage["input_tokens"]) + 10
                usage["cost_cny"] = float(usage["cost_cny"]) + 0.000005
                usage["known_input_tokens_count"] = int(usage["known_input_tokens_count"]) + 1
                usage["known_cost_cny_count"] = int(usage["known_cost_cny_count"]) + 1
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    output_root = tmp_path / "corpora"

    def config(corpus_id: str) -> LoCoMoCorpusConfig:
        return LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=output_root,
            corpus_id=corpus_id,
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
            embedding_usage=lambda: dict(usage),
            question_set_path=question_set_path,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                build_locomo_corpus,
                config(corpus_id),
                memory_factory=ConcurrentMeteredMemory,
            )
            for corpus_id in ("concurrent-contract-a", "concurrent-contract-b")
        ]
        artifacts = [future.result() for future in futures]

    third = build_locomo_corpus(
        config("sequential-contract-c"),
        memory_factory=ConcurrentMeteredMemory,
    )
    assert {artifact.corpus_dir for artifact in (*artifacts, third)} == {artifacts[0].corpus_dir}
    assert ConcurrentMeteredMemory.ingest_calls == 2
    assert usage["call_count"] == 2
    assert len(list(output_root.glob("corpus-*"))) == 1


def test_corpus_reuse_rejects_tampered_repository_commit_before_ingest(
    tmp_path: Path,
) -> None:
    class CountingMemory(FakeMemory):
        ingest_calls = 0

        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            type(self).ingest_calls += 1
            return super().ingest(conversation, dataset_sha256=dataset_sha256)

    config = LoCoMoCorpusConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "corpora",
        corpus_id="repository-commit-tamper",
        repository_commit="abc123",
        expected_dataset_sha256=None,
        retrieval_config=FAKE_RETRIEVAL_CONFIG,
    )
    corpus = build_locomo_corpus(config, memory_factory=CountingMemory)
    assert CountingMemory.ingest_calls == 2
    manifest_path = corpus.corpus_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["repository_commit"] = "tampered"
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="repository commit mirror"):
        validate_locomo_corpus_preflight(
            corpus.corpus_dir,
            dataset=load_locomo_dataset(FIXTURE),
            expected_content_sha256=corpus.content_sha256,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        )
    with pytest.raises(ValueError, match="exact build contract is invalid"):
        build_locomo_corpus(
            replace(config, corpus_id="repository-commit-tamper-retry"),
            memory_factory=CountingMemory,
        )
    assert CountingMemory.ingest_calls == 2


def test_shared_corpus_rejects_incomplete_ingest_before_publication(tmp_path: Path) -> None:
    class RejectingMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            result = super().ingest(conversation, dataset_sha256=dataset_sha256)
            return replace(result, rejected_memory_count=1)

    output_root = tmp_path / "corpora"
    with pytest.raises(ValueError, match="rejected_memory_count"):
        build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                corpus_id="rejected-ingest",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=FAKE_RETRIEVAL_CONFIG,
            ),
            memory_factory=RejectingMemory,
        )

    assert not list(output_root.glob("corpus-*"))
    assert not (output_root / ".building-rejected-ingest" / "manifest.json").exists()


def test_worker_rejects_an_obsolete_corpus_projection_contract(tmp_path: Path) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="obsolete-projection",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )
    manifest_path = corpus.corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["build_contract"]["projection_contract"] = "locomo-session-episode-with-turn-facts-v4"
    manifest["build_contract_sha256"] = hashlib.sha256(
        canonical_json(manifest["build_contract"]).encode()
    ).hexdigest()
    manifest["content"]["build_contract_sha256"] = manifest["build_contract_sha256"]
    manifest["content_sha256"] = hashlib.sha256(
        canonical_json(manifest["content"]).encode()
    ).hexdigest()
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    dataset = load_locomo_dataset(FIXTURE)
    with pytest.raises(ValueError):
        validate_locomo_corpus_preflight(
            corpus.corpus_dir,
            dataset=dataset,
            expected_content_sha256=manifest["content_sha256"],
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        )
    with pytest.raises(ValueError, match="projection contract is not supported"):
        validate_locomo_corpus_conversation(
            corpus.corpus_dir,
            conversation,
            expected_content_sha256=manifest["content_sha256"],
            memory_factory=FakeMemory,
        )


def test_worker_rejects_semantic_projection_digest_not_derived_from_config(
    tmp_path: Path,
) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="invalid-semantic-projection-digest",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )
    manifest_path = corpus.corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["build_contract"]["semantic_projection_sha256"] = "0" * 64
    manifest["build_contract_sha256"] = hashlib.sha256(
        canonical_json(manifest["build_contract"]).encode()
    ).hexdigest()
    manifest["content"]["build_contract_sha256"] = manifest["build_contract_sha256"]
    manifest["content_sha256"] = hashlib.sha256(
        canonical_json(manifest["content"]).encode()
    ).hexdigest()
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="semantic projection config digest does not match"):
        validate_locomo_corpus_conversation(
            corpus.corpus_dir,
            load_locomo_dataset(FIXTURE).conversations[0],
            expected_content_sha256=manifest["content_sha256"],
            memory_factory=FakeMemory,
        )


def test_shared_corpus_delegates_each_conversation_to_an_injected_worker(
    tmp_path: Path,
) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="worker-corpus",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )
    delegated: list[str] = []

    def worker(work: LoCoMoConversationWork) -> None:
        delegated.append(work.conversation.sample_id)
        run_locomo_conversation_questions(
            work.conversation_index,
            work.conversation,
            config=work.config,
            run_dir=work.run_dir,
            corpus_dir=work.corpus_dir,
            memory_factory=FakeMemory,
            answer_model=None,
            judge_model=None,
            selected_question_ids=set(work.question_ids),
        )

    run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="delegated-run",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
            corpus_path=corpus.corpus_dir,
            execution_phase="questions",
        ),
        memory_factory=FakeMemory,
        answer_model=None,
        judge_model=None,
        question_worker=worker,
    )

    assert delegated == ["conv-test-1", "conv-test-2"]


def test_delegated_shared_corpus_never_opens_runtime_in_parent(tmp_path: Path) -> None:
    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="parent-lightweight-corpus",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=FakeMemory,
    )

    def forbidden_parent_memory_factory(_root: Path) -> FakeMemory:
        raise AssertionError("parent process must not open a shared-corpus runtime")

    def worker(work: LoCoMoConversationWork) -> None:
        run_locomo_conversation_questions(
            work.conversation_index,
            work.conversation,
            config=work.config,
            run_dir=work.run_dir,
            corpus_dir=work.corpus_dir,
            memory_factory=FakeMemory,
            answer_model=None,
            judge_model=None,
            selected_question_ids=set(work.question_ids),
        )

    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="parent-lightweight-run",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
            corpus_path=corpus.corpus_dir,
            execution_phase="questions",
        ),
        memory_factory=forbidden_parent_memory_factory,
        answer_model=None,
        judge_model=None,
        question_worker=worker,
    )

    assert artifact.summary["completed_question_count"] == 4


def test_shared_corpus_run_rejects_any_file_mutation(tmp_path: Path) -> None:
    class MutatingMemory(FakeMemory):
        def recall(self, question: str, *, limit: int) -> RecallResult:
            (self.root / "unexpected-write").write_text("mutated", encoding="utf-8")
            return super().recall(question, limit=limit)

    corpus = build_locomo_corpus(
        LoCoMoCorpusConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "corpora",
            corpus_id="mutation-corpus",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
        ),
        memory_factory=MutatingMemory,
    )

    with pytest.raises(ValueError, match="changed during the read-only run"):
        run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "runs",
                run_id="mutating-run",
                repository_commit="abc123",
                mode="smoke",
                expected_dataset_sha256=None,
                retrieval_config=FAKE_RETRIEVAL_CONFIG,
                corpus_path=corpus.corpus_dir,
                execution_phase="questions",
            ),
            memory_factory=MutatingMemory,
            answer_model=FakeAnswerModel(),
            judge_model=None,
        )


def test_frozen_query_vectors_fail_closed_without_provider_fallback(tmp_path: Path) -> None:
    class CountingEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"

        def __init__(self) -> None:
            self.query_calls = 0
            self.document_calls = 0

        def embed_query(self, text: str) -> tuple[float, ...]:
            self.query_calls += 1
            return (1.0, 2.0, 3.0)

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.document_calls += 1
            return tuple((1.0, 2.0, 3.0) for _text in texts)

    provider = CountingEmbedder()
    vectors = build_locomo_query_vectors(
        LoCoMoQueryVectorConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "query-vectors",
            vector_set_id="synthetic-queries",
            expected_dataset_sha256=None,
        ),
        embedder=provider,
    )
    dataset = load_locomo_dataset(FIXTURE)
    scored_questions = [
        question
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 2, 3, 4}
    ]

    assert provider.query_calls == len(scored_questions)
    frozen = FrozenQueryEmbeddingAdapter(vectors.vector_set_dir)
    assert frozen.embed_query(scored_questions[0].question) == (1.0, 2.0, 3.0)
    with pytest.raises(KeyError, match="not present"):
        frozen.embed_query("a query outside the frozen selection")
    with pytest.raises(RuntimeError, match="document embedding"):
        frozen.embed_documents(("must not call the provider",))
    assert provider.document_calls == 0

    manifest_path = vectors.vector_set_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["question_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="record count"):
        FrozenQueryEmbeddingAdapter(vectors.vector_set_dir)


def test_frozen_query_vectors_preserve_the_document_embedding_contract(
    tmp_path: Path,
) -> None:
    class PricedEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"
        input_price_cny_per_million = 0.5

        def embed_query(self, text: str) -> tuple[float, ...]:
            return (1.0, 2.0, 3.0)

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector construction must not embed documents")

    class Reranker:
        model_id = "test/reranker"
        source_id = "test/reranker-source"
        revision = "b" * 40
        batch_size = 8

    embedder = PricedEmbedder()
    vectors = build_locomo_query_vectors(
        LoCoMoQueryVectorConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "query-vectors",
            vector_set_id="priced-query-vectors",
            expected_dataset_sha256=None,
        ),
        embedder=embedder,
    )
    providers = RetrievalProviders(
        profile="dashscope",
        embedder=embedder,
        reranker=Reranker(),
        embedding_license="test embedding license",
        reranker_license="test reranker license",
    )
    frozen = replace(
        providers,
        embedder=FrozenQueryEmbeddingAdapter(vectors.vector_set_dir),
    )

    assert frozen.public_config["embedding"] == providers.public_config["embedding"]


def test_query_vectors_reject_top_level_payload_digest_rebinding(tmp_path: Path) -> None:
    class FixedEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"

        def embed_query(self, text: str) -> tuple[float, ...]:
            return (1.0, 2.0, 3.0)

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector construction must not embed documents")

    vectors = build_locomo_query_vectors(
        LoCoMoQueryVectorConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "query-vectors",
            vector_set_id="query-vector-rebinding",
            expected_dataset_sha256=None,
        ),
        embedder=FixedEmbedder(),
    )
    vectors_path = vectors.vector_set_dir / "vectors.jsonl"
    vectors_path.write_bytes(vectors_path.read_bytes() + b"\n")
    manifest_path = vectors.vector_set_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["vectors_sha256"] = hashlib.sha256(vectors_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=r"manifest mirror.*vectors_sha256"):
        FrozenQueryEmbeddingAdapter(vectors.vector_set_dir)


def test_query_vector_build_batches_and_audits_embedding_usage(tmp_path: Path) -> None:
    class AuditedBatchEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"
        query_batch_size = 2

        def __init__(self) -> None:
            self.calls = 0
            self.input_tokens = 0

        @property
        def usage(self) -> dict[str, object]:
            return {
                "call_count": self.calls,
                "provider_attempt_count": self.calls,
                "unobserved_provider_attempt_count": 0,
                "input_tokens": self.input_tokens,
                "cost_cny": self.input_tokens / 10_000,
                "known_input_tokens_count": self.calls,
                "known_cost_cny_count": self.calls,
            }

        def embed_query(self, text: str) -> tuple[float, ...]:
            raise AssertionError("query-vector build must use the batch interface")

        def embed_queries(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.calls += 1
            self.input_tokens += 5 * len(texts)
            return tuple((float(index + 1), 2.0, 3.0) for index, _text in enumerate(texts))

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector build must not embed documents")

    provider = AuditedBatchEmbedder()
    artifact = build_locomo_query_vectors(
        LoCoMoQueryVectorConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "query-vectors",
            vector_set_id="batched-audited-queries",
            expected_dataset_sha256=None,
        ),
        embedder=provider,
    )

    assert provider.calls == 2
    assert artifact.manifest["batch_count"] == 2
    assert artifact.manifest["usage"] == {
        "call_count": 2,
        "provider_attempt_count": 2,
        "unobserved_provider_attempt_count": 0,
        "input_tokens": 20,
        "cost_cny": pytest.approx(0.002),
        "known_input_tokens_count": 2,
        "known_cost_cny_count": 2,
    }
    assert len(list((artifact.vector_set_dir / "checkpoints").glob("batch-*.json"))) == 2
    FrozenQueryEmbeddingAdapter(artifact.vector_set_dir, load_vectors=False)


def test_query_vector_exact_contract_is_locked_and_reused_without_provider_calls(
    tmp_path: Path,
) -> None:
    class ConcurrentBatchEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"
        query_batch_size = 2

        def __init__(self) -> None:
            self.calls = 0
            self.lock = threading.Lock()

        def embed_queries(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            with self.lock:
                self.calls += 1
            return tuple((1.0, 2.0, 3.0) for _text in texts)

        def embed_query(self, text: str) -> tuple[float, ...]:
            raise AssertionError("query-vector build must use the batch interface")

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector build must not embed documents")

    provider = ConcurrentBatchEmbedder()
    output_root = tmp_path / "query-vectors"

    def config(vector_set_id: str) -> LoCoMoQueryVectorConfig:
        return LoCoMoQueryVectorConfig(
            dataset_path=FIXTURE,
            output_root=output_root,
            vector_set_id=vector_set_id,
            expected_dataset_sha256=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                build_locomo_query_vectors,
                config(vector_set_id),
                embedder=provider,
            )
            for vector_set_id in ("concurrent-query-a", "concurrent-query-b")
        ]
        artifacts = [future.result() for future in futures]

    calls_after_publish = provider.calls
    third = build_locomo_query_vectors(
        config("sequential-query-c"),
        embedder=provider,
    )
    assert calls_after_publish == 2
    assert provider.calls == calls_after_publish
    assert {artifact.vector_set_dir for artifact in (*artifacts, third)} == {
        artifacts[0].vector_set_dir
    }
    assert len(list(output_root.glob("queries-*"))) == 1


def test_query_vector_reuse_rejects_invalid_exact_contract_before_provider_call(
    tmp_path: Path,
) -> None:
    class CountingBatchEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"
        query_batch_size = 2

        def __init__(self) -> None:
            self.calls = 0

        def embed_queries(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.calls += 1
            return tuple((1.0, 2.0, 3.0) for _text in texts)

        def embed_query(self, text: str) -> tuple[float, ...]:
            raise AssertionError("batch interface expected")

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector build must not embed documents")

    provider = CountingBatchEmbedder()
    config = LoCoMoQueryVectorConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "query-vectors",
        vector_set_id="invalid-query-reuse",
        expected_dataset_sha256=None,
    )
    artifact = build_locomo_query_vectors(config, embedder=provider)
    calls_after_publish = provider.calls
    manifest_path = artifact.vector_set_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["question_count"] = int(manifest["question_count"]) + 1
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    with pytest.raises(ValueError, match="exact build contract are invalid"):
        build_locomo_query_vectors(
            replace(config, vector_set_id="invalid-query-reuse-second"),
            embedder=provider,
        )
    assert provider.calls == calls_after_publish


def test_query_vector_resume_fails_closed_after_uncheckpointed_provider_attempt(
    tmp_path: Path,
) -> None:
    class InterruptedBatchEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"
        query_batch_size = 2

        def __init__(self) -> None:
            self.calls = 0

        @property
        def usage(self) -> dict[str, object]:
            return {
                "call_count": self.calls,
                "provider_attempt_count": self.calls,
                "unobserved_provider_attempt_count": 0,
                "input_tokens": None,
                "cost_cny": None,
                "known_input_tokens_count": 0,
                "known_cost_cny_count": 0,
            }

        def embed_queries(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.calls += 1
            raise RuntimeError("simulated embedding interruption")

        def embed_query(self, text: str) -> tuple[float, ...]:
            raise AssertionError("batch interface expected")

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector build must not embed documents")

    provider = InterruptedBatchEmbedder()
    config = LoCoMoQueryVectorConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "query-vectors",
        vector_set_id="interrupted-query-vectors",
        expected_dataset_sha256=None,
    )
    with pytest.raises(RuntimeError, match="simulated embedding interruption"):
        build_locomo_query_vectors(config, embedder=provider)
    assert provider.calls == 1

    with pytest.raises(ValueError, match="provider spend is unknown"):
        build_locomo_query_vectors(replace(config, resume=True), embedder=provider)
    assert provider.calls == 1


def test_query_vector_resume_validates_all_receipts_before_new_calls(tmp_path: Path) -> None:
    class BatchEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"
        query_batch_size = 2

        def __init__(self) -> None:
            self.calls = 0

        def embed_queries(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.calls += 1
            return tuple((1.0, 2.0, 3.0) for _text in texts)

        def embed_query(self, text: str) -> tuple[float, ...]:
            raise AssertionError("batch interface expected")

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector build must not embed documents")

    provider = BatchEmbedder()
    config = LoCoMoQueryVectorConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "query-vectors",
        vector_set_id="receipt-preflight",
        expected_dataset_sha256=None,
    )
    artifact = build_locomo_query_vectors(config, embedder=provider)
    initial_calls = provider.calls
    building_dir = config.output_root / f".building-{config.vector_set_id}"
    artifact.vector_set_dir.rename(building_dir)
    (building_dir / "attempts" / "batch-000001.json").unlink()

    with pytest.raises(ValueError, match="checkpoint receipt set is incomplete"):
        build_locomo_query_vectors(replace(config, resume=True), embedder=provider)
    assert provider.calls == initial_calls


def test_query_vector_paid_provider_requires_frozen_question_set(tmp_path: Path) -> None:
    class RemoteEmbedder:
        model_id = "text-embedding-v4"
        source_id = "https://dashscope.example/v1"
        revision = "provider-managed"
        dimension = 3
        index_identity = "dashscope-openai-compatible@1:test"
        query_batch_size = 10

        def embed_queries(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("paid preflight must reject before provider calls")

        def embed_query(self, text: str) -> tuple[float, ...]:
            raise AssertionError("paid preflight must reject before provider calls")

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("paid preflight must reject before provider calls")

    output_root = tmp_path / "query-vectors"
    with pytest.raises(ValueError, match="require a frozen question set"):
        build_locomo_query_vectors(
            LoCoMoQueryVectorConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                vector_set_id="paid-query-vectors",
                expected_dataset_sha256=None,
            ),
            embedder=RemoteEmbedder(),
        )
    assert not output_root.exists()


def test_frozen_query_vector_superset_can_serve_an_audited_subset(tmp_path: Path) -> None:
    class FixedEmbedder:
        model_id = "test/embedding"
        source_id = "test/embedding-source"
        revision = "a" * 40
        dimension = 3
        index_identity = "test:embedding@revision:3"

        def embed_query(self, text: str) -> tuple[float, ...]:
            return (1.0, 2.0, 3.0)

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("query-vector construction must not embed documents")

    vectors = build_locomo_query_vectors(
        LoCoMoQueryVectorConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "query-vectors",
            vector_set_id="synthetic-query-superset",
            expected_dataset_sha256=None,
        ),
        embedder=FixedEmbedder(),
    )
    dataset = load_locomo_dataset(FIXTURE)
    selected = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category == 1
    )
    question_set_path = tmp_path / "diagnostic-subset.json"
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "synthetic-query-subset",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {"1": 1},
            "selection_sha256": hashlib.sha256(
                json.dumps(sorted(selected), separators=(",", ":")).encode()
            ).hexdigest(),
        },
    )

    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="query-vector-subset",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
            question_set_path=question_set_path,
            query_vectors_path=vectors.vector_set_dir,
        ),
        memory_factory=FakeMemory,
        answer_model=FailingAnswerModel(),
        judge_model=None,
    )

    manifest = json.loads((artifact.run_dir / "manifest.json").read_text())
    assert manifest["query_vectors"]["coverage"] == "superset"
    assert manifest["query_vectors"]["artifact_question_count"] == sum(
        question.category in {1, 2, 3, 4}
        for conversation in dataset.conversations
        for question in conversation.questions
    )
    assert manifest["query_vectors"]["run_question_count"] == 1
    assert (
        manifest["query_vectors"]["run_selection_sha256"]
        == hashlib.sha256(json.dumps(sorted(selected), separators=(",", ":")).encode()).hexdigest()
    )


def test_retrieval_mode_never_calls_answer_or_judge_models(tmp_path: Path) -> None:
    dataset = load_locomo_dataset(FIXTURE)
    selected = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 4}
    )
    question_set_path = tmp_path / "retrieval-canary.json"
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "synthetic-retrieval-canary",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {"1": 1, "4": 1},
            "selection_sha256": hashlib.sha256(
                json.dumps(sorted(selected), separators=(",", ":")).encode()
            ).hexdigest(),
        },
    )
    answer = FailingAnswerModel()

    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="retrieval-only-canary",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
            question_set_path=question_set_path,
        ),
        memory_factory=FakeMemory,
        answer_model=answer,
        judge_model=None,
    )

    assert answer.calls == 0
    assert artifact.summary["question_artifact_count"] == 2
    assert artifact.summary["completed_question_count"] == 2
    assert artifact.summary["scored"] is False
    assert artifact.summary["unscored_reason"] == "retrieval mode never calls answer or judge"
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    ]
    assert all("retrieval" in record and "answer" not in record for record in records)


def test_frozen_question_set_selects_exact_strata_and_reports_retrieval_diagnostics(
    tmp_path: Path,
) -> None:
    dataset = load_locomo_dataset(FIXTURE)
    selected = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 4}
    )
    selection_sha256 = hashlib.sha256(
        json.dumps(sorted(selected), separators=(",", ":")).encode()
    ).hexdigest()
    question_set_path = tmp_path / "diagnostic.json"
    write_json_exclusive(
        question_set_path,
        {
            "schema_version": 1,
            "selection_id": "synthetic-diagnostic",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {"1": 1, "4": 1},
            "selection_sha256": selection_sha256,
        },
    )

    question_set = load_locomo_question_set(question_set_path, dataset=dataset)
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-diagnostic",
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=FAKE_RETRIEVAL_CONFIG,
            question_set_path=question_set_path,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )

    assert question_set.question_ids == selected
    assert artifact.summary["question_artifact_count"] == 2
    assert artifact.summary["retrieval_diagnostics"] == {
        "latency_ms": {"p50": 1.25, "p95": 1.25, "max": 1.25},
        "route_counts": {"episode_first": 2},
        "average_counts": {
            "atomic_fact_lexical_candidate_count": 0.0,
            "atomic_fact_entity_lexical_candidate_count": 0.0,
            "atomic_fact_temporal_lexical_candidate_count": 0.0,
            "atomic_fact_vector_candidate_count": 0.0,
            "entity_posting_candidate_count": 0.0,
            "episode_entity_lexical_candidate_count": 0.0,
            "episode_lexical_candidate_count": 0.0,
            "episode_temporal_lexical_candidate_count": 0.0,
            "episode_vector_candidate_count": 0.0,
            "neighbor_expansion_count": 0.0,
        },
        "context": {
            "renderer_counts": {CONTEXT_RENDERER_ID: 2},
            "averages": {
                "char_count": 69.0,
                "omitted_parent_count": 0.0,
                "omitted_snippet_count": 0.0,
                "rendered_fact_count": 1.0,
                "rendered_parent_count": 1.0,
                "token_count": 35.0,
            },
        },
    }
    manifest = json.loads((artifact.run_dir / "manifest.json").read_text())
    assert manifest["selection"]["question_set"]["question_count"] == 2
    assert manifest["selection"]["question_set"]["question_ids"] == list(selected)


def test_frozen_question_set_fails_closed_on_selection_drift(tmp_path: Path) -> None:
    dataset = load_locomo_dataset(FIXTURE)
    path = tmp_path / "drifted.json"
    write_json_exclusive(
        path,
        {
            "schema_version": 1,
            "selection_id": "drifted-diagnostic",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {"1": 1},
            "selection_sha256": "0" * 64,
        },
    )

    with pytest.raises(ValueError, match="digest"):
        load_locomo_question_set(path, dataset=dataset)


def test_ablation_report_validates_constant_protocol_and_frozen_gates(tmp_path: Path) -> None:
    dataset = load_locomo_dataset(FIXTURE)
    selected = tuple(
        question.question_id
        for conversation in dataset.conversations
        for question in conversation.questions
        if question.category in {1, 2, 4}
    )
    selection_sha256 = hashlib.sha256(
        json.dumps(sorted(selected), separators=(",", ":")).encode()
    ).hexdigest()
    frozen_planner_protocol = {
        field: value
        for field, value in RecallPlannerConfig().public_config.items()
        if field not in {"mode", "neighbor_window", "temporal_neighbor_window"}
    }
    definition_path = tmp_path / "ablation.json"
    write_json_exclusive(
        definition_path,
        {
            "schema_version": 1,
            "selection_id": "synthetic-ablation",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "selection-seed",
            "category_targets": {"1": 1, "2": 1, "4": 1},
            "selection_sha256": selection_sha256,
            "variants": [
                {"id": "episode-only", "recall_mode": "episode-only"},
                {
                    "id": "hierarchy-no-neighbors",
                    "recall_mode": "hierarchy-no-neighbors",
                },
                {"id": "hierarchy", "recall_mode": "hierarchy"},
            ],
            "protocol": {
                "answer_model": "fake-answer",
                "answer_evidence_contract": "grounded-cited-answer-v13",
                "answer_retry_contract": "grounded-answer-contract-retry-v1",
                "answer_response_max_attempts": 2,
                "judge_model": "fake-judge",
                "judge_contract": "locomo-generous-semantic-equivalence-v1",
                "judge_votes": 3,
                "judge_response_max_attempts": 3,
                "judge_response_max_chars": 32_768,
                "seed": 17,
                "top_k": 20,
                "inference_threads": 2,
                "tokenizer_parallelism": False,
                "tokenizer_threads": 1,
                "max_workers": 1,
                "ingest_max_workers": 1,
                "retrieval_max_workers": 1,
                "retrieval_thread_count": 1,
                "execution_phase_contract": "process-isolated-ingest-then-questions-v1",
                "worker_contract": None,
                "worker_max_rss_bytes": None,
                "worker_stall_timeout_seconds": None,
                "worker_poll_interval_seconds": None,
                "worker_rss_poll_interval_seconds": None,
                "worker_progress_signal": None,
                "worker_publish_policy": None,
                "embedding_adapter": None,
                "embedding_model": "test/embedding",
                "embedding_dimension": 3,
                "reranker_model": "test/reranker",
                "reranker_batch_size": None,
                "neighbor_windows": {
                    "episode-only": {
                        "neighbor_window": 0,
                        "temporal_neighbor_window": 0,
                    },
                    "hierarchy-no-neighbors": {
                        "neighbor_window": 0,
                        "temporal_neighbor_window": 0,
                    },
                    "hierarchy": {
                        "neighbor_window": 1,
                        "temporal_neighbor_window": 2,
                    },
                },
                **frozen_planner_protocol,
            },
            "gates": {
                "required_scored_questions_per_variant": 3,
                "maximum_infrastructure_failures": 0,
                "hierarchy_no_neighbors_vs_episode_minimum_accuracy_delta_points": 0.0,
                "temporal_neighbor_minimum_overall_accuracy_delta_points": 0.0,
                "temporal_neighbor_minimum_temporal_or_multihop_delta_points": 0.0,
                "temporal_neighbor_maximum_p95_increase_percent": 20.0,
                "selected_maximum_retrieval_p95_ms": 2.0,
            },
        },
    )
    drifted_definition = json.loads(definition_path.read_text(encoding="utf-8"))
    drifted_definition["protocol"]["context_renderer"] = "incompatible-renderer"
    drifted_definition_path = tmp_path / "drifted-protocol.json"
    write_json_exclusive(drifted_definition_path, drifted_definition)
    answer_model = FakeAnswerModel()
    judge_model = AlternatingJudgeModel()
    with pytest.raises(ValueError, match="context_renderer"):
        run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "preflight-must-not-exist",
                run_id="protocol-drift",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config={
                    **FAKE_RETRIEVAL_CONFIG,
                    "planner": RecallPlannerConfig().public_config,
                },
                question_set_path=drifted_definition_path,
            ),
            memory_factory=FakeMemory,
            answer_model=answer_model,
            judge_model=judge_model,
        )
    assert answer_model.calls == 0
    assert judge_model.calls == 0
    assert not (tmp_path / "preflight-must-not-exist").exists()

    run_paths: dict[str, Path] = {}
    for mode in ("episode-only", "hierarchy-no-neighbors", "hierarchy"):
        retrieval_config = {
            **FAKE_RETRIEVAL_CONFIG,
            "planner": RecallPlannerConfig.for_mode(mode).public_config,
        }

        class ConfiguredMemory(FakeMemory):
            config_sha256 = retrieval_config_sha256(retrieval_config)

            def recall(self, question: str, *, limit: int) -> RecallResult:
                result = super().recall(question, limit=limit)
                return replace(
                    result,
                    sidecar=replace(
                        result.sidecar,
                        retrieval_config_sha256=self.config_sha256,
                    ),
                )

        artifact = run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "runs",
                run_id=f"diagnostic-{mode}",
                repository_commit="abc123",
                expected_dataset_sha256=None,
                retrieval_config=retrieval_config,
                question_set_path=definition_path,
            ),
            memory_factory=ConfiguredMemory,
            answer_model=FakeAnswerModel(),
            judge_model=AlternatingJudgeModel(),
        )
        run_paths[mode] = artifact.run_dir

    report = build_locomo_ablation_report(
        LoCoMoAblationConfig(
            question_set_path=definition_path,
            episode_only_run=run_paths["episode-only"],
            hierarchy_no_neighbors_run=run_paths["hierarchy-no-neighbors"],
            hierarchy_run=run_paths["hierarchy"],
            output_path=tmp_path / "ablation-report.json",
        )
    )

    assert report["gate_passed"] is True
    assert report["accuracy_delta_points"] == {
        "hierarchy_no_neighbors_vs_episode_only": 0.0,
        "hierarchy_vs_hierarchy_no_neighbors": 0.0,
        "hierarchy_temporal_category_vs_no_neighbors": 0.0,
        "hierarchy_multihop_category_vs_no_neighbors": 0.0,
    }
    assert report["selected_variant"] == "hierarchy"
    assert report["selected_run_contract"] == {
        "repository_commit": "abc123",
        "recall_mode": "hierarchy",
        "corpus": None,
        "query_vectors": None,
        "answer_model": FakeAnswerModel().public_config,
        "judge_model": AlternatingJudgeModel().public_config,
    }
    assert (tmp_path / "ablation-report.json").is_file()

    hierarchy_checkpoint_path = sorted(
        (run_paths["hierarchy"] / "checkpoints" / "questions").glob("*/*.json")
    )[0]
    hierarchy_checkpoint = json.loads(hierarchy_checkpoint_path.read_text(encoding="utf-8"))
    without_trace = json.loads(json.dumps(hierarchy_checkpoint))
    without_trace["retrieval"].pop("context_trace")
    hierarchy_checkpoint_path.write_text(json.dumps(without_trace), encoding="utf-8")
    with pytest.raises(ValueError, match="no Recall Context trace"):
        build_locomo_ablation_report(
            LoCoMoAblationConfig(
                question_set_path=definition_path,
                episode_only_run=run_paths["episode-only"],
                hierarchy_no_neighbors_run=run_paths["hierarchy-no-neighbors"],
                hierarchy_run=run_paths["hierarchy"],
                output_path=tmp_path / "missing-trace-ablation-report.json",
            )
        )
    hierarchy_checkpoint_path.write_text(json.dumps(hierarchy_checkpoint), encoding="utf-8")

    episode_manifest_path = run_paths["episode-only"] / "manifest.json"
    episode_manifest = json.loads(episode_manifest_path.read_text(encoding="utf-8"))
    episode_manifest["max_workers"] = 2
    episode_manifest_path.write_text(json.dumps(episode_manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="max_workers"):
        build_locomo_ablation_report(
            LoCoMoAblationConfig(
                question_set_path=definition_path,
                episode_only_run=run_paths["episode-only"],
                hierarchy_no_neighbors_run=run_paths["hierarchy-no-neighbors"],
                hierarchy_run=run_paths["hierarchy"],
                output_path=tmp_path / "drifted-ablation-report.json",
            )
        )

    episode_manifest["max_workers"] = 1
    episode_retrieval = episode_manifest["retrieval"]
    assert isinstance(episode_retrieval, dict)
    episode_planner = episode_retrieval["planner"]
    assert isinstance(episode_planner, dict)
    episode_planner["context_renderer"] = "incompatible-renderer"
    episode_manifest_path.write_text(json.dumps(episode_manifest), encoding="utf-8")
    mutated_retrieval_config = {
        key: value for key, value in episode_retrieval.items() if key != "top_k"
    }
    mutated_config_sha256 = retrieval_config_sha256(mutated_retrieval_config)
    for checkpoint_path in sorted(
        (run_paths["episode-only"] / "checkpoints" / "questions").glob("*/*.json")
    ):
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["retrieval"]["retrieval_config_sha256"] = mutated_config_sha256
        checkpoint["retrieval"]["context_trace"]["renderer"] = "incompatible-renderer"
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(ValueError, match="context_renderer"):
        build_locomo_ablation_report(
            LoCoMoAblationConfig(
                question_set_path=definition_path,
                episode_only_run=run_paths["episode-only"],
                hierarchy_no_neighbors_run=run_paths["hierarchy-no-neighbors"],
                hierarchy_run=run_paths["hierarchy"],
                output_path=tmp_path / "renderer-drifted-ablation-report.json",
            )
        )


def test_official_v15_command_contract_passes_preflight() -> None:
    definition = json.loads(
        (Path(__file__).parents[1] / "benchmarks/locomo/diagnostic-200-v15.json").read_text(
            encoding="utf-8"
        )
    )
    protocol = definition["protocol"]
    question_set = LoCoMoQuestionSet(
        selection_id="test",
        definition_sha256="a" * 64,
        dataset_sha256="b" * 64,
        algorithm="stratified-sha256-v1",
        seed="test",
        category_targets=((1, 1),),
        question_ids=("q1",),
        selection_sha256="c" * 64,
        protocol=protocol,
    )
    retrieval_config = {
        "inference_threads": 2,
        "tokenizer_parallelism": False,
        "tokenizer_threads": 1,
        "embedding": {
            "adapter": "dashscope-openai-compatible",
            "model": "text-embedding-v4",
            "dimension": 1024,
        },
        "reranker": {
            "model": "Xenova/ms-marco-MiniLM-L-6-v2",
            "batch_size": 8,
        },
        "planner": RecallPlannerConfig().public_config,
    }
    worker_contract = {
        "name": "verified-shared-corpus-exec-per-conversation-v3",
        "max_rss_bytes": 2147483648,
        "stall_timeout_seconds": 600.0,
        "poll_interval_seconds": 0.25,
        "rss_poll_interval_seconds": 1.0,
        "progress_signal": "heartbeat-evidence-and-durable-question-checkpoint-deadline-v2",
        "publish_policy": "conversation-directory-atomic-rename-v1",
        "reranker_warmup": "one-local-document-before-question-timing-v1",
    }

    class FrozenProtocolModel(FakeAnswerModel):
        @property
        def model_id(self) -> str:
            return "deepseek-v4-flash"

    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=Path("unused"),
        run_id="official-v15",
        repository_commit="abc123",
        max_workers=10,
        retrieval_config=retrieval_config,
        corpus_path=Path("content-addressed-corpus"),
    )
    validate_run_protocol(
        question_set,
        config=config,
        answer_model=FrozenProtocolModel(),
        judge_model=FrozenProtocolModel(),
        question_worker_contract=worker_contract,
    )
    missing_selector_protocol = dict(protocol)
    missing_selector_protocol.pop("fact_selector")
    with pytest.raises(ValueError, match="fact_selector"):
        validate_run_protocol(
            replace(question_set, protocol=missing_selector_protocol),
            config=config,
            answer_model=FrozenProtocolModel(),
            judge_model=FrozenProtocolModel(),
            question_worker_contract=worker_contract,
        )
    drifted_fact_limit_config = json.loads(json.dumps(retrieval_config))
    drifted_fact_limit_config["planner"]["fact_rerank_max_candidates"] = 255
    with pytest.raises(ValueError, match="fact_rerank_max_candidates"):
        validate_run_protocol(
            question_set,
            config=replace(config, retrieval_config=drifted_fact_limit_config),
            answer_model=FrozenProtocolModel(),
            judge_model=FrozenProtocolModel(),
            question_worker_contract=worker_contract,
        )
    drifted_retrieval_config = json.loads(json.dumps(retrieval_config))
    drifted_retrieval_config["planner"]["neighbor_window"] = 9
    with pytest.raises(ValueError, match="neighbor_window"):
        validate_run_protocol(
            question_set,
            config=replace(config, retrieval_config=drifted_retrieval_config),
            answer_model=FrozenProtocolModel(),
            judge_model=FrozenProtocolModel(),
            question_worker_contract=worker_contract,
        )
    with pytest.raises(ValueError, match="max_workers"):
        validate_run_protocol(
            question_set,
            config=replace(config, max_workers=1),
            answer_model=FrozenProtocolModel(),
            judge_model=FrozenProtocolModel(),
            question_worker_contract=worker_contract,
        )


def test_locomo_marks_retrieval_identity_mismatch_as_infrastructure_failure(
    tmp_path: Path,
) -> None:
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="locomo-retrieval-mismatch",
        repository_commit="abc123",
        mode="smoke",
        expected_dataset_sha256=None,
        retrieval_config={
            "method": "hybrid-rrf-cross-encoder",
            "embedding": {
                "model": "different/embedding",
                "source": "different/embedding-source",
                "revision": "c" * 40,
            },
            "reranker": {
                "model": "test/reranker",
                "source": "test/reranker-source",
                "revision": "b" * 40,
            },
        },
    )

    artifact = run_locomo(
        config,
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )

    assert artifact.summary["infrastructure_failed_count"] == 2
    records = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["status"] == "infrastructure_failed"
    assert payload["phase"] == "retrieval"
    assert payload["error_type"] == "ValueError"


def test_full_run_retries_malformed_judge_output_and_accounts_for_every_attempt(
    tmp_path: Path,
) -> None:
    judge = MalformedThenValidJudgeModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-judge-retry",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=judge,
    )

    assert judge.calls == 13
    assert judge.seeds[:2] == [18, 1_000_018]
    assert "attempt 1 of 3" in judge.systems[0]
    assert "attempt 2 of 3" in judge.systems[1]
    assert artifact.summary["scored_question_count"] == 4
    assert artifact.summary["infrastructure_failed_count"] == 0
    assert artifact.summary["usage"] == {
        "input_tokens": 144,
        "output_tokens": 38,
        "known_cost_count": 17,
        "cost_usd": 1.304,
        "answer_call_count": 4,
        "answer_response_count": 4,
        "journal_application_call_count": 17,
        "journal_completed_outcome_count": 17,
        "journal_provider_attempt_count": 17,
        "journal_known_provider_attempt_count": 17,
        "journal_unknown_spend_count": 0,
    }
    manifest = json.loads((artifact.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["judge_response_max_attempts"] == 3
    assert manifest["judge_response_max_chars"] == 32_768
    question_files = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in question_files]
    retried_vote = next(
        vote
        for payload in payloads
        for vote in payload["judge_votes"]
        if vote["attempt_count"] == 2
    )
    assert retried_vote["label"] == "correct"
    assert retried_vote["input_tokens"] == 16
    assert retried_vote["output_tokens"] == 4
    assert retried_vote["failed_attempts"] == [
        {
            "attempt_index": 1,
            "error_type": "JSONDecodeError",
            "input_tokens": 8,
            "model": "fake-judge",
            "output_tokens": 2,
            "raw_response": "not-json",
            "response_chars": 8,
            "cost_usd": 0.1,
        }
    ]


def test_smoke_retries_malformed_grounded_answer_and_accounts_for_both_attempts(
    tmp_path: Path,
) -> None:
    answer = MalformedThenValidAnswerModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-answer-contract-retry",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=answer,
        judge_model=None,
    )

    assert answer.calls == 3
    assert answer.seeds[:2] == [17, 1_000_017]
    assert "attempt 1 of 2" in answer.systems[0]
    assert "attempt 2 of 2" in answer.systems[1]
    assert artifact.summary["completed_question_count"] == 2
    assert artifact.summary["infrastructure_failed_count"] == 0
    assert artifact.summary["usage"]["input_tokens"] == 30
    assert artifact.summary["usage"]["output_tokens"] == 9
    assert artifact.summary["usage"]["cost_cny"] == pytest.approx(0.003)
    assert artifact.summary["usage"]["answer_call_count"] == 3
    assert artifact.summary["usage"]["answer_response_count"] == 3
    assert artifact.summary["answer_attempts"] == {
        "contract": "grounded-answer-contract-retry-v1",
        "max_attempts": 2,
        "receipt_count": 2,
        "call_count": 3,
        "response_count": 3,
        "contract_rejected_count": 1,
        "provider_failed_count": 0,
    }
    manifest = json.loads((artifact.run_dir / "manifest.json").read_text())
    assert manifest["answer_retry_contract"] == "grounded-answer-contract-retry-v1"
    assert manifest["answer_response_max_attempts"] == 2
    checkpoints = sorted((artifact.run_dir / "checkpoints/questions").glob("*/*.json"))
    records = [json.loads(path.read_text()) for path in checkpoints]
    retried = next(
        record for record in records if record["answer_attempt_receipt"]["attempt_count"] == 2
    )
    assert [attempt["status"] for attempt in retried["answer_attempt_receipt"]["attempts"]] == [
        "contract_rejected",
        "accepted",
    ]


def test_report_rejects_a_priced_response_without_observed_cost(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="priced model response cost is incomplete"):
        run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "runs",
                run_id="locomo-priced-cost-missing",
                repository_commit="abc123",
                mode="smoke",
                expected_dataset_sha256=None,
            ),
            memory_factory=FakeMemory,
            answer_model=PricedMissingCostAnswerModel(),
            judge_model=None,
        )


def test_exhausted_grounded_answer_retries_keep_failure_usage(tmp_path: Path) -> None:
    answer = AlwaysUnknownCitationAnswerModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-answer-contract-exhausted",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=answer,
        judge_model=None,
    )

    assert answer.calls == 4
    assert artifact.summary["completed_question_count"] == 0
    assert artifact.summary["infrastructure_failed_count"] == 2
    assert artifact.summary["usage"]["input_tokens"] == 40
    assert artifact.summary["usage"]["output_tokens"] == 12
    assert artifact.summary["usage"]["cost_cny"] == pytest.approx(0.004)
    assert artifact.summary["usage"]["answer_call_count"] == 4
    assert artifact.summary["answer_attempts"]["contract_rejected_count"] == 4
    checkpoints = sorted((artifact.run_dir / "checkpoints/questions").glob("*/*.json"))
    for path in checkpoints:
        record = json.loads(path.read_text())
        assert record["status"] == "infrastructure_failed"
        assert record["phase"] == "answer"
        assert record["answer_attempt_receipt"]["status"] == "contract_exhausted"
        assert record["answer_attempt_receipt"]["usage"]["known_cost_cny_count"] == 2


def test_full_run_keeps_exhausted_judge_retries_out_of_the_score(tmp_path: Path) -> None:
    judge = AlwaysMalformedJudgeModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-judge-retry-exhausted",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=judge,
    )

    assert judge.calls == 12
    assert artifact.summary["completed_question_count"] == 0
    assert artifact.summary["scored_question_count"] == 0
    assert artifact.summary["infrastructure_failed_count"] == 4
    question_files = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    for question_path in question_files:
        payload = json.loads(question_path.read_text(encoding="utf-8"))
        assert payload["status"] == "infrastructure_failed"
        assert payload["phase"] == "judge"
        assert payload["error_type"] == "JSONDecodeError"
        assert len(payload["judge_votes"]) == 1
        for vote in payload["judge_votes"]:
            assert vote["label"] == "invalid"
            assert vote["attempt_count"] == 3
            assert len(vote["failed_attempts"]) == 3


def test_full_run_counts_each_cny_cost_observation_across_judge_retries(
    tmp_path: Path,
) -> None:
    judge = MalformedThenValidJudgeModel(cost_usd=None, cost_cny=0.1)
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-cny-judge-retry",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=judge,
    )

    assert judge.calls == 13
    assert artifact.summary["usage"] == {
        "input_tokens": 144,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 38,
        "reasoning_tokens": 0,
        "known_cost_count": 4,
        "cost_usd": 0.004,
        "known_cost_cny_count": 13,
        "cost_cny": 1.3,
        "answer_call_count": 4,
        "answer_response_count": 4,
        "journal_application_call_count": 17,
        "journal_completed_outcome_count": 17,
        "journal_provider_attempt_count": 17,
        "journal_known_provider_attempt_count": 17,
        "journal_unknown_spend_count": 0,
    }


def test_report_rejects_retry_metadata_that_exceeds_the_manifest_limit(tmp_path: Path) -> None:
    judge = MalformedThenValidJudgeModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-invalid-retry-metadata",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=judge,
    )
    manifest_path = artifact.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["judge_response_max_attempts"] = 1
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    report = report_locomo(artifact.run_dir)

    assert report["question_artifact_count"] == 4
    assert report["scored_question_count"] == 3
    assert report["infrastructure_failed_count"] == 1


def test_report_rejects_answer_citations_outside_retrieved_evidence(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-forged-answer-citation",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["answer_evidence"]["evidence_ids"] = ["forged-evidence-id"]
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="do not match retrieved evidence"):
        report_locomo(artifact.run_dir)


def test_report_rejects_v3_parent_memory_id_as_an_answer_citation(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-parent-id-answer-citation",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    context_trace = question["retrieval"]["context_trace"]
    assert context_trace["renderer"] == CONTEXT_RENDERER_ID
    question["answer_evidence"]["evidence_ids"] = [context_trace["rendered_memory_ids"][0]]
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="do not match retrieved evidence"):
        report_locomo(artifact.run_dir)


def test_v5_selection_validation_rejects_fact_without_selector_evidence(
    tmp_path: Path,
) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-missing-selector-evidence",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["retrieval"]["ranked"][0]["snippets"][0]["selection_source"] = None

    with pytest.raises(ValueError, match="invalid selection evidence"):
        validate_scored_fact_selection(
            question["retrieval"],
            selector=FACT_SELECTOR_ID,
        )


def test_report_rejects_answer_retry_usage_that_omits_a_failed_attempt(tmp_path: Path) -> None:
    answer = MalformedThenValidAnswerModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-tampered-answer-retry",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=answer,
        judge_model=None,
    )
    question_paths = sorted((artifact.run_dir / "checkpoints/questions").glob("*/*.json"))
    question_path = next(
        path
        for path in question_paths
        if json.loads(path.read_text())["answer_attempt_receipt"]["attempt_count"] == 2
    )
    question = json.loads(question_path.read_text())
    question["answer_attempt_receipt"]["usage"]["input_tokens"] = 10
    question["answer_attempt_receipt"]["usage"]["known_input_tokens_count"] = 1
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="not derived from its attempts"):
        report_locomo(artifact.run_dir)


def test_report_rejects_v3_context_token_trace_drift(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-drifted-token-trace",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["retrieval"]["context_trace"]["token_count"] += 1
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="token trace does not match"):
        report_locomo(artifact.run_dir)


def test_report_rejects_unstructured_answer_under_v13_contract(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-unstructured-v13-answer",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["answer_evidence"]["format"] = "unstructured-fallback"
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="metadata is invalid"):
        report_locomo(artifact.run_dir)


def _typed_expansion_question(
    tmp_path: Path,
    *,
    run_id: str,
) -> tuple[Path, dict[str, object]]:
    retrieval_config = {
        **FAKE_RETRIEVAL_CONFIG,
        "planner": RecallPlannerConfig().public_config,
    }

    class ConfiguredMemory(FakeMemory):
        def recall(self, question: str, *, limit: int) -> RecallResult:
            result = super().recall(question, limit=limit)
            return replace(
                result,
                sidecar=replace(
                    result.sidecar,
                    retrieval_config_sha256=retrieval_config_sha256(retrieval_config),
                ),
            )

    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id=run_id,
            repository_commit="abc123",
            expected_dataset_sha256=None,
            retrieval_config=retrieval_config,
        ),
        memory_factory=ConfiguredMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    return question_path, question


def test_report_rejects_typed_expansion_over_its_manifest_budget(tmp_path: Path) -> None:
    question_path, question = _typed_expansion_question(
        tmp_path,
        run_id="locomo-over-budget-expansion",
    )
    question["retrieval"]["expansion_fact_count"] = 25
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="exceeds its manifest budget"):
        report_locomo(question_path.parents[3])


def test_report_rejects_expansion_total_that_disagrees_with_components(
    tmp_path: Path,
) -> None:
    question_path, question = _typed_expansion_question(
        tmp_path,
        run_id="locomo-forged-expansion-total",
    )
    question["retrieval"]["episode_entity_lexical_candidate_count"] = 1
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="exceeds its manifest budget"):
        report_locomo(question_path.parents[3])


@pytest.mark.parametrize("invalid_count", [-1, True])
def test_report_rejects_invalid_expansion_component_count(
    tmp_path: Path,
    invalid_count: object,
) -> None:
    question_path, question = _typed_expansion_question(
        tmp_path,
        run_id=f"locomo-invalid-expansion-{type(invalid_count).__name__}",
    )
    question["retrieval"]["entity_posting_candidate_count"] = invalid_count
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="invalid counts"):
        report_locomo(question_path.parents[3])


def test_report_rejects_answer_citations_omitted_from_compiled_context(
    tmp_path: Path,
) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-omitted-answer-citation",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["retrieval"]["ranked"] = [
        {
            "memory_id": "memory-1",
            "snippets": [
                {"fact_id": "fact-rendered"},
                {"fact_id": "fact-omitted"},
            ],
        }
    ]
    question["retrieval"]["context_trace"] = {
        "renderer": "facts-first-round-robin-v1",
        "char_count": len(question["recall_markdown"]),
        "rendered_memory_ids": ["memory-1"],
        "rendered_fact_ids": ["fact-rendered"],
        "omitted_memory_ids": [],
        "omitted_snippet_count": 1,
    }
    question["answer_evidence"]["evidence_ids"] = ["fact-omitted"]
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="do not match retrieved evidence"):
        report_locomo(artifact.run_dir)


def test_report_rejects_forged_context_trace_evidence(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-forged-context-trace",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["retrieval"]["ranked"] = [
        {
            "memory_id": "memory-real",
            "snippets": [{"fact_id": "fact-real"}],
            "episode_fact_ids": [],
        }
    ]
    question["retrieval"]["context_trace"] = {
        "renderer": "facts-first-round-robin-v1",
        "char_count": len(question["recall_markdown"]),
        "rendered_memory_ids": ["memory-forged"],
        "rendered_fact_ids": ["fact-forged"],
        "omitted_memory_ids": [],
        "omitted_snippet_count": 0,
    }
    question["answer_evidence"]["evidence_ids"] = ["fact-forged"]
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="unavailable evidence"):
        report_locomo(artifact.run_dir)


def test_report_rejects_incomplete_context_trace_partition(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-incomplete-context-trace",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["retrieval"]["ranked"] = [
        {
            "memory_id": "memory-real",
            "snippets": [{"fact_id": "fact-real"}],
            "episode_fact_ids": [],
        }
    ]
    question["retrieval"]["context_trace"] = {
        "renderer": "facts-first-round-robin-v1",
        "char_count": len(question["recall_markdown"]),
        "rendered_memory_ids": [],
        "rendered_fact_ids": [],
        "omitted_memory_ids": [],
        "omitted_snippet_count": 999_999,
    }
    question_path.unlink()
    write_json_exclusive(question_path, question)

    with pytest.raises(ValueError, match="unavailable evidence"):
        report_locomo(artifact.run_dir)


def test_report_rejects_missing_question_checkpoint_from_manifest_inventory(
    tmp_path: Path,
) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-missing-question-inventory",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=None,
        judge_model=None,
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question_path.unlink()

    with pytest.raises(ValueError, match="inventory is incomplete"):
        report_locomo(artifact.run_dir)


def test_report_rejects_question_checkpoint_whose_identity_differs_from_its_path(
    tmp_path: Path,
) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-mismatched-question-inventory",
            repository_commit="abc123",
            mode="retrieval",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=None,
        judge_model=None,
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    record = json.loads(question_path.read_text(encoding="utf-8"))
    record["question_id"] = "locomo-question_wrong"
    question_path.unlink()
    write_json_exclusive(question_path, record)

    with pytest.raises(ValueError, match="path does not match"):
        report_locomo(artifact.run_dir)


def test_report_rejects_judge_responses_longer_than_the_manifest_limit(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-invalid-response-length",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=AlternatingJudgeModel(),
    )
    manifest_path = artifact.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["judge_response_max_chars"] = 1
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)

    report = report_locomo(artifact.run_dir)

    assert report["question_artifact_count"] == 4
    assert report["scored_question_count"] == 0
    assert report["infrastructure_failed_count"] == 4


def test_full_run_retries_deeply_nested_judge_json_without_aborting(tmp_path: Path) -> None:
    deeply_nested = "[" * 10_000 + "0" + "]" * 10_000
    judge = MalformedThenValidJudgeModel(first_response=deeply_nested)

    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-deep-json-retry",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=judge,
    )

    assert judge.calls == 13
    assert artifact.summary["scored_question_count"] == 4
    assert artifact.summary["infrastructure_failed_count"] == 0
    question_files = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    error_types = [
        failed_attempt["error_type"]
        for question_path in question_files
        for vote in json.loads(question_path.read_text(encoding="utf-8"))["judge_votes"]
        for failed_attempt in vote["failed_attempts"]
    ]
    assert error_types == ["RecursionError"]


def test_full_run_retries_an_oversized_otherwise_valid_judge_response(tmp_path: Path) -> None:
    oversized = json.dumps({"label": "CORRECT", "padding": "x" * 32_768})
    judge = MalformedThenValidJudgeModel(first_response=oversized)

    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-oversized-json-retry",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=judge,
    )

    assert judge.calls == 13
    assert artifact.summary["scored_question_count"] == 4
    assert artifact.summary["infrastructure_failed_count"] == 0
    question_files = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    error_types = [
        failed_attempt["error_type"]
        for question_path in question_files
        for vote in json.loads(question_path.read_text(encoding="utf-8"))["judge_votes"]
        for failed_attempt in vote["failed_attempts"]
    ]
    assert error_types == ["ValueError"]


def test_answer_and_judge_prompts_treat_benchmark_content_as_untrusted_data(
    tmp_path: Path,
) -> None:
    model = CapturingTextModel()
    run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-prompt-boundary",
            repository_commit="abc123",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=model,
        judge_model=model,
    )

    assert len(model.requests) == 16
    for system, user, response_format in model.requests:
        assert "untrusted data" in system
        payload = json.loads(user)
        assert isinstance(payload, dict)
        if "generated_answer" in payload:
            assert response_format == "json"
            assert set(payload) == {"generated_answer", "gold_answer", "question"}
            assert "generous semantic equivalence" in system.casefold()
        else:
            assert response_format == "json"
            assert set(payload) in (
                {"memory_context", "question", "rendered_evidence", "speakers"},
                {
                    "memory_context",
                    "question",
                    "rendered_evidence",
                    "speakers",
                    "temporal_hints",
                },
            )
            assert "inspect the whole supplied context" in system.casefold()
            assert "only after checking every supplied item" in system.casefold()
            assert "ordinary common-sense inferences" not in system.casefold()


def test_smoke_run_is_explicitly_unscored_and_never_calls_a_judge(tmp_path: Path) -> None:
    answer = FakeAnswerModel()
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="locomo-smoke-test",
        repository_commit="abc123",
        mode="smoke",
        expected_dataset_sha256=None,
    )

    artifact = run_locomo(
        config,
        memory_factory=FakeMemory,
        answer_model=answer,
        judge_model=None,
    )

    assert artifact.summary["scored"] is False
    assert artifact.summary["accuracy"] is None
    assert artifact.summary["by_category"] == {}
    assert artifact.summary["unscored_reason"] == "smoke mode is never scored"
    assert artifact.summary["question_artifact_count"] == 2
    assert answer.calls == 2


def test_run_serializes_memory_bound_ingest_then_parallelizes_questions(tmp_path: Path) -> None:
    StagedConcurrencyMemory.ingest_barrier = threading.Barrier(2)
    StagedConcurrencyMemory.recall_barrier = threading.Barrier(2)
    StagedConcurrencyMemory.active_ingests = 0
    StagedConcurrencyMemory.max_active_ingests = 0
    StagedConcurrencyMemory.active_recalls = 0
    StagedConcurrencyMemory.max_active_recalls = 0
    StagedConcurrencyMemory.recall_thread_ids = set()
    ConcurrentAnswerModel.barrier = threading.Barrier(2)
    ConcurrentAnswerModel.active_calls = 0
    ConcurrentAnswerModel.max_active_calls = 0
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-concurrent-smoke",
            repository_commit="abc123",
            mode="smoke",
            max_workers=2,
            expected_dataset_sha256=None,
        ),
        memory_factory=StagedConcurrencyMemory,
        answer_model=ConcurrentAnswerModel(),
        judge_model=None,
    )

    assert artifact.summary["question_artifact_count"] == 2
    assert StagedConcurrencyMemory.max_active_ingests == 1
    assert StagedConcurrencyMemory.max_active_recalls == 1
    assert len(StagedConcurrencyMemory.recall_thread_ids) == 1
    assert ConcurrentAnswerModel.max_active_calls == 2
    manifest = (artifact.run_dir / "manifest.json").read_text(encoding="utf-8")
    assert '"max_workers": 2' in manifest
    assert '"ingest_max_workers": 1' in manifest
    assert '"retrieval_max_workers": 1' in manifest
    assert '"retrieval_thread_count": 1' in manifest


def test_resume_only_fills_missing_question_checkpoints(tmp_path: Path) -> None:
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="locomo-resume-smoke",
        repository_commit="abc123",
        mode="smoke",
        expected_dataset_sha256=None,
    )
    initial = run_locomo(
        config,
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )
    question_files = sorted((initial.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    preserved_path, missing_path = question_files
    preserved_before = preserved_path.read_bytes()
    missing_journal = missing_path.parent / ".attempt-journal" / missing_path.stem
    journal_before = {
        path.name: path.read_bytes() for path in sorted(missing_journal.glob("*.json"))
    }
    missing_path.unlink()
    (initial.run_dir / "summary.json").unlink()

    class ResumeMemory(FakeMemory):
        def ingest(
            self,
            conversation: LoCoMoConversation,
            *,
            dataset_sha256: str,
        ) -> ConversationIngestResult:
            raise AssertionError("completed ingest checkpoints must not be replayed")

    answer = FakeAnswerModel()
    resumed = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-resume-smoke",
            repository_commit="abc123",
            mode="smoke",
            resume=True,
            expected_dataset_sha256=None,
        ),
        memory_factory=ResumeMemory,
        answer_model=answer,
        judge_model=None,
    )

    assert answer.calls == 0
    assert {
        path.name: path.read_bytes() for path in sorted(missing_journal.glob("*.json"))
    } == journal_before
    assert preserved_path.read_bytes() == preserved_before
    assert resumed.summary["question_artifact_count"] == 2
    assert resumed.summary["infrastructure_failed_count"] == 0
    assert resumed.summary["usage"]["journal_application_call_count"] == 2


def test_resume_fails_closed_for_a_started_answer_with_unknown_spend(
    tmp_path: Path,
) -> None:
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="locomo-resume-unknown-spend",
        repository_commit="abc123",
        mode="smoke",
        expected_dataset_sha256=None,
    )
    initial = run_locomo(
        config,
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )
    question_path = sorted((initial.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    journal_dir = question_path.parent / ".attempt-journal" / question_path.stem
    outcome_path = journal_dir / "answer.app-001.outcome.json"
    assert outcome_path.is_file()
    outcome_path.unlink()
    question_path.unlink()
    (initial.run_dir / "summary.json").unlink()
    replacement = FakeAnswerModel()

    resumed = run_locomo(
        replace(config, resume=True),
        memory_factory=FakeMemory,
        answer_model=replacement,
        judge_model=None,
    )

    assert replacement.calls == 0
    recovered = read_json(question_path)
    assert isinstance(recovered, dict)
    assert recovered["status"] == "infrastructure_failed"
    assert recovered["phase"] == "answer"
    assert recovered["error_type"] == "UnknownProviderSpend"
    assert resumed.summary["infrastructure_failed_count"] == 1
    assert resumed.summary["usage"]["journal_unknown_spend_count"] == 1


def test_report_rejects_unknown_spend_not_bound_to_question_failure(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-unbound-unknown-spend",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    journal = ModelAttemptJournal(
        question_path.parent / ".attempt-journal" / question_path.stem,
        question_id=question_path.stem,
    )
    journal.invoke(
        FakeAnswerModel(),
        stage="answer",
        application_attempt=2,
        seed=999,
        system="unbound extra attempt",
        user="unbound extra attempt",
        response_format="json",
    )
    (
        question_path.parent
        / ".attempt-journal"
        / question_path.stem
        / "answer.app-002.outcome.json"
    ).unlink()
    record = json.loads(question_path.read_text(encoding="utf-8"))
    record["attempt_journal"] = journal.snapshot()
    question_path.unlink()
    write_json_exclusive(question_path, record)

    with pytest.raises(ValueError, match="attempt journal does not match the question"):
        report_locomo(artifact.run_dir)


def test_resume_rejects_ingest_checkpoint_without_runtime_state(tmp_path: Path) -> None:
    initial = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-missing-runtime",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )
    shutil.rmtree(initial.run_dir / "runtime" / "conv-test-1")
    (initial.run_dir / "summary.json").unlink()

    with pytest.raises(ValueError, match="runtime state"):
        run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "runs",
                run_id="locomo-missing-runtime",
                repository_commit="abc123",
                mode="smoke",
                resume=True,
                expected_dataset_sha256=None,
            ),
            memory_factory=FakeMemory,
            answer_model=FakeAnswerModel(),
            judge_model=None,
        )


def test_resume_rejects_configuration_drift_before_model_calls(tmp_path: Path) -> None:
    initial = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-resume-drift",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )
    answer = FakeAnswerModel()

    with pytest.raises(ValueError, match="does not match"):
        run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=tmp_path / "runs",
                run_id="locomo-resume-drift",
                repository_commit="abc123",
                mode="smoke",
                top_k=5,
                resume=True,
                expected_dataset_sha256=None,
            ),
            memory_factory=FakeMemory,
            answer_model=answer,
            judge_model=None,
        )

    assert initial.run_dir.is_dir()
    assert answer.calls == 0


def test_smoke_report_counts_infrastructure_failures_without_scoring_them(
    tmp_path: Path,
) -> None:
    answer = FailingAnswerModel()
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-failed-smoke",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=answer,
        judge_model=None,
    )

    assert artifact.summary["question_artifact_count"] == 2
    assert artifact.summary["completed_question_count"] == 0
    assert artifact.summary["infrastructure_failed_count"] == 2
    assert artifact.summary["scored_question_count"] == 0
    assert artifact.summary["accuracy"] is None
    assert answer.calls == 2
    assert artifact.summary["usage"]["answer_call_count"] == 2
    assert artifact.summary["usage"]["answer_response_count"] == 0
    assert artifact.summary["answer_attempts"]["provider_failed_count"] == 2
    question_files = sorted((artifact.run_dir / "checkpoints/questions").glob("*/*.json"))
    for question_path in question_files:
        record = json.loads(question_path.read_text())
        assert record["phase"] == "answer"
        assert record["answer_attempt_receipt"]["status"] == "provider_failed"
        assert record["answer_attempt_receipt"]["attempt_count"] == 1


def test_report_keeps_deepseek_cache_reasoning_and_cny_usage(tmp_path: Path) -> None:
    artifact = run_locomo(
        LoCoMoRunConfig(
            dataset_path=FIXTURE,
            output_root=tmp_path / "runs",
            run_id="locomo-deepseek-usage",
            repository_commit="abc123",
            mode="smoke",
            expected_dataset_sha256=None,
        ),
        memory_factory=FakeMemory,
        answer_model=CnyAnswerModel(),
        judge_model=None,
    )

    assert artifact.summary["usage"] == {
        "input_tokens": 20,
        "cached_input_tokens": 12,
        "uncached_input_tokens": 8,
        "output_tokens": 6,
        "reasoning_tokens": 4,
        "known_cost_count": 0,
        "cost_usd": None,
        "known_cost_cny_count": 2,
        "cost_cny": 0.002,
        "answer_call_count": 2,
        "answer_response_count": 2,
        "journal_application_call_count": 2,
        "journal_completed_outcome_count": 2,
        "journal_provider_attempt_count": 2,
        "journal_known_provider_attempt_count": 2,
        "journal_unknown_spend_count": 0,
    }


def test_run_rejects_path_traversal_identifiers(tmp_path: Path) -> None:
    config = LoCoMoRunConfig(
        dataset_path=FIXTURE,
        output_root=tmp_path / "runs",
        run_id="..",
        repository_commit="abc123",
        mode="smoke",
        expected_dataset_sha256=None,
    )

    with pytest.raises(ValueError, match="safe path segment"):
        run_locomo(
            config,
            memory_factory=FakeMemory,
            answer_model=FakeAnswerModel(),
            judge_model=None,
        )


def test_run_rejects_output_symlink_escape(tmp_path: Path) -> None:
    output_root = tmp_path / "runs"
    output_root.mkdir()
    (output_root / "escaped-run").symlink_to(
        tmp_path / "outside" / "escaped-run",
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="output root"):
        run_locomo(
            LoCoMoRunConfig(
                dataset_path=FIXTURE,
                output_root=output_root,
                run_id="escaped-run",
                repository_commit="abc123",
                mode="smoke",
                expected_dataset_sha256=None,
            ),
            memory_factory=FakeMemory,
            answer_model=FakeAnswerModel(),
            judge_model=None,
        )

    assert not (tmp_path / "outside").exists()


def _tree_fingerprints(root: Path) -> dict[str, tuple[int, str]]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
