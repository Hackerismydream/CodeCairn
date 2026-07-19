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
    app.add_typer(evaluation_app, name="eval")

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
        max_workers: Annotated[int, typer.Option("--max-workers", min=1)] = 1,
    ) -> None:
        """Execute one immutable evaluation suite run."""
        if mode not in {"full", "smoke"}:
            raise typer.BadParameter("mode must be 'full' or 'smoke'", param_hint="--mode")
        result = application_factory(root).run_evaluation(
            EvaluationRunRequest(
                suite=_evaluation_suite(suite),
                input_path=input_path,
                output_root=output_root,
                run_id=run_id,
                repository_commit=repository_commit,
                mode=cast(Literal["full", "smoke"], mode),
                model=model,
                max_workers=max_workers,
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
