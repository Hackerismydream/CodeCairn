"""Composition root for the local CodeCairn runtime."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from codecairn.configuration import discover_repository, resolve_runtime_config
from codecairn.entrypoints.cli import build_app
from codecairn.entrypoints.hooks import HookClient, detect_client_version, parse_hook_event
from codecairn.importers.history import history_source_root, source_matches_repository
from codecairn.importers.jsonl import read_import_scan
from codecairn.importers.session import SessionImporter
from codecairn.memory.config import RetrievalConfig, SemanticConfig
from codecairn.memory.errors import TraceImportError
from codecairn.memory.library import memory_revision_sha256
from codecairn.memory.models import HookOutcome, HookReceipt, IndexHealth, RebuildReport
from codecairn.memory.providers import create_retrieval_adapters
from codecairn.memory.retrieval import EmbeddingProvider, RerankingProvider
from codecairn.memory.schema import CodingMemory, UserPreferencePayload, typed_id
from codecairn.memory.semantic_provider import create_semantic_extractor
from codecairn.namespace_ops import export_namespace, reset_namespace
from codecairn.service.application import ApplicationOperations, CodeCairnApplication
from codecairn.service.cascade import MiniCascade
from codecairn.service.myna import MemoryLibraryApplication
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.library_markdown import MarkdownLibraryStore
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SCHEMA_REVISION, SQLiteState


def create_runtime(
    root: Path,
    *,
    retrieval: RetrievalConfig | None = None,
    semantic: SemanticConfig | None = None,
    retrieval_adapters: tuple[EmbeddingProvider, RerankingProvider] | None = None,
    environment: Mapping[str, str] | None = None,
) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    resolved_environment = dict(os.environ if environment is None else environment)
    state = SQLiteState(resolved / "state.sqlite3")
    recall = RecallEngine(state=state)
    adapters = retrieval_adapters
    if adapters is None and retrieval is not None:
        adapters = create_retrieval_adapters(retrieval, environment=resolved_environment)
    if adapters is not None:
        embedder, reranker = adapters
        index = LanceMemoryIndex(resolved / "index.lance", embedder=embedder)
        recall = RecallEngine(
            state=state, index=index, embedder=embedder, reranker=reranker, preflight=MiniCascade(state=state, index=index)
        )
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=state,
        recall_engine=recall,
        library_lock=MarkdownLibraryStore(resolved).lock,
        semantic_extractor=(create_semantic_extractor(semantic, environment=resolved_environment) if semantic is not None else None),
    )


class _LocalOperations(ApplicationOperations):
    def __init__(
        self,
        root: Path,
        *,
        repo_key: str | None,
        retrieval: RetrievalConfig | None,
        semantic: SemanticConfig | None,
        retrieval_adapters: tuple[EmbeddingProvider, RerankingProvider] | None,
        environment: Mapping[str, str] | None,
    ) -> None:
        self._root = root.resolve()
        self._repo_key = repo_key
        self._retrieval = retrieval
        self._semantic = semantic or SemanticConfig()
        self._environment = environment
        self._retrieval_adapters = retrieval_adapters

    def doctor(self, *, live: bool = False) -> dict[str, object]:
        state = SQLiteState(self._root / "state.sqlite3")
        counts = state.operational_counts()
        semantic = state.semantic_job_counts()
        hooks = state.recent_hook_receipts(repo_key=self._repo_key)
        hook_failed = any(item.outcome in {"failed", "unsupported"} for item in hooks)
        index = self.index_status()
        markdown = MarkdownMemoryStore(self._root)
        truth_issues = len(markdown.scan().issues) + len(markdown.scan_evolutions().issues)
        person_library = _person_library_health(self._root, state)
        provider: str = self._retrieval.profile if self._retrieval is not None else "injected"
        if self._retrieval is None and self._retrieval_adapters is None:
            provider = "unconfigured"
        environment = os.environ if self._environment is None else self._environment
        semantic_missing = self._semantic.network and not environment.get("CODECAIRN_SEMANTIC_API_KEY")
        live_state = "not_checked"
        if live:
            embedder, reranker = self._adapters()
            live_state = "live_verified" if len(embedder.embed_query("CodeCairn provider check")) == embedder.dimension else "failed"
            if self._retrieval and self._retrieval.profile == "fastembed":
                reranker.rerank("CodeCairn provider check", (("provider-check", "local reranker check", 0.0),))
        degraded = bool(
            counts.conflicted_recovery_count
            or counts.pending_recovery_count
            or semantic["failed"]
            or index.failed
            or (self._retrieval is None and self._retrieval_adapters is None)
            or semantic_missing
            or truth_issues
            or hook_failed
            or person_library["status"] == "degraded"
        )
        subsystems = {
            "config": _doctor_row(
                "ok" if self._retrieval is not None or self._retrieval_adapters is not None else "degraded", "codecairn init"
            ),
            "source_import": _doctor_row("ok", "codecairn import <source>"),
            "semantic_queue": _doctor_row(
                "degraded" if semantic["failed"] or semantic_missing else "ok", "codecairn process --semantic --no-index"
            ),
            "markdown": _doctor_row("degraded" if truth_issues else "ok", "restore the latest namespace export"),
            "sqlite": _doctor_row("degraded" if counts.pending_recovery_count else "ok", "codecairn init"),
            "index_queue": _doctor_row("degraded" if index.failed or index.stale else "ok", "codecairn index sync"),
            "lancedb": _doctor_row("degraded" if index.pending or index.failed or index.stale else "ok", "codecairn index rebuild"),
            "hooks": _doctor_row(
                "degraded" if hook_failed else "ok" if hooks else "not_configured",
                next((item.retry_command for item in hooks if item.retry_command), None) or "codecairn hook install",
            ),
            "person_library": _doctor_row(
                str(person_library["status"]), "restore Myna Person Library and referenced memories from backup"
            ),
            "privacy": _doctor_row("ok", "codecairn doctor"),
        }
        return {
            "status": "degraded" if degraded else "ok",
            "root": str(self._root),
            "schema": SCHEMA_REVISION,
            "repo_key": self._repo_key,
            "imports": counts.import_count,
            "observed_events": counts.observed_event_count,
            "memories": counts.memory_count,
            "pending_recovery": counts.pending_recovery_count,
            "conflicted_recovery": counts.conflicted_recovery_count,
            "semantic_jobs": semantic,
            "hook_receipts": {
                "total": len(hooks),
                "failed": sum(item.outcome in {"failed", "unsupported"} for item in hooks),
                "latest_retry": next((item.retry_command for item in hooks if item.retry_command), None),
            },
            "index_jobs": asdict(index),
            "person_library": person_library,
            "subsystems": subsystems,
            "providers": {
                "retrieval": provider,
                "retrieval_state": live_state
                if live
                else ("configured" if self._retrieval is not None or self._retrieval_adapters is not None else "missing"),
                "semantic": self._semantic.profile,
                "semantic_state": ("disabled" if not self._semantic.network else "missing_key" if semantic_missing else "configured"),
            },
            "privacy": {
                "storage": "local",
                "embedding": ("network" if self._retrieval and self._retrieval.network else "local"),
                "semantic_extraction": ("network" if self._semantic.network else "disabled"),
                "source_content_egress": (
                    "trace excerpts"
                    if self._semantic.network
                    else "memory text"
                    if self._retrieval and self._retrieval.network
                    else "none"
                ),
            },
            "remediation": next((row["remediation"] for row in subsystems.values() if row["status"] == "degraded"), None),
        }

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth:
        cascade = self._cascade()
        cascade.preflight(repo_key=self._required_repo_key(), worker_id=worker_id, max_jobs=max_jobs or 128)
        return self.index_status()

    def rebuild_index(self) -> RebuildReport:
        return self._cascade().rebuild(repo_key=self._required_repo_key())

    def index_status(self) -> IndexHealth:
        return SQLiteState(self._root / "state.sqlite3").index_health(repo_key=self._repo_key)

    def export_namespace(self, output: Path) -> dict[str, object]:
        return export_namespace(root=self._root, repo_key=self._required_repo_key(), output=output)

    def reset_namespace(self, *, confirm: str | None, dry_run: bool) -> dict[str, object]:
        state = SQLiteState(self._root / "state.sqlite3")
        index = None
        if self._retrieval_adapters is not None or self._retrieval is not None:
            embedder, _reranker = self._adapters()
            index = LanceMemoryIndex(self._root / "index.lance", embedder=embedder)
        return reset_namespace(
            root=self._root, repo_key=self._required_repo_key(), confirm=confirm, dry_run=dry_run, state=state, index=index
        )

    def read_memory_markdown(self, *, repo_key: str, memory_id: str) -> str:
        for artifact in MarkdownMemoryStore(self._root).scan().memories:
            if artifact.memory.memory_id != memory_id:
                continue
            if artifact.memory.repo_key != repo_key:
                raise ValueError("foreign_namespace")
            return artifact.path.read_text()
        raise KeyError(memory_id)

    def read_memory_truth(self, *, repo_key: str, memory_id: str) -> CodingMemory:
        for artifact in MarkdownMemoryStore(self._root).scan().memories:
            if artifact.memory.memory_id == memory_id and artifact.memory.repo_key == repo_key:
                return artifact.memory
        raise KeyError(memory_id)

    def has_supersession(self, *, repo_key: str, predecessor_id: str, successor_id: str) -> bool:
        scan = MarkdownMemoryStore(self._root).scan_evolutions()
        if scan.issues:
            raise ValueError("evolution_truth_invalid")
        return _evolution_reachable(
            {(item.record.repo_key, item.record.predecessor_id, item.record.successor_id) for item in scan.evolutions},
            repo_key,
            predecessor_id,
            successor_id,
        )

    def has_durable_successor(self, *, repo_key: str, memory_id: str) -> bool:
        scan = MarkdownMemoryStore(self._root).scan_evolutions()
        if scan.issues:
            raise ValueError("evolution_truth_invalid")
        return any(item.record.repo_key == repo_key and item.record.predecessor_id == memory_id for item in scan.evolutions)

    def record_hook_receipt(self, receipt: HookReceipt) -> None:
        SQLiteState(self._root / "state.sqlite3").record_hook_receipt(receipt)

    def recent_hook_receipts(self) -> tuple[HookReceipt, ...]:
        return SQLiteState(self._root / "state.sqlite3").recent_hook_receipts(repo_key=self._repo_key)

    def _adapters(self) -> tuple[EmbeddingProvider, RerankingProvider]:
        if self._retrieval_adapters is not None:
            return self._retrieval_adapters
        if self._retrieval is None:
            from codecairn.memory.errors import ProviderConfigurationError

            raise ProviderConfigurationError("Retrieval profile is not configured")
        return create_retrieval_adapters(
            self._retrieval, environment=dict(os.environ if self._environment is None else self._environment)
        )

    def _cascade(self) -> MiniCascade:
        embedder, _reranker = self._adapters()
        state = SQLiteState(self._root / "state.sqlite3")
        return MiniCascade(state=state, index=LanceMemoryIndex(self._root / "index.lance", embedder=embedder))

    def _required_repo_key(self) -> str:
        if self._repo_key is None:
            raise ValueError("Repository namespace is required for index operations")
        return self._repo_key


def create_application(
    root: Path,
    *,
    repo_key: str | None = None,
    retrieval: RetrievalConfig | None = None,
    semantic: SemanticConfig | None = None,
    retrieval_adapters: tuple[EmbeddingProvider, RerankingProvider] | None = None,
    environment: Mapping[str, str] | None = None,
) -> CodeCairnApplication:
    resolved = root.resolve()
    return CodeCairnApplication(
        runtime_factory=lambda: create_runtime(
            resolved, retrieval=retrieval, semantic=semantic, retrieval_adapters=retrieval_adapters, environment=environment
        ),
        operations=_LocalOperations(
            resolved,
            repo_key=repo_key,
            retrieval=retrieval,
            semantic=semantic,
            retrieval_adapters=retrieval_adapters,
            environment=environment,
        ),
    )


def create_myna_application(
    root: Path,
    *,
    repository_key: str,
    retrieval: RetrievalConfig | None = None,
    semantic: SemanticConfig | None = None,
    retrieval_adapters: tuple[EmbeddingProvider, RerankingProvider] | None = None,
    environment: Mapping[str, str] | None = None,
) -> MemoryLibraryApplication:
    """Compose one server-bound Person Library without changing legacy clients."""
    resolved = root.resolve()
    SQLiteState(resolved / "state.sqlite3")
    memory = create_application(
        resolved,
        repo_key=repository_key,
        retrieval=retrieval,
        semantic=semantic,
        retrieval_adapters=retrieval_adapters,
        environment=environment,
    )
    return MemoryLibraryApplication(memory=memory, truth=MarkdownLibraryStore(resolved), repository_key=repository_key)


def run_hook(client: HookClient, raw: bytes) -> HookReceipt:
    started_ns = time.time_ns()
    started = started_ns // 1_000_000
    home = Path(os.environ.get("CODECAIRN_HOME", Path.home())).resolve()
    event = None
    config = None
    version = "unknown"
    outcome: HookOutcome = "failed"
    error_code = None
    try:
        version = detect_client_version(client)
        event = parse_hook_event(client, raw, client_version=version, home=home)
        config = resolve_runtime_config(start=event.cwd)
        if event.cwd.is_relative_to(config.runtime_root):
            outcome = "noop"
        else:
            source_root = history_source_root(home, client)
            try:
                scan = read_import_scan(event.source_path, source_root=source_root, checkpoint=None)
                trace = SessionImporter().from_scan(scan)
            except (OSError, TraceImportError) as error:
                raise ValueError("source_unavailable") from error
            if trace.provider != client or not source_matches_repository(
                client, scan.records, expected_common_dir=discover_repository(event.cwd).common_dir
            ):
                raise ValueError("source_unavailable")
            imported = create_application(config.runtime_root, repo_key=config.repo_key).import_session(
                event.source_path,
                repo_key=config.repo_key,
                source_root=source_root,
                index=False,
                boundary_kind=("claude_session_end" if client == "claude" else "codex_stop"),
                expected_source_sha256=trace.source_sha256,
            )
            outcome = "imported" if imported.result.created_memory_count else "noop"
    except Exception as error:
        error_code = (
            str(error)
            if str(error) in {"hook_input_invalid", "source_unavailable", "unsupported_client"}
            else getattr(error, "code", "hook_failed")
        )
        outcome = "unsupported" if error_code == "unsupported_client" else "failed"
    session_digest = event.session_identity_sha256 if event is not None else sha256(raw[: 64 * 1024]).hexdigest()
    source_digest = event.source_identity_sha256 if event is not None and error_code != "source_unavailable" else None
    identity = {
        "client": client,
        "event": "session_end" if client == "claude" else "stop",
        "session_identity_sha256": session_digest,
        "source_identity_sha256": source_digest,
        "started_at_ns": started_ns,
    }
    receipt = HookReceipt(
        schema_version=1,
        receipt_id=typed_id("hook", identity),
        repo_key=config.repo_key if config is not None else None,
        client=client,
        event="session_end" if client == "claude" else "stop",
        client_version=str(version),
        session_identity_sha256=session_digest,
        source_identity_sha256=source_digest,
        outcome=outcome,
        error_code=error_code,
        retry_command=("codecairn import <owned-session.jsonl>" if outcome in {"failed", "unsupported"} else None),
        started_at_ms=started,
        duration_ms=max(0, time.time_ns() // 1_000_000 - started),
    )
    try:
        root = config.runtime_root if config is not None else home / ".codecairn"
        create_application(root, repo_key=receipt.repo_key).record_hook_receipt(receipt)
    except Exception:
        pass
    return receipt


app = build_app(create_application, hook_runner=run_hook)


def main() -> None:
    app()


def mcp_main() -> None:
    from codecairn.entrypoints.mcp import build_server

    build_server(create_application).run(transport="stdio")


def _doctor_row(status: str, remediation: str) -> dict[str, str]:
    return {"status": status, "remediation": remediation}


def _person_library_health(root: Path, state: SQLiteState) -> dict[str, object]:
    store = MarkdownLibraryStore(root)
    issues: list[dict[str, str]] = []
    with store.lock():
        person_scan = store.scan_person()
        try:
            promotion_scan = store.scan_promotions()
        except (OSError, UnicodeError, ValueError):
            promotion_scan = None

    configured = person_scan.present or promotion_scan is None or promotion_scan.present
    if not configured:
        return {"status": "not_configured", "person_id": None, "promotion_count": 0, "effective_promotion_count": 0, "issues": ()}

    memory_scan = MarkdownMemoryStore(root).scan()
    memory_truth = {(item.memory.repo_key, item.memory.memory_id): item.memory for item in memory_scan.memories}
    evolution_scan = MarkdownMemoryStore(root).scan_evolutions()
    evolution_edges = {
        (item.record.repo_key, item.record.predecessor_id, item.record.successor_id) for item in evolution_scan.evolutions
    }
    if evolution_scan.issues:
        issues.append({"code": "promotion_evolution_truth_invalid"})

    for issue in person_scan.issues:
        issues.append({"code": "person_truth_invalid", "detail": issue.error_code})
    person = person_scan.person
    if person is None and not person_scan.present:
        issues.append({"code": "person_missing"})

    promotions = () if promotion_scan is None else tuple(item.promotion for item in promotion_scan.promotions)
    if promotion_scan is None:
        issues.append({"code": "promotion_truth_invalid"})
    else:
        for issue in promotion_scan.issues:
            issues.append({"code": "promotion_truth_invalid", "detail": issue.error_code})

    by_id = {promotion.promotion_id: promotion for promotion in promotions}
    if len(by_id) != len(promotions):
        issues.append({"code": "duplicate_promotion_id"})
    replaced_by: dict[str, str] = {}
    for promotion in promotions:
        if person is not None and promotion.person_id != person.person_id:
            issues.append({"code": "promotion_owner_mismatch", "promotion_id": promotion.promotion_id})
        predecessor_id = promotion.replaces_promotion_id
        if predecessor_id is None:
            continue
        predecessor = by_id.get(predecessor_id)
        if (
            predecessor is None
            or predecessor.subject_key != promotion.subject_key
            or predecessor_id == promotion.promotion_id
            or predecessor_id in replaced_by
        ):
            issues.append({"code": "promotion_replacement_invalid", "promotion_id": promotion.promotion_id})
            continue
        if predecessor.source.repository_key != promotion.source.repository_key or not _evolution_reachable(
            evolution_edges, predecessor.source.repository_key, predecessor.source.memory_id, promotion.source.memory_id
        ):
            issues.append({"code": "promotion_replacement_invalid", "promotion_id": promotion.promotion_id})
            continue
        replaced_by[predecessor_id] = promotion.promotion_id

    effective = tuple(promotion for promotion in promotions if promotion.promotion_id not in replaced_by)
    effective_subjects: dict[str, list[str]] = {}
    for promotion in effective:
        effective_subjects.setdefault(promotion.subject_key, []).append(promotion.promotion_id)
    for promotion_ids in effective_subjects.values():
        if len(promotion_ids) > 1:
            issues.append({"code": "duplicate_effective_subject", "promotion_id": ",".join(sorted(promotion_ids))})

    effective_ids = {promotion.promotion_id for promotion in effective}
    for promotion in promotions:
        source = promotion.source
        try:
            memory = state.get_memory(repo_key=source.repository_key, memory_id=source.memory_id)
        except (UnicodeError, ValueError):
            issues.append({"code": "promotion_source_invalid", "promotion_id": promotion.promotion_id})
            continue
        if memory is None:
            issues.append({"code": "promotion_source_missing", "promotion_id": promotion.promotion_id})
            continue
        truth_memory = memory_truth.get((source.repository_key, source.memory_id))
        if truth_memory is None:
            issues.append({"code": "promotion_source_truth_missing", "promotion_id": promotion.promotion_id})
        elif truth_memory != memory:
            issues.append({"code": "promotion_source_truth_mismatch", "promotion_id": promotion.promotion_id})
        if memory.memory_type != "user_preference" or not isinstance(memory.payload, UserPreferencePayload):
            issues.append({"code": "promotion_source_wrong_type", "promotion_id": promotion.promotion_id})
        elif memory.payload.subject_key != promotion.subject_key:
            issues.append({"code": "promotion_source_subject_mismatch", "promotion_id": promotion.promotion_id})
        if memory_revision_sha256(memory) != source.revision_sha256:
            issues.append({"code": "promotion_source_revision_mismatch", "promotion_id": promotion.promotion_id})
        expected_status = "active" if promotion.promotion_id in effective_ids else "superseded"
        if state.memory_status(repo_key=source.repository_key, memory_id=source.memory_id) != expected_status:
            issues.append({"code": "promotion_source_status_invalid", "promotion_id": promotion.promotion_id})
        durable_status = (
            "superseded"
            if any(repo == source.repository_key and predecessor == source.memory_id for repo, predecessor, _ in evolution_edges)
            else "active"
        )
        if durable_status != expected_status:
            issues.append({"code": "promotion_source_status_invalid", "promotion_id": promotion.promotion_id})

    ordered_issues = tuple(sorted(issues, key=lambda item: (item["code"], item.get("promotion_id", ""), item.get("detail", ""))))
    return {
        "status": "degraded" if ordered_issues else "ok",
        "person_id": None if person is None else person.person_id,
        "promotion_count": len(promotions),
        "effective_promotion_count": len(effective),
        "issues": ordered_issues,
    }


def _evolution_reachable(edges: set[tuple[str, str, str]], repo_key: str, predecessor_id: str, successor_id: str) -> bool:
    frontier, seen = {predecessor_id}, set()
    while frontier:
        seen.update(frontier)
        frontier = {successor for repo, predecessor, successor in edges if repo == repo_key and predecessor in frontier} - seen
        if successor_id in frontier:
            return True
    return False
