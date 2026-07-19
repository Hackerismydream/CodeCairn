from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from codecairn.bootstrap import create_cascade, create_runtime
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
