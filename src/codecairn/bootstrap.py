"""Composition root for the local CodeCairn runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from codecairn.entrypoints.cli import build_app
from codecairn.importers.session import SessionImporter
from codecairn.memory.config import RetrievalConfig, SemanticConfig
from codecairn.memory.models import IndexHealth, RebuildReport
from codecairn.memory.providers import create_retrieval_adapters
from codecairn.memory.retrieval import (
    EmbeddingProvider,
    FusionReranker,
    HashingEmbedder,
    RerankingProvider,
)
from codecairn.memory.semantic_provider import create_semantic_extractor
from codecairn.namespace_ops import export_namespace, reset_namespace
from codecairn.service.application import (
    ApplicationOperations,
    CodeCairnApplication,
    EvaluationReportRequest,
    EvaluationRunRequest,
)
from codecairn.service.cascade import MiniCascade
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def create_runtime(
    root: Path,
    *,
    retrieval: RetrievalConfig | None = None,
    semantic: SemanticConfig | None = None,
    test_retrieval: bool = False,
    environment: Mapping[str, str] | None = None,
) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    resolved_environment = dict(os.environ if environment is None else environment)
    state = SQLiteState(resolved / "state.sqlite3")
    recall = RecallEngine(state=state)
    if test_retrieval or retrieval is not None:
        embedder: EmbeddingProvider
        reranker: RerankingProvider
        if test_retrieval:
            embedder, reranker = HashingEmbedder(), FusionReranker()
        else:
            assert retrieval is not None
            embedder, reranker = create_retrieval_adapters(
                retrieval,
                environment=resolved_environment,
            )
        index = LanceMemoryIndex(resolved / "index.lance", embedder=embedder)
        recall = RecallEngine(
            state=state,
            index=index,
            embedder=embedder,
            reranker=reranker,
            preflight=MiniCascade(state=state, index=index),
        )
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=state,
        recall_engine=recall,
        semantic_extractor=(
            create_semantic_extractor(semantic, environment=resolved_environment)
            if semantic is not None
            else None
        ),
    )


class _LocalOperations(ApplicationOperations):
    def __init__(
        self,
        root: Path,
        *,
        repo_key: str | None,
        retrieval: RetrievalConfig | None,
        semantic: SemanticConfig | None,
        test_retrieval: bool,
        environment: Mapping[str, str] | None,
    ) -> None:
        self._root = root.resolve()
        self._repo_key = repo_key
        self._retrieval = retrieval
        self._semantic = semantic or SemanticConfig()
        self._environment = environment
        self._test_retrieval = test_retrieval

    def doctor(self, *, live: bool = False) -> dict[str, object]:
        state = SQLiteState(self._root / "state.sqlite3")
        counts = state.operational_counts()
        semantic = state.semantic_job_counts()
        index = self.index_status()
        markdown = MarkdownMemoryStore(self._root)
        truth_issues = len(markdown.scan().issues) + len(markdown.scan_evolutions().issues)
        provider = (
            "test"
            if self._test_retrieval
            else (self._retrieval.profile if self._retrieval is not None else "unconfigured")
        )
        environment = os.environ if self._environment is None else self._environment
        semantic_missing = self._semantic.network and not environment.get(
            "CODECAIRN_SEMANTIC_API_KEY"
        )
        live_state = "not_checked"
        if live:
            embedder, reranker = self._adapters()
            live_state = (
                "live_verified"
                if len(embedder.embed_query("CodeCairn provider check")) == embedder.dimension
                else "failed"
            )
            if self._retrieval and self._retrieval.profile == "fastembed":
                reranker.rerank(
                    "CodeCairn provider check",
                    (("provider-check", "local reranker check", 0.0),),
                )
        degraded = bool(
            counts.conflicted_recovery_count
            or semantic["failed"]
            or index.failed
            or (self._retrieval is None and not self._test_retrieval)
            or semantic_missing
            or truth_issues
        )
        subsystems = {
            "config": _doctor_row(
                "ok" if self._retrieval is not None or self._test_retrieval else "degraded",
                "codecairn init",
            ),
            "source_import": _doctor_row("ok", "codecairn import <source>"),
            "semantic_queue": _doctor_row(
                "degraded" if semantic["failed"] or semantic_missing else "ok",
                "codecairn process --semantic --retry-failed",
            ),
            "markdown": _doctor_row(
                "degraded" if truth_issues else "ok",
                "restore the latest namespace export",
            ),
            "sqlite": _doctor_row("ok", "codecairn init"),
            "index_queue": _doctor_row(
                "degraded" if index.failed or index.stale else "ok",
                "codecairn process --index --retry-failed",
            ),
            "lancedb": _doctor_row(
                "degraded" if index.pending or index.failed or index.stale else "ok",
                "codecairn index rebuild",
            ),
            "hooks": _doctor_row("not_configured", "codecairn hooks install"),
            "privacy": _doctor_row("ok", "codecairn doctor"),
        }
        return {
            "status": "degraded" if degraded else "ok",
            "root": str(self._root),
            "schema": "codecairn-v01-4",
            "repo_key": self._repo_key,
            "imports": counts.import_count,
            "observed_events": counts.observed_event_count,
            "memories": counts.memory_count,
            "pending_recovery": counts.pending_recovery_count,
            "conflicted_recovery": counts.conflicted_recovery_count,
            "semantic_jobs": semantic,
            "index_jobs": asdict(index),
            "subsystems": subsystems,
            "providers": {
                "retrieval": provider,
                "retrieval_state": live_state
                if live
                else (
                    "configured"
                    if self._retrieval is not None or self._test_retrieval
                    else "missing"
                ),
                "semantic": self._semantic.profile,
                "semantic_state": (
                    "disabled"
                    if not self._semantic.network
                    else "missing_key"
                    if semantic_missing
                    else "configured"
                ),
            },
            "privacy": {
                "storage": "local",
                "embedding": (
                    "network" if self._retrieval and self._retrieval.network else "local"
                ),
                "semantic_extraction": ("network" if self._semantic.network else "disabled"),
                "source_content_egress": (
                    "trace excerpts"
                    if self._semantic.network
                    else "memory text"
                    if self._retrieval and self._retrieval.network
                    else "none"
                ),
            },
            "remediation": next(
                (row["remediation"] for row in subsystems.values() if row["status"] == "degraded"),
                None,
            ),
        }

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth:
        cascade = self._cascade()
        cascade.preflight(
            repo_key=self._required_repo_key(),
            worker_id=worker_id,
            max_jobs=max_jobs or 128,
        )
        return self.index_status()

    def rebuild_index(self) -> RebuildReport:
        return self._cascade().rebuild(repo_key=self._required_repo_key())

    def index_status(self) -> IndexHealth:
        return SQLiteState(self._root / "state.sqlite3").index_health(repo_key=self._repo_key)

    def export_namespace(self, output: Path) -> dict[str, object]:
        return export_namespace(
            root=self._root,
            repo_key=self._required_repo_key(),
            output=output,
        )

    def reset_namespace(
        self,
        *,
        confirm: str | None,
        dry_run: bool,
    ) -> dict[str, object]:
        state = SQLiteState(self._root / "state.sqlite3")
        index = None
        if self._test_retrieval or self._retrieval is not None:
            embedder, _reranker = self._adapters()
            index = LanceMemoryIndex(self._root / "index.lance", embedder=embedder)
        return reset_namespace(
            root=self._root,
            repo_key=self._required_repo_key(),
            confirm=confirm,
            dry_run=dry_run,
            state=state,
            index=index,
        )

    def _adapters(self) -> tuple[EmbeddingProvider, RerankingProvider]:
        if self._test_retrieval:
            return HashingEmbedder(), FusionReranker()
        if self._retrieval is None:
            from codecairn.memory.errors import ProviderConfigurationError

            raise ProviderConfigurationError("Retrieval profile is not configured")
        return create_retrieval_adapters(
            self._retrieval,
            environment=dict(os.environ if self._environment is None else self._environment),
        )

    def _cascade(self) -> MiniCascade:
        embedder, _reranker = self._adapters()
        state = SQLiteState(self._root / "state.sqlite3")
        return MiniCascade(
            state=state,
            index=LanceMemoryIndex(self._root / "index.lance", embedder=embedder),
        )

    def _required_repo_key(self) -> str:
        if self._repo_key is None:
            raise ValueError("Repository namespace is required for index operations")
        return self._repo_key

    def run_evaluation(self, request: EvaluationRunRequest) -> dict[str, object]:
        raise NotImplementedError(
            f"Evaluation execution for {request.suite} is being migrated to v0.1 manifests"
        )

    def report_evaluation(self, request: EvaluationReportRequest) -> dict[str, object]:
        from codecairn.evaluation.historical_reader import (
            report_coding,
            report_locomo_composite,
            report_recovery,
            report_retrieval,
        )

        reports = {
            "locomo": report_locomo_composite,
            "retrieval": report_retrieval,
            "recovery": report_recovery,
            "coding": report_coding,
        }
        return reports[request.suite](request.run_dir)

    def verify_evidence_bundle(self, bundle_dir: Path) -> dict[str, object]:
        from codecairn.evaluation.evidence_bundle import verify_evidence_bundle

        return verify_evidence_bundle(bundle_dir)


def create_application(
    root: Path,
    *,
    repo_key: str | None = None,
    retrieval: RetrievalConfig | None = None,
    semantic: SemanticConfig | None = None,
    test_retrieval: bool = False,
    environment: Mapping[str, str] | None = None,
) -> CodeCairnApplication:
    resolved = root.resolve()
    return CodeCairnApplication(
        runtime_factory=lambda: create_runtime(
            resolved,
            retrieval=retrieval,
            semantic=semantic,
            test_retrieval=test_retrieval,
            environment=environment,
        ),
        operations=_LocalOperations(
            resolved,
            repo_key=repo_key,
            retrieval=retrieval,
            semantic=semantic,
            test_retrieval=test_retrieval,
            environment=environment,
        ),
    )


app = build_app(create_application)


def main() -> None:
    app()


def _doctor_row(status: str, remediation: str) -> dict[str, str]:
    return {"status": status, "remediation": remediation}
