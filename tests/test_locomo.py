from __future__ import annotations

import hashlib
import json
import shutil
import threading
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

import pytest

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.evaluation.artifacts import write_json_exclusive
from codecairn.evaluation.locomo import (
    CodeCairnConversationMemory,
    ConversationIngestResult,
    EvidenceAnswerSynthesizer,
    FrozenQueryEmbeddingAdapter,
    LoCoMoConversation,
    LoCoMoConversationWork,
    LoCoMoCorpusConfig,
    LoCoMoQueryVectorConfig,
    LoCoMoRunConfig,
    build_locomo_corpus,
    build_locomo_query_vectors,
    load_locomo_dataset,
    load_locomo_question_set,
    report_locomo,
    run_locomo,
    run_locomo_conversation_questions,
)
from codecairn.evaluation.locomo_ablation import (
    LoCoMoAblationConfig,
    build_locomo_ablation_report,
)
from codecairn.evaluation.model import ModelResponse
from codecairn.memory.models import RankedRecall, RecallResult, RecallSidecar, RecallSnippet
from codecairn.memory.retrieval import retrieval_config_sha256
from codecairn.storage.sqlite import SQLiteState

FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"
FAKE_RETRIEVAL_CONFIG: dict[str, object] = {
    "method": "hybrid-rrf-cross-encoder",
    "inference_threads": 1,
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


class FakeMemory:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        turn_count = sum(len(session.turns) for session in conversation.sessions)
        return ConversationIngestResult(
            session_count=len(conversation.sessions),
            turn_count=turn_count,
            accepted_memory_count=turn_count,
            rejected_memory_count=0,
        )

    def recall(self, question: str, *, limit: int) -> RecallResult:
        return RecallResult(
            markdown="# Recall Context\n\nA relevant attributed memory.\n",
            sidecar=RecallSidecar(
                query=question,
                repo_key=f"locomo/{self.root.name}",
                limit=limit,
                latency_ms=1.25,
                vector_candidate_count=1,
                lexical_candidate_count=1,
                ranked=(),
                reranker_model="test/reranker",
                reranker_source="test/reranker-source",
                reranker_revision="b" * 40,
                embedding_model="test/embedding",
                embedding_source="test/embedding-source",
                embedding_revision="a" * 40,
                retrieval_config_sha256=retrieval_config_sha256(FAKE_RETRIEVAL_CONFIG),
            ),
        )

    def corpus_snapshot(self) -> dict[str, object]:
        return {"adapter": "fake", "sample_id": self.root.name}


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
            text="A concise answer",
            model=self.model_id,
            input_tokens=10,
            output_tokens=3,
            cost_usd=0.001,
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
            text="A concise answer",
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
        text = '{"label":"CORRECT"}' if response_format == "json" else "A concise answer"
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


def test_locomo_sessions_ingest_as_real_episode_parents_through_public_interfaces(
    tmp_path: Path,
) -> None:
    conversation = load_locomo_dataset(FIXTURE).conversations[0]
    root = tmp_path / "memory"
    adapter = CodeCairnConversationMemory(
        runtime=create_runtime(root),
        cascade=create_cascade(root),
        repo_key=f"locomo/{conversation.sample_id}",
    )

    ingested = adapter.ingest(conversation, dataset_sha256="f" * 64)
    recalled = adapter.recall("What breed is Caroline's dog?", limit=5)

    assert ingested.session_count == 2
    assert ingested.turn_count == 3
    assert ingested.accepted_memory_count == 2
    assert ingested.rejected_memory_count == 0
    assert recalled.sidecar.repo_key == "locomo/conv-test-1"
    assert recalled.sidecar.ranked[0].memory_type == "user_preference"
    assert "beagle" in recalled.markdown
    memories = create_runtime(root).list_memories(repo_key="locomo/conv-test-1")
    assert len(memories) == 2
    assert sorted(len(item.facts) for item in memories) == [1, 2]
    assert all(len({fact.episode_id for fact in item.facts}) == 1 for item in memories)
    assert all(" — " in fact.text and ": " in fact.text for item in memories for fact in item.facts)
    entity_hits = SQLiteState(root / "state.sqlite3").find_entity_memories(
        repo_key="locomo/conv-test-1",
        entity_keys=("caroline",),
        limit=10,
    )
    assert entity_hits
    assert all(any("Caroline" in fact.text for fact in item.facts) for item in entity_hits)
    assert {item.evidence[0].provider for item in memories} == {"locomo"}


def test_evidence_answer_synthesizer_does_not_invent_citations_for_plain_answers() -> None:
    class CitedAnswerModel(FakeAnswerModel):
        def generate(
            self,
            *,
            system: str,
            user: str,
            seed: int,
            response_format: str = "text",
        ) -> ModelResponse:
            assert response_format == "text"
            return ModelResponse(text="A beagle.", model=self.model_id)

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
        conversation,
        conversation.questions[0],
        recall=recall,
        model=CitedAnswerModel(),
        seed=7,
    )

    assert answer.response.text == "A beagle."
    assert answer.evidence_ids == ()
    assert answer.invalid_evidence_ids == ()
    assert answer.format == "unstructured-fallback"


def test_evidence_answer_synthesizer_uses_bounded_attributed_markdown() -> None:
    @dataclass
    class CapturingAnswerModel:
        user_payload: dict[str, object] | None = None
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
            self.user_payload = json.loads(user)
            self.response_format = response_format
            return ModelResponse(text="rock", model=self.model_id)

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
        conversation,
        question,
        recall=recall,
        model=model,
        seed=7,
    )

    assert model.user_payload is not None
    assert set(model.user_payload) == {"memory_context", "question", "speakers"}
    assert len(model.user_payload["memory_context"]) == 24_000
    assert model.response_format == "text"
    assert answer.response.text == "rock"
    assert answer.evidence_ids == ()
    assert answer.invalid_evidence_ids == ()
    assert answer.format == "unstructured-fallback"


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
    assert corpus.manifest["build_contract"]["projection_contract"] == ("locomo-session-episode-v2")
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
            "atomic_fact_vector_candidate_count": 0.0,
            "episode_lexical_candidate_count": 0.0,
            "episode_vector_candidate_count": 0.0,
            "neighbor_expansion_count": 0.0,
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
                "answer_evidence_contract": "bounded-attributed-markdown-v4",
                "judge_model": "fake-judge",
                "judge_votes": 3,
                "top_k": 20,
                "inference_threads": 1,
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
                "primary_candidate_multiplier": 2,
                "secondary_candidate_multiplier": 1,
                "minimum_primary_candidates": 40,
                "minimum_secondary_candidates": 20,
                "neighbor_snippet_budget": 20,
                "enrichment_order": "matched-adjacency-rerank-top-k-neighbors-v2",
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
    run_paths: dict[str, Path] = {}
    for mode in ("episode-only", "hierarchy-no-neighbors", "hierarchy"):
        retrieval_config = {
            **FAKE_RETRIEVAL_CONFIG,
            "planner": {
                "mode": mode,
                "primary_candidate_multiplier": 2,
                "secondary_candidate_multiplier": 1,
                "minimum_primary_candidates": 40,
                "minimum_secondary_candidates": 20,
                "neighbor_snippet_budget": 20,
                "enrichment_order": "matched-adjacency-rerank-top-k-neighbors-v2",
            },
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
    assert (tmp_path / "ablation-report.json").is_file()

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

    assert judge.calls == 36
    assert artifact.summary["completed_question_count"] == 4
    assert artifact.summary["scored_question_count"] == 0
    assert artifact.summary["infrastructure_failed_count"] == 4
    question_files = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))
    for question_path in question_files:
        payload = json.loads(question_path.read_text(encoding="utf-8"))
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
    }


def test_report_rejects_retry_metadata_that_exceeds_the_manifest_limit(tmp_path: Path) -> None:
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
        judge_model=AlternatingJudgeModel(),
    )
    manifest_path = artifact.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["judge_response_max_attempts"] = 1
    manifest_path.unlink()
    write_json_exclusive(manifest_path, manifest)
    question_path = sorted((artifact.run_dir / "checkpoints" / "questions").glob("*/*.json"))[0]
    question = json.loads(question_path.read_text(encoding="utf-8"))
    question["judge_votes"][0]["attempt_count"] = 2
    question["judge_votes"][0]["failed_attempts"] = [
        {"attempt_index": 1, "error_type": "JSONDecodeError"}
    ]
    question_path.unlink()
    write_json_exclusive(question_path, question)

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
        else:
            assert response_format == "text"
            assert set(payload) == {"memory_context", "question", "speakers"}
            assert "inspect the whole supplied context" in system.casefold()
            assert "for list questions include every supported item" in system.casefold()


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

    assert answer.calls == 1
    assert preserved_path.read_bytes() == preserved_before
    assert resumed.summary["question_artifact_count"] == 2
    assert resumed.summary["infrastructure_failed_count"] == 0


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
