from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

from codecairn.memory.episode import BoundaryKind
from codecairn.memory.evolution import EvolutionProposer, EvolutionRecord, MemoryHistory
from codecairn.memory.models import (
    CodingMemory,
    ImportResult,
    IndexHealth,
    RebuildReport,
    RecallResult,
)
from codecairn.memory.semantic import SemanticProcessReport
from codecairn.service.runtime import MemoryRuntime

EvaluationSuite = Literal["locomo", "retrieval", "recovery", "coding"]

IMPORT_INDEX_WORKER_ID = "import"


@dataclass(frozen=True, slots=True)
class EvaluationRunRequest:
    suite: EvaluationSuite
    input_path: Path
    output_root: Path
    run_id: str
    repository_commit: str
    mode: Literal["full", "smoke", "retrieval"] = "full"
    model: str | None = None
    judge_model: str | None = None
    max_workers: int = 1
    resume: bool = False
    question_set_path: Path | None = None
    execution_phase: Literal["all", "ingest", "questions"] = "all"
    corpus_path: Path | None = None
    query_vectors_path: Path | None = None
    retrieval_gate_question_set_path: Path | None = None
    retrieval_canary_run_path: Path | None = None
    retrieval_holdout_run_path: Path | None = None
    expected_dataset_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class LoCoMoCorpusBuildRequest:
    input_path: Path
    output_root: Path
    corpus_id: str
    repository_commit: str
    resume: bool = False
    expected_dataset_sha256: str | None = None
    question_set_path: Path | None = None


@dataclass(frozen=True, slots=True)
class LoCoMoQueryVectorBuildRequest:
    input_path: Path
    output_root: Path
    vector_set_id: str
    resume: bool = False
    question_set_path: Path | None = None
    expected_dataset_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationReportRequest:
    suite: EvaluationSuite
    run_dir: Path


@dataclass(frozen=True, slots=True)
class EvidenceBundleBuildRequest:
    bundle_id: str
    output_root: Path
    locomo_run_dir: Path
    retrieval_run_dir: Path
    recovery_run_dir: Path
    coding_run_dir: Path
    quality_junit_path: Path
    quality_coverage_path: Path
    repository_root: Path
    generator_commit: str


@dataclass(frozen=True, slots=True)
class LoCoMoAblationRequest:
    question_set_path: Path
    episode_only_run: Path
    hierarchy_no_neighbors_run: Path
    hierarchy_run: Path
    output_path: Path
    natural_weight_question_set_path: Path | None = None


@dataclass(frozen=True, slots=True)
class LoCoMoPromotionRequest:
    question_set_path: Path
    selection_report_path: Path
    episode_only_run: Path
    hierarchy_no_neighbors_run: Path
    hierarchy_run: Path
    run_dir: Path
    output_path: Path


@dataclass(frozen=True, slots=True)
class LoCoMoRepairRequest:
    target_question_set_path: Path
    repair_question_set_path: Path
    base_run: Path
    repair_run: Path
    output_path: Path


@dataclass(frozen=True, slots=True)
class LoCoMoEvidenceCoverageRequest:
    run_dir: Path
    dataset_path: Path
    output_path: Path | None = None
    oracle_max_tokens: int = 4_000


@dataclass(frozen=True, slots=True)
class IndexSyncReport:
    """Result of the index drain that follows one import commit."""

    requested: bool
    synced: bool
    health: IndexHealth | None = None
    error_type: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """One durable import result plus the index drain attempted after it."""

    result: ImportResult
    index: IndexSyncReport


class ApplicationOperations(Protocol):
    def doctor(self) -> dict[str, object]: ...

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth: ...

    def rebuild_index(self) -> RebuildReport: ...

    def index_status(self) -> IndexHealth: ...

    def run_evaluation(self, request: EvaluationRunRequest) -> dict[str, object]: ...

    def report_evaluation(self, request: EvaluationReportRequest) -> dict[str, object]: ...

    def build_evidence_bundle(self, request: EvidenceBundleBuildRequest) -> dict[str, object]: ...

    def verify_evidence_bundle(self, bundle_dir: Path) -> dict[str, object]: ...

    def build_locomo_ablation_report(self, request: LoCoMoAblationRequest) -> dict[str, object]: ...

    def build_locomo_promotion_report(
        self, request: LoCoMoPromotionRequest
    ) -> dict[str, object]: ...

    def build_locomo_repair_report(self, request: LoCoMoRepairRequest) -> dict[str, object]: ...

    def report_locomo_evidence_coverage(
        self,
        request: LoCoMoEvidenceCoverageRequest,
    ) -> dict[str, object]: ...

    def build_locomo_corpus(self, request: LoCoMoCorpusBuildRequest) -> dict[str, object]: ...

    def build_locomo_query_vectors(
        self, request: LoCoMoQueryVectorBuildRequest
    ) -> dict[str, object]: ...


class CodeCairnApplication:
    """Shared use-case surface consumed by CLI and HTTP presentation adapters."""

    def __init__(
        self,
        *,
        runtime_factory: Callable[[], MemoryRuntime],
        operations: ApplicationOperations,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._operations = operations
        self._runtime: MemoryRuntime | None = None

    def import_session(
        self,
        source_path: Path,
        *,
        repo_key: str,
        source_root: Path | None = None,
        index: bool = True,
        boundary_kind: BoundaryKind | None = None,
    ) -> ImportOutcome:
        result = self._memory_runtime().import_session(
            source_path,
            repo_key=repo_key,
            source_root=source_root,
            boundary_kind=boundary_kind,
        )
        return ImportOutcome(result=result, index=self._drain_index(requested=index))

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._memory_runtime().list_memories(repo_key=repo_key)

    def recall(
        self,
        query: str,
        *,
        repo_key: str,
        limit: int = 20,
        include_superseded: bool = False,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult:
        return self._memory_runtime().recall(
            query,
            repo_key=repo_key,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )

    def supersede(
        self,
        *,
        repo_key: str,
        predecessor_id: str,
        successor_id: str,
        reason: str,
        proposer: EvolutionProposer,
    ) -> EvolutionRecord:
        return self._memory_runtime().supersede(
            repo_key=repo_key,
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            reason=reason,
            proposer=proposer,
        )

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory:
        return self._memory_runtime().memory_history(
            repo_key=repo_key,
            memory_id=memory_id,
        )

    def restore(self, *, repo_key: str, memory_id: str) -> CodingMemory:
        return self._memory_runtime().restore(
            repo_key=repo_key,
            memory_id=memory_id,
        )

    def process_pending(
        self,
        *,
        worker_id: str,
        max_jobs: int = 8,
    ) -> SemanticProcessReport:
        return self._memory_runtime().process_pending(
            worker_id=worker_id,
            max_jobs=max_jobs,
        )

    def doctor(self) -> dict[str, object]:
        return self._operations.doctor()

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth:
        return self._operations.sync_index(worker_id=worker_id, max_jobs=max_jobs)

    def rebuild_index(self) -> RebuildReport:
        return self._operations.rebuild_index()

    def index_status(self) -> IndexHealth:
        return self._operations.index_status()

    def run_evaluation(self, request: EvaluationRunRequest) -> dict[str, object]:
        return self._operations.run_evaluation(request)

    def report_evaluation(self, request: EvaluationReportRequest) -> dict[str, object]:
        return self._operations.report_evaluation(request)

    def build_evidence_bundle(self, request: EvidenceBundleBuildRequest) -> dict[str, object]:
        return self._operations.build_evidence_bundle(request)

    def verify_evidence_bundle(self, bundle_dir: Path) -> dict[str, object]:
        return self._operations.verify_evidence_bundle(bundle_dir)

    def build_locomo_ablation_report(self, request: LoCoMoAblationRequest) -> dict[str, object]:
        return self._operations.build_locomo_ablation_report(request)

    def build_locomo_promotion_report(
        self,
        request: LoCoMoPromotionRequest,
    ) -> dict[str, object]:
        return self._operations.build_locomo_promotion_report(request)

    def build_locomo_repair_report(self, request: LoCoMoRepairRequest) -> dict[str, object]:
        return self._operations.build_locomo_repair_report(request)

    def report_locomo_evidence_coverage(
        self,
        request: LoCoMoEvidenceCoverageRequest,
    ) -> dict[str, object]:
        return self._operations.report_locomo_evidence_coverage(request)

    def build_locomo_corpus(self, request: LoCoMoCorpusBuildRequest) -> dict[str, object]:
        return self._operations.build_locomo_corpus(request)

    def build_locomo_query_vectors(
        self, request: LoCoMoQueryVectorBuildRequest
    ) -> dict[str, object]:
        return self._operations.build_locomo_query_vectors(request)

    def _memory_runtime(self) -> MemoryRuntime:
        if self._runtime is None:
            self._runtime = self._runtime_factory()
        return self._runtime

    def _drain_index(self, *, requested: bool) -> IndexSyncReport:
        """Drain the outbox after the import commit without owning its durability."""
        if not requested:
            return IndexSyncReport(requested=False, synced=False)
        try:
            health = self._operations.sync_index(worker_id=IMPORT_INDEX_WORKER_ID)
        except Exception as error:
            return IndexSyncReport(
                requested=True,
                synced=False,
                error_type=type(error).__name__,
                error=str(error),
            )
        return IndexSyncReport(
            requested=True,
            synced=(
                health.pending == 0
                and health.leased == 0
                and health.failed == 0
                and health.stale == 0
            ),
            health=health,
        )


def import_response(outcome: ImportOutcome) -> dict[str, object]:
    """Render one import outcome as the shared CLI and HTTP payload."""
    return {**asdict(outcome.result), "index": asdict(outcome.index)}
