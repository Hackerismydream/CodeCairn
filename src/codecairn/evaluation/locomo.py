"""Lean immutable LoCoMo runner over the version 0.1 public recall path."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import httpx

from codecairn.bootstrap import create_runtime
from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json, write_json_exclusive
from codecairn.memory.config import RetrievalConfig
from codecairn.memory.schema import CodingMemory, RepositoryKnowledgePayload

ANSWER_INSTRUCTION = (
    "Use the supplied Memory as evidence to answer the Question. Search all memory sections and connect relevant facts. "
    "You may use ordinary common knowledge or causal inference, but do not invent personal facts. "
    "For list questions, collect every matching fact; for temporal questions, compare dates; "
    "for likely or potential questions, infer the single answer that best satisfies all stated constraints. "
    "Return a JSON object with one string field named answer."
)
JUDGE_INSTRUCTION = (
    "Judge whether Candidate is semantically equivalent to Reference for the Question. "
    "Allow extra correct detail. Return JSON with label equal to correct or wrong."
)


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    sample_id: str
    text: str
    answer: str
    category: int


@dataclass(frozen=True, slots=True)
class Session:
    sample_id: str
    session_id: str
    date: str
    speakers: tuple[str, str]
    content: str


@dataclass(frozen=True, slots=True)
class Selection:
    selection_id: str
    dataset_sha256: str
    selection_sha256: str
    questions: tuple[Question, ...]
    sessions: tuple[Session, ...]


class TextProvider:
    def __init__(self, role: Literal["ANSWER", "JUDGE"], *, environment: dict[str, str]) -> None:
        prefix = f"CODECAIRN_{role}_"
        self.role = role.casefold()
        self.model = environment.get(prefix + "MODEL", "")
        self.endpoint = environment.get(prefix + "BASE_URL", "").rstrip("/")
        api_key = environment.get(prefix + "API_KEY", "")
        url = httpx.URL(self.endpoint)
        if not self.model or not api_key or url.scheme != "https" or not url.host or url.userinfo:
            raise ValueError(f"{self.role} provider requires model, key, and credential-free HTTPS endpoint")
        self._client = httpx.Client(base_url=f"{self.endpoint}/", headers={"Authorization": f"Bearer {api_key}"}, timeout=120)

    @property
    def identity(self) -> dict[str, object]:
        return {
            "role": self.role,
            "adapter": "openai-compatible-chat-v1",
            "endpoint": self.endpoint,
            "model": self.model,
            "revision": "provider-managed",
        }

    def complete(self, *, system: str, prompt: str) -> tuple[dict[str, object], dict[str, object]]:
        response = self._client.post(
            "chat/completions",
            json={
                "model": self.model,
                "temperature": 0,
                "max_completion_tokens": 512,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"{system}\n\n{prompt}"}],
            },
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError(f"{self.role} provider did not return a JSON object")
        usage = body.get("usage", {})
        return cast(dict[str, object], parsed), cast(dict[str, object], usage if isinstance(usage, dict) else {})


def run_locomo(
    *,
    dataset_path: Path,
    protocol_path: Path,
    question_set_path: Path,
    output_root: Path,
    suite: Literal["locomo-200", "locomo-full"],
    run_id: str,
    implementation_sha: str,
    spend_ceiling_usd: float,
    max_call_cost_usd: float,
    environment: dict[str, str],
) -> dict[str, object]:
    protocol = _dict(read_json(protocol_path), field="protocol")
    if protocol.get("schema_version") != 1 or protocol.get("protocol_id") != "codecairn-locomo-v01":
        raise ValueError("LoCoMo protocol is not the version 0.1 contract")
    dataset_contract = _dict(protocol.get("dataset"), field="protocol dataset")
    question_set_contracts = _dict(protocol.get("question_sets"), field="protocol question sets")
    selected_question_set_sha256 = file_sha256(question_set_path)
    accepted_question_set_sha256 = {
        _string(_dict(value, field="protocol question set"), "sha256") for value in question_set_contracts.values()
    }
    if (
        file_sha256(dataset_path) != _string(dataset_contract, "sha256")
        or selected_question_set_sha256 not in accepted_question_set_sha256
    ):
        raise ValueError("LoCoMo inputs are not bound by the version 0.1 protocol")
    selection = load_selection(dataset_path, question_set_path)
    answer = TextProvider("ANSWER", environment=environment)
    judge = TextProvider("JUDGE", environment=environment)
    retrieval = RetrievalConfig.default(cast(Literal["dashscope", "fastembed"], environment.get("RETRIEVAL_PROFILE", "dashscope")))
    run_dir = output_root / suite / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "protocol": {
            "id": "codecairn-locomo-v01",
            "path": str(protocol_path),
            "sha256": file_sha256(protocol_path),
            "contract": protocol,
        },
        "suite": suite,
        "run_id": run_id,
        "implementation_sha": implementation_sha,
        "dataset_sha256": selection.dataset_sha256,
        "question_set": {
            "path": str(question_set_path),
            "selection_id": selection.selection_id,
            "selection_sha256": selection.selection_sha256,
            "question_count": len(selection.questions),
        },
        "providers": {"answer": answer.identity, "judge": judge.identity, "retrieval": retrieval.public_config},
        "budget": {
            "spend_ceiling_usd": spend_ceiling_usd,
            "max_call_cost_usd": max_call_cost_usd,
            "max_completion_tokens": 512,
            "answer_attempts": 2,
            "judge_votes": 3,
            "judge_attempts": 2,
        },
    }
    write_json_exclusive(run_dir / "manifest.json", manifest)
    runtime = create_runtime(run_dir / "runtime", retrieval=retrieval, environment=environment)
    _ingest_sessions(runtime, selection.sessions)
    results_dir = run_dir / "questions"
    results_dir.mkdir()
    for question in selection.questions:
        result = _run_question(runtime, question, answer=answer, judge=judge, root=results_dir / question.question_id)
        write_json_exclusive(results_dir / f"{question.question_id}.json", result)
    report = report_locomo(run_dir)
    write_json_exclusive(run_dir / "aggregate.json", report)
    if report["infrastructure_failed_count"]:
        raise RuntimeError(f"LoCoMo run retained infrastructure failures in {run_dir}")
    return {"run_dir": str(run_dir), "aggregate": report}


def report_locomo(run_dir: Path) -> dict[str, object]:
    manifest = _dict(read_json(run_dir / "manifest.json"), field="manifest")
    results = [_dict(read_json(path), field="question result") for path in sorted((run_dir / "questions").glob("*.json"))]
    return _reduce(manifest, results)


def write_repair_selection(base_run: Path, output: Path) -> dict[str, object]:
    manifest = _dict(read_json(base_run / "manifest.json"), field="manifest")
    results = [_dict(read_json(path), field="question result") for path in sorted((base_run / "questions").glob("*.json"))]
    failed = [item for item in results if item["outcome"] == "infrastructure_failure"]
    if not failed:
        raise ValueError("base run has no infrastructure failures to repair")
    ids = sorted(cast(str, item["question_id"]) for item in failed)
    targets = Counter(cast(int, item["category"]) for item in failed)
    selection = {
        "schema_version": 1,
        "selection_id": f"repair-{base_run.name}",
        "dataset_sha256": manifest["dataset_sha256"],
        "algorithm": "explicit-question-ids-v1",
        "seed": f"exact-failures:{canonical_sha256(ids)}",
        "category_targets": {str(category): count for category, count in sorted(targets.items())},
        "question_ids": ids,
        "selection_sha256": hashlib.sha256(json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "base_manifest_sha256": canonical_sha256(manifest),
    }
    write_json_exclusive(output, selection)
    return selection


def compose_repair(base_run: Path, repair_run: Path, output: Path) -> dict[str, object]:
    base_manifest = _dict(read_json(base_run / "manifest.json"), field="base manifest")
    repair_manifest = _dict(read_json(repair_run / "manifest.json"), field="repair manifest")
    for field in ("protocol", "dataset_sha256", "providers"):
        if base_manifest[field] != repair_manifest[field]:
            raise ValueError(f"repair changed frozen {field}")
    base_results = [_dict(read_json(path), field="base result") for path in sorted((base_run / "questions").glob("*.json"))]
    repair_results = [_dict(read_json(path), field="repair result") for path in sorted((repair_run / "questions").glob("*.json"))]
    failed_ids = {item["question_id"] for item in base_results if item["outcome"] == "infrastructure_failure"}
    repair_ids = {item["question_id"] for item in repair_results}
    if not failed_ids or repair_ids != failed_ids or any(item["outcome"] not in {"correct", "wrong"} for item in repair_results):
        raise ValueError("repair results are not the exact complete infrastructure-failure set")
    replacements = {item["question_id"]: item for item in repair_results}
    combined = [replacements.get(item["question_id"], item) for item in base_results]
    aggregate = _reduce(base_manifest, combined)
    composite = {
        "schema_version": 1,
        "contract": "codecairn-exact-repair-v01",
        "base_manifest_sha256": canonical_sha256(base_manifest),
        "repair_manifest_sha256": canonical_sha256(repair_manifest),
        "repaired_question_ids": sorted(failed_ids),
        "aggregate": aggregate,
    }
    write_json_exclusive(output, composite)
    return composite


def _reduce(manifest: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, object]:
    expected = cast(dict[str, object], manifest["question_set"])["question_count"]
    if len(results) != expected or len({item["question_id"] for item in results}) != len(results):
        raise ValueError("LoCoMo result inventory is incomplete or duplicated")
    scored = [item for item in results if item["outcome"] in {"correct", "wrong"}]
    categories: dict[str, object] = {}
    for category in range(1, 5):
        items = [item for item in scored if item["category"] == category]
        categories[str(category)] = {
            "scored": len(items),
            "correct": sum(item["outcome"] == "correct" for item in items),
            "accuracy": None if not items else sum(item["outcome"] == "correct" for item in items) / len(items),
        }
    latencies = sorted(float(item["retrieval_latency_ms"]) for item in results if "retrieval_latency_ms" in item)
    return {
        "schema_version": 1,
        "protocol": manifest["protocol"],
        "selected_question_count": expected,
        "scored_question_count": len(scored),
        "correct_count": sum(item["outcome"] == "correct" for item in scored),
        "wrong_count": sum(item["outcome"] == "wrong" for item in scored),
        "infrastructure_failed_count": sum(item["outcome"] == "infrastructure_failure" for item in results),
        "accuracy": None if not scored else sum(item["outcome"] == "correct" for item in scored) / len(scored),
        "retrieval_p95_ms": None if not latencies else latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)],
        "categories": categories,
        "manifest_sha256": canonical_sha256(manifest),
        "result_inventory_sha256": canonical_sha256(results),
    }


def load_selection(dataset_path: Path, question_set_path: Path) -> Selection:
    dataset_raw = read_json(dataset_path)
    if not isinstance(dataset_raw, list):
        raise ValueError("LoCoMo dataset must be an array")
    dataset_sha256 = file_sha256(dataset_path)
    definition = _dict(read_json(question_set_path), field="question set")
    if definition.get("schema_version") != 1 or definition.get("dataset_sha256") != dataset_sha256:
        raise ValueError("LoCoMo question set does not bind this dataset")
    questions: list[Question] = []
    sessions: list[Session] = []
    for raw_sample in dataset_raw:
        sample = _dict(raw_sample, field="sample")
        sample_id = _string(sample, "sample_id")
        conversation = _dict(sample.get("conversation"), field="conversation")
        speakers = (_string(conversation, "speaker_a"), _string(conversation, "speaker_b"))
        session_index = 1
        while f"session_{session_index}_date_time" in conversation:
            session_id = f"session_{session_index}"
            turns = conversation.get(session_id, [])
            if not isinstance(turns, list):
                raise ValueError("LoCoMo session must be an array")
            lines = []
            for raw_turn in turns:
                turn = _dict(raw_turn, field="turn")
                text = turn.get("text")
                caption = turn.get("blip_caption")
                content = " ".join(item for item in (text, f"[Image: {caption}]" if caption else None) if isinstance(item, str))
                if content:
                    lines.append(f"{_string(turn, 'speaker')}: {content}")
            date = _string(conversation, f"{session_id}_date_time")
            sessions.append(
                Session(
                    sample_id=sample_id,
                    session_id=session_id,
                    date=date,
                    speakers=speakers,
                    content=_bounded_utf8(f"Date: {date}\n" + "\n".join(lines), 32_768),
                )
            )
            session_index += 1
        qa = sample.get("qa")
        if not isinstance(qa, list):
            raise ValueError("LoCoMo QA must be an array")
        for index, raw_qa in enumerate(qa):
            item = _dict(raw_qa, field="QA")
            text = _string(item, "question")
            category = item.get("category")
            if type(category) is not int or category not in {1, 2, 3, 4, 5}:
                raise ValueError("LoCoMo category is invalid")
            raw_answer = item.get("answer")
            answer = raw_answer if isinstance(raw_answer, str) else json.dumps(raw_answer, ensure_ascii=False)
            questions.append(
                Question(
                    question_id=_stable_id("locomo-question", sample_id, str(index), text),
                    sample_id=sample_id,
                    text=text,
                    answer=answer,
                    category=category,
                )
            )
    selected = _select_questions(questions, definition)
    return Selection(
        selection_id=_string(definition, "selection_id"),
        dataset_sha256=dataset_sha256,
        selection_sha256=_string(definition, "selection_sha256"),
        questions=tuple(selected),
        sessions=tuple(sessions),
    )


def _select_questions(questions: list[Question], definition: dict[str, Any]) -> list[Question]:
    algorithm = _string(definition, "algorithm")
    seed = _string(definition, "seed")
    targets = _dict(definition.get("category_targets"), field="category targets")
    selected: set[str] = set()
    if algorithm == "explicit-question-ids-v1":
        raw_ids = definition.get("question_ids")
        if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
            raise ValueError("explicit question IDs must be a string array")
        selected.update(cast(list[str], raw_ids))
    elif algorithm == "stratified-sha256-v1":
        for raw_category, raw_count in targets.items():
            category, count = int(raw_category), cast(int, raw_count)
            candidates = [question for question in questions if question.category == category]
            candidates.sort(
                key=lambda question: (hashlib.sha256(f"{seed}\0{question.question_id}".encode()).hexdigest(), question.question_id)
            )
            selected.update(question.question_id for question in candidates[:count])
    else:
        raise ValueError("unsupported LoCoMo selection algorithm")
    ordered = [question for question in questions if question.question_id in selected]
    digest = hashlib.sha256(json.dumps(sorted(selected), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if digest != _string(definition, "selection_sha256") or len(ordered) != len(selected):
        raise ValueError("LoCoMo deterministic selection digest is invalid")
    observed = Counter(question.category for question in ordered)
    if observed != Counter({int(category): count for category, count in targets.items()}):
        raise ValueError("LoCoMo category targets do not match selection")
    return ordered


def _ingest_sessions(runtime: Any, sessions: tuple[Session, ...]) -> None:
    for index, session in enumerate(sessions):
        repo_key = f"locomo/{session.sample_id}"
        memory = CodingMemory.create(
            repo_key=repo_key,
            memory_type="repository_knowledge",
            title=f"{session.date}: {session.speakers[0]} and {session.speakers[1]}",
            content=session.content,
            category="other",
            tags=("locomo",),
            created_at_ms=index,
            episode_id=None,
            evidence=(),
            facts=(),
            origin="agent_asserted",
            restored_from=None,
            restore_predecessor_id=None,
            source_order_key=None,
            payload=RepositoryKnowledgePayload(
                subject_key=f"{session.sample_id}/{session.session_id}".casefold(), claim=session.content
            ),
        )
        runtime.store_memory(memory)


def _run_question(runtime: Any, question: Question, *, answer: TextProvider, judge: TextProvider, root: Path) -> dict[str, object]:
    root.mkdir()
    started = time.perf_counter()
    try:
        recall = runtime.recall(question.text, repo_key=f"locomo/{question.sample_id}", limit=40, token_budget=8_192)
        answer_data, answer_usage = _attempt(
            answer,
            root=root,
            phase="answer",
            system=ANSWER_INSTRUCTION,
            prompt=f"Memory:\n{recall.markdown}\n\nQuestion: {question.text}",
            attempts=2,
        )
        candidate = answer_data.get("answer")
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError("answer provider returned no answer")
        votes: list[str] = []
        judge_usage: list[dict[str, object]] = []
        for vote in range(3):
            judged, usage = _attempt(
                judge,
                root=root,
                phase=f"judge-{vote + 1}",
                system=JUDGE_INSTRUCTION,
                prompt=f"Question: {question.text}\nReference: {question.answer}\nCandidate: {candidate}",
                attempts=2,
            )
            label = judged.get("label")
            if label not in {"correct", "wrong"}:
                raise ValueError("judge returned an invalid label")
            votes.append(label)
            judge_usage.append(usage)
        outcome = "correct" if votes.count("correct") >= 2 else "wrong"
        return {
            "question_id": question.question_id,
            "category": question.category,
            "outcome": outcome,
            "candidate_answer": candidate,
            "reference_answer": question.answer,
            "judge_votes": votes,
            "answer_usage": answer_usage,
            "judge_usage": judge_usage,
            "retrieval_latency_ms": recall.sidecar.latency_ms,
            "selected_memory_ids": [item.memory_id for item in recall.sidecar.ranked],
            "duration_ms": (time.perf_counter() - started) * 1_000,
        }
    except Exception as error:
        return {
            "question_id": question.question_id,
            "category": question.category,
            "outcome": "infrastructure_failure",
            "error_type": type(error).__name__,
            "error": str(error)[:512],
            "duration_ms": (time.perf_counter() - started) * 1_000,
        }


def _attempt(
    provider: TextProvider, *, root: Path, phase: str, system: str, prompt: str, attempts: int
) -> tuple[dict[str, object], dict[str, object]]:
    failures = []
    for attempt in range(1, attempts + 1):
        identity = {"phase": phase, "attempt": attempt, "provider": provider.identity, "prompt_sha256": canonical_sha256(prompt)}
        write_json_exclusive(root / f"{phase}-{attempt}.started.json", identity)
        try:
            value, usage = provider.complete(system=system, prompt=prompt)
            write_json_exclusive(root / f"{phase}-{attempt}.finished.json", {"status": "success", "value": value, "usage": usage})
            return value, usage
        except Exception as error:
            failure = {"status": "failed", "error_type": type(error).__name__, "error": str(error)[:512]}
            write_json_exclusive(root / f"{phase}-{attempt}.finished.json", failure)
            failures.append(failure)
    raise RuntimeError(f"{phase} exhausted provider attempts: {failures}")


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(parts).encode()).hexdigest()[:20]}"


def _bounded_utf8(value: str, maximum: int) -> str:
    encoded = value.encode()
    return value if len(encoded) <= maximum else encoded[: maximum - 3].decode(errors="ignore") + "..."


def _dict(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _string(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    repair = commands.add_parser("repair-selection")
    repair.add_argument("base_run", type=Path)
    repair.add_argument("output", type=Path)
    compose = commands.add_parser("compose")
    compose.add_argument("base_run", type=Path)
    compose.add_argument("repair_run", type=Path)
    compose.add_argument("output", type=Path)
    args = parser.parse_args()
    result = (
        write_repair_selection(args.base_run, args.output)
        if args.command == "repair-selection"
        else compose_repair(args.base_run, args.repair_run, args.output)
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
