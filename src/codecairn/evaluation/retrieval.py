from __future__ import annotations

import math
import re
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codecairn.bootstrap import create_cascade, create_retrieval_providers, create_runtime
from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.memory.evidence import collect_repository_rule_fact
from codecairn.memory.models import MemoryProposal
from codecairn.memory.retrieval import retrieval_config_sha256
from codecairn.memory.trace import stable_id
from codecairn.service.cascade import MiniCascade
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class RetrievalRunConfig:
    corpus_path: Path
    queries_path: Path
    output_root: Path
    run_id: str
    repository_commit: str
    top_k: int = 10


@dataclass(frozen=True, slots=True)
class RetrievalRunArtifact:
    run_dir: Path
    summary: dict[str, object]


@dataclass(frozen=True, slots=True)
class RecoveryRunConfig:
    source_fixture: Path
    output_root: Path
    run_id: str
    repository_commit: str


@dataclass(frozen=True, slots=True)
class RecoveryRunArtifact:
    run_dir: Path
    summary: dict[str, object]


@dataclass(frozen=True, slots=True)
class _CorpusEntry:
    key: str
    repo_key: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class _RetrievalQuery:
    query_id: str
    repo_key: str
    text: str
    relevant_keys: tuple[str, ...]


def run_retrieval_evaluation(config: RetrievalRunConfig) -> RetrievalRunArtifact:
    _validate_run_identity(config.run_id, repository_commit=config.repository_commit)
    if not 1 <= config.top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    corpus = _load_corpus(config.corpus_path)
    queries = _load_queries(config.queries_path)
    _validate_relevance(corpus, queries)
    run_dir = (config.output_root / config.run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    retrieval = create_retrieval_providers()
    manifest = {
        "schema_version": 1,
        "suite": "retrieval",
        "run_id": config.run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "repository_commit": config.repository_commit,
        "corpus_sha256": file_sha256(config.corpus_path),
        "queries_sha256": file_sha256(config.queries_path),
        "corpus_count": len(corpus),
        "query_count": len(queries),
        "top_k": config.top_k,
        "retrieval": retrieval.public_config,
        "metrics": ["Recall@1", "Recall@5", "MRR", "irrelevant@5", "P95 latency"],
    }
    write_json_exclusive(run_dir / "manifest.json", manifest)

    runtime_root = run_dir / "runtime"
    runtime = create_runtime(runtime_root, retrieval=retrieval)
    memory_ids: dict[str, str] = {}
    entries_by_key = {entry.key: entry for entry in corpus}
    for entry in corpus:
        fact = collect_repository_rule_fact(
            repo_key=entry.repo_key,
            source_path=f"benchmark://retrieval/{entry.key}",
            content=entry.content.encode(),
        )
        proposal = MemoryProposal(
            proposal_id=stable_id("retrieval-proposal", entry.repo_key, entry.key),
            repo_key=entry.repo_key,
            memory_type="repository_convention",
            title=entry.title,
            summary=entry.content,
            fact_ids=(fact.fact_id,),
            confidence=1.0,
        )
        decision = runtime.evaluate_proposal(proposal, facts=(fact,))
        if decision.memory is None:
            raise ValueError(f"Retrieval corpus memory was rejected: {entry.key}")
        memory_ids[entry.key] = decision.memory.memory_id
    create_cascade(runtime_root, retrieval=retrieval).run_until_idle(worker_id="retrieval-eval")
    write_json_exclusive(
        run_dir / "corpus.json",
        {
            "entries": [
                {
                    "key": entry.key,
                    "repo_key": entry.repo_key,
                    "memory_id": memory_ids[entry.key],
                    "content_sha256": next(
                        memory.content_sha256
                        for memory in runtime.list_memories(repo_key=entry.repo_key)
                        if memory.memory_id == memory_ids[entry.key]
                    ),
                }
                for entry in corpus
            ]
        },
    )
    keys_by_memory_id = {memory_id: key for key, memory_id in memory_ids.items()}
    for query in queries:
        result = runtime.recall(query.text, repo_key=query.repo_key, limit=config.top_k)
        rankings: list[dict[str, object]] = []
        isolation_violation = False
        for ranked in result.sidecar.ranked:
            key = keys_by_memory_id.get(ranked.memory_id)
            ranked_entry = entries_by_key.get(key) if key is not None else None
            if ranked_entry is None or ranked_entry.repo_key != query.repo_key:
                isolation_violation = True
            rankings.append(
                {
                    "rank": ranked.rank,
                    "key": key,
                    "memory_id": ranked.memory_id,
                    "candidate_sources": list(ranked.candidate_sources),
                    "vector_score": ranked.vector_score,
                    "vector_rank": ranked.vector_rank,
                    "lexical_score": ranked.lexical_score,
                    "lexical_rank": ranked.lexical_rank,
                    "final_score": ranked.final_score,
                    "reranker_score": ranked.reranker_score,
                    "content_sha256": ranked.content_sha256,
                }
            )
        write_json_exclusive(
            run_dir / "queries" / f"{query.query_id}.json",
            {
                "schema_version": 1,
                "query_id": query.query_id,
                "repo_key": query.repo_key,
                "text": query.text,
                "relevant_keys": list(query.relevant_keys),
                "latency_ms": result.sidecar.latency_ms,
                "vector_candidate_count": result.sidecar.vector_candidate_count,
                "lexical_candidate_count": result.sidecar.lexical_candidate_count,
                "limit": result.sidecar.limit,
                "embedding_model": result.sidecar.embedding_model,
                "embedding_source": result.sidecar.embedding_source,
                "embedding_revision": result.sidecar.embedding_revision,
                "reranker_model": result.sidecar.reranker_model,
                "reranker_source": result.sidecar.reranker_source,
                "reranker_revision": result.sidecar.reranker_revision,
                "retrieval_config_sha256": result.sidecar.retrieval_config_sha256,
                "repository_isolation_violation": isolation_violation,
                "rankings": rankings,
            },
        )
    summary = report_retrieval(run_dir)
    write_json_exclusive(run_dir / "summary.json", summary)
    return RetrievalRunArtifact(run_dir=run_dir, summary=summary)


def report_retrieval(run_dir: Path) -> dict[str, object]:
    manifest = _required_dict(read_json(run_dir / "manifest.json"), field="manifest")
    retrieval_contract = _report_retrieval_contract(manifest)
    records = [
        _required_dict(read_json(path), field="query artifact")
        for path in sorted((run_dir / "queries").glob("*.json"))
    ]
    recall_at_1: list[float] = []
    recall_at_5: list[float] = []
    reciprocal_ranks: list[float] = []
    irrelevant_rates: list[float] = []
    latencies: list[float] = []
    isolation_violations = 0
    for record in records:
        _validate_report_retrieval(record, contract=retrieval_contract)
        relevant = _required_string_set(record.get("relevant_keys"), field="relevant_keys")
        raw_rankings = record.get("rankings")
        if not isinstance(raw_rankings, list):
            raise ValueError("Query rankings must be an array")
        ranked_keys = [
            ranking.get("key")
            for ranking in raw_rankings
            if isinstance(ranking, dict) and isinstance(ranking.get("key"), str)
        ]
        recall_at_1.append(len(relevant.intersection(ranked_keys[:1])) / len(relevant))
        recall_at_5.append(len(relevant.intersection(ranked_keys[:5])) / len(relevant))
        first_relevant = next(
            (rank for rank, key in enumerate(ranked_keys, start=1) if key in relevant),
            None,
        )
        reciprocal_ranks.append(0.0 if first_relevant is None else 1.0 / first_relevant)
        top_five = ranked_keys[:5]
        irrelevant_rates.append(
            0.0 if not top_five else sum(key not in relevant for key in top_five) / len(top_five)
        )
        latency = record.get("latency_ms")
        if not isinstance(latency, int | float):
            raise ValueError("Query latency must be numeric")
        latencies.append(float(latency))
        isolation_violations += int(record.get("repository_isolation_violation") is True)
    return {
        "schema_version": 1,
        "suite": "retrieval",
        "run_id": _required_str(manifest, "run_id"),
        "query_count": len(records),
        "recall_at_1": _mean(recall_at_1),
        "recall_at_5": _mean(recall_at_5),
        "mrr": _mean(reciprocal_ranks),
        "irrelevant_at_5_rate": _mean(irrelevant_rates),
        "p95_latency_ms": _percentile_nearest_rank(latencies, percentile=0.95),
        "repository_isolation_violation_count": isolation_violations,
    }


def _report_retrieval_contract(
    manifest: dict[str, object],
) -> tuple[dict[str, object], int, str] | None:
    raw = manifest.get("retrieval")
    if not isinstance(raw, dict) or not all(
        isinstance(raw.get(name), dict) for name in ("embedding", "reranker")
    ):
        return None
    top_k = _required_int(manifest, "top_k")
    return raw, top_k, retrieval_config_sha256(cast(dict[str, object], raw))


def _validate_report_retrieval(
    record: dict[str, object],
    *,
    contract: tuple[dict[str, object], int, str] | None,
) -> None:
    if contract is None:
        return
    retrieval_config, top_k, config_sha256 = contract
    if record.get("limit") != top_k:
        raise ValueError("Retrieval query limit does not match its manifest")
    if record.get("retrieval_config_sha256") != config_sha256:
        raise ValueError("Retrieval query configuration hash does not match its manifest")
    for provider_name in ("embedding", "reranker"):
        expected = _required_dict(
            retrieval_config.get(provider_name),
            field=f"retrieval {provider_name}",
        )
        for identity_field in ("model", "source", "revision"):
            if record.get(f"{provider_name}_{identity_field}") != expected.get(identity_field):
                raise ValueError(
                    f"Retrieval {provider_name} {identity_field} does not match its manifest"
                )


def run_recovery_suite(config: RecoveryRunConfig) -> RecoveryRunArtifact:
    _validate_run_identity(config.run_id, repository_commit=config.repository_commit)
    run_dir = (config.output_root / config.run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    retrieval = create_retrieval_providers()
    write_json_exclusive(
        run_dir / "manifest.json",
        {
            "schema_version": 1,
            "suite": "storage-recovery",
            "run_id": config.run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "repository_commit": config.repository_commit,
            "source_fixture_sha256": file_sha256(config.source_fixture),
            "retrieval": retrieval.public_config,
        },
    )
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    import_root = run_dir / "runtime" / "import"
    source = run_dir / "workspace" / "session.jsonl"
    source.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(config.source_fixture, source)
    runtime = create_runtime(import_root, retrieval=retrieval)
    initial = runtime.import_session(source, repo_key="acme/widgets")
    repeated = runtime.import_session(source, repo_key="acme/widgets")
    checks["import_idempotency"] = (
        initial.created_memory_count == 1
        and repeated.created_memory_count == 0
        and len(runtime.list_memories(repo_key="acme/widgets")) == 1
    )
    cross = runtime.import_session(source, repo_key="acme/other")
    widget_ids = {item.memory_id for item in runtime.list_memories(repo_key="acme/widgets")}
    other_ids = {item.memory_id for item in runtime.list_memories(repo_key="acme/other")}
    checks["cross_repository_import"] = (
        cross.created_memory_count == 1 and bool(widget_ids) and widget_ids.isdisjoint(other_ids)
    )
    with source.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"event_msg","payload":{"type":"task_complete"}}\n')
    appended = runtime.import_session(source, repo_key="acme/widgets")
    checks["append_resume"] = (
        appended.resumed_from_raw_event_index > 0
        and appended.processed_raw_event_count < appended.raw_event_count
    )
    details["append_resume"] = asdict(appended)

    cascade = create_cascade(import_root, retrieval=retrieval)
    cascade.run_until_idle(worker_id="recovery-index")
    truth = {
        (memory.repo_key, memory.memory_id, memory.content_sha256 or "")
        for repo_key in ("acme/widgets", "acme/other")
        for memory in runtime.list_memories(repo_key=repo_key)
    }
    index_path = import_root / "index.lancedb"
    shutil.rmtree(index_path)
    rebuild = create_cascade(import_root, retrieval=retrieval).rebuild()
    checks["index_rebuild_parity"] = rebuild.parity and (
        create_cascade(import_root, retrieval=retrieval).index_fingerprints() == truth
    )
    details["index_rebuild"] = asdict(rebuild)

    replay_root = run_dir / "runtime" / "queue-replay"
    replay_runtime = create_runtime(replay_root, retrieval=retrieval)
    replay_runtime.import_session(config.source_fixture, repo_key="acme/replay")
    replay_state = SQLiteState(replay_root / "state.sqlite3")
    interrupted = replay_state.claim_index_job(
        worker_id="interrupted-worker",
        now_ms=1_000,
        lease_ms=10,
    )
    replay = MiniCascade(
        truth=MarkdownMemoryStore(replay_root),
        state=replay_state,
        index=LanceMemoryIndex(replay_root / "index.lancedb", embedder=retrieval.embedder),
        clock_ms=lambda: 1_010,
        lease_ms=10,
    )
    checks["queue_replay"] = (
        interrupted is not None
        and replay.run_once(worker_id="replacement-worker")
        and replay.health().indexed == 1
        and replay.health().leased == 0
    )

    corrupt_root = run_dir / "runtime" / "corruption"
    corrupt_runtime = create_runtime(corrupt_root, retrieval=retrieval)
    corrupt_runtime.import_session(config.source_fixture, repo_key="acme/corrupt")
    corrupt_memory = corrupt_runtime.list_memories(repo_key="acme/corrupt")[0]
    if corrupt_memory.markdown_path is None:
        raise ValueError("Recovery fixture did not persist Markdown")
    Path(corrupt_memory.markdown_path).write_bytes(b"corrupt markdown\xff")
    corrupt_cascade = create_cascade(corrupt_root, retrieval=retrieval)
    reconcile = corrupt_cascade.reconcile()
    checks["corruption_detection"] = reconcile.corrupt == 1 and (
        corrupt_cascade.health().stale == 1
    )
    details["corruption"] = asdict(reconcile)

    write_json_exclusive(
        run_dir / "checks.json",
        {
            "schema_version": 1,
            "checks": dict(sorted(checks.items())),
            "details": details,
        },
    )
    summary = report_recovery(run_dir)
    write_json_exclusive(run_dir / "summary.json", summary)
    return RecoveryRunArtifact(run_dir=run_dir, summary=summary)


def report_recovery(run_dir: Path) -> dict[str, object]:
    manifest = _required_dict(read_json(run_dir / "manifest.json"), field="manifest")
    raw = _required_dict(read_json(run_dir / "checks.json"), field="recovery checks")
    raw_checks = _required_dict(raw.get("checks"), field="check results")
    if not raw_checks or not all(isinstance(value, bool) for value in raw_checks.values()):
        raise ValueError("Recovery check results must be non-empty booleans")
    checks = {key: cast(bool, value) for key, value in sorted(raw_checks.items())}
    return {
        "schema_version": 1,
        "suite": "storage-recovery",
        "run_id": _required_str(manifest, "run_id"),
        "checks": checks,
        "all_passed": all(checks.values()),
        "index_rebuild_consistency": 1.0 if checks["index_rebuild_parity"] else 0.0,
        "details": raw.get("details"),
    }


def _load_corpus(path: Path) -> tuple[_CorpusEntry, ...]:
    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError("Retrieval corpus must be an array")
    entries = tuple(
        _CorpusEntry(
            key=_safe_id(_required_str(record, "key"), field="corpus key"),
            repo_key=_required_str(record, "repo_key"),
            title=_required_str(record, "title"),
            content=_required_str(record, "content"),
        )
        for item in payload
        for record in [_required_dict(item, field="corpus entry")]
    )
    if len({entry.key for entry in entries}) != len(entries):
        raise ValueError("Retrieval corpus keys must be unique")
    return entries


def _load_queries(path: Path) -> tuple[_RetrievalQuery, ...]:
    payload = read_json(path)
    if isinstance(payload, dict):
        groups = payload.get("groups")
        if not isinstance(groups, list):
            raise ValueError("Grouped retrieval queries must contain a groups array")
        payload = _expand_query_groups(groups)
    if not isinstance(payload, list):
        raise ValueError("Retrieval queries must be an array or grouped object")
    queries: list[_RetrievalQuery] = []
    for item in payload:
        record = _required_dict(item, field="retrieval query")
        relevant = _required_string_set(record.get("relevant_keys"), field="relevant_keys")
        queries.append(
            _RetrievalQuery(
                query_id=_safe_id(_required_str(record, "query_id"), field="query_id"),
                repo_key=_required_str(record, "repo_key"),
                text=_required_str(record, "text"),
                relevant_keys=tuple(sorted(relevant)),
            )
        )
    if len({query.query_id for query in queries}) != len(queries):
        raise ValueError("Retrieval query identifiers must be unique")
    return tuple(queries)


def _expand_query_groups(groups: list[object]) -> list[object]:
    expanded: list[object] = []
    for item in groups:
        group = _required_dict(item, field="query group")
        repo_key = _required_str(group, "repo_key")
        relevant = sorted(_required_string_set(group.get("relevant_keys"), field="relevant_keys"))
        raw_queries = group.get("queries")
        if not isinstance(raw_queries, list) or not raw_queries:
            raise ValueError("Query group queries must be a non-empty array")
        for raw_query in raw_queries:
            query = _required_dict(raw_query, field="grouped query")
            expanded.append(
                {
                    "query_id": _required_str(query, "query_id"),
                    "repo_key": repo_key,
                    "text": _required_str(query, "text"),
                    "relevant_keys": relevant,
                }
            )
    return expanded


def _validate_relevance(
    corpus: tuple[_CorpusEntry, ...],
    queries: tuple[_RetrievalQuery, ...],
) -> None:
    entries = {entry.key: entry for entry in corpus}
    keys_by_repo: dict[str, set[str]] = defaultdict(set)
    for entry in corpus:
        keys_by_repo[entry.repo_key].add(entry.key)
    for query in queries:
        repo_keys = keys_by_repo.get(query.repo_key)
        relevant = set(query.relevant_keys)
        if repo_keys is None:
            raise ValueError(f"Query references unknown repository: {query.query_id}")
        if relevant == repo_keys:
            raise ValueError("Relevance labels cannot be repository membership")
        if not relevant or not relevant.issubset(repo_keys):
            raise ValueError(f"Query has invalid relevance labels: {query.query_id}")
        if any(entries[key].title.casefold() == query.text.casefold() for key in relevant):
            raise ValueError("Retrieval query must not copy its generated memory title")


def _mean(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 6)


def _percentile_nearest_rank(values: list[float], *, percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _validate_run_identity(run_id: str, *, repository_commit: str) -> None:
    _safe_id(run_id, field="run_id")
    if not repository_commit.strip():
        raise ValueError("repository_commit must not be empty")


def _safe_id(value: str, *, field: str) -> str:
    if _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} must be a safe identifier")
    return value


def _required_dict(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field.capitalize()} must be an object")
    return cast(dict[str, object], value)


def _required_str(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_int(record: dict[str, object], field: str) -> int:
    value = record.get(field)
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _required_string_set(value: object, *, field: str) -> set[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a non-empty string array")
    result = set(cast(list[str], value))
    if len(result) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    return result
