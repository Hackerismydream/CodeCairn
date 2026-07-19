from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, cast

from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.model import TextModel
from codecairn.memory.models import (
    EvidenceFact,
    EvidenceReference,
    MemoryProposal,
    RecallResult,
)
from codecairn.memory.trace import stable_id
from codecairn.service.cascade import MiniCascade
from codecairn.service.runtime import MemoryRuntime

LOCOMO_DATASET_URL = (
    "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
)
LOCOMO_DATASET_SHA256 = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
LOCOMO_LICENSE = "CC BY-NC 4.0"
CATEGORY_NAMES = {
    1: "single-hop",
    2: "multi-hop",
    3: "open-domain",
    4: "temporal",
    5: "adversarial",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

RunMode = Literal["full", "smoke"]


@dataclass(frozen=True, slots=True)
class LoCoMoTurn:
    dia_id: str
    speaker: str
    text: str
    timestamp: str
    timestamp_iso: str
    turn_index: int


@dataclass(frozen=True, slots=True)
class LoCoMoSession:
    session_id: str
    timestamp: str
    turns: tuple[LoCoMoTurn, ...]


@dataclass(frozen=True, slots=True)
class LoCoMoQuestion:
    question_id: str
    question: str
    golden_answer: str | None
    adversarial_answer: str | None
    category: int
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoCoMoConversation:
    sample_id: str
    speaker_a: str
    speaker_b: str
    sessions: tuple[LoCoMoSession, ...]
    questions: tuple[LoCoMoQuestion, ...]


@dataclass(frozen=True, slots=True)
class LoCoMoDataset:
    source_path: str
    sha256: str
    conversations: tuple[LoCoMoConversation, ...]


@dataclass(frozen=True, slots=True)
class ConversationIngestResult:
    session_count: int
    turn_count: int
    accepted_memory_count: int
    rejected_memory_count: int


class ConversationMemory(Protocol):
    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult: ...

    def recall(self, question: str, *, limit: int) -> RecallResult: ...


@dataclass(frozen=True, slots=True)
class LoCoMoRunConfig:
    dataset_path: Path
    output_root: Path
    run_id: str
    repository_commit: str
    mode: RunMode = "full"
    categories: tuple[int, ...] = (1, 2, 3, 4)
    conversation_ids: tuple[str, ...] = ()
    top_k: int = 20
    judge_votes: int = 3
    seed: int = 17
    expected_dataset_sha256: str | None = LOCOMO_DATASET_SHA256


@dataclass(frozen=True, slots=True)
class LoCoMoRunArtifact:
    run_dir: Path
    summary: dict[str, object]


MemoryFactory = Callable[[Path], ConversationMemory]


class CodeCairnConversationMemory:
    """Map attributed dialog turns to exact-quote memories through public use cases."""

    def __init__(
        self,
        *,
        runtime: MemoryRuntime,
        cascade: MiniCascade,
        repo_key: str,
    ) -> None:
        self._runtime = runtime
        self._cascade = cascade
        self._repo_key = repo_key

    def ingest(
        self,
        conversation: LoCoMoConversation,
        *,
        dataset_sha256: str,
    ) -> ConversationIngestResult:
        accepted = 0
        rejected = 0
        turn_count = 0
        for session in conversation.sessions:
            for turn in session.turns:
                turn_count += 1
                evidence = _turn_evidence(
                    conversation,
                    session,
                    turn,
                    dataset_sha256=dataset_sha256,
                )
                fact_id = stable_id(
                    "locomo-fact",
                    self._repo_key,
                    session.session_id,
                    turn.dia_id,
                    evidence.raw_event_sha256,
                )
                fact = EvidenceFact(
                    fact_id=fact_id,
                    repo_key=self._repo_key,
                    episode_id=stable_id(
                        "locomo-session",
                        self._repo_key,
                        session.session_id,
                    ),
                    kind="user_quote",
                    text=turn.text,
                    role="user",
                    evidence=(evidence,),
                )
                proposal = MemoryProposal(
                    proposal_id=stable_id("locomo-proposal", self._repo_key, fact_id),
                    repo_key=self._repo_key,
                    memory_type="user_preference",
                    title=f"{turn.speaker} in {session.session_id}",
                    summary=f"{turn.timestamp_iso} — {turn.speaker}: {turn.text}",
                    fact_ids=(fact_id,),
                    quote=turn.text,
                    quote_role="user",
                    confidence=1.0,
                )
                decision = self._runtime.evaluate_proposal(proposal, facts=(fact,))
                if decision.accepted:
                    accepted += 1
                else:
                    rejected += 1
        self._cascade.run_until_idle(worker_id=f"locomo-{conversation.sample_id}")
        return ConversationIngestResult(
            session_count=len(conversation.sessions),
            turn_count=turn_count,
            accepted_memory_count=accepted,
            rejected_memory_count=rejected,
        )

    def recall(self, question: str, *, limit: int) -> RecallResult:
        return self._runtime.recall(question, repo_key=self._repo_key, limit=limit)


def load_locomo_dataset(path: Path) -> LoCoMoDataset:
    sha256 = file_sha256(path)
    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError("LoCoMo dataset must be a JSON array")
    conversations = tuple(
        _parse_conversation(_required_dict(item, field="conversation record")) for item in payload
    )
    sample_ids = [conversation.sample_id for conversation in conversations]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("LoCoMo sample identifiers must be unique")
    return LoCoMoDataset(
        source_path=str(path.resolve()),
        sha256=sha256,
        conversations=conversations,
    )


def run_locomo(
    config: LoCoMoRunConfig,
    *,
    memory_factory: MemoryFactory,
    answer_model: TextModel,
    judge_model: TextModel | None,
) -> LoCoMoRunArtifact:
    _validate_config(config, judge_model=judge_model)
    dataset = load_locomo_dataset(config.dataset_path)
    if (
        config.expected_dataset_sha256 is not None
        and dataset.sha256 != config.expected_dataset_sha256
    ):
        raise ValueError("LoCoMo dataset digest does not match the run manifest")
    selected = _select_conversations(dataset, config.conversation_ids)
    run_dir = (config.output_root / config.run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    question_counts = Counter(
        question.category
        for conversation in selected
        for question in conversation.questions
        if question.category in config.categories
    )
    dataset_category_counts = Counter(
        question.category
        for conversation in dataset.conversations
        for question in conversation.questions
    )
    manifest = {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": config.run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": config.mode,
        "scored": config.mode == "full",
        "repository_commit": config.repository_commit,
        "dataset": {
            "url": LOCOMO_DATASET_URL,
            "sha256": dataset.sha256,
            "license": LOCOMO_LICENSE,
            "conversation_count": len(dataset.conversations),
            "session_count": sum(
                len(conversation.sessions) for conversation in dataset.conversations
            ),
            "turn_count": sum(
                len(session.turns)
                for conversation in dataset.conversations
                for session in conversation.sessions
            ),
            "question_count": sum(dataset_category_counts.values()),
            "category_counts": {
                str(key): value for key, value in sorted(dataset_category_counts.items())
            },
        },
        "selection": {
            "conversation_ids": [item.sample_id for item in selected],
            "categories": list(config.categories),
            "question_counts": {str(key): value for key, value in sorted(question_counts.items())},
        },
        "retrieval": {"method": "hybrid-rrf", "top_k": config.top_k},
        "answer_model": answer_model.public_config,
        "judge_model": None if judge_model is None else judge_model.public_config,
        "judge_votes": config.judge_votes if config.mode == "full" else 0,
        "seed": config.seed,
    }
    write_json_exclusive(run_dir / "manifest.json", manifest)

    for conversation_index, conversation in enumerate(selected):
        memory_root = run_dir / "runtime" / conversation.sample_id
        memory_root.mkdir(parents=True, exist_ok=False)
        memory = memory_factory(memory_root)
        ingest = memory.ingest(conversation, dataset_sha256=dataset.sha256)
        write_json_exclusive(
            run_dir / "checkpoints" / "ingest" / f"{conversation.sample_id}.json",
            {
                "sample_id": conversation.sample_id,
                "speaker_a": conversation.speaker_a,
                "speaker_b": conversation.speaker_b,
                "memory_root": str(memory_root.relative_to(run_dir)),
                **asdict(ingest),
            },
        )
        selected_questions = [
            question
            for question in conversation.questions
            if question.category in config.categories
        ]
        if config.mode == "smoke":
            selected_questions = selected_questions[:1]
        for question_index, question in enumerate(selected_questions):
            seed = config.seed + conversation_index * 10_000 + question_index
            record = _run_question(
                conversation,
                question,
                memory=memory,
                answer_model=answer_model,
                judge_model=judge_model,
                judge_votes=config.judge_votes if config.mode == "full" else 0,
                top_k=config.top_k,
                seed=seed,
            )
            write_json_exclusive(
                run_dir
                / "checkpoints"
                / "questions"
                / conversation.sample_id
                / f"{question.question_id}.json",
                record,
            )

    summary = report_locomo(run_dir)
    write_json_exclusive(run_dir / "summary.json", summary)
    return LoCoMoRunArtifact(run_dir=run_dir, summary=summary)


def report_locomo(run_dir: Path) -> dict[str, object]:
    manifest = _required_dict(read_json(run_dir / "manifest.json"), field="run manifest")
    mode = _required_str(manifest, "mode")
    expected_votes = _required_int(manifest, "judge_votes")
    records = [
        _required_dict(read_json(path), field="question checkpoint")
        for path in sorted((run_dir / "checkpoints" / "questions").glob("*/*.json"))
    ]
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0
    known_cost_count = 0
    correct = 0
    scored = 0
    infrastructure_failed = 0
    categories: dict[int, list[bool]] = {}
    for record in records:
        for response_key in ("answer",):
            response = record.get(response_key)
            if isinstance(response, dict):
                total_input_tokens += _optional_int(response.get("input_tokens")) or 0
                total_output_tokens += _optional_int(response.get("output_tokens")) or 0
                cost = _optional_float(response.get("cost_usd"))
                if cost is not None:
                    total_cost_usd += cost
                    known_cost_count += 1
        votes = record.get("judge_votes")
        if isinstance(votes, list):
            for vote in votes:
                if not isinstance(vote, dict):
                    continue
                total_input_tokens += _optional_int(vote.get("input_tokens")) or 0
                total_output_tokens += _optional_int(vote.get("output_tokens")) or 0
                cost = _optional_float(vote.get("cost_usd"))
                if cost is not None:
                    total_cost_usd += cost
                    known_cost_count += 1
        if mode != "full":
            continue
        if record.get("status") != "completed" or not isinstance(votes, list):
            infrastructure_failed += 1
            continue
        labels = [vote.get("label") for vote in votes if isinstance(vote, dict)]
        if len(labels) != expected_votes or any(
            label not in {"correct", "wrong"} for label in labels
        ):
            infrastructure_failed += 1
            continue
        is_correct = labels.count("correct") > expected_votes / 2
        category = _required_int(record, "category")
        categories.setdefault(category, []).append(is_correct)
        correct += int(is_correct)
        scored += 1
    by_category = {
        str(category): {
            "name": CATEGORY_NAMES.get(category, "unknown"),
            "correct": sum(results),
            "count": len(results),
            "accuracy": round(sum(results) / len(results), 6),
        }
        for category, results in sorted(categories.items())
        if results
    }
    return {
        "schema_version": 1,
        "suite": "locomo",
        "run_id": _required_str(manifest, "run_id"),
        "mode": mode,
        "scored": mode == "full",
        "question_artifact_count": len(records),
        "scored_question_count": scored,
        "infrastructure_failed_count": infrastructure_failed,
        "correct_count": correct if mode == "full" else None,
        "accuracy": round(correct / scored, 6) if scored else None,
        "by_category": by_category if mode == "full" else {},
        "usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "known_cost_count": known_cost_count,
            "cost_usd": round(total_cost_usd, 8) if known_cost_count else None,
        },
        "unscored_reason": "smoke mode is never scored" if mode == "smoke" else None,
    }


def _run_question(
    conversation: LoCoMoConversation,
    question: LoCoMoQuestion,
    *,
    memory: ConversationMemory,
    answer_model: TextModel,
    judge_model: TextModel | None,
    judge_votes: int,
    top_k: int,
    seed: int,
) -> dict[str, object]:
    try:
        recall = memory.recall(question.question, limit=top_k)
    except Exception as exc:
        return {
            "schema_version": 1,
            "sample_id": conversation.sample_id,
            "question_id": question.question_id,
            "category": question.category,
            "status": "infrastructure_failed",
            "phase": "retrieval",
            "error_type": type(exc).__name__,
            "judge_votes": [],
        }
    try:
        answer = answer_model.generate(
            system=(
                "Answer the question using only the attributed, timestamped memory context. "
                "Give a concise answer and say when the context is insufficient."
            ),
            user=(
                f"Speakers: {conversation.speaker_a} and {conversation.speaker_b}\n\n"
                f"{recall.markdown}\nQuestion: {question.question}"
            ),
            seed=seed,
        )
    except Exception as exc:
        return {
            "schema_version": 1,
            "sample_id": conversation.sample_id,
            "question_id": question.question_id,
            "category": question.category,
            "status": "infrastructure_failed",
            "error_type": type(exc).__name__,
            "retrieval": asdict(recall.sidecar),
            "judge_votes": [],
        }
    votes: list[dict[str, object]] = []
    for vote_index in range(judge_votes):
        assert judge_model is not None
        votes.append(
            _judge_answer(
                question,
                generated_answer=answer.text,
                judge_model=judge_model,
                vote_index=vote_index,
                seed=seed + vote_index + 1,
            )
        )
    return {
        "schema_version": 1,
        "sample_id": conversation.sample_id,
        "question_id": question.question_id,
        "question": question.question,
        "golden_answer": question.golden_answer,
        "category": question.category,
        "category_name": CATEGORY_NAMES.get(question.category, "unknown"),
        "evidence": list(question.evidence),
        "status": "completed",
        "retrieval": asdict(recall.sidecar),
        "recall_markdown": recall.markdown,
        "answer": asdict(answer),
        "judge_votes": votes,
    }


def _judge_answer(
    question: LoCoMoQuestion,
    *,
    generated_answer: str,
    judge_model: TextModel,
    vote_index: int,
    seed: int,
) -> dict[str, object]:
    if question.golden_answer is None:
        return {
            "vote_index": vote_index,
            "label": "invalid",
            "error_type": "MissingGoldenAnswer",
        }
    try:
        response = judge_model.generate(
            system=(
                "Grade whether a generated answer matches the gold answer. "
                "Return JSON only with label equal to CORRECT or WRONG."
            ),
            user=(
                f"Question: {question.question}\n"
                f"Gold answer: {question.golden_answer}\n"
                f"Generated answer: {generated_answer}"
            ),
            seed=seed,
            response_format="json",
        )
        label = _parse_judge_label(response.text)
        return {
            "vote_index": vote_index,
            "label": label,
            "raw_response": response.text,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
        }
    except Exception as exc:
        return {
            "vote_index": vote_index,
            "label": "invalid",
            "error_type": type(exc).__name__,
        }


def _parse_judge_label(text: str) -> Literal["correct", "wrong"]:
    payload = json.loads(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("label"), str):
        raise ValueError("Judge response must contain a string label")
    label = cast(str, payload["label"]).strip().casefold()
    if label not in {"correct", "wrong"}:
        raise ValueError("Judge label must be CORRECT or WRONG")
    return cast(Literal["correct", "wrong"], label)


def _turn_evidence(
    conversation: LoCoMoConversation,
    session: LoCoMoSession,
    turn: LoCoMoTurn,
    *,
    dataset_sha256: str,
) -> EvidenceReference:
    raw = {
        "sample_id": conversation.sample_id,
        "session_id": session.session_id,
        "dia_id": turn.dia_id,
        "speaker": turn.speaker,
        "text": turn.text,
        "timestamp": turn.timestamp,
    }
    digest = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return EvidenceReference(
        provider="locomo",
        session_id=f"{conversation.sample_id}/{session.session_id}",
        source_path=(f"locomo://{dataset_sha256}/{conversation.sample_id}/{session.session_id}"),
        raw_event_sha256=digest,
        raw_event_index=turn.turn_index,
        raw_event_type="locomo_turn",
    )


def _parse_conversation(record: dict[str, object]) -> LoCoMoConversation:
    sample_id = _required_str(record, "sample_id")
    if _SAFE_ID.fullmatch(sample_id) is None:
        raise ValueError("LoCoMo sample_id must be a safe path segment")
    raw_conversation = _required_dict(record.get("conversation"), field="conversation")
    speaker_a = _required_str(raw_conversation, "speaker_a")
    speaker_b = _required_str(raw_conversation, "speaker_b")
    sessions: list[LoCoMoSession] = []
    session_index = 1
    global_turn_index = 0
    while f"session_{session_index}_date_time" in raw_conversation:
        session_id = f"session_{session_index}"
        timestamp = _required_str(raw_conversation, f"{session_id}_date_time")
        base_time = _parse_timestamp(timestamp)
        raw_turns = raw_conversation.get(session_id)
        if raw_turns is None:
            session_index += 1
            continue
        if not isinstance(raw_turns, list):
            raise ValueError(f"LoCoMo {session_id} must be an array")
        turns: list[LoCoMoTurn] = []
        for position, raw_turn in enumerate(raw_turns):
            turn = _required_dict(raw_turn, field="conversation turn")
            text = _turn_text(turn)
            if not text:
                continue
            turns.append(
                LoCoMoTurn(
                    dia_id=_required_str(turn, "dia_id"),
                    speaker=_required_str(turn, "speaker"),
                    text=text,
                    timestamp=timestamp,
                    timestamp_iso=(base_time + timedelta(seconds=position * 30)).isoformat(),
                    turn_index=global_turn_index,
                )
            )
            global_turn_index += 1
        sessions.append(
            LoCoMoSession(
                session_id=session_id,
                timestamp=timestamp,
                turns=tuple(turns),
            )
        )
        session_index += 1
    raw_questions = record.get("qa")
    if not isinstance(raw_questions, list):
        raise ValueError("LoCoMo qa must be an array")
    questions = tuple(
        _parse_question(sample_id, index, _required_dict(item, field="QA record"))
        for index, item in enumerate(raw_questions)
    )
    return LoCoMoConversation(
        sample_id=sample_id,
        speaker_a=speaker_a,
        speaker_b=speaker_b,
        sessions=tuple(sessions),
        questions=questions,
    )


def _parse_question(
    sample_id: str,
    index: int,
    record: dict[str, object],
) -> LoCoMoQuestion:
    question = _required_str(record, "question")
    category = _required_int(record, "category")
    if category not in CATEGORY_NAMES:
        raise ValueError(f"Unknown LoCoMo category: {category}")
    evidence = record.get("evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ValueError("LoCoMo evidence must be an array of dialog identifiers")
    return LoCoMoQuestion(
        question_id=stable_id("locomo-question", sample_id, str(index), question),
        question=question,
        golden_answer=_optional_answer(record.get("answer")),
        adversarial_answer=_optional_str(record.get("adversarial_answer")),
        category=category,
        evidence=tuple(cast(list[str], evidence)),
    )


def _turn_text(turn: dict[str, object]) -> str:
    parts: list[str] = []
    text = _optional_str(turn.get("text"))
    caption = _optional_str(turn.get("blip_caption"))
    if text:
        parts.append(text)
    if caption:
        parts.append(f"[Image caption: {caption}]")
    return " ".join(parts)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.strptime(value.strip(), "%I:%M %p on %d %B, %Y")
    return parsed.replace(tzinfo=UTC)


def _select_conversations(
    dataset: LoCoMoDataset,
    selected_ids: tuple[str, ...],
) -> tuple[LoCoMoConversation, ...]:
    if not selected_ids:
        return dataset.conversations
    selected = set(selected_ids)
    conversations = tuple(item for item in dataset.conversations if item.sample_id in selected)
    if {item.sample_id for item in conversations} != selected:
        raise ValueError("Run selects an unknown LoCoMo conversation")
    return conversations


def _validate_config(config: LoCoMoRunConfig, *, judge_model: TextModel | None) -> None:
    if _SAFE_ID.fullmatch(config.run_id) is None:
        raise ValueError("run_id must be a safe path segment")
    if not config.repository_commit.strip():
        raise ValueError("repository_commit must not be empty")
    if not 1 <= config.top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    if not config.categories or any(
        category not in CATEGORY_NAMES for category in config.categories
    ):
        raise ValueError("categories must contain known LoCoMo categories")
    if config.mode == "full" and config.judge_votes < 1:
        raise ValueError("Full LoCoMo runs require judge votes")
    if config.mode == "full" and config.judge_votes % 2 == 0:
        raise ValueError("judge_votes must be odd for majority voting")
    if config.mode == "full" and judge_model is None:
        raise ValueError("Full LoCoMo runs require a judge model")


def _required_dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field.capitalize()} must be a JSON object")
    return cast(dict[str, object], value)


def _required_str(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_int(record: dict[str, object], field: str) -> int:
    value = record.get(field)
    if not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional LoCoMo text must be a string")
    return value


def _optional_answer(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        return str(value)
    raise ValueError("Optional LoCoMo answer must be text or a number")


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None
