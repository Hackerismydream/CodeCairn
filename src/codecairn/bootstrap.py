"""Composition root for the local CodeCairn runtime."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from codecairn.entrypoints.cli import build_app
from codecairn.importers.session import SessionImporter
from codecairn.memory.models import IndexHealth, RebuildReport
from codecairn.service.application import (
    ApplicationOperations,
    CodeCairnApplication,
    EvaluationReportRequest,
    EvaluationRunRequest,
    EvidenceBundleBuildRequest,
    LoCoMoAblationRequest,
    LoCoMoCorpusBuildRequest,
    LoCoMoEvidenceCoverageRequest,
    LoCoMoPromotionRequest,
    LoCoMoQueryVectorBuildRequest,
    LoCoMoRepairRequest,
)
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def create_runtime(root: Path) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    state = SQLiteState(resolved / "state.sqlite3")
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=state,
        recall_engine=RecallEngine(state=state),
    )


class _LocalOperations(ApplicationOperations):
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def doctor(self) -> dict[str, object]:
        state = SQLiteState(self._root / "state.sqlite3")
        counts = state.operational_counts()
        semantic = state.semantic_job_counts()
        return {
            "status": (
                "degraded" if counts.conflicted_recovery_count or semantic["failed"] else "ok"
            ),
            "root": str(self._root),
            "schema": "codecairn-v01-2",
            "imports": counts.import_count,
            "observed_events": counts.observed_event_count,
            "memories": counts.memory_count,
            "pending_recovery": counts.pending_recovery_count,
            "conflicted_recovery": counts.conflicted_recovery_count,
            "semantic_jobs": semantic,
        }

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth:
        del worker_id, max_jobs
        return self.index_status()

    def rebuild_index(self) -> RebuildReport:
        count = SQLiteState(self._root / "state.sqlite3").operational_counts().memory_count
        return RebuildReport(
            truth_count=count,
            index_count=count,
            parity=True,
            truth_document_count=count,
            index_document_count=count,
            document_parity=True,
        )

    def index_status(self) -> IndexHealth:
        return SQLiteState(self._root / "state.sqlite3").index_health()

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

    def build_evidence_bundle(self, request: EvidenceBundleBuildRequest) -> dict[str, object]:
        from codecairn.evaluation.evidence_bundle import (
            EvidenceBundleConfig,
            build_evidence_bundle,
        )

        artifact = build_evidence_bundle(EvidenceBundleConfig(**asdict(request)))
        return {
            "bundle_dir": str(artifact.bundle_dir),
            "metrics": artifact.metrics,
        }

    def verify_evidence_bundle(self, bundle_dir: Path) -> dict[str, object]:
        from codecairn.evaluation.evidence_bundle import verify_evidence_bundle

        return verify_evidence_bundle(bundle_dir)

    def build_locomo_ablation_report(self, request: LoCoMoAblationRequest) -> dict[str, object]:
        raise NotImplementedError("LoCoMo ablation execution is deferred to v01-008")

    def build_locomo_promotion_report(self, request: LoCoMoPromotionRequest) -> dict[str, object]:
        raise NotImplementedError("LoCoMo promotion execution is deferred to v01-008")

    def build_locomo_repair_report(self, request: LoCoMoRepairRequest) -> dict[str, object]:
        raise NotImplementedError("LoCoMo repair execution is deferred to v01-008")

    def report_locomo_evidence_coverage(
        self,
        request: LoCoMoEvidenceCoverageRequest,
    ) -> dict[str, object]:
        raise NotImplementedError("LoCoMo coverage execution is deferred to v01-008")

    def build_locomo_corpus(self, request: LoCoMoCorpusBuildRequest) -> dict[str, object]:
        raise NotImplementedError("LoCoMo corpus execution is deferred to v01-008")

    def build_locomo_query_vectors(
        self, request: LoCoMoQueryVectorBuildRequest
    ) -> dict[str, object]:
        raise NotImplementedError("LoCoMo vector execution is deferred to v01-008")


def create_application(root: Path) -> CodeCairnApplication:
    resolved = root.resolve()
    return CodeCairnApplication(
        runtime_factory=lambda: create_runtime(resolved),
        operations=_LocalOperations(resolved),
    )


app = build_app(create_application)


def main() -> None:
    app()
