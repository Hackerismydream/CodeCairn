"""Small, reproducible version 0.1 evaluation gates and paid-run plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from statistics import mean
from typing import Any, Literal, cast

from codecairn.bootstrap import create_runtime
from codecairn.evaluation.artifacts import canonical_sha256, read_json, write_json_exclusive
from codecairn.evaluation.coding import CodexExecAgent, CodingRunConfig, run_coding_evaluation
from codecairn.evaluation.locomo import run_locomo
from codecairn.memory.episode import BoundaryKind
from codecairn.memory.schema import CodingMemory, RepositoryKnowledgePayload

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "benchmarks/retrieval/corpus.json"
QUERIES = ROOT / "benchmarks/retrieval/queries.json"
LOCOMO_200 = ROOT / "benchmarks/locomo/diagnostic-200-v24.json"
LOCOMO_FULL = ROOT / "benchmarks/locomo/full-1540-v24.json"
LOCOMO_PROTOCOL = ROOT / "benchmarks/locomo/v01-protocol.json"
CODING_SUITE = ROOT / "benchmarks/coding/suite.json"
FIXTURES = ROOT / "tests/fixtures"


class EvaluationEmbedder:
    """Deterministic evaluation-only adapter; never selected by product config."""

    model_id = "codecairn-eval-hashing-v1"
    source_id = "checked-in-evaluation"
    revision = "1"
    dimension = 256
    index_identity = f"{model_id}:{dimension}"

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed(text)

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed(text) for text in texts)

    def _embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimension
        for token in _terms(text):
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:2], "big") % self.dimension] += 1
        magnitude = math.sqrt(sum(value * value for value in vector))
        return tuple(value / magnitude for value in vector) if magnitude else tuple(vector)


class EvaluationReranker:
    model_id = "codecairn-eval-lexical-v1"

    def rerank(self, query: str, documents: tuple[tuple[str, str, float], ...]) -> tuple[tuple[str, float], ...]:
        query_terms = _terms(query)
        scores = (
            (memory_id, vector_score + 2 * len(query_terms & _terms(text)) / max(1, len(query_terms)))
            for memory_id, text, vector_score in documents
        )
        return tuple(sorted(scores, key=lambda item: (-item[1], item[0])))


def run_retrieval(*, output_root: Path, run_id: str | None) -> dict[str, object]:
    corpus = _list(read_json(CORPUS), field="retrieval corpus")
    groups = _dict(read_json(QUERIES), field="retrieval queries").get("groups")
    query_groups = _list(groups, field="retrieval query groups")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="codecairn-retrieval-") as directory:
        runtime = create_runtime(Path(directory), retrieval_adapters=(EvaluationEmbedder(), EvaluationReranker()))
        key_by_id: dict[str, str] = {}
        for index, raw_item in enumerate(corpus):
            item = _dict(raw_item, field="retrieval corpus item")
            key = _string(item, "key")
            memory = CodingMemory.create(
                repo_key=_string(item, "repo_key"),
                memory_type="repository_knowledge",
                title=_string(item, "title"),
                content=_string(item, "content"),
                category="other",
                tags=("evaluation",),
                created_at_ms=index,
                episode_id=None,
                evidence=(),
                facts=(),
                origin="agent_asserted",
                restored_from=None,
                restore_predecessor_id=None,
                source_order_key=None,
                payload=RepositoryKnowledgePayload(subject_key=key, claim=_string(item, "content")),
            )
            key_by_id[runtime.store_memory(memory).memory_id] = key
        outcomes: list[dict[str, object]] = []
        for raw_group in query_groups:
            group = _dict(raw_group, field="retrieval query group")
            relevant = set(_string_list(group.get("relevant_keys"), field="relevant keys"))
            repo_key = _string(group, "repo_key")
            for raw_query in _list(group.get("queries"), field="queries"):
                query = _dict(raw_query, field="query")
                result = runtime.recall(_string(query, "text"), repo_key=repo_key, limit=5, token_budget=2_048)
                candidates = [
                    {
                        "rank": ranked.rank,
                        "key": key_by_id[ranked.memory_id],
                        "memory_id": ranked.memory_id,
                        "status": ranked.status,
                        "candidate_sources": ranked.candidate_sources,
                        "source_uri": ranked.source_uri,
                        "content_sha256": ranked.content_sha256,
                    }
                    for ranked in result.sidecar.ranked
                ]
                selected = {cast(str, item["key"]) for item in candidates}
                outcomes.append(
                    {
                        "query_id": _string(query, "query_id"),
                        "repo_key": repo_key,
                        "relevant_keys": sorted(relevant),
                        "candidates": candidates,
                        "recall_at_5": bool(relevant & selected),
                        "precision_at_5": len(relevant & selected) / max(1, len(selected)),
                        "provenance_covered": all(item["source_uri"] and item["content_sha256"] for item in candidates),
                        "stale_predecessor_count": sum(item["status"] != "active" for item in candidates),
                        "latency_ms": result.sidecar.latency_ms,
                    }
                )
    latencies = [cast(float, item["latency_ms"]) for item in outcomes]
    aggregate = {
        "schema_version": 1,
        "protocol": "codecairn-retrieval-100-v01",
        "corpus_sha256": canonical_sha256(corpus),
        "queries_sha256": canonical_sha256(_dict(read_json(QUERIES), field="retrieval queries")),
        "query_count": len(outcomes),
        "recall_at_5": mean(cast(bool, item["recall_at_5"]) for item in outcomes),
        "precision_at_5": mean(cast(float, item["precision_at_5"]) for item in outcomes),
        "provenance_coverage": mean(cast(bool, item["provenance_covered"]) for item in outcomes),
        "stale_predecessor_leakage": sum(cast(int, item["stale_predecessor_count"]) for item in outcomes),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "duration_ms": (time.perf_counter() - started) * 1_000,
    }
    if (
        aggregate["query_count"] != 100
        or cast(float, aggregate["recall_at_5"]) < 0.9
        or aggregate["provenance_coverage"] != 1
        or aggregate["stale_predecessor_leakage"] != 0
        or cast(float, aggregate["p95_latency_ms"]) > 4_000
    ):
        raise RuntimeError(f"retrieval gate failed: {aggregate}")
    return _publish("retrieval", output_root=output_root, run_id=run_id, outcomes=outcomes, aggregate=aggregate)


def run_smoke(*, output_root: Path, run_id: str | None) -> dict[str, object]:
    outcomes: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="codecairn-smoke-") as directory:
        for provider in ("codex", "claude"):
            root = Path(directory) / provider
            runtime = create_runtime(root / "runtime", retrieval_adapters=(EvaluationEmbedder(), EvaluationReranker()))
            source = root / "session.jsonl"
            source.parent.mkdir(exist_ok=True)
            shutil.copyfile(FIXTURES / provider / "failed_command.jsonl", source)
            boundary: BoundaryKind = "codex_stop" if provider == "codex" else "claude_session_end"
            first = runtime.import_session(source, repo_key=f"eval/{provider}", boundary_kind=boundary)
            initial_ids = tuple(memory.memory_id for memory in runtime.list_memories(repo_key=f"eval/{provider}"))
            repeats = [runtime.import_session(source, repo_key=f"eval/{provider}", boundary_kind=boundary) for _ in range(100)]
            recalled = runtime.recall("repository test suite", repo_key=f"eval/{provider}")
            appended = (
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Continue with the next task."}],
                    },
                }
                if provider == "codex"
                else {
                    "type": "user",
                    "sessionId": "claude-session-test-001",
                    "uuid": "user-continuation",
                    "parentUuid": "assistant-003",
                    "message": {"role": "user", "content": "Continue with the next task."},
                }
            )
            with source.open("a") as target:
                target.write(json.dumps(appended, sort_keys=True) + "\n")
            continuation = runtime.import_session(source, repo_key=f"eval/{provider}", boundary_kind=boundary)
            final_ids = tuple(memory.memory_id for memory in runtime.list_memories(repo_key=f"eval/{provider}"))
            outcomes.append(
                {
                    "provider": provider,
                    "created_memory_count": first.created_memory_count,
                    "repeat_created_memory_count": sum(item.created_memory_count for item in repeats),
                    "read_your_writes": bool(recalled.sidecar.ranked),
                    "freshness": recalled.sidecar.freshness,
                    "initial_memory_ids": initial_ids,
                    "continuation_created_memory_count": continuation.created_memory_count,
                    "committed_identity_preserved": set(initial_ids) <= set(final_ids),
                }
            )
    aggregate = {
        "schema_version": 1,
        "protocol": "codecairn-fixture-hook-smoke-v01",
        "client_family_count": len(outcomes),
        "trigger_count": len(outcomes) * 102,
        "read_your_writes_rate": mean(cast(bool, item["read_your_writes"]) for item in outcomes),
        "duplicate_memory_count": sum(cast(int, item["repeat_created_memory_count"]) for item in outcomes),
        "fresh_or_semantic_pending_count": sum(item["freshness"] in {"fresh", "semantic_pending"} for item in outcomes),
        "continuation_success_rate": mean(
            cast(int, item["continuation_created_memory_count"]) == 1 and cast(bool, item["committed_identity_preserved"])
            for item in outcomes
        ),
    }
    if (
        aggregate["client_family_count"] != 2
        or aggregate["read_your_writes_rate"] != 1
        or aggregate["duplicate_memory_count"] != 0
        or aggregate["continuation_success_rate"] != 1
    ):
        raise RuntimeError(f"fixture hook smoke failed: {aggregate}")
    return _publish("smoke", output_root=output_root, run_id=run_id, outcomes=outcomes, aggregate=aggregate)


def run_scale(*, output_root: Path, run_id: str | None) -> dict[str, object]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="codecairn-scale-") as directory:
        root = Path(directory)
        sources = root / "sources"
        sources.mkdir()
        runtime = create_runtime(root / "runtime")
        first_created = repeated_created = event_count = 0
        episodes: set[str] = set()
        for index in range(1_000):
            provider = "codex" if index < 500 else "claude"
            source = sources / f"{provider}-{index:04d}.jsonl"
            source.write_text(_synthetic_session(provider, index))
            first = runtime.import_session(source, repo_key="eval/scale", boundary_kind="manual_finalize")
            second = runtime.import_session(source, repo_key="eval/scale", boundary_kind="manual_finalize")
            first_created += first.created_memory_count
            repeated_created += second.created_memory_count
            event_count += first.raw_event_count
        memories = runtime.list_memories(repo_key="eval/scale")
        episodes.update(memory.episode_id for memory in memories if memory.episode_id)
        aggregate = {
            "schema_version": 1,
            "protocol": "codecairn-scale-1000x100-v01",
            "generator_seed": 17,
            "session_count": 1_000,
            "codex_session_count": 500,
            "claude_session_count": 500,
            "raw_event_count": event_count,
            "episode_count": len(episodes),
            "memory_count": len(memories),
            "first_import_created_count": first_created,
            "repeat_created_count": repeated_created,
            "duplicate_episode_count": len(memories) - len(episodes),
            "duration_ms": (time.perf_counter() - started) * 1_000,
        }
    expected = {
        "session_count": 1_000,
        "raw_event_count": 100_000,
        "episode_count": 1_000,
        "memory_count": 1_000,
        "first_import_created_count": 1_000,
        "repeat_created_count": 0,
        "duplicate_episode_count": 0,
    }
    if any(aggregate[field] != value for field, value in expected.items()):
        raise RuntimeError(f"scale gate failed: {aggregate}")
    return _publish("scale", output_root=output_root, run_id=run_id, outcomes=[], aggregate=aggregate)


def paid_plan(suite: str, *, help_only: bool) -> dict[str, object]:
    commit, dirty = _git_state()
    credentials: tuple[str, ...]
    budget_guard: dict[str, str] = {}
    if suite == "coding-ab":
        inputs = {"suite": str(CODING_SUITE.relative_to(ROOT)), "runs": 120, "agent": "Codex CLI"}
        command = "RUN_ID=<immutable-id> MODEL=<codex-model> SPEND_ACK=YES SPEND_CEILING_USD=<ceiling> make eval-coding-ab"
        credentials = ("CODEX_HOME or authenticated codex CLI",)
    else:
        selection = LOCOMO_200 if suite == "locomo-200" else LOCOMO_FULL
        count = 200 if suite == "locomo-200" else 1_540
        inputs = {
            "dataset": "benchmarks/locomo/data/locomo10.json",
            "dataset_sha256": _string(_dict(read_json(selection), field="question set"), "dataset_sha256"),
            "protocol": str(LOCOMO_PROTOCOL.relative_to(ROOT)),
            "protocol_sha256": hashlib.sha256(LOCOMO_PROTOCOL.read_bytes()).hexdigest(),
            "question_set": str(selection.relative_to(ROOT)),
            "question_count": count,
            "answer_provider_role": "CODECAIRN_ANSWER_*",
            "judge_provider_role": "CODECAIRN_JUDGE_*",
            "retrieval_provider_role": "CODECAIRN_EMBEDDING_*",
        }
        command = (
            f"RUN_ID=<immutable-id> SPEND_ACK=YES SPEND_CEILING_USD=<ceiling> MAX_CALL_COST_USD=<bound> "
            f"DATASET=benchmarks/locomo/data/locomo10.json make eval-{suite}"
        )
        credentials = ("CODECAIRN_EMBEDDING_API_KEY", "CODECAIRN_ANSWER_API_KEY", "CODECAIRN_JUDGE_API_KEY")
        budget_guard["MAX_CALL_COST_USD"] = "provider upper bound used to reserve every allowed attempt"
    return {
        "schema_version": 1,
        "kind": "paid_run_plan",
        "suite": suite,
        "help_only": help_only,
        "implementation_sha": commit,
        "clean_worktree": not dirty,
        "inputs": inputs,
        "required": {
            "RUN_ID": "unique immutable run identity",
            "SPEND_ACK": "must equal YES",
            "SPEND_CEILING_USD": "positive hard ceiling",
            **budget_guard,
            "clean_commit": True,
            "credentials": credentials,
        },
        "expected_output": f"benchmark_results/{suite}/<RUN_ID>",
        "command": command,
        "failure_boundary": "missing credentials or provider errors are infrastructure failures, never scored outcomes",
    }


def run_coding(*, output_root: Path) -> dict[str, object]:
    run_id, commit, ceiling = _paid_preflight(("MODEL",))
    artifact = run_coding_evaluation(
        CodingRunConfig(
            suite_path=CODING_SUITE,
            output_root=output_root / "coding-ab",
            experiment_id=run_id,
            repository_commit=commit,
            repeats=int(os.environ.get("REPEATS", "3")),
            seed=17,
            max_workers=int(os.environ.get("MAX_WORKERS", "1")),
            spend_ceiling_usd=ceiling,
        ),
        agent=CodexExecAgent(
            executable=os.environ.get("CODEX_EXECUTABLE", "codex"),
            model=os.environ["MODEL"],
            timeout_seconds=int(os.environ.get("AGENT_TIMEOUT_SECONDS", "900")),
        ),
    )
    result = {"suite": "coding-ab", "spend_ceiling_usd": ceiling, "artifact": str(artifact.run_dir), "aggregate": artifact.summary}
    print(json.dumps(result, sort_keys=True))
    return result


def run_paid_locomo(suite: str, *, dataset: Path, output_root: Path) -> dict[str, object]:
    required: tuple[str, ...] = (
        "CODECAIRN_ANSWER_API_KEY",
        "CODECAIRN_ANSWER_BASE_URL",
        "CODECAIRN_ANSWER_MODEL",
        "CODECAIRN_JUDGE_API_KEY",
        "CODECAIRN_JUDGE_BASE_URL",
        "CODECAIRN_JUDGE_MODEL",
    )
    if os.environ.get("RETRIEVAL_PROFILE", "dashscope") == "dashscope":
        required += ("CODECAIRN_EMBEDDING_API_KEY",)
    run_id, commit, ceiling = _paid_preflight(required)
    selection = LOCOMO_200 if suite == "locomo-200" else LOCOMO_FULL
    question_count = 200 if suite == "locomo-200" else 1_540
    try:
        max_call_cost = float(os.environ.get("MAX_CALL_COST_USD", ""))
    except ValueError as error:
        raise ValueError("MAX_CALL_COST_USD must be a positive number") from error
    maximum_calls = question_count * 8
    if not math.isfinite(max_call_cost) or max_call_cost <= 0 or maximum_calls * max_call_cost > ceiling:
        raise ValueError("spend ceiling does not reserve every bounded LoCoMo provider attempt")
    result = run_locomo(
        dataset_path=dataset,
        protocol_path=LOCOMO_PROTOCOL,
        question_set_path=selection,
        output_root=output_root,
        suite=cast(Literal["locomo-200", "locomo-full"], suite),
        run_id=run_id,
        implementation_sha=commit,
        spend_ceiling_usd=ceiling,
        environment=dict(os.environ),
    )
    print(json.dumps(result, sort_keys=True))
    return result


def _paid_preflight(required_environment: tuple[str, ...]) -> tuple[str, str, float]:
    run_id = os.environ.get("RUN_ID", "")
    _safe_id(run_id)
    if os.environ.get("SPEND_ACK") != "YES":
        raise ValueError("SPEND_ACK must equal YES")
    try:
        ceiling = float(os.environ.get("SPEND_CEILING_USD", ""))
    except ValueError as error:
        raise ValueError("SPEND_CEILING_USD must be a positive number") from error
    if not math.isfinite(ceiling) or ceiling <= 0:
        raise ValueError("SPEND_CEILING_USD must be a positive number")
    missing = tuple(name for name in required_environment if not os.environ.get(name))
    if missing:
        raise ValueError(f"missing paid-run configuration: {', '.join(missing)}")
    commit, dirty = _git_state()
    if dirty:
        raise ValueError("paid evaluation requires a clean worktree")
    return run_id, commit, ceiling


def _publish(
    suite: str, *, output_root: Path, run_id: str | None, outcomes: list[dict[str, object]], aggregate: dict[str, object]
) -> dict[str, object]:
    result: dict[str, object] = {"suite": suite, "aggregate": aggregate, "artifact": None}
    if run_id is None:
        print(json.dumps(result, sort_keys=True))
        return result
    _safe_id(run_id)
    target = output_root / suite / run_id
    target.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "implementation_sha": _git_state()[0],
        "suite": suite,
        "aggregate_sha256": canonical_sha256(aggregate),
        "outcome_count": len(outcomes),
    }
    write_json_exclusive(target / "manifest.json", manifest)
    write_json_exclusive(target / "outcomes.json", outcomes)
    write_json_exclusive(target / "aggregate.json", aggregate)
    result["artifact"] = str(target)
    print(json.dumps(result, sort_keys=True))
    return result


def _synthetic_session(provider: str, index: int) -> str:
    session_id = f"scale-{provider}-{index:04d}"
    if provider == "codex":
        records: list[dict[str, object]] = [
            {"type": "session_meta", "payload": {"id": session_id}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"Task {index}"}]},
            },
        ]
        records.extend(
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": f"step {step}"}]},
            }
            for step in range(98)
        )
    else:
        records = [
            {
                "type": "user" if step == 0 else "assistant",
                "sessionId": session_id,
                "uuid": f"{session_id}-{step}",
                "parentUuid": None if step == 0 else f"{session_id}-{step - 1}",
                "message": {
                    "role": "user" if step == 0 else "assistant",
                    "content": f"{'Task' if step == 0 else 'step'} {index if step == 0 else step}",
                },
            }
            for step in range(100)
        ]
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def _terms(text: str) -> set[str]:
    return {token.strip(".,;:!?()[]{}'\"`/").casefold() for token in text.split() if token.strip(".,;:!?()[]{}'\"`/")}


def _percentile(values: list[float], percentile: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * percentile) - 1)]


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(("git", "-C", str(ROOT), "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(("git", "-C", str(ROOT), "status", "--porcelain"), check=True, capture_output=True, text=True).stdout)
    return commit, dirty


def _safe_id(value: str) -> None:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"
    if not value or len(value) > 128 or any(character not in allowed for character in value):
        raise ValueError("run ID must be a safe path segment")


def _dict(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _string(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a string array")
    return cast(list[str], value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scale", "retrieval", "smoke"):
        command = commands.add_parser(name)
        command.add_argument("--output-root", type=Path, default=Path("benchmark_results"))
        command.add_argument("--run-id")
    plan = commands.add_parser("plan")
    plan.add_argument("suite", choices=("locomo-200", "locomo-full", "coding-ab"))
    plan.add_argument("--help-only", action="store_true")
    coding = commands.add_parser("coding")
    coding.add_argument("--output-root", type=Path, default=Path("benchmark_results"))
    locomo = commands.add_parser("locomo")
    locomo.add_argument("suite", choices=("locomo-200", "locomo-full"))
    locomo.add_argument("--dataset", type=Path, required=True)
    locomo.add_argument("--output-root", type=Path, default=Path("benchmark_results"))
    args = parser.parse_args()
    if args.command == "scale":
        run_scale(output_root=args.output_root, run_id=args.run_id)
    elif args.command == "retrieval":
        run_retrieval(output_root=args.output_root, run_id=args.run_id)
    elif args.command == "smoke":
        run_smoke(output_root=args.output_root, run_id=args.run_id)
    elif args.command == "coding":
        run_coding(output_root=args.output_root)
    elif args.command == "locomo":
        run_paid_locomo(args.suite, dataset=args.dataset, output_root=args.output_root)
    else:
        print(json.dumps(paid_plan(args.suite, help_only=args.help_only), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
