"""Composition root for the local CodeCairn runtime."""

import hashlib
import math
import os
import shutil
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from filelock import FileLock

from codecairn.entrypoints.cli import build_app
from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.attempt_journal import validate_model_attempt_journal
from codecairn.evaluation.worker_process import WorkerProcessLimits, run_monitored_worker
from codecairn.importers.session import SessionImporter
from codecairn.memory.embedding import (
    DASHSCOPE_TEXT_V4_DIMENSIONS,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_INPUT_PRICE_CNY_PER_MILLION,
    DEFAULT_EMBEDDING_LICENSE,
    DEFAULT_EMBEDDING_MAX_ATTEMPTS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_RETRY_BACKOFF_SECONDS,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_EMBEDDING_SOURCE,
    DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
    DEFAULT_FASTEMBED_EMBEDDING_DIMENSION,
    DEFAULT_FASTEMBED_EMBEDDING_LICENSE,
    DEFAULT_FASTEMBED_EMBEDDING_MODEL,
    DEFAULT_FASTEMBED_EMBEDDING_REVISION,
    DEFAULT_FASTEMBED_EMBEDDING_SOURCE,
    DashScopeEmbeddingAdapter,
    FastEmbedEmbeddingAdapter,
    HashingEmbedder,
)
from codecairn.memory.episode import EpisodeSemanticizer, LosslessEpisodeSemanticizer
from codecairn.memory.evidence import EvidenceGate
from codecairn.memory.model_artifact import validate_hf_artifact
from codecairn.memory.projection import fingerprint, project_recall_documents
from codecairn.memory.recall_planner import RecallPlannerConfig, RecallPlannerMode
from codecairn.memory.reranking import (
    DEFAULT_RERANKER_BATCH_SIZE,
    DEFAULT_RERANKER_LICENSE,
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
    DEFAULT_RERANKER_SOURCE,
    RERANKER_WARMUP_CONTRACT,
    FastEmbedRerankingAdapter,
    FusionScoreRerankingAdapter,
)
from codecairn.memory.retrieval import RetrievalProviders
from codecairn.memory.semantic import (
    ClauseProjectionAdapter,
    GroundedClauseSemanticizer,
    LosslessClauseProjectionAdapter,
)
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
)
from codecairn.service.cascade import MemoryIndex, MiniCascade
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.semantic_cache import JsonProjectionCache
from codecairn.storage.sqlite import SQLiteState

if TYPE_CHECKING:
    from codecairn.evaluation.locomo import LoCoMoConversationWork


def create_retrieval_providers(
    *,
    environment: Mapping[str, str] | None = None,
) -> RetrievalProviders:
    """Resolve one fail-closed retrieval configuration without loading model weights."""
    resolved_environment = os.environ if environment is None else environment
    profile = resolved_environment.get("CODECAIRN_RETRIEVAL_PROFILE", "dashscope")
    planner = _recall_planner_config(resolved_environment)
    if profile == "hashing-test":
        return RetrievalProviders(
            profile="hashing-test",
            embedder=HashingEmbedder(),
            reranker=FusionScoreRerankingAdapter(),
            embedding_license="Unreleased CodeCairn test adapter",
            reranker_license="Unreleased CodeCairn test adapter",
            planner=planner,
        )
    if profile not in {"dashscope", "fastembed"}:
        raise ValueError(f"Unknown retrieval profile: {profile}")
    reranker_model = resolved_environment.get(
        "CODECAIRN_RERANKER_MODEL",
        DEFAULT_RERANKER_MODEL,
    )
    reranker_revision = _model_revision(
        environment=resolved_environment,
        environment_key="CODECAIRN_RERANKER_REVISION",
        model_id=reranker_model,
        default_model_id=DEFAULT_RERANKER_MODEL,
        default_revision=DEFAULT_RERANKER_REVISION,
    )
    reranker_source = _model_source(
        environment=resolved_environment,
        environment_key="CODECAIRN_RERANKER_SOURCE",
        model_id=reranker_model,
        default_model_id=DEFAULT_RERANKER_MODEL,
        default_source=DEFAULT_RERANKER_SOURCE,
    )
    validate_hf_artifact(source_id=reranker_source, revision=reranker_revision)
    reranker_license = _model_license(
        environment=resolved_environment,
        environment_key="CODECAIRN_RERANKER_LICENSE",
        model_id=reranker_model,
        source_id=reranker_source,
        revision=reranker_revision,
        default_model_id=DEFAULT_RERANKER_MODEL,
        default_source_id=DEFAULT_RERANKER_SOURCE,
        default_revision=DEFAULT_RERANKER_REVISION,
        default_license=DEFAULT_RERANKER_LICENSE,
    )
    cache_dir = resolved_environment.get("CODECAIRN_MODEL_CACHE") or None
    reranker_batch_size = _integer_environment(
        environment=resolved_environment,
        key="CODECAIRN_RERANKER_BATCH_SIZE",
        default=DEFAULT_RERANKER_BATCH_SIZE,
    )
    if reranker_batch_size < 1:
        raise ValueError("CODECAIRN_RERANKER_BATCH_SIZE must be positive")
    if profile == "dashscope":
        embedding_model = resolved_environment.get(
            "CODECAIRN_EMBEDDING_MODEL",
            DEFAULT_EMBEDDING_MODEL,
        )
        dimension = _integer_environment(
            environment=resolved_environment,
            key="CODECAIRN_EMBEDDING_DIMENSION",
            default=DEFAULT_EMBEDDING_DIMENSION,
        )
        if (
            embedding_model == DEFAULT_EMBEDDING_MODEL
            and dimension not in DASHSCOPE_TEXT_V4_DIMENSIONS
        ):
            supported = ", ".join(str(item) for item in sorted(DASHSCOPE_TEXT_V4_DIMENSIONS))
            raise ValueError(
                f"CODECAIRN_EMBEDDING_DIMENSION must be one of {supported} "
                f"for {DEFAULT_EMBEDDING_MODEL}"
            )
        batch_size = _integer_environment(
            environment=resolved_environment,
            key="CODECAIRN_EMBEDDING_BATCH_SIZE",
            default=DEFAULT_EMBEDDING_BATCH_SIZE,
        )
        if embedding_model == DEFAULT_EMBEDDING_MODEL and batch_size > 10:
            raise ValueError(
                f"CODECAIRN_EMBEDDING_BATCH_SIZE must not exceed 10 for {DEFAULT_EMBEDDING_MODEL}"
            )
        api_key = resolved_environment.get(
            "CODECAIRN_EMBEDDING_API_KEY", ""
        ) or resolved_environment.get(
            "DASHSCOPE_API_KEY",
            "",
        )
        embedding_source = resolved_environment.get(
            "CODECAIRN_EMBEDDING_BASE_URL",
            DEFAULT_EMBEDDING_SOURCE,
        )
        embedding_revision = resolved_environment.get(
            "CODECAIRN_EMBEDDING_REVISION",
            DEFAULT_EMBEDDING_REVISION,
        )
        embedding_license = resolved_environment.get(
            "CODECAIRN_EMBEDDING_LICENSE",
            DEFAULT_EMBEDDING_LICENSE,
        )
        if not embedding_license.strip():
            raise ValueError("CODECAIRN_EMBEDDING_LICENSE must not be empty")
        return RetrievalProviders(
            profile="dashscope",
            embedder=DashScopeEmbeddingAdapter(
                api_key=api_key,
                model_id=embedding_model,
                base_url=embedding_source,
                revision=embedding_revision,
                dimension=dimension,
                batch_size=batch_size,
                timeout_seconds=_float_environment(
                    environment=resolved_environment,
                    key="CODECAIRN_EMBEDDING_TIMEOUT_SECONDS",
                    default=DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
                ),
                max_attempts=_integer_environment(
                    environment=resolved_environment,
                    key="CODECAIRN_EMBEDDING_MAX_ATTEMPTS",
                    default=DEFAULT_EMBEDDING_MAX_ATTEMPTS,
                ),
                retry_backoff_seconds=_float_environment(
                    environment=resolved_environment,
                    key="CODECAIRN_EMBEDDING_RETRY_BACKOFF_SECONDS",
                    default=DEFAULT_EMBEDDING_RETRY_BACKOFF_SECONDS,
                ),
                input_price_cny_per_million=(
                    _float_environment(
                        environment=resolved_environment,
                        key="CODECAIRN_EMBEDDING_INPUT_PRICE_CNY_PER_MILLION",
                        default=DEFAULT_EMBEDDING_INPUT_PRICE_CNY_PER_MILLION,
                    )
                    if embedding_model == DEFAULT_EMBEDDING_MODEL
                    or "CODECAIRN_EMBEDDING_INPUT_PRICE_CNY_PER_MILLION" in resolved_environment
                    else None
                ),
            ),
            reranker=FastEmbedRerankingAdapter(
                model_id=reranker_model,
                source_id=reranker_source,
                revision=reranker_revision,
                cache_dir=cache_dir,
                batch_size=reranker_batch_size,
            ),
            embedding_license=embedding_license,
            reranker_license=reranker_license,
            planner=planner,
        )
    embedding_model = resolved_environment.get(
        "CODECAIRN_EMBEDDING_MODEL",
        DEFAULT_FASTEMBED_EMBEDDING_MODEL,
    )
    raw_dimension = resolved_environment.get("CODECAIRN_EMBEDDING_DIMENSION")
    if raw_dimension is None:
        if embedding_model != DEFAULT_FASTEMBED_EMBEDDING_MODEL:
            raise ValueError("Custom embedding models require CODECAIRN_EMBEDDING_DIMENSION")
        dimension = DEFAULT_FASTEMBED_EMBEDDING_DIMENSION
    else:
        dimension = _integer_environment(
            environment=resolved_environment,
            key="CODECAIRN_EMBEDDING_DIMENSION",
            default=DEFAULT_FASTEMBED_EMBEDDING_DIMENSION,
        )
    embedding_revision = _model_revision(
        environment=resolved_environment,
        environment_key="CODECAIRN_EMBEDDING_REVISION",
        model_id=embedding_model,
        default_model_id=DEFAULT_FASTEMBED_EMBEDDING_MODEL,
        default_revision=DEFAULT_FASTEMBED_EMBEDDING_REVISION,
    )
    embedding_source = _model_source(
        environment=resolved_environment,
        environment_key="CODECAIRN_EMBEDDING_SOURCE",
        model_id=embedding_model,
        default_model_id=DEFAULT_FASTEMBED_EMBEDDING_MODEL,
        default_source=DEFAULT_FASTEMBED_EMBEDDING_SOURCE,
    )
    validate_hf_artifact(source_id=embedding_source, revision=embedding_revision)
    embedding_license = _model_license(
        environment=resolved_environment,
        environment_key="CODECAIRN_EMBEDDING_LICENSE",
        model_id=embedding_model,
        source_id=embedding_source,
        revision=embedding_revision,
        default_model_id=DEFAULT_FASTEMBED_EMBEDDING_MODEL,
        default_source_id=DEFAULT_FASTEMBED_EMBEDDING_SOURCE,
        default_revision=DEFAULT_FASTEMBED_EMBEDDING_REVISION,
        default_license=DEFAULT_FASTEMBED_EMBEDDING_LICENSE,
    )
    return RetrievalProviders(
        profile="fastembed",
        embedder=FastEmbedEmbeddingAdapter(
            model_id=embedding_model,
            source_id=embedding_source,
            revision=embedding_revision,
            dimension=dimension,
            cache_dir=cache_dir,
        ),
        reranker=FastEmbedRerankingAdapter(
            model_id=reranker_model,
            source_id=reranker_source,
            revision=reranker_revision,
            cache_dir=cache_dir,
            batch_size=reranker_batch_size,
        ),
        embedding_license=embedding_license,
        reranker_license=reranker_license,
        planner=planner,
    )


def _integer_environment(
    *,
    environment: Mapping[str, str],
    key: str,
    default: int,
) -> int:
    raw = environment.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error


def _float_environment(
    *,
    environment: Mapping[str, str],
    key: str,
    default: float,
) -> float:
    raw = environment.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be numeric") from error


def create_runtime(
    root: Path,
    *,
    retrieval: RetrievalProviders | None = None,
    episode_semanticizer: EpisodeSemanticizer | None = None,
    clause_adapter: ClauseProjectionAdapter | None = None,
) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    if episode_semanticizer is not None and clause_adapter is not None:
        raise ValueError("Configure only one semantic projection strategy")
    semanticizer = episode_semanticizer
    if semanticizer is None:
        if clause_adapter is None:
            semanticizer = LosslessEpisodeSemanticizer()
        else:
            semanticizer = GroundedClauseSemanticizer(
                adapter=clause_adapter,
                cache=JsonProjectionCache(resolved / ".projection-cache"),
                max_clause_chars=(
                    64 * 1024 * 1024
                    if isinstance(clause_adapter, LosslessClauseProjectionAdapter)
                    else 4_096
                ),
            )
    providers = retrieval or create_retrieval_providers()
    state = SQLiteState(resolved / "state.sqlite3")
    index = LanceMemoryIndex(resolved / "index.lancedb", embedder=providers.embedder)
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=state,
        evidence_gate=EvidenceGate(),
        episode_semanticizer=semanticizer,
        recall_engine=RecallEngine(
            index=index,
            state=state,
            embedder=providers.embedder,
            reranker=providers.reranker,
            planner_config=providers.planner,
            retrieval_config_sha256=providers.config_sha256,
        ),
    )


def create_clause_projection_adapter(
    *,
    environment: Mapping[str, str] | None = None,
) -> ClauseProjectionAdapter:
    """Resolve one ingestion-time semantic projection profile without calling it."""

    resolved_environment = os.environ if environment is None else environment
    profile = resolved_environment.get("CODECAIRN_SEMANTICIZER_PROFILE", "lossless")
    if profile == "lossless":
        return LosslessClauseProjectionAdapter()
    if profile != "structured":
        raise ValueError(f"Unknown semantic projection profile: {profile}")
    from codecairn.evaluation.providers import create_locomo_text_model
    from codecairn.evaluation.semantic import StructuredModelClauseProjectionAdapter

    revision = resolved_environment.get(
        "CODECAIRN_SEMANTIC_REVISION",
        "grounded-clause-json-v2",
    )
    if not revision.strip():
        raise ValueError("CODECAIRN_SEMANTIC_REVISION must not be empty")
    return StructuredModelClauseProjectionAdapter(
        model=create_locomo_text_model(
            role="semantic",
            environment=resolved_environment,
        ),
        revision=revision,
        max_facts_per_request=_integer_environment(
            environment=resolved_environment,
            key="CODECAIRN_SEMANTIC_MAX_FACTS_PER_REQUEST",
            default=48,
        ),
        max_request_chars=_integer_environment(
            environment=resolved_environment,
            key="CODECAIRN_SEMANTIC_MAX_REQUEST_CHARS",
            default=48_000,
        ),
        max_response_chars=_integer_environment(
            environment=resolved_environment,
            key="CODECAIRN_SEMANTIC_MAX_RESPONSE_CHARS",
            default=96_000,
        ),
    )


def _semantic_projection_public_config(
    adapter: ClauseProjectionAdapter,
) -> dict[str, object]:
    configured = getattr(adapter, "public_config", None)
    if isinstance(configured, dict):
        return dict(configured)
    return {
        "adapter": adapter.identity.adapter_id,
        "revision": adapter.identity.revision,
        "model": adapter.identity.model_id,
    }


def _semantic_projection_usage(adapter: ClauseProjectionAdapter) -> dict[str, object]:
    usage = getattr(adapter, "usage", None)
    if usage is None:
        return {
            "call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "cost_cny": 0.0,
        }
    return asdict(usage)


def _embedding_usage(adapter: DashScopeEmbeddingAdapter) -> dict[str, object]:
    return asdict(adapter.usage)


def create_cascade(
    root: Path,
    *,
    index: MemoryIndex | None = None,
    retrieval: RetrievalProviders | None = None,
) -> MiniCascade:
    """Build the recoverable Markdown-to-LanceDB synchronization service."""
    resolved = root.resolve()
    if index is None:
        providers = retrieval or create_retrieval_providers()
        index = LanceMemoryIndex(resolved / "index.lancedb", embedder=providers.embedder)
    return MiniCascade(
        truth=MarkdownMemoryStore(resolved),
        state=SQLiteState(resolved / "state.sqlite3"),
        index=index,
    )


def _recall_planner_config(environment: Mapping[str, str]) -> RecallPlannerConfig:
    value = environment.get("CODECAIRN_RECALL_MODE", "hierarchy")
    if value not in {"episode-only", "hierarchy-no-neighbors", "hierarchy"}:
        raise ValueError(f"Unknown recall mode: {value}")
    default = RecallPlannerConfig.for_mode(cast(RecallPlannerMode, value))
    return replace(
        default,
        fact_rerank_max_candidates=_integer_environment(
            environment=environment,
            key="CODECAIRN_FACT_RERANK_MAX_CANDIDATES",
            default=default.fact_rerank_max_candidates,
        ),
        fact_rerank_max_candidates_per_parent=_integer_environment(
            environment=environment,
            key="CODECAIRN_FACT_RERANK_MAX_CANDIDATES_PER_PARENT",
            default=default.fact_rerank_max_candidates_per_parent,
        ),
        fact_rerank_max_document_chars=_integer_environment(
            environment=environment,
            key="CODECAIRN_FACT_RERANK_MAX_DOCUMENT_CHARS",
            default=default.fact_rerank_max_document_chars,
        ),
    )


def _model_revision(
    *,
    environment: Mapping[str, str],
    environment_key: str,
    model_id: str,
    default_model_id: str,
    default_revision: str,
) -> str:
    configured = environment.get(environment_key)
    if configured is not None:
        return configured
    if model_id != default_model_id:
        raise ValueError(f"Custom model {model_id} requires {environment_key}")
    return default_revision


def _model_source(
    *,
    environment: Mapping[str, str],
    environment_key: str,
    model_id: str,
    default_model_id: str,
    default_source: str,
) -> str:
    configured = environment.get(environment_key)
    if configured is not None:
        return configured
    if model_id != default_model_id:
        raise ValueError(f"Custom model {model_id} requires {environment_key}")
    return default_source


def _model_license(
    *,
    environment: Mapping[str, str],
    environment_key: str,
    model_id: str,
    source_id: str,
    revision: str,
    default_model_id: str,
    default_source_id: str,
    default_revision: str,
    default_license: str,
) -> str:
    configured = environment.get(environment_key)
    if configured is not None:
        if not configured.strip():
            raise ValueError(f"{environment_key} must not be empty")
        return configured
    if (model_id, source_id, revision) != (
        default_model_id,
        default_source_id,
        default_revision,
    ):
        raise ValueError(f"Custom model artifact {source_id}@{revision} requires {environment_key}")
    return default_license


class _LocalOperations(ApplicationOperations):
    def __init__(self, root: Path, *, retrieval: RetrievalProviders) -> None:
        self._root = root.resolve()
        self._retrieval = retrieval

    def doctor(self) -> dict[str, object]:
        truth_store = MarkdownMemoryStore(self._root)
        truth = truth_store.scan()
        state = SQLiteState(self._root / "state.sqlite3")
        ledger = state.operational_counts()
        queue = create_cascade(self._root, retrieval=self._retrieval).health()
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
            index = LanceMemoryIndex(index_path, embedder=self._retrieval.embedder)
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
        provider_status = _provider_status(retrieval=self._retrieval)
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
        if request.suite != "locomo":
            locomo_only_inputs = (
                request.question_set_path,
                request.corpus_path,
                request.query_vectors_path,
                request.retrieval_gate_question_set_path,
                request.retrieval_canary_run_path,
                request.retrieval_holdout_run_path,
            )
            if request.execution_phase != "all" or any(
                value is not None for value in locomo_only_inputs
            ):
                raise ValueError("LoCoMo-only evaluation inputs require the LoCoMo suite")
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

    def build_locomo_ablation_report(self, request: LoCoMoAblationRequest) -> dict[str, object]:
        from codecairn.evaluation.locomo_ablation import (
            LoCoMoAblationConfig,
            build_locomo_ablation_report,
        )

        return build_locomo_ablation_report(
            LoCoMoAblationConfig(
                question_set_path=request.question_set_path,
                episode_only_run=request.episode_only_run,
                hierarchy_no_neighbors_run=request.hierarchy_no_neighbors_run,
                hierarchy_run=request.hierarchy_run,
                output_path=request.output_path,
            )
        )

    def build_locomo_promotion_report(
        self,
        request: LoCoMoPromotionRequest,
    ) -> dict[str, object]:
        from codecairn.evaluation.locomo_promotion import (
            LoCoMoPromotionConfig,
            build_locomo_promotion_report,
        )

        return build_locomo_promotion_report(
            LoCoMoPromotionConfig(
                question_set_path=request.question_set_path,
                selection_report_path=request.selection_report_path,
                episode_only_run=request.episode_only_run,
                hierarchy_no_neighbors_run=request.hierarchy_no_neighbors_run,
                hierarchy_run=request.hierarchy_run,
                run_dir=request.run_dir,
                output_path=request.output_path,
            )
        )

    def report_locomo_evidence_coverage(
        self,
        request: LoCoMoEvidenceCoverageRequest,
    ) -> dict[str, object]:
        from codecairn.evaluation.locomo_evidence import (
            LoCoMoEvidenceCoverageConfig,
            report_locomo_evidence_coverage,
        )

        return report_locomo_evidence_coverage(
            LoCoMoEvidenceCoverageConfig(
                run_dir=request.run_dir,
                dataset_path=request.dataset_path,
                output_path=request.output_path,
                oracle_max_tokens=request.oracle_max_tokens,
            )
        )

    def build_locomo_corpus(self, request: LoCoMoCorpusBuildRequest) -> dict[str, object]:
        from codecairn.evaluation.locomo import (
            LOCOMO_DATASET_SHA256,
            CodeCairnConversationMemory,
            LoCoMoCorpusConfig,
            build_locomo_corpus,
        )

        projection_adapter = create_clause_projection_adapter()
        projection_config = _semantic_projection_public_config(projection_adapter)

        def memory_factory(root: Path) -> CodeCairnConversationMemory:
            return CodeCairnConversationMemory(
                runtime=create_runtime(
                    root,
                    retrieval=self._retrieval,
                    clause_adapter=projection_adapter,
                ),
                cascade=create_cascade(root, retrieval=self._retrieval),
                repo_key=f"locomo/{root.name}",
                semantic_projection=projection_config,
            )

        artifact = build_locomo_corpus(
            LoCoMoCorpusConfig(
                dataset_path=request.input_path,
                output_root=request.output_root,
                corpus_id=request.corpus_id,
                repository_commit=request.repository_commit,
                retrieval_config=self._retrieval.public_config,
                semantic_projection=projection_config,
                semantic_projection_usage=lambda: _semantic_projection_usage(projection_adapter),
                embedding_usage=(
                    (
                        lambda: _embedding_usage(
                            cast(DashScopeEmbeddingAdapter, self._retrieval.embedder)
                        )
                    )
                    if isinstance(self._retrieval.embedder, DashScopeEmbeddingAdapter)
                    else None
                ),
                resume=request.resume,
                question_set_path=request.question_set_path,
                expected_dataset_sha256=(
                    LOCOMO_DATASET_SHA256
                    if request.expected_dataset_sha256 is None
                    else request.expected_dataset_sha256
                ),
            ),
            memory_factory=memory_factory,
        )
        return {
            "corpus_dir": str(artifact.corpus_dir),
            "content_sha256": artifact.content_sha256,
            "counts": artifact.manifest["counts"],
        }

    def build_locomo_query_vectors(
        self,
        request: LoCoMoQueryVectorBuildRequest,
    ) -> dict[str, object]:
        from codecairn.evaluation.locomo import (
            LOCOMO_DATASET_SHA256,
            LoCoMoQueryVectorConfig,
            build_locomo_query_vectors,
        )

        artifact = build_locomo_query_vectors(
            LoCoMoQueryVectorConfig(
                dataset_path=request.input_path,
                output_root=request.output_root,
                vector_set_id=request.vector_set_id,
                resume=request.resume,
                question_set_path=request.question_set_path,
                expected_dataset_sha256=(
                    LOCOMO_DATASET_SHA256
                    if request.expected_dataset_sha256 is None
                    else request.expected_dataset_sha256
                ),
            ),
            embedder=self._retrieval.embedder,
        )
        return {
            "query_vectors_dir": str(artifact.vector_set_dir),
            "content_sha256": artifact.content_sha256,
            "question_count": artifact.manifest["question_count"],
        }

    def _run_locomo(
        self,
        request: EvaluationRunRequest,
        *,
        output_root: Path,
    ) -> dict[str, object]:
        from codecairn.evaluation.locomo import (
            LOCOMO_DATASET_SHA256,
            LOCOMO_PAID_SCORING_GATE_CONTRACT,
            CodeCairnConversationMemory,
            FrozenQueryEmbeddingAdapter,
            LoCoMoConversationWork,
            LoCoMoRunConfig,
            run_locomo,
            validate_locomo_run_id,
        )
        from codecairn.evaluation.locomo_retrieval_gate import (
            LoCoMoRetrievalGateConfig,
            verify_locomo_retrieval_gate,
        )
        from codecairn.evaluation.providers import create_locomo_text_model

        paid_scoring_preflight: dict[str, object] | None = None
        gate_inputs = (
            request.retrieval_gate_question_set_path,
            request.retrieval_canary_run_path,
            request.retrieval_holdout_run_path,
        )
        supplied_gate_input = any(value is not None for value in gate_inputs)
        if request.mode == "retrieval":
            if supplied_gate_input:
                raise ValueError("LoCoMo retrieval mode does not accept paid-scoring gates")
        elif request.question_set_path is None:
            if supplied_gate_input:
                raise ValueError("LoCoMo paid-scoring gates require a frozen question set")
        else:
            definition = _bootstrap_mapping(
                read_json(request.question_set_path),
                field="LoCoMo question set",
            )
            raw_protocol = definition.get("protocol")
            if raw_protocol is None:
                if supplied_gate_input:
                    raise ValueError(
                        "LoCoMo question-set protocol does not support paid-scoring gates"
                    )
            else:
                protocol = _bootstrap_mapping(
                    raw_protocol,
                    field="LoCoMo question-set protocol",
                )
                gate_contract = protocol.get("paid_scoring_gate")
                if gate_contract == LOCOMO_PAID_SCORING_GATE_CONTRACT:
                    required_paths = {
                        "retrieval gate question set": request.retrieval_gate_question_set_path,
                        "retrieval canary run": request.retrieval_canary_run_path,
                        "retrieval holdout run": request.retrieval_holdout_run_path,
                        "corpus": request.corpus_path,
                        "query vectors": request.query_vectors_path,
                    }
                    missing = [name for name, value in required_paths.items() if value is None]
                    if missing:
                        raise ValueError(
                            "LoCoMo paid-scoring gate is missing: " + ", ".join(missing)
                        )
                    paid_scoring_preflight = verify_locomo_retrieval_gate(
                        LoCoMoRetrievalGateConfig(
                            target_question_set_path=cast(
                                Path,
                                request.retrieval_gate_question_set_path,
                            ),
                            scored_question_set_path=request.question_set_path,
                            dataset_path=request.input_path,
                            canary_run_dir=cast(Path, request.retrieval_canary_run_path),
                            holdout_run_dir=cast(Path, request.retrieval_holdout_run_path),
                            repository_commit=request.repository_commit,
                            corpus_path=cast(Path, request.corpus_path),
                            query_vectors_path=cast(Path, request.query_vectors_path),
                        )
                    )
                    if paid_scoring_preflight.get("scored_question_set_sha256") != file_sha256(
                        request.question_set_path
                    ):
                        raise ValueError(
                            "LoCoMo paid-scoring gate does not target the scored question set"
                        )
                else:
                    if supplied_gate_input:
                        raise ValueError(
                            "LoCoMo question-set protocol does not support paid-scoring gates"
                        )
                    if protocol.get("query_sketcher") == (
                        "codecairn/deterministic-query-sketch-v4"
                    ):
                        raise ValueError(
                            "LoCoMo v18 paid scoring requires a retrieval gate contract"
                        )

        answer_model = (
            None
            if request.mode == "retrieval"
            else create_locomo_text_model(
                role="answer",
                environment=os.environ,
                model_override=request.model,
            )
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

        retrieval = self._retrieval
        if request.query_vectors_path is not None:
            retrieval = replace(
                retrieval,
                embedder=FrozenQueryEmbeddingAdapter(
                    request.query_vectors_path,
                    load_vectors=request.corpus_path is None,
                ),
            )
        if request.corpus_path is not None and request.query_vectors_path is None:
            raise ValueError("Exec-isolated shared-corpus LoCoMo runs require frozen query vectors")

        worker_limits = WorkerProcessLimits(
            max_rss_bytes=_positive_environment_int(
                "CODECAIRN_EVAL_MAX_RSS_BYTES", 2 * 1024 * 1024 * 1024
            ),
            stall_timeout_seconds=_positive_environment_float(
                "CODECAIRN_EVAL_WORKER_STALL_SECONDS", 600.0
            ),
            poll_interval_seconds=_positive_environment_float(
                "CODECAIRN_EVAL_WORKER_POLL_SECONDS", 0.25
            ),
            rss_poll_interval_seconds=_positive_environment_float(
                "CODECAIRN_EVAL_WORKER_RSS_POLL_SECONDS",
                1.0,
            ),
        )
        worker_contract = {
            "name": "verified-shared-corpus-exec-per-conversation-v3",
            "max_rss_bytes": worker_limits.max_rss_bytes,
            "stall_timeout_seconds": worker_limits.stall_timeout_seconds,
            "poll_interval_seconds": worker_limits.poll_interval_seconds,
            "rss_poll_interval_seconds": worker_limits.rss_poll_interval_seconds,
            "progress_signal": "heartbeat-evidence-and-durable-question-checkpoint-deadline-v2",
            "publish_policy": "conversation-directory-atomic-rename-v1",
            "reranker_warmup": RERANKER_WARMUP_CONTRACT,
        }
        lossless_clause_adapter = LosslessClauseProjectionAdapter()
        lossless_semantic_projection = _semantic_projection_public_config(lossless_clause_adapter)

        def memory_factory(root: Path) -> CodeCairnConversationMemory:
            return CodeCairnConversationMemory(
                runtime=create_runtime(
                    root,
                    retrieval=retrieval,
                    clause_adapter=lossless_clause_adapter,
                ),
                cascade=create_cascade(root, retrieval=retrieval),
                repo_key=f"locomo/{root.name}",
                semantic_projection=lossless_semantic_projection,
            )

        def question_worker(work: LoCoMoConversationWork) -> None:
            _run_locomo_question_worker(
                work,
                retrieval_config=retrieval.public_config,
                answer_model_config=(None if answer_model is None else answer_model.public_config),
                judge_model_config=None if judge_model is None else judge_model.public_config,
                limits=worker_limits,
            )

        validate_locomo_run_id(request.run_id)
        lock_path = output_root / ".locks" / f"{request.run_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(lock_path, timeout=0):
            if request.resume:
                _reject_locomo_run_hard_breaches(
                    output_root / request.run_id,
                    limits=worker_limits,
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
                    retrieval_config=retrieval.public_config,
                    question_set_path=request.question_set_path,
                    execution_phase=request.execution_phase,
                    corpus_path=request.corpus_path,
                    query_vectors_path=request.query_vectors_path,
                    paid_scoring_preflight=paid_scoring_preflight,
                    expected_dataset_sha256=(
                        LOCOMO_DATASET_SHA256
                        if request.expected_dataset_sha256 is None
                        else request.expected_dataset_sha256
                    ),
                ),
                memory_factory=memory_factory,
                answer_model=answer_model,
                judge_model=judge_model,
                question_worker=question_worker if request.corpus_path is not None else None,
                question_worker_contract=(
                    worker_contract if request.corpus_path is not None else None
                ),
            )
            worker_resources = artifact.summary.get("worker_resources")
            if isinstance(worker_resources, dict):
                resource_usage_path = artifact.run_dir / "resource-usage.json"
                if resource_usage_path.exists():
                    if read_json(resource_usage_path) != worker_resources:
                        raise ValueError(
                            "LoCoMo resource usage artifact does not match the run summary"
                        )
                else:
                    write_json_exclusive(resource_usage_path, worker_resources)
        return artifact.summary


def _run_locomo_question_worker(
    work: "LoCoMoConversationWork",
    *,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    limits: WorkerProcessLimits,
) -> None:
    conversation_id = work.conversation.sample_id
    if not work.question_ids:
        return
    canonical_question_dir = _locomo_run_child(
        work.run_dir,
        "checkpoints",
        "questions",
        conversation_id,
    )
    worker_root = _locomo_run_child(work.run_dir, "workers", conversation_id)
    _reject_prior_locomo_worker_hard_breach(work, limits=limits)
    if canonical_question_dir.exists():
        _validate_worker_question_inventory(
            canonical_question_dir,
            conversation_id=conversation_id,
            expected_question_ids=work.question_ids,
        )
        _recover_locomo_worker_receipt(
            work,
            limits=limits,
            canonical_question_dir=canonical_question_dir,
            retrieval_config=retrieval_config,
            answer_model_config=answer_model_config,
            judge_model_config=judge_model_config,
        )
        _require_locomo_worker_attempt_receipts(work, worker_root=worker_root)
        return

    worker_root.mkdir(parents=True, exist_ok=True)
    if _recover_completed_locomo_worker_attempt(
        work,
        worker_root=worker_root,
        canonical_question_dir=canonical_question_dir,
        retrieval_config=retrieval_config,
        answer_model_config=answer_model_config,
        judge_model_config=judge_model_config,
        limits=limits,
    ):
        _require_locomo_worker_attempt_receipts(work, worker_root=worker_root)
        return
    _cleanup_stale_locomo_worker_copies(work, worker_root=worker_root)
    _recover_interrupted_locomo_worker_receipts(
        work,
        worker_root=worker_root,
        retrieval_config=retrieval_config,
        answer_model_config=answer_model_config,
        judge_model_config=judge_model_config,
        limits=limits,
    )
    _require_locomo_worker_attempt_receipts(work, worker_root=worker_root)
    attempt = _next_worker_attempt(worker_root)
    attempt_dir = worker_root / f"attempt-{attempt}"
    attempt_dir.mkdir()
    worker_run_dir = attempt_dir / "run"
    worker_run_dir.mkdir()
    worker_corpus_dir = worker_run_dir / "corpus"
    try:
        _copy_locomo_conversation_corpus(
            work.corpus_dir.resolve(),
            worker_corpus_dir,
            conversation_id=conversation_id,
        )
        _execute_locomo_worker_attempt(
            work,
            attempt=attempt,
            attempt_dir=attempt_dir,
            canonical_question_dir=canonical_question_dir,
            retrieval_config=retrieval_config,
            answer_model_config=answer_model_config,
            judge_model_config=judge_model_config,
            limits=limits,
        )
    finally:
        _remove_worker_corpus_copy(worker_corpus_dir, attempt_dir=attempt_dir)


def _execute_locomo_worker_attempt(
    work: "LoCoMoConversationWork",
    *,
    attempt: int,
    attempt_dir: Path,
    canonical_question_dir: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    limits: WorkerProcessLimits,
) -> None:
    conversation_id = work.conversation.sample_id
    worker_run_dir = attempt_dir / "run"
    reused_sources = _reuse_locomo_worker_checkpoints(
        work,
        worker_root=attempt_dir.parent,
        target_question_dir=(worker_run_dir / "checkpoints" / "questions" / conversation_id),
        current_attempt=attempt,
        retrieval_config=retrieval_config,
        answer_model_config=answer_model_config,
        judge_model_config=judge_model_config,
        limits=limits,
    )
    spec_path = attempt_dir / "spec.json"
    spec = _build_locomo_worker_spec(
        work,
        attempt_dir=attempt_dir,
        retrieval_config=retrieval_config,
        answer_model_config=answer_model_config,
        judge_model_config=judge_model_config,
        reused_sources=reused_sources,
    )
    write_json_exclusive(spec_path, spec)
    staged_question_dir = worker_run_dir / "checkpoints" / "questions" / conversation_id

    def record_worker_identity(pid: int) -> None:
        write_json_exclusive(
            attempt_dir / "worker.json",
            {
                "schema_version": 1,
                "pid": pid,
                "parent_pid": spec["parent_pid"],
                "spec_sha256": file_sha256(spec_path),
            },
        )

    process_result = run_monitored_worker(
        (sys.executable, "-m", "codecairn.locomo_worker", str(spec_path)),
        progress_root=staged_question_dir,
        limits=limits,
        on_started=record_worker_identity,
    )
    monitor_path = attempt_dir / "monitor.json"
    write_json_exclusive(
        monitor_path,
        {
            "schema_version": 1,
            "pid": process_result.pid,
            "returncode": process_result.returncode,
            "observed_max_rss_bytes": process_result.max_rss_bytes,
            "wall_time_seconds": process_result.wall_time_seconds,
            "termination_reason": process_result.termination_reason,
            "monitor_error_type": process_result.monitor_error_type,
        },
    )
    raw_resource_path = attempt_dir / "worker-receipt.json"
    raw_resource = (
        _bootstrap_mapping(read_json(raw_resource_path), field="worker receipt")
        if raw_resource_path.is_file()
        else None
    )
    reported_rss = raw_resource.get("max_rss_bytes") if raw_resource is not None else None
    reranker_warmup_ms = (
        raw_resource.get("reranker_warmup_ms") if raw_resource is not None else None
    )
    valid_reported_rss = type(reported_rss) is int and reported_rss > 0
    max_rss_bytes = max(
        process_result.max_rss_bytes,
        cast(int, reported_rss) if valid_reported_rss else 0,
    )
    accepted = (
        process_result.returncode == 0
        and process_result.termination_reason is None
        and raw_resource is not None
        and raw_resource.get("status") == "completed"
        and raw_resource.get("conversation_id") == conversation_id
        and raw_resource.get("parent_pid") == os.getpid()
        and raw_resource.get("pid") == process_result.pid
        and valid_reported_rss
        and _valid_nonnegative_number(reranker_warmup_ms)
        and max_rss_bytes <= limits.max_rss_bytes
    )
    if accepted:
        try:
            _validate_worker_question_inventory(
                staged_question_dir,
                conversation_id=conversation_id,
                expected_question_ids=work.question_ids,
            )
        except (FileNotFoundError, ValueError):
            accepted = False
    completed_checkpoints = _worker_question_checkpoint_files(
        staged_question_dir,
        conversation_id=conversation_id,
        expected_question_ids=work.question_ids,
    )
    checkpoint_sha256 = _worker_question_checkpoint_sha256(staged_question_dir)
    if accepted and raw_resource is not None:
        accepted = (
            raw_resource.get("completed_question_checkpoints") == completed_checkpoints
            and raw_resource.get("question_checkpoint_sha256") == checkpoint_sha256
        )

    resource_record: dict[str, object] = {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "attempt": attempt,
        "accepted": accepted,
        "worker_started": True,
        "status": "completed" if accepted else "failed",
        "parent_pid": spec["parent_pid"],
        "worker_pid": process_result.pid,
        "returncode": process_result.returncode,
        "termination_reason": process_result.termination_reason,
        "monitor_error_type": process_result.monitor_error_type,
        "observed_max_rss_bytes": process_result.max_rss_bytes,
        "reported_max_rss_bytes": reported_rss,
        "max_rss_bytes": max_rss_bytes,
        "rss_limit_bytes": limits.max_rss_bytes,
        "wall_time_seconds": process_result.wall_time_seconds,
        "reranker_warmup_ms": reranker_warmup_ms,
        "run_manifest_sha256": spec["run_manifest_sha256"],
        "spec_sha256": file_sha256(spec_path),
        "expected_question_ids": list(work.question_ids),
        "reused_question_sources": reused_sources,
        "completed_question_checkpoints": completed_checkpoints,
        "question_checkpoint_sha256": checkpoint_sha256,
    }
    resource_path = _locomo_worker_resource_path(work, attempt=attempt)
    if not accepted:
        write_json_exclusive(resource_path, resource_record)
        if process_result.termination_reason == "rss_limit" or max_rss_bytes > limits.max_rss_bytes:
            raise MemoryError(f"LoCoMo worker exceeded the RSS gate for {conversation_id}")
        if process_result.termination_reason == "stalled":
            raise TimeoutError(f"LoCoMo worker stopped making progress for {conversation_id}")
        error_type = None if raw_resource is None else raw_resource.get("error_type")
        detail = error_type if isinstance(error_type, str) else "no-child-receipt"
        raise RuntimeError(
            f"LoCoMo worker failed for {conversation_id} in attempt {attempt}: {detail}"
        )

    publish_marker_path = attempt_dir / "publish.json"
    write_json_exclusive(
        publish_marker_path,
        {
            "schema_version": 1,
            "conversation_id": conversation_id,
            "attempt": attempt,
            "question_ids": list(work.question_ids),
            "question_checkpoint_sha256": checkpoint_sha256,
            "run_manifest_sha256": spec["run_manifest_sha256"],
            "spec_sha256": file_sha256(spec_path),
            "monitor_sha256": file_sha256(monitor_path),
            "worker_receipt_sha256": file_sha256(raw_resource_path),
        },
    )
    resource_record["question_checkpoint_sha256"] = checkpoint_sha256
    resource_record["publish_marker_sha256"] = file_sha256(publish_marker_path)
    canonical_question_dir.parent.mkdir(parents=True, exist_ok=True)
    staged_question_dir.rename(canonical_question_dir)
    write_json_exclusive(resource_path, resource_record)


def _build_locomo_worker_spec(
    work: "LoCoMoConversationWork",
    *,
    attempt_dir: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    reused_sources: list[dict[str, object]],
) -> dict[str, object]:
    run_manifest_path = (work.run_dir / "manifest.json").resolve()
    run_manifest = _bootstrap_mapping(read_json(run_manifest_path), field="run manifest")
    corpus_manifest = _bootstrap_mapping(run_manifest.get("corpus"), field="run corpus")
    query_manifest = _bootstrap_mapping(
        run_manifest.get("query_vectors"), field="run query vectors"
    )
    query_vectors_path = work.config.query_vectors_path
    if query_vectors_path is None:
        raise ValueError("LoCoMo exec worker requires frozen query vectors")
    worker_run_dir = attempt_dir / "run"
    paid_scoring_preflight = work.config.paid_scoring_preflight
    return {
        "schema_version": 2,
        "dataset_path": str(work.config.dataset_path.resolve()),
        "dataset_sha256": work.config.expected_dataset_sha256,
        "repository_commit": work.config.repository_commit,
        "run_manifest_path": str(run_manifest_path),
        "run_manifest_sha256": file_sha256(run_manifest_path),
        "paid_scoring_preflight_sha256": (
            None
            if paid_scoring_preflight is None
            else _bootstrap_string(
                paid_scoring_preflight,
                "receipt_sha256",
                field="paid-scoring preflight",
            )
        ),
        "corpus_dir": str(work.corpus_dir.resolve()),
        "worker_corpus_dir": str((worker_run_dir / "corpus").resolve()),
        "worker_run_dir": str(worker_run_dir.resolve()),
        "corpus_content_sha256": _bootstrap_string(
            corpus_manifest, "content_sha256", field="run corpus"
        ),
        "corpus_repository_commit": _bootstrap_string(
            corpus_manifest, "repository_commit", field="run corpus"
        ),
        "corpus_tree_sha256": _bootstrap_string(corpus_manifest, "tree_sha256", field="run corpus"),
        "query_vectors_path": str(query_vectors_path.resolve()),
        "query_vectors_content_sha256": _bootstrap_string(
            query_manifest, "content_sha256", field="run query vectors"
        ),
        "resource_path": str((attempt_dir / "worker-receipt.json").resolve()),
        "heartbeat_path": str((attempt_dir / "heartbeat.json").resolve()),
        "worker_identity_path": str((attempt_dir / "worker.json").resolve()),
        "parent_pid": os.getpid(),
        "conversation_id": work.conversation.sample_id,
        "conversation_index": work.conversation_index,
        "question_ids": list(work.question_ids),
        "mode": work.config.mode,
        "categories": list(work.config.categories),
        "top_k": work.config.top_k,
        "judge_votes": work.config.judge_votes,
        "judge_response_max_attempts": work.config.judge_response_max_attempts,
        "judge_response_max_chars": work.config.judge_response_max_chars,
        "seed": work.config.seed,
        "max_workers": work.config.max_workers,
        "retrieval_config": retrieval_config,
        "answer_model": answer_model_config,
        "judge_model": judge_model_config,
        "reused_question_sources": reused_sources,
    }


def _copy_locomo_conversation_corpus(
    corpus_dir: Path,
    worker_corpus_dir: Path,
    *,
    conversation_id: str,
) -> None:
    source_runtime = (corpus_dir / "runtime" / conversation_id).resolve()
    if not source_runtime.is_relative_to(corpus_dir) or not source_runtime.is_dir():
        raise ValueError("LoCoMo worker source runtime is invalid")
    target_runtime = worker_corpus_dir / "runtime" / conversation_id
    shutil.copytree(source_runtime, target_runtime, ignore=_ignore_runtime_temporary_files)
    source_ingest = corpus_dir / "checkpoints" / "ingest" / f"{conversation_id}.json"
    if not source_ingest.is_file():
        raise ValueError("LoCoMo worker source ingest checkpoint is missing")
    target_ingest = worker_corpus_dir / "checkpoints" / "ingest" / source_ingest.name
    target_ingest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_ingest, target_ingest)


def _ignore_runtime_temporary_files(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name == ".index.lancedb.lock"]


def _remove_worker_corpus_copy(worker_corpus_dir: Path, *, attempt_dir: Path) -> None:
    resolved = worker_corpus_dir.resolve()
    if not resolved.is_relative_to(attempt_dir.resolve()) or resolved == attempt_dir.resolve():
        raise ValueError("LoCoMo worker corpus cleanup target is unsafe")
    if resolved.exists():
        shutil.rmtree(resolved)


def _cleanup_stale_locomo_worker_copies(
    work: "LoCoMoConversationWork",
    *,
    worker_root: Path,
) -> None:
    now = time.time()
    for attempt_dir in _locomo_worker_attempt_dirs(worker_root):
        worker_corpus_dir = _locomo_attempt_child(attempt_dir, "run", "corpus")
        if not worker_corpus_dir.exists():
            continue
        attempt = int(attempt_dir.name.removeprefix("attempt-"))
        if _locomo_worker_resource_path(work, attempt=attempt).is_file():
            _remove_worker_corpus_copy(worker_corpus_dir, attempt_dir=attempt_dir)
            continue
        spec_path = _locomo_attempt_child(attempt_dir, "spec.json")
        if not spec_path.is_file():
            _remove_worker_corpus_copy(worker_corpus_dir, attempt_dir=attempt_dir)
            continue
        try:
            spec = _bootstrap_mapping(read_json(spec_path), field="worker spec")
            parent_pid = spec.get("parent_pid")
            spec_age = now - spec_path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if type(parent_pid) is not int or parent_pid < 1 or _process_exists(parent_pid):
            continue
        worker_identity_path = _locomo_attempt_child(attempt_dir, "worker.json")
        if not worker_identity_path.is_file():
            if spec_age >= 3.0:
                _remove_worker_corpus_copy(worker_corpus_dir, attempt_dir=attempt_dir)
            continue
        try:
            identity = _bootstrap_mapping(read_json(worker_identity_path), field="worker identity")
            worker_pid = identity.get("pid")
        except (OSError, ValueError):
            continue
        if (
            identity.get("parent_pid") != parent_pid
            or type(worker_pid) is not int
            or worker_pid < 1
        ):
            continue
        deadline = time.monotonic() + 3.0
        while _process_exists(worker_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _process_exists(worker_pid):
            raise RuntimeError("A previous LoCoMo worker is still running")
        if not _process_exists(parent_pid):
            _remove_worker_corpus_copy(worker_corpus_dir, attempt_dir=attempt_dir)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _recover_interrupted_locomo_worker_receipts(
    work: "LoCoMoConversationWork",
    *,
    worker_root: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    limits: WorkerProcessLimits,
) -> None:
    for attempt_dir in _locomo_worker_attempt_dirs(worker_root):
        attempt = int(attempt_dir.name.removeprefix("attempt-"))
        resource_path = _locomo_worker_resource_path(work, attempt=attempt)
        spec_path = _locomo_attempt_child(attempt_dir, "spec.json")
        raw_resource_path = _locomo_attempt_child(attempt_dir, "worker-receipt.json")
        worker_identity_path = _locomo_attempt_child(attempt_dir, "worker.json")
        if resource_path.exists() or not spec_path.is_file():
            continue
        try:
            spec = _bootstrap_mapping(read_json(spec_path), field="worker spec")
        except (OSError, ValueError):
            continue
        if not _worker_attempt_spec_matches(
            spec,
            work,
            attempt_dir=attempt_dir,
            retrieval_config=retrieval_config,
            answer_model_config=answer_model_config,
            judge_model_config=judge_model_config,
        ):
            continue
        staged_question_dir = _locomo_attempt_child(
            attempt_dir,
            "run",
            "checkpoints",
            "questions",
            work.conversation.sample_id,
        )
        completed_checkpoints = _worker_question_checkpoint_files(
            staged_question_dir,
            conversation_id=work.conversation.sample_id,
            expected_question_ids=work.question_ids,
        )
        checkpoint_sha256 = _worker_question_checkpoint_sha256(staged_question_dir)
        if not raw_resource_path.exists() and not worker_identity_path.exists():
            parent_pid = spec.get("parent_pid")
            if type(parent_pid) is not int or parent_pid < 1 or _process_exists(parent_pid):
                continue
            write_json_exclusive(
                resource_path,
                {
                    "schema_version": 1,
                    "conversation_id": work.conversation.sample_id,
                    "attempt": attempt,
                    "accepted": False,
                    "worker_started": False,
                    "status": "failed",
                    "parent_pid": parent_pid,
                    "worker_pid": None,
                    "returncode": None,
                    "termination_reason": "coordinator_terminated_before_worker_start",
                    "observed_max_rss_bytes": 0,
                    "reported_max_rss_bytes": None,
                    "max_rss_bytes": 0,
                    "rss_limit_bytes": limits.max_rss_bytes,
                    "wall_time_seconds": None,
                    "reranker_warmup_ms": None,
                    "run_manifest_sha256": spec["run_manifest_sha256"],
                    "spec_sha256": file_sha256(spec_path),
                    "expected_question_ids": list(work.question_ids),
                    "reused_question_sources": spec.get("reused_question_sources", []),
                    "completed_question_checkpoints": completed_checkpoints,
                    "question_checkpoint_sha256": checkpoint_sha256,
                },
            )
            continue
        if not raw_resource_path.is_file() or not worker_identity_path.is_file():
            continue
        try:
            raw_resource = _bootstrap_mapping(read_json(raw_resource_path), field="worker receipt")
            identity = _bootstrap_mapping(read_json(worker_identity_path), field="worker identity")
        except (OSError, ValueError):
            continue
        reported = raw_resource.get("max_rss_bytes")
        reranker_warmup_ms = raw_resource.get("reranker_warmup_ms")
        worker_pid = raw_resource.get("pid")
        status = raw_resource.get("status")
        if (
            status not in {"completed", "failed", "parent_lost"}
            or raw_resource.get("conversation_id") != work.conversation.sample_id
            or raw_resource.get("parent_pid") != spec.get("parent_pid")
            or identity.get("schema_version") != 1
            or identity.get("pid") != worker_pid
            or identity.get("parent_pid") != spec.get("parent_pid")
            or identity.get("spec_sha256") != file_sha256(spec_path)
            or type(worker_pid) is not int
            or worker_pid < 1
            or type(reported) is not int
            or reported < 1
            or (status == "completed" and not _valid_nonnegative_number(reranker_warmup_ms))
            or (
                reranker_warmup_ms is not None and not _valid_nonnegative_number(reranker_warmup_ms)
            )
            or raw_resource.get("completed_question_checkpoints") != completed_checkpoints
            or raw_resource.get("question_checkpoint_sha256") != checkpoint_sha256
        ):
            raise ValueError("Interrupted LoCoMo worker receipt does not match its checkpoints")
        record = {
            "schema_version": 1,
            "conversation_id": work.conversation.sample_id,
            "attempt": attempt,
            "accepted": False,
            "worker_started": True,
            "status": "failed",
            "parent_pid": spec["parent_pid"],
            "worker_pid": worker_pid,
            "returncode": 70 if status == "parent_lost" else 1,
            "termination_reason": "parent_lost",
            "observed_max_rss_bytes": 0,
            "reported_max_rss_bytes": reported,
            "max_rss_bytes": reported,
            "rss_limit_bytes": limits.max_rss_bytes,
            "wall_time_seconds": raw_resource.get("wall_time_seconds"),
            "reranker_warmup_ms": raw_resource.get("reranker_warmup_ms"),
            "run_manifest_sha256": spec["run_manifest_sha256"],
            "spec_sha256": file_sha256(spec_path),
            "expected_question_ids": list(work.question_ids),
            "reused_question_sources": spec.get("reused_question_sources", []),
            "completed_question_checkpoints": completed_checkpoints,
            "question_checkpoint_sha256": checkpoint_sha256,
            "monitoring_gap": "coordinator-lost-child-ru-maxrss-only-v1",
        }
        write_json_exclusive(resource_path, record)
        if reported > limits.max_rss_bytes:
            raise MemoryError("Interrupted LoCoMo worker exceeded the RSS gate")


def _validate_worker_question_inventory(
    question_dir: Path,
    *,
    conversation_id: str,
    expected_question_ids: tuple[str, ...],
) -> None:
    if not question_dir.is_dir():
        raise FileNotFoundError("LoCoMo worker did not create a question checkpoint directory")
    paths = sorted(question_dir.glob("*.json"))
    if {path.stem for path in paths} != set(expected_question_ids) or len(paths) != len(
        expected_question_ids
    ):
        raise ValueError("LoCoMo worker question inventory is incomplete or contains extras")
    for path in paths:
        record = _bootstrap_mapping(read_json(path), field="question checkpoint")
        if record.get("sample_id") != conversation_id or record.get("question_id") != path.stem:
            raise ValueError("LoCoMo worker question checkpoint identity does not match its path")


def _worker_question_checkpoint_sha256(question_dir: Path) -> str:
    digest = hashlib.sha256()
    tree = sorted(question_dir.rglob("*"))
    if any(path.is_symlink() or not path.resolve().is_relative_to(question_dir) for path in tree):
        raise ValueError("LoCoMo worker question artifact escapes its directory")
    for path in (item for item in tree if item.is_file()):
        digest.update(path.relative_to(question_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def _worker_question_checkpoint_files(
    question_dir: Path,
    *,
    conversation_id: str,
    expected_question_ids: tuple[str, ...],
) -> dict[str, str]:
    expected = set(expected_question_ids)
    completed: dict[str, str] = {}
    for path in sorted(question_dir.glob("*.json")):
        if path.stem not in expected:
            continue
        try:
            record = _bootstrap_mapping(read_json(path), field="question checkpoint")
        except (OSError, ValueError):
            continue
        if record.get("sample_id") == conversation_id and record.get("question_id") == path.stem:
            completed[path.stem] = file_sha256(path)
    return completed


def _locomo_worker_attempt_dirs(worker_root: Path) -> list[Path]:
    attempts = [
        (int(path.name.removeprefix("attempt-")), path)
        for path in worker_root.glob("attempt-*")
        if path.is_dir()
        and not path.is_symlink()
        and path.name.removeprefix("attempt-").isdigit()
        and path.resolve().is_relative_to(worker_root.resolve())
    ]
    return [path for _number, path in sorted(attempts, reverse=True)]


def _require_locomo_worker_attempt_receipts(
    work: "LoCoMoConversationWork", *, worker_root: Path
) -> None:
    for attempt_dir in worker_root.glob("attempt-*"):
        number = attempt_dir.name.removeprefix("attempt-")
        if (
            attempt_dir.is_symlink()
            or not attempt_dir.is_dir()
            or not attempt_dir.resolve().is_relative_to(worker_root.resolve())
            or not number.isdigit()
            or int(number) < 1
        ):
            raise ValueError("LoCoMo worker attempt is not a safe numbered directory")
        durable_evidence = any(
            _locomo_attempt_child(attempt_dir, name).exists()
            for name in (
                "spec.json",
                "worker.json",
                "monitor.json",
                "worker-receipt.json",
                "publish.json",
            )
        )
        if not durable_evidence:
            continue
        if not _locomo_worker_resource_path(work, attempt=int(number)).is_file():
            raise ValueError("LoCoMo worker attempt has no resource receipt")


def _locomo_worker_resource_path(
    work: "LoCoMoConversationWork",
    *,
    attempt: int,
) -> Path:
    return _locomo_run_child(
        work.run_dir,
        "resources",
        "conversations",
        f"{work.conversation.sample_id}.attempt-{attempt}.json",
    )


def _reject_prior_locomo_worker_hard_breach(
    work: "LoCoMoConversationWork",
    *,
    limits: WorkerProcessLimits,
) -> None:
    resource_root = _locomo_run_child(work.run_dir, "resources", "conversations")
    for path in sorted(resource_root.glob(f"{work.conversation.sample_id}.attempt-*.json")):
        if path.is_symlink() or not path.resolve().is_relative_to(work.run_dir.resolve()):
            raise ValueError("LoCoMo worker resource receipt escapes the run")
        record = _bootstrap_mapping(read_json(path), field="worker resource receipt")
        maximum = record.get("max_rss_bytes")
        if (type(maximum) is int and maximum > limits.max_rss_bytes) or record.get(
            "termination_reason"
        ) == "rss_limit":
            raise MemoryError(
                "A prior LoCoMo worker attempt exceeded the RSS gate; use a new run_id"
            )


def _reject_locomo_run_hard_breaches(
    run_dir: Path,
    *,
    limits: WorkerProcessLimits,
) -> None:
    if not run_dir.is_dir():
        return
    resource_root = _locomo_run_child(run_dir, "resources", "conversations")
    paths = sorted(resource_root.glob("*.json"))
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("LoCoMo worker resource receipt escapes the run")
        record = _bootstrap_mapping(read_json(path), field="worker resource receipt")
        maximum = record.get("max_rss_bytes")
        if (type(maximum) is int and maximum > limits.max_rss_bytes) or record.get(
            "termination_reason"
        ) == "rss_limit":
            raise MemoryError(
                "A prior LoCoMo worker attempt exceeded the RSS gate; use a new run_id"
            )


def _locomo_run_child(run_dir: Path, *parts: str) -> Path:
    root = run_dir.resolve()
    current = root
    for part in parts:
        raw = Path(part)
        if raw.is_absolute() or len(raw.parts) != 1 or part in {"", ".", ".."}:
            raise ValueError("LoCoMo artifact path has an unsafe component")
        current /= part
        if current.is_symlink():
            raise ValueError("LoCoMo artifact path must not traverse a symlink")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("LoCoMo artifact path escapes the run directory")
    return resolved


def _locomo_attempt_child(attempt_dir: Path, *parts: str) -> Path:
    if attempt_dir.is_symlink():
        raise ValueError("LoCoMo worker attempt must not be a symlink")
    root = attempt_dir.resolve()
    current = root
    for part in parts:
        raw = Path(part)
        if raw.is_absolute() or len(raw.parts) != 1 or part in {"", ".", ".."}:
            raise ValueError("LoCoMo worker attempt path has an unsafe component")
        current /= part
        if current.is_symlink():
            raise ValueError("LoCoMo worker attempt path must not traverse a symlink")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("LoCoMo worker attempt path escapes its directory")
    return resolved


def _worker_attempt_spec_matches(
    spec: dict[str, object],
    work: "LoCoMoConversationWork",
    *,
    attempt_dir: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
) -> bool:
    expected = _build_locomo_worker_spec(
        work,
        attempt_dir=attempt_dir,
        retrieval_config=retrieval_config,
        answer_model_config=answer_model_config,
        judge_model_config=judge_model_config,
        reused_sources=[],
    )
    observed = dict(spec)
    for field in ("parent_pid", "reused_question_sources"):
        observed.pop(field, None)
        expected.pop(field, None)
    return observed == expected


def _completed_worker_attempt_evidence(
    work: "LoCoMoConversationWork",
    *,
    attempt_dir: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    limits: WorkerProcessLimits,
    question_dir: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]] | None:
    spec_path = _locomo_attempt_child(attempt_dir, "spec.json")
    monitor_path = _locomo_attempt_child(attempt_dir, "monitor.json")
    raw_resource_path = _locomo_attempt_child(attempt_dir, "worker-receipt.json")
    worker_identity_path = _locomo_attempt_child(attempt_dir, "worker.json")
    if not all(
        path.is_file()
        for path in (spec_path, monitor_path, raw_resource_path, worker_identity_path)
    ):
        return None
    try:
        spec = _bootstrap_mapping(read_json(spec_path), field="worker spec")
        monitor = _bootstrap_mapping(read_json(monitor_path), field="worker monitor")
        raw_resource = _bootstrap_mapping(read_json(raw_resource_path), field="worker receipt")
        identity = _bootstrap_mapping(read_json(worker_identity_path), field="worker identity")
    except (OSError, ValueError):
        return None
    if not _worker_attempt_spec_matches(
        spec,
        work,
        attempt_dir=attempt_dir,
        retrieval_config=retrieval_config,
        answer_model_config=answer_model_config,
        judge_model_config=judge_model_config,
    ):
        return None
    monitor_pid = monitor.get("pid")
    observed = monitor.get("observed_max_rss_bytes")
    reported = raw_resource.get("max_rss_bytes")
    reranker_warmup_ms = raw_resource.get("reranker_warmup_ms")
    completed_checkpoints = _worker_question_checkpoint_files(
        question_dir,
        conversation_id=work.conversation.sample_id,
        expected_question_ids=work.question_ids,
    )
    checkpoint_sha256 = _worker_question_checkpoint_sha256(question_dir)
    if (
        type(monitor_pid) is not int
        or monitor_pid < 1
        or monitor.get("returncode") != 0
        or monitor.get("termination_reason") is not None
        or type(observed) is not int
        or observed < 0
        or raw_resource.get("status") != "completed"
        or raw_resource.get("conversation_id") != work.conversation.sample_id
        or raw_resource.get("parent_pid") != spec.get("parent_pid")
        or raw_resource.get("pid") != monitor_pid
        or identity.get("schema_version") != 1
        or identity.get("pid") != monitor_pid
        or identity.get("parent_pid") != spec.get("parent_pid")
        or identity.get("spec_sha256") != file_sha256(spec_path)
        or raw_resource.get("completed_question_checkpoints") != completed_checkpoints
        or raw_resource.get("question_checkpoint_sha256") != checkpoint_sha256
        or type(reported) is not int
        or reported < 1
        or not _valid_nonnegative_number(reranker_warmup_ms)
        or max(observed, reported) > limits.max_rss_bytes
    ):
        return None
    return spec, monitor, raw_resource


def _valid_worker_publish_marker(
    work: "LoCoMoConversationWork",
    *,
    attempt_dir: Path,
    question_dir: Path,
    spec: dict[str, object],
) -> dict[str, object] | None:
    marker_path = _locomo_attempt_child(attempt_dir, "publish.json")
    if not marker_path.is_file():
        return None
    try:
        marker = _bootstrap_mapping(read_json(marker_path), field="worker publish marker")
    except (OSError, ValueError):
        return None
    attempt = int(attempt_dir.name.removeprefix("attempt-"))
    if (
        marker.get("schema_version") != 1
        or marker.get("conversation_id") != work.conversation.sample_id
        or marker.get("attempt") != attempt
        or marker.get("question_ids") != list(work.question_ids)
        or marker.get("question_checkpoint_sha256")
        != _worker_question_checkpoint_sha256(question_dir)
        or marker.get("run_manifest_sha256") != spec.get("run_manifest_sha256")
        or marker.get("spec_sha256") != file_sha256(_locomo_attempt_child(attempt_dir, "spec.json"))
        or marker.get("monitor_sha256")
        != file_sha256(_locomo_attempt_child(attempt_dir, "monitor.json"))
        or marker.get("worker_receipt_sha256")
        != file_sha256(_locomo_attempt_child(attempt_dir, "worker-receipt.json"))
    ):
        return None
    return marker


def _recover_completed_locomo_worker_attempt(
    work: "LoCoMoConversationWork",
    *,
    worker_root: Path,
    canonical_question_dir: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    limits: WorkerProcessLimits,
) -> bool:
    for attempt_dir in _locomo_worker_attempt_dirs(worker_root):
        attempt = int(attempt_dir.name.removeprefix("attempt-"))
        resource_path = _locomo_worker_resource_path(work, attempt=attempt)
        if resource_path.exists():
            continue
        staged_question_dir = _locomo_attempt_child(
            attempt_dir,
            "run",
            "checkpoints",
            "questions",
            work.conversation.sample_id,
        )
        try:
            _validate_worker_question_inventory(
                staged_question_dir,
                conversation_id=work.conversation.sample_id,
                expected_question_ids=work.question_ids,
            )
        except (FileNotFoundError, ValueError):
            continue
        evidence = _completed_worker_attempt_evidence(
            work,
            attempt_dir=attempt_dir,
            retrieval_config=retrieval_config,
            answer_model_config=answer_model_config,
            judge_model_config=judge_model_config,
            limits=limits,
            question_dir=staged_question_dir,
        )
        if evidence is None:
            continue
        spec, monitor, raw_resource = evidence
        publish_marker_path = _locomo_attempt_child(attempt_dir, "publish.json")
        if not publish_marker_path.exists():
            write_json_exclusive(
                publish_marker_path,
                {
                    "schema_version": 1,
                    "conversation_id": work.conversation.sample_id,
                    "attempt": attempt,
                    "question_ids": list(work.question_ids),
                    "question_checkpoint_sha256": _worker_question_checkpoint_sha256(
                        staged_question_dir
                    ),
                    "run_manifest_sha256": spec["run_manifest_sha256"],
                    "spec_sha256": file_sha256(_locomo_attempt_child(attempt_dir, "spec.json")),
                    "monitor_sha256": file_sha256(
                        _locomo_attempt_child(attempt_dir, "monitor.json")
                    ),
                    "worker_receipt_sha256": file_sha256(
                        _locomo_attempt_child(attempt_dir, "worker-receipt.json")
                    ),
                },
            )
        marker = _valid_worker_publish_marker(
            work,
            attempt_dir=attempt_dir,
            question_dir=staged_question_dir,
            spec=spec,
        )
        if marker is None:
            continue
        observed = cast(int, monitor["observed_max_rss_bytes"])
        reported = cast(int, raw_resource["max_rss_bytes"])
        completed_checkpoints = _worker_question_checkpoint_files(
            staged_question_dir,
            conversation_id=work.conversation.sample_id,
            expected_question_ids=work.question_ids,
        )
        worker_corpus_dir = _locomo_attempt_child(attempt_dir, "run", "corpus")
        _remove_worker_corpus_copy(worker_corpus_dir, attempt_dir=attempt_dir)
        canonical_question_dir.parent.mkdir(parents=True, exist_ok=True)
        staged_question_dir.rename(canonical_question_dir)
        write_json_exclusive(
            resource_path,
            {
                "schema_version": 1,
                "conversation_id": work.conversation.sample_id,
                "attempt": attempt,
                "accepted": True,
                "worker_started": True,
                "status": "completed",
                "recovered_before_publish": True,
                "parent_pid": spec["parent_pid"],
                "worker_pid": monitor["pid"],
                "returncode": 0,
                "termination_reason": None,
                "observed_max_rss_bytes": observed,
                "reported_max_rss_bytes": reported,
                "max_rss_bytes": max(observed, reported),
                "rss_limit_bytes": limits.max_rss_bytes,
                "wall_time_seconds": monitor.get("wall_time_seconds"),
                "reranker_warmup_ms": raw_resource.get("reranker_warmup_ms"),
                "run_manifest_sha256": spec["run_manifest_sha256"],
                "spec_sha256": file_sha256(_locomo_attempt_child(attempt_dir, "spec.json")),
                "expected_question_ids": list(work.question_ids),
                "question_checkpoint_sha256": marker["question_checkpoint_sha256"],
                "completed_question_checkpoints": completed_checkpoints,
                "publish_marker_sha256": file_sha256(
                    _locomo_attempt_child(attempt_dir, "publish.json")
                ),
                "reused_question_sources": spec.get("reused_question_sources", []),
            },
        )
        return True
    return False


def _reuse_locomo_worker_checkpoints(
    work: "LoCoMoConversationWork",
    *,
    worker_root: Path,
    target_question_dir: Path,
    current_attempt: int,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
    limits: WorkerProcessLimits,
) -> list[dict[str, object]]:
    expected_ids = set(work.question_ids)
    reused_ids: set[str] = set()
    reused_journal_ids: set[str] = set()
    sources: list[dict[str, object]] = []
    for attempt_dir in _locomo_worker_attempt_dirs(worker_root):
        attempt = int(attempt_dir.name.removeprefix("attempt-"))
        if attempt >= current_attempt:
            continue
        resource_path = _locomo_worker_resource_path(work, attempt=attempt)
        spec_path = _locomo_attempt_child(attempt_dir, "spec.json")
        source_question_dir = _locomo_attempt_child(
            attempt_dir,
            "run",
            "checkpoints",
            "questions",
            work.conversation.sample_id,
        )
        has_journal_evidence = (
            _preflight_locomo_worker_attempt_journals(
                source_question_dir,
                question_ids=work.question_ids,
            )
            if source_question_dir.is_dir()
            else False
        )
        if (
            not resource_path.is_file()
            or not spec_path.is_file()
            or not source_question_dir.is_dir()
        ):
            if has_journal_evidence:
                raise ValueError(
                    "LoCoMo worker resume cannot bind model attempt journals to a worker receipt"
                )
            continue
        try:
            receipt = _bootstrap_mapping(read_json(resource_path), field="worker resource receipt")
            spec = _bootstrap_mapping(read_json(spec_path), field="worker spec")
        except (OSError, ValueError) as error:
            if has_journal_evidence:
                raise ValueError(
                    "LoCoMo worker resume cannot bind model attempt journals to immutable "
                    "worker evidence"
                ) from error
            continue
        source_max_rss = receipt.get("max_rss_bytes")
        raw_checkpoint_files = receipt.get("completed_question_checkpoints")
        reusable = not (
            receipt.get("accepted") is not False
            or receipt.get("conversation_id") != work.conversation.sample_id
            or receipt.get("attempt") != attempt
            or receipt.get("run_manifest_sha256") != file_sha256(work.run_dir / "manifest.json")
            or receipt.get("expected_question_ids") != list(work.question_ids)
            or receipt.get("rss_limit_bytes") != limits.max_rss_bytes
            or type(source_max_rss) is not int
            or not 0 <= source_max_rss <= limits.max_rss_bytes
            or receipt.get("termination_reason") == "rss_limit"
            or not isinstance(raw_checkpoint_files, dict)
            or receipt.get("question_checkpoint_sha256")
            != _worker_question_checkpoint_sha256(source_question_dir)
            or receipt.get("spec_sha256") != file_sha256(spec_path)
            or not _worker_attempt_spec_matches(
                spec,
                work,
                attempt_dir=attempt_dir,
                retrieval_config=retrieval_config,
                answer_model_config=answer_model_config,
                judge_model_config=judge_model_config,
            )
        )
        if not reusable:
            if has_journal_evidence:
                raise ValueError(
                    "LoCoMo worker resume cannot bind model attempt journals to immutable "
                    "worker evidence"
                )
            continue
        checkpoint_files = cast(dict[str, object], raw_checkpoint_files)
        copied_journals = _copy_locomo_worker_attempt_journals(
            source_question_dir,
            target_question_dir,
            question_ids=work.question_ids,
            excluded_question_ids=reused_ids | reused_journal_ids,
        )
        reused_journal_ids.update(copied_journals)
        copied: list[str] = []
        for path in sorted(source_question_dir.glob("*.json")):
            if (
                path.stem not in expected_ids
                or path.stem in reused_ids
                or (path.stem in reused_journal_ids and path.stem not in copied_journals)
            ):
                continue
            if checkpoint_files.get(path.stem) != file_sha256(path):
                continue
            try:
                record = _bootstrap_mapping(read_json(path), field="question checkpoint")
            except (OSError, ValueError):
                continue
            if (
                record.get("sample_id") != work.conversation.sample_id
                or record.get("question_id") != path.stem
            ):
                continue
            target_question_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_question_dir / path.name)
            reused_ids.add(path.stem)
            copied.append(path.stem)
        if copied or copied_journals:
            sources.append(
                {
                    "attempt": attempt,
                    "question_ids": copied,
                    "attempt_journal_question_ids": copied_journals,
                    "question_checkpoint_sha256": _worker_question_checkpoint_sha256(
                        source_question_dir
                    ),
                    "resource_receipt_sha256": file_sha256(resource_path),
                }
            )
        if reused_ids | reused_journal_ids == expected_ids:
            break
    return sources


def _copy_locomo_worker_attempt_journals(
    source_question_dir: Path,
    target_question_dir: Path,
    *,
    question_ids: tuple[str, ...],
    excluded_question_ids: set[str],
) -> list[str]:
    copied: list[str] = []
    source_journal_root = source_question_dir / ".attempt-journal"
    for question_id in question_ids:
        if question_id in excluded_question_ids:
            continue
        source_journal = source_journal_root / question_id
        if not source_journal.is_dir():
            continue
        try:
            snapshot = validate_model_attempt_journal(
                source_journal,
                question_id=question_id,
            )
        except (OSError, ValueError) as error:
            raise ValueError(
                f"LoCoMo worker resume found an invalid model attempt journal: {question_id}"
            ) from error
        entries = snapshot.get("entries")
        if not isinstance(entries, list) or not entries:
            continue
        target_journal = target_question_dir / ".attempt-journal" / question_id
        target_journal.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_journal, target_journal)
        validate_model_attempt_journal(target_journal, question_id=question_id)
        copied.append(question_id)
    return copied


def _preflight_locomo_worker_attempt_journals(
    source_question_dir: Path,
    *,
    question_ids: tuple[str, ...],
) -> bool:
    journal_root = source_question_dir / ".attempt-journal"
    if not journal_root.exists():
        return False
    if journal_root.is_symlink() or not journal_root.is_dir():
        raise ValueError("LoCoMo worker model attempt journal root is unsafe")
    expected = set(question_ids)
    children = sorted(journal_root.iterdir())
    if any(
        child.is_symlink()
        or not child.is_dir()
        or child.name not in expected
        or not child.resolve().is_relative_to(journal_root.resolve())
        for child in children
    ):
        raise ValueError("LoCoMo worker model attempt journal inventory is invalid")
    has_evidence = False
    for child in children:
        try:
            snapshot = validate_model_attempt_journal(child, question_id=child.name)
        except (OSError, ValueError) as error:
            raise ValueError(
                f"LoCoMo worker resume found an invalid model attempt journal: {child.name}"
            ) from error
        entries = snapshot.get("entries")
        if not isinstance(entries, list):
            raise ValueError("LoCoMo worker model attempt journal entries are invalid")
        has_evidence = has_evidence or bool(entries)
    return has_evidence


def _recover_locomo_worker_receipt(
    work: "LoCoMoConversationWork",
    *,
    limits: WorkerProcessLimits,
    canonical_question_dir: Path,
    retrieval_config: dict[str, object],
    answer_model_config: dict[str, object] | None,
    judge_model_config: dict[str, object] | None,
) -> None:
    resource_dir = _locomo_run_child(work.run_dir, "resources", "conversations")
    resource_paths = sorted(resource_dir.glob(f"{work.conversation.sample_id}.attempt-*.json"))
    if any(
        path.is_symlink() or not path.resolve().is_relative_to(work.run_dir.resolve())
        for path in resource_paths
    ):
        raise ValueError("LoCoMo worker resource receipt escapes the run")
    existing = [
        (path, _bootstrap_mapping(read_json(path), field="worker resource receipt"))
        for path in resource_paths
    ]
    accepted = [(path, record) for path, record in existing if record.get("accepted") is True]
    worker_root = _locomo_run_child(work.run_dir, "workers", work.conversation.sample_id)
    if accepted:
        if len(accepted) != 1:
            raise ValueError("Published LoCoMo checkpoints have multiple accepted receipts")
        path, record = accepted[0]
        attempt = record.get("attempt")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Accepted LoCoMo worker receipt has an invalid attempt")
        attempt_dir = _locomo_run_child(
            work.run_dir,
            "workers",
            work.conversation.sample_id,
            f"attempt-{attempt}",
        )
        evidence = _completed_worker_attempt_evidence(
            work,
            attempt_dir=attempt_dir,
            retrieval_config=retrieval_config,
            answer_model_config=answer_model_config,
            judge_model_config=judge_model_config,
            limits=limits,
            question_dir=canonical_question_dir,
        )
        if evidence is None:
            raise ValueError("Accepted LoCoMo worker receipt has no valid process evidence")
        spec, monitor, raw_resource = evidence
        marker = _valid_worker_publish_marker(
            work,
            attempt_dir=attempt_dir,
            question_dir=canonical_question_dir,
            spec=spec,
        )
        observed = monitor.get("observed_max_rss_bytes")
        reported = raw_resource.get("max_rss_bytes")
        reranker_warmup_ms = raw_resource.get("reranker_warmup_ms")
        worker_pid = monitor.get("pid")
        parent_pid = record.get("parent_pid")
        if (
            marker is None
            or path.name != f"{work.conversation.sample_id}.attempt-{attempt}.json"
            or record.get("schema_version") != 1
            or record.get("conversation_id") != work.conversation.sample_id
            or record.get("status") != "completed"
            or record.get("returncode") != 0
            or record.get("termination_reason") is not None
            or record.get("worker_pid") != worker_pid
            or parent_pid != spec.get("parent_pid")
            or type(parent_pid) is not int
            or parent_pid < 1
            or parent_pid == worker_pid
            or record.get("observed_max_rss_bytes") != observed
            or record.get("reported_max_rss_bytes") != reported
            or record.get("reranker_warmup_ms") != reranker_warmup_ms
            or record.get("max_rss_bytes") != max(cast(int, observed), cast(int, reported))
            or record.get("rss_limit_bytes") != limits.max_rss_bytes
            or record.get("run_manifest_sha256") != spec.get("run_manifest_sha256")
            or record.get("spec_sha256")
            != file_sha256(_locomo_attempt_child(attempt_dir, "spec.json"))
            or record.get("expected_question_ids") != list(work.question_ids)
            or record.get("question_checkpoint_sha256") != marker.get("question_checkpoint_sha256")
            or record.get("completed_question_checkpoints")
            != _worker_question_checkpoint_files(
                canonical_question_dir,
                conversation_id=work.conversation.sample_id,
                expected_question_ids=work.question_ids,
            )
            or record.get("publish_marker_sha256")
            != file_sha256(_locomo_attempt_child(attempt_dir, "publish.json"))
            or record.get("reused_question_sources") != spec.get("reused_question_sources", [])
        ):
            raise ValueError("Accepted LoCoMo worker receipt does not match its evidence")
        return

    for attempt_dir in _locomo_worker_attempt_dirs(worker_root):
        evidence = _completed_worker_attempt_evidence(
            work,
            attempt_dir=attempt_dir,
            retrieval_config=retrieval_config,
            answer_model_config=answer_model_config,
            judge_model_config=judge_model_config,
            limits=limits,
            question_dir=canonical_question_dir,
        )
        if evidence is None:
            continue
        spec, monitor, raw_resource = evidence
        marker = _valid_worker_publish_marker(
            work,
            attempt_dir=attempt_dir,
            question_dir=canonical_question_dir,
            spec=spec,
        )
        if marker is None:
            continue
        observed = cast(int, monitor["observed_max_rss_bytes"])
        reported = cast(int, raw_resource["max_rss_bytes"])
        attempt = int(attempt_dir.name.removeprefix("attempt-"))
        write_json_exclusive(
            _locomo_worker_resource_path(work, attempt=attempt),
            {
                "schema_version": 1,
                "conversation_id": work.conversation.sample_id,
                "attempt": attempt,
                "accepted": True,
                "worker_started": True,
                "status": "completed",
                "recovered_after_publish": True,
                "parent_pid": spec["parent_pid"],
                "worker_pid": monitor.get("pid"),
                "returncode": 0,
                "termination_reason": None,
                "observed_max_rss_bytes": observed,
                "reported_max_rss_bytes": reported,
                "max_rss_bytes": max(observed, reported),
                "rss_limit_bytes": limits.max_rss_bytes,
                "reranker_warmup_ms": raw_resource.get("reranker_warmup_ms"),
                "run_manifest_sha256": spec.get("run_manifest_sha256"),
                "spec_sha256": file_sha256(attempt_dir / "spec.json"),
                "expected_question_ids": list(work.question_ids),
                "question_checkpoint_sha256": marker["question_checkpoint_sha256"],
                "completed_question_checkpoints": _worker_question_checkpoint_files(
                    canonical_question_dir,
                    conversation_id=work.conversation.sample_id,
                    expected_question_ids=work.question_ids,
                ),
                "publish_marker_sha256": file_sha256(attempt_dir / "publish.json"),
                "reused_question_sources": spec.get("reused_question_sources", []),
            },
        )
        return
    raise ValueError("Published LoCoMo question checkpoints have no accepted worker receipt")


def _next_worker_attempt(worker_root: Path) -> int:
    attempts = [
        int(path.name.removeprefix("attempt-")) for path in _locomo_worker_attempt_dirs(worker_root)
    ]
    return max(attempts, default=0) + 1


def _bootstrap_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"LoCoMo {field} must be an object")
    return cast(dict[str, object], value)


def _bootstrap_string(value: dict[str, object], key: str, *, field: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"LoCoMo {field} has no {key}")
    return item


def _positive_environment_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_environment_float(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _valid_nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def create_application(root: Path) -> CodeCairnApplication:
    resolved = root.resolve()
    retrieval = create_retrieval_providers()
    return CodeCairnApplication(
        runtime=create_runtime(resolved, retrieval=retrieval),
        operations=_LocalOperations(resolved, retrieval=retrieval),
    )


def _provider_status(*, retrieval: RetrievalProviders) -> dict[str, object]:
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
        "retrieval": retrieval.public_config,
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
