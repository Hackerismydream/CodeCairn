from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from codecairn.service.application import (
    CodeCairnApplication,
    EvaluationReportRequest,
    EvaluationRunRequest,
    EvaluationSuite,
    EvidenceBundleBuildRequest,
    LoCoMoAblationRequest,
    LoCoMoCorpusBuildRequest,
    LoCoMoQueryVectorBuildRequest,
)

ApplicationFactory = Callable[[Path], CodeCairnApplication]


def build_app(application_factory: ApplicationFactory) -> typer.Typer:
    """Build the CLI against an injected runtime composition function."""
    app = typer.Typer(
        name="codecairn",
        help="Auditable long-term memory runtime for coding agents.",
        no_args_is_help=True,
    )
    evaluation_app = typer.Typer(help="Run or report immutable evaluation artifacts.")
    evidence_app = typer.Typer(help="Build or verify a public benchmark evidence bundle.")
    app.add_typer(evaluation_app, name="eval")
    app.add_typer(evidence_app, name="evidence")

    @app.command("import")
    def import_session_command(
        source: Annotated[
            Path,
            typer.Argument(exists=True, dir_okay=False, readable=True),
        ],
        repo_key: Annotated[str, typer.Option("--repo-key")],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
    ) -> None:
        """Import one supported agent session and persist evidence-backed memories."""
        result = application_factory(root).import_session(source, repo_key=repo_key)
        typer.echo(json.dumps(asdict(result), sort_keys=True))

    @app.command("list")
    def list_memories_command(
        repo_key: Annotated[str, typer.Option("--repo-key")],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
    ) -> None:
        """List durable memories in one repository namespace."""
        memories = application_factory(root).list_memories(repo_key=repo_key)
        typer.echo(json.dumps([asdict(memory) for memory in memories], sort_keys=True))

    @app.command("recall")
    def recall_command(
        task: Annotated[str, typer.Argument(help="Current coding task")],
        repo_key: Annotated[str, typer.Option("--repo-key")],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
        limit: Annotated[int, typer.Option("--limit", min=1, max=20)] = 5,
        output_format: Annotated[str, typer.Option("--format")] = "json",
    ) -> None:
        """Generate task-shaped Recall Context from hybrid candidates."""
        result = application_factory(root).recall(task, repo_key=repo_key, limit=limit)
        if output_format == "markdown":
            typer.echo(result.markdown, nl=False)
            return
        if output_format != "json":
            raise typer.BadParameter("format must be 'json' or 'markdown'", param_hint="--format")
        typer.echo(json.dumps(asdict(result), sort_keys=True))

    @evaluation_app.command("run")
    def evaluation_run_command(
        suite: Annotated[str, typer.Argument(help="locomo, retrieval, recovery, or coding")],
        input_path: Annotated[
            Path,
            typer.Argument(exists=True, readable=True),
        ],
        run_id: Annotated[str, typer.Option("--run-id")],
        repository_commit: Annotated[str, typer.Option("--repository-commit")],
        output_root: Annotated[Path, typer.Option("--output-root")] = Path("artifacts"),
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
        mode: Annotated[str, typer.Option("--mode")] = "full",
        model: Annotated[str | None, typer.Option("--model")] = None,
        judge_model: Annotated[str | None, typer.Option("--judge-model")] = None,
        max_workers: Annotated[int, typer.Option("--max-workers", min=1)] = 1,
        resume: Annotated[bool, typer.Option("--resume")] = False,
        execution_phase: Annotated[str, typer.Option("--execution-phase")] = "all",
        question_set: Annotated[
            Path | None,
            typer.Option("--question-set", exists=True, dir_okay=False, readable=True),
        ] = None,
        corpus: Annotated[
            Path | None,
            typer.Option("--corpus", exists=True, file_okay=False, readable=True),
        ] = None,
        query_vectors: Annotated[
            Path | None,
            typer.Option("--query-vectors", exists=True, file_okay=False, readable=True),
        ] = None,
    ) -> None:
        """Execute one immutable evaluation suite run."""
        if mode not in {"full", "smoke", "retrieval"}:
            raise typer.BadParameter(
                "mode must be 'full', 'smoke', or 'retrieval'", param_hint="--mode"
            )
        if execution_phase not in {"all", "ingest", "questions"}:
            raise typer.BadParameter(
                "execution-phase must be 'all', 'ingest', or 'questions'",
                param_hint="--execution-phase",
            )
        if suite != "locomo" and execution_phase != "all":
            raise typer.BadParameter(
                "execution-phase is supported only for LoCoMo",
                param_hint="--execution-phase",
            )
        if suite != "locomo" and (corpus is not None or query_vectors is not None):
            raise typer.BadParameter(
                "corpus and query-vectors are supported only for LoCoMo",
                param_hint="--corpus",
            )
        if corpus is not None and execution_phase == "all":
            execution_phase = "questions"
        result = application_factory(root).run_evaluation(
            EvaluationRunRequest(
                suite=_evaluation_suite(suite),
                input_path=input_path,
                output_root=output_root,
                run_id=run_id,
                repository_commit=repository_commit,
                mode=cast(Literal["full", "smoke", "retrieval"], mode),
                model=model,
                judge_model=judge_model,
                max_workers=max_workers,
                resume=resume,
                question_set_path=question_set,
                execution_phase=cast(Literal["all", "ingest", "questions"], execution_phase),
                corpus_path=corpus,
                query_vectors_path=query_vectors,
            )
        )
        typer.echo(json.dumps(result, sort_keys=True))

    @evaluation_app.command("build-locomo-corpus")
    def build_locomo_corpus_command(
        input_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        corpus_id: Annotated[str, typer.Option("--corpus-id")],
        repository_commit: Annotated[str, typer.Option("--repository-commit")],
        output_root: Annotated[Path, typer.Option("--output-root")],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
        resume: Annotated[bool, typer.Option("--resume")] = False,
        expected_dataset_sha256: Annotated[
            str | None, typer.Option("--expected-dataset-sha256")
        ] = None,
    ) -> None:
        """Build and atomically publish one reusable LoCoMo corpus."""
        result = application_factory(root).build_locomo_corpus(
            LoCoMoCorpusBuildRequest(
                input_path=input_path,
                output_root=output_root,
                corpus_id=corpus_id,
                repository_commit=repository_commit,
                resume=resume,
                expected_dataset_sha256=expected_dataset_sha256,
            )
        )
        typer.echo(json.dumps(result, sort_keys=True))

    @evaluation_app.command("build-locomo-query-vectors")
    def build_locomo_query_vectors_command(
        input_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        vector_set_id: Annotated[str, typer.Option("--vector-set-id")],
        output_root: Annotated[Path, typer.Option("--output-root")],
        question_set: Annotated[
            Path | None,
            typer.Option("--question-set", exists=True, dir_okay=False, readable=True),
        ] = None,
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
        expected_dataset_sha256: Annotated[
            str | None, typer.Option("--expected-dataset-sha256")
        ] = None,
    ) -> None:
        """Freeze query vectors for one LoCoMo question selection."""
        result = application_factory(root).build_locomo_query_vectors(
            LoCoMoQueryVectorBuildRequest(
                input_path=input_path,
                output_root=output_root,
                vector_set_id=vector_set_id,
                question_set_path=question_set,
                expected_dataset_sha256=expected_dataset_sha256,
            )
        )
        typer.echo(json.dumps(result, sort_keys=True))

    @evaluation_app.command("report")
    def evaluation_report_command(
        suite: Annotated[str, typer.Argument(help="locomo, retrieval, recovery, or coding")],
        run_dir: Annotated[
            Path,
            typer.Argument(exists=True, file_okay=False, readable=True),
        ],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
    ) -> None:
        """Read one existing evaluation run without mutating it."""
        result = application_factory(root).report_evaluation(
            EvaluationReportRequest(
                suite=_evaluation_suite(suite),
                run_dir=run_dir,
            )
        )
        typer.echo(json.dumps(result, sort_keys=True))

    @evaluation_app.command("compare-locomo")
    def compare_locomo_command(
        question_set: Annotated[
            Path,
            typer.Argument(exists=True, dir_okay=False, readable=True),
        ],
        episode_only_run: Annotated[
            Path, typer.Option("--episode-only-run", exists=True, file_okay=False)
        ],
        hierarchy_no_neighbors_run: Annotated[
            Path,
            typer.Option("--hierarchy-no-neighbors-run", exists=True, file_okay=False),
        ],
        hierarchy_run: Annotated[
            Path, typer.Option("--hierarchy-run", exists=True, file_okay=False)
        ],
        output: Annotated[Path, typer.Option("--output")],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
    ) -> None:
        """Compare the frozen three-layer LoCoMo diagnostic and evaluate its gate."""
        result = application_factory(root).build_locomo_ablation_report(
            LoCoMoAblationRequest(
                question_set_path=question_set,
                episode_only_run=episode_only_run,
                hierarchy_no_neighbors_run=hierarchy_no_neighbors_run,
                hierarchy_run=hierarchy_run,
                output_path=output,
            )
        )
        typer.echo(json.dumps(result, sort_keys=True))

    @evidence_app.command("build")
    def evidence_build_command(
        bundle_id: Annotated[str, typer.Option("--bundle-id")],
        locomo_run: Annotated[Path, typer.Option("--locomo-run", exists=True, file_okay=False)],
        retrieval_run: Annotated[
            Path, typer.Option("--retrieval-run", exists=True, file_okay=False)
        ],
        recovery_run: Annotated[Path, typer.Option("--recovery-run", exists=True, file_okay=False)],
        coding_run: Annotated[Path, typer.Option("--coding-run", exists=True, file_okay=False)],
        quality_junit: Annotated[
            Path, typer.Option("--quality-junit", exists=True, dir_okay=False)
        ],
        quality_coverage: Annotated[
            Path, typer.Option("--quality-coverage", exists=True, dir_okay=False)
        ],
        generator_commit: Annotated[str, typer.Option("--generator-commit")],
        output_root: Annotated[Path, typer.Option("--output-root")] = Path("evidence"),
        repository_root: Annotated[Path, typer.Option("--repository-root")] = Path("."),
    ) -> None:
        """Generate immutable metrics and recruiting copy from completed artifacts."""
        result = application_factory(Path(".codecairn")).build_evidence_bundle(
            EvidenceBundleBuildRequest(
                bundle_id=bundle_id,
                output_root=output_root,
                locomo_run_dir=locomo_run,
                retrieval_run_dir=retrieval_run,
                recovery_run_dir=recovery_run,
                coding_run_dir=coding_run,
                quality_junit_path=quality_junit,
                quality_coverage_path=quality_coverage,
                repository_root=repository_root,
                generator_commit=generator_commit,
            )
        )
        typer.echo(json.dumps(result, sort_keys=True))

    @evidence_app.command("verify")
    def evidence_verify_command(
        bundle_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    ) -> None:
        """Recompute and verify one public evidence bundle without provider access."""
        result = application_factory(Path(".codecairn")).verify_evidence_bundle(bundle_dir)
        typer.echo(json.dumps(result, sort_keys=True))

    @app.command("doctor")
    def doctor_command(
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
    ) -> None:
        """Inspect durable truth, import state, index state, and providers."""
        typer.echo(json.dumps(application_factory(root).doctor(), sort_keys=True))

    return app


def _evaluation_suite(value: str) -> EvaluationSuite:
    if value not in {"locomo", "retrieval", "recovery", "coding"}:
        raise typer.BadParameter(
            "suite must be locomo, retrieval, recovery, or coding",
            param_hint="suite",
        )
    return cast(EvaluationSuite, value)
