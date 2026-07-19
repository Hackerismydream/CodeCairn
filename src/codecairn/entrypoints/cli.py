from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from codecairn.service.runtime import MemoryRuntime

RuntimeFactory = Callable[[Path], MemoryRuntime]


def build_app(runtime_factory: RuntimeFactory) -> typer.Typer:
    """Build the CLI against an injected runtime composition function."""
    app = typer.Typer(
        name="codecairn",
        help="Auditable long-term memory runtime for coding agents.",
        no_args_is_help=True,
    )

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
        result = runtime_factory(root).import_session(source, repo_key=repo_key)
        typer.echo(json.dumps(asdict(result), sort_keys=True))

    @app.command("list")
    def list_memories_command(
        repo_key: Annotated[str, typer.Option("--repo-key")],
        root: Annotated[Path, typer.Option("--root")] = Path(".codecairn"),
    ) -> None:
        """List durable memories in one repository namespace."""
        memories = runtime_factory(root).list_memories(repo_key=repo_key)
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
        result = runtime_factory(root).recall(task, repo_key=repo_key, limit=limit)
        if output_format == "markdown":
            typer.echo(result.markdown, nl=False)
            return
        if output_format != "json":
            raise typer.BadParameter("format must be 'json' or 'markdown'", param_hint="--format")
        typer.echo(json.dumps(asdict(result), sort_keys=True))

    return app
