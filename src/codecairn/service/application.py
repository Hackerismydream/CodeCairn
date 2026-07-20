from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from codecairn.memory.models import CodingMemory, ImportResult, RecallResult
from codecairn.service.runtime import MemoryRuntime

EvaluationSuite = Literal["locomo", "retrieval", "recovery", "coding"]


@dataclass(frozen=True, slots=True)
class EvaluationRunRequest:
    suite: EvaluationSuite
    input_path: Path
    output_root: Path
    run_id: str
    repository_commit: str
    mode: Literal["full", "smoke"] = "full"
    model: str | None = None
    judge_model: str | None = None
    max_workers: int = 1
    resume: bool = False
    question_set_path: Path | None = None
    execution_phase: Literal["all", "ingest", "questions"] = "all"


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


class ApplicationOperations(Protocol):
    def doctor(self) -> dict[str, object]: ...

    def run_evaluation(self, request: EvaluationRunRequest) -> dict[str, object]: ...

    def report_evaluation(self, request: EvaluationReportRequest) -> dict[str, object]: ...

    def build_evidence_bundle(self, request: EvidenceBundleBuildRequest) -> dict[str, object]: ...

    def verify_evidence_bundle(self, bundle_dir: Path) -> dict[str, object]: ...

    def build_locomo_ablation_report(self, request: LoCoMoAblationRequest) -> dict[str, object]: ...


class CodeCairnApplication:
    """Shared use-case surface consumed by CLI and HTTP presentation adapters."""

    def __init__(self, *, runtime: MemoryRuntime, operations: ApplicationOperations) -> None:
        self._runtime = runtime
        self._operations = operations

    def import_session(
        self,
        source_path: Path,
        *,
        repo_key: str,
        source_root: Path | None = None,
    ) -> ImportResult:
        return self._runtime.import_session(
            source_path,
            repo_key=repo_key,
            source_root=source_root,
        )

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._runtime.list_memories(repo_key=repo_key)

    def recall(self, query: str, *, repo_key: str, limit: int = 5) -> RecallResult:
        return self._runtime.recall(query, repo_key=repo_key, limit=limit)

    def doctor(self) -> dict[str, object]:
        return self._operations.doctor()

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
