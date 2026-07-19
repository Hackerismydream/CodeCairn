from __future__ import annotations

import hashlib
import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.evaluation.artifacts import write_json_exclusive
from codecairn.evaluation.locomo import (
    CodeCairnConversationMemory,
    ConversationIngestResult,
    LoCoMoConversation,
    LoCoMoRunConfig,
    load_locomo_dataset,
    report_locomo,
    run_locomo,
)
from codecairn.evaluation.model import ModelResponse
from codecairn.memory.models import RecallResult, RecallSidecar

FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"


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
            ),
        )


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


class ConcurrentIngestMemory(FakeMemory):
    barrier = threading.Barrier(2)

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        self.barrier.wait(timeout=2)
        return super().ingest(conversation, dataset_sha256=dataset_sha256)


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


def test_locomo_turns_ingest_through_public_gate_and_recall_interfaces(
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
    assert ingested.accepted_memory_count == 3
    assert ingested.rejected_memory_count == 0
    assert recalled.sidecar.repo_key == "locomo/conv-test-1"
    assert recalled.sidecar.ranked[0].memory_type == "user_preference"
    assert "beagle" in recalled.markdown
    memories = create_runtime(root).list_memories(repo_key="locomo/conv-test-1")
    assert len(memories) == 3
    assert {item.evidence[0].provider for item in memories} == {"locomo"}


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
    )

    artifact = run_locomo(
        config,
        memory_factory=memory_factory,
        answer_model=answer,
        judge_model=judge,
    )

    assert len(roots) == 2
    assert len(set(roots)) == 2
    assert all(root.is_relative_to(artifact.run_dir / "runtime") for root in roots)
    assert artifact.summary["scored"] is True
    assert artifact.summary["question_artifact_count"] == 4
    assert artifact.summary["scored_question_count"] == 4
    assert artifact.summary["accuracy"] == 1.0
    assert artifact.summary["by_category"] == {
        "1": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "single-hop"},
        "2": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "multi-hop"},
        "3": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "open-domain"},
        "4": {"accuracy": 1.0, "correct": 1, "count": 1, "name": "temporal"},
    }
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
        if response_format == "json":
            assert set(payload) == {"generated_answer", "gold_answer", "question"}
        else:
            assert set(payload) == {"memory_context", "question", "speakers"}


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


def test_run_processes_isolated_conversations_with_bounded_parallelism(tmp_path: Path) -> None:
    ConcurrentIngestMemory.barrier = threading.Barrier(2)
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
        memory_factory=ConcurrentIngestMemory,
        answer_model=FakeAnswerModel(),
        judge_model=None,
    )

    assert artifact.summary["question_artifact_count"] == 2
    manifest = (artifact.run_dir / "manifest.json").read_text(encoding="utf-8")
    assert '"max_workers": 2' in manifest


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
