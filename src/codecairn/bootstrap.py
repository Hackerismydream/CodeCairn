"""Composition root for the local CodeCairn runtime."""

import os
import shutil
from dataclasses import asdict
from pathlib import Path

from codecairn.entrypoints.cli import build_app
from codecairn.importers.session import SessionImporter
from codecairn.memory.embedding import HashingEmbedder
from codecairn.memory.evidence import EvidenceGate
from codecairn.memory.projection import fingerprint, project_recall_documents
from codecairn.service.application import (
    ApplicationOperations,
    CodeCairnApplication,
    EvaluationReportRequest,
    EvaluationRunRequest,
    EvidenceBundleBuildRequest,
)
from codecairn.service.cascade import MemoryIndex, MiniCascade
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def create_runtime(root: Path) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    state = SQLiteState(resolved / "state.sqlite3")
    index = LanceMemoryIndex(resolved / "index.lancedb")
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=state,
        evidence_gate=EvidenceGate(),
        recall_engine=RecallEngine(
            index=index,
            state=state,
            embedder=HashingEmbedder(),
        ),
    )


def create_cascade(root: Path, *, index: MemoryIndex | None = None) -> MiniCascade:
    """Build the recoverable Markdown-to-LanceDB synchronization service."""
    resolved = root.resolve()
    return MiniCascade(
        truth=MarkdownMemoryStore(resolved),
        state=SQLiteState(resolved / "state.sqlite3"),
        index=index or LanceMemoryIndex(resolved / "index.lancedb"),
    )


class _LocalOperations(ApplicationOperations):
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def doctor(self) -> dict[str, object]:
        truth_store = MarkdownMemoryStore(self._root)
        truth = truth_store.scan()
        state = SQLiteState(self._root / "state.sqlite3")
        ledger = state.operational_counts()
        queue = create_cascade(self._root).health()
        truth_fingerprints = {
            (memory.repo_key, memory.memory_id, memory.content_sha256 or "")
            for memory in truth.memories
        }
        truth_document_fingerprints = {
            fingerprint(document)
            for memory in truth.memories
            for document in project_recall_documents(
                memory,
                markdown=truth_store.read_markdown(memory),
            )
        }
        index_path = self._root / "index.lancedb"
        index_error: str | None = None
        try:
            index = LanceMemoryIndex(index_path)
            if index_path.exists():
                index_fingerprints, index_document_fingerprints = index.fingerprint_snapshot()
            else:
                index_fingerprints, index_document_fingerprints = set(), set()
        except Exception as error:
            index_fingerprints = set()
            index_document_fingerprints = set()
            index_error = type(error).__name__
        markdown_ready = not truth.issues
        index_ready = (
            index_error is None
            and index_fingerprints == truth_fingerprints
            and index_document_fingerprints == truth_document_fingerprints
            and queue.pending == 0
            and queue.leased == 0
            and queue.failed == 0
            and queue.stale == 0
        )
        provider_status = _provider_status()
        status = (
            "healthy"
            if markdown_ready and index_ready and ledger.pending_recovery_count == 0
            else "degraded"
        )
        return {
            "schema_version": 1,
            "status": status,
            "markdown_truth": {
                "ready": markdown_ready,
                "memory_count": len(truth.memories),
                "issue_count": len(truth.issues),
                "issues": [asdict(issue) for issue in truth.issues],
            },
            "import_ledger": asdict(ledger),
            "index_queue": asdict(queue),
            "index": {
                "ready": index_ready,
                "fingerprint_count": len(index_fingerprints),
                "truth_fingerprint_count": len(truth_fingerprints),
                "document_fingerprint_count": len(index_document_fingerprints),
                "truth_document_fingerprint_count": len(truth_document_fingerprints),
                "error_type": index_error,
            },
            "providers": provider_status,
        }

    def run_evaluation(self, request: EvaluationRunRequest) -> dict[str, object]:
        output_root = request.output_root.resolve() / request.suite
        if request.suite == "retrieval":
            from codecairn.evaluation.retrieval import (
                RetrievalRunConfig,
                run_retrieval_evaluation,
            )

            retrieval_artifact = run_retrieval_evaluation(
                RetrievalRunConfig(
                    corpus_path=request.input_path / "corpus.json",
                    queries_path=request.input_path / "queries.json",
                    output_root=output_root,
                    run_id=request.run_id,
                    repository_commit=request.repository_commit,
                )
            )
            return retrieval_artifact.summary
        if request.suite == "recovery":
            from codecairn.evaluation.retrieval import RecoveryRunConfig, run_recovery_suite

            recovery_artifact = run_recovery_suite(
                RecoveryRunConfig(
                    source_fixture=request.input_path,
                    output_root=output_root,
                    run_id=request.run_id,
                    repository_commit=request.repository_commit,
                )
            )
            return recovery_artifact.summary
        if request.suite == "coding":
            from codecairn.evaluation.coding import (
                CodexExecAgent,
                CodingRunConfig,
                run_coding_evaluation,
            )

            if request.model is None:
                raise ValueError("Coding evaluation requires an explicit model")
            coding_artifact = run_coding_evaluation(
                CodingRunConfig(
                    suite_path=request.input_path,
                    output_root=output_root,
                    experiment_id=request.run_id,
                    repository_commit=request.repository_commit,
                    max_workers=request.max_workers,
                ),
                agent=CodexExecAgent(model=request.model),
            )
            return coding_artifact.summary
        return self._run_locomo(request, output_root=output_root)

    def report_evaluation(self, request: EvaluationReportRequest) -> dict[str, object]:
        if request.suite == "locomo":
            from codecairn.evaluation.locomo import report_locomo

            return report_locomo(request.run_dir)
        if request.suite == "coding":
            from codecairn.evaluation.coding import report_coding_runs

            return report_coding_runs(request.run_dir)
        from codecairn.evaluation.retrieval import report_recovery, report_retrieval

        if request.suite == "retrieval":
            return report_retrieval(request.run_dir)
        return report_recovery(request.run_dir)

    def build_evidence_bundle(self, request: EvidenceBundleBuildRequest) -> dict[str, object]:
        from codecairn.evaluation.evidence_bundle import (
            EvidenceBundleConfig,
            build_evidence_bundle,
        )

        artifact = build_evidence_bundle(
            EvidenceBundleConfig(
                bundle_id=request.bundle_id,
                output_root=request.output_root,
                locomo_run_dir=request.locomo_run_dir,
                retrieval_run_dir=request.retrieval_run_dir,
                recovery_run_dir=request.recovery_run_dir,
                coding_run_dir=request.coding_run_dir,
                quality_junit_path=request.quality_junit_path,
                quality_coverage_path=request.quality_coverage_path,
                repository_root=request.repository_root,
                generator_commit=request.generator_commit,
            )
        )
        return {"bundle_dir": str(artifact.bundle_dir), "generated": True}

    def verify_evidence_bundle(self, bundle_dir: Path) -> dict[str, object]:
        from codecairn.evaluation.evidence_bundle import verify_evidence_bundle

        return verify_evidence_bundle(bundle_dir)

    def _run_locomo(
        self,
        request: EvaluationRunRequest,
        *,
        output_root: Path,
    ) -> dict[str, object]:
        from codecairn.evaluation.locomo import (
            CodeCairnConversationMemory,
            LoCoMoRunConfig,
            run_locomo,
        )
        from codecairn.evaluation.providers import create_locomo_text_model

        answer_model = create_locomo_text_model(
            role="answer",
            environment=os.environ,
            model_override=request.model,
        )
        judge_model = (
            create_locomo_text_model(
                role="judge",
                environment=os.environ,
                model_override=request.judge_model or request.model,
            )
            if request.mode == "full"
            else None
        )

        def memory_factory(root: Path) -> CodeCairnConversationMemory:
            return CodeCairnConversationMemory(
                runtime=create_runtime(root),
                cascade=create_cascade(root),
                repo_key=f"locomo/{root.name}",
            )

        artifact = run_locomo(
            LoCoMoRunConfig(
                dataset_path=request.input_path,
                output_root=output_root,
                run_id=request.run_id,
                repository_commit=request.repository_commit,
                mode=request.mode,
                max_workers=request.max_workers,
                resume=request.resume,
            ),
            memory_factory=memory_factory,
            answer_model=answer_model,
            judge_model=judge_model,
        )
        return artifact.summary


def create_application(root: Path) -> CodeCairnApplication:
    resolved = root.resolve()
    return CodeCairnApplication(
        runtime=create_runtime(resolved),
        operations=_LocalOperations(resolved),
    )


def _provider_status() -> dict[str, object]:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    answer_configured = _provider_role_configured("ANSWER")
    judge_configured = _provider_role_configured("JUDGE")
    return {
        "codex_cli": {
            "configured": shutil.which("codex") is not None
            and (codex_home / "auth.json").is_file(),
            "executable_available": shutil.which("codex") is not None,
            "authentication_available": (codex_home / "auth.json").is_file(),
        },
        "openai_compatible": {
            "configured": answer_configured and judge_configured,
            "answer_configured": answer_configured,
            "judge_configured": judge_configured,
        },
    }


def _provider_role_configured(role: str) -> bool:
    prefix = f"CODECAIRN_{role}_"
    deepseek_configured = bool(os.environ.get("DEEPSEEK_API_KEY"))
    base_url = (
        os.environ.get(f"{prefix}BASE_URL")
        or os.environ.get("CODECAIRN_OPENAI_BASE_URL")
        or ("https://api.deepseek.com" if deepseek_configured else "")
    )
    api_key = (
        os.environ.get(f"{prefix}API_KEY")
        or os.environ.get("CODECAIRN_OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    )
    model = (
        os.environ.get(f"{prefix}MODEL")
        or os.environ.get("CODECAIRN_OPENAI_MODEL")
        or ("deepseek-v4-pro" if deepseek_configured else "")
    )
    return bool(base_url and api_key and model)


app = build_app(create_application)


def main() -> None:
    """Run the dependency-injected local CLI."""
    app()
