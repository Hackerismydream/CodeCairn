from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, cast

import typer
from typer.core import TyperGroup

from codecairn.configuration import initialize_repository, resolve_runtime_config
from codecairn.memory.config import RetrievalConfig, RuntimeConfig, SemanticConfig
from codecairn.memory.errors import (
    ConfigurationError,
    IndexNotReady,
    ProviderConfigurationError,
)
from codecairn.memory.schema import MemoryType
from codecairn.service.application import (
    CodeCairnApplication,
    RememberRequest,
    import_response,
)


class ApplicationFactory(Protocol):
    def __call__(
        self,
        root: Path,
        *,
        repo_key: str | None = None,
        retrieval: RetrievalConfig | None = None,
        semantic: SemanticConfig | None = None,
    ) -> CodeCairnApplication: ...


PROVIDER_CONFIGURATION_EXIT_CODE = 2


class _FailClosedGroup(TyperGroup):
    """Render an unusable retrieval configuration as one actionable line."""

    # ctx stays Any because click.Context is not a declared dependency of this package.
    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except (ConfigurationError, IndexNotReady, ProviderConfigurationError) as error:
            typer.echo(f"codecairn: {error}", err=True)
            remediation = getattr(error, "remediation", "Run `codecairn doctor`.")
            typer.echo(f"hint: {remediation}", err=True)
            raise typer.Exit(code=PROVIDER_CONFIGURATION_EXIT_CODE) from None


def build_app(application_factory: ApplicationFactory) -> typer.Typer:
    """Build the CLI against an injected runtime composition function."""
    app = typer.Typer(
        name="codecairn",
        cls=_FailClosedGroup,
        help="Auditable long-term memory runtime for coding agents.",
        no_args_is_help=True,
    )
    evidence_app = typer.Typer(help="Build or verify a public benchmark evidence bundle.")
    index_app = typer.Typer(help="Operate the rebuildable search index.")
    memory_app = typer.Typer(help="Inspect and evolve durable memory.")
    namespace_app = typer.Typer(help="Export or reset one memory namespace.")
    app.add_typer(evidence_app, name="evidence")
    app.add_typer(index_app, name="index")
    app.add_typer(memory_app, name="memory")
    app.add_typer(namespace_app, name="namespace")

    @app.command("init")
    def init_command(
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        remote: Annotated[str | None, typer.Option("--remote")] = None,
        retrieval_profile: Annotated[
            Literal["dashscope", "fastembed"] | None,
            typer.Option("--retrieval-profile"),
        ] = None,
        semantic_profile: Annotated[str | None, typer.Option("--semantic-profile")] = None,
        prefetch: Annotated[bool, typer.Option("--prefetch")] = False,
        check_provider: Annotated[bool, typer.Option("--check-provider")] = False,
        force: Annotated[bool, typer.Option("--force")] = False,
    ) -> None:
        """Initialize one repository binding and validate its runtime."""
        resolved = initialize_repository(
            start=Path.cwd(),
            config_path=config,
            root=root,
            repo_key=repo_key,
            remote=remote,
            retrieval_profile=retrieval_profile,
            semantic_profile=semantic_profile,
            force=force,
        )
        application = _application(application_factory, resolved)
        live = check_provider or (prefetch and resolved.retrieval.profile == "fastembed")
        result = {
            "status": "initialized",
            "config": str(resolved.binding_path),
            "root": str(resolved.runtime_root),
            "repo_key": resolved.repo_key,
            "retrieval": resolved.retrieval.public_config,
            "semantic": resolved.semantic.profile,
            "provider_state": application.doctor(live=live)["providers"],
            "commands": {
                "import": "codecairn import <owned-session.jsonl>",
                "recall": 'codecairn recall "<task>"',
                "doctor": "codecairn doctor",
            },
            "agent_docs": {
                "AGENTS.md": "Use `codecairn recall` before repository work.",
                "CLAUDE.md": "Use `codecairn recall` before repository work.",
            },
        }
        typer.echo(json.dumps(result, sort_keys=True))

    @app.command("import")
    def import_session_command(
        source: Annotated[
            Path,
            typer.Argument(exists=True, dir_okay=False, readable=True),
        ],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
        index: Annotated[bool, typer.Option("--index/--no-index")] = True,
        finalize: Annotated[bool, typer.Option("--finalize")] = False,
    ) -> None:
        """Import one supported agent session and persist evidence-backed memories."""
        application, resolved = _resolve_application(
            application_factory,
            config=config,
            root=root,
            repo_key=repo_key,
        )
        outcome = application.import_session(
            source,
            repo_key=resolved.repo_key,
            index=index,
            boundary_kind="manual_finalize" if finalize else None,
        )
        typer.echo(json.dumps(import_response(outcome), sort_keys=True))

    @app.command("list")
    def list_memories_command(
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        """List durable memories in one repository namespace."""
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        memories = application.list_memories(repo_key=resolved.repo_key)
        typer.echo(json.dumps([asdict(memory) for memory in memories], sort_keys=True))

    @app.command("recall")
    def recall_command(
        task: Annotated[str, typer.Argument(help="Current coding task")],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
        limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
        workstream_key: Annotated[str | None, typer.Option("--workstream-key")] = None,
        include_superseded: Annotated[bool, typer.Option("--include-superseded")] = False,
        token_budget: Annotated[int, typer.Option("--token-budget", min=256, max=32_768)] = 8_192,
        output_format: Annotated[str, typer.Option("--format")] = "json",
    ) -> None:
        """Generate task-shaped Recall Context from hybrid candidates."""
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        result = application.recall(
            task,
            repo_key=resolved.repo_key,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )
        if output_format == "markdown":
            typer.echo(result.markdown, nl=False)
            return
        if output_format != "json":
            raise typer.BadParameter("format must be 'json' or 'markdown'", param_hint="--format")
        typer.echo(json.dumps(asdict(result), sort_keys=True))

    @app.command("process")
    def process_pending_command(
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
        worker_id: Annotated[str, typer.Option("--worker-id")] = "cli",
        max_jobs: Annotated[int, typer.Option("--max-jobs", min=1)] = 8,
        semantic: Annotated[bool, typer.Option("--semantic/--no-semantic")] = True,
        index: Annotated[bool, typer.Option("--index/--no-index")] = True,
        retry_failed: Annotated[bool, typer.Option("--retry-failed")] = False,
    ) -> None:
        """Process bounded semantic and index queues."""
        del retry_failed
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        report: dict[str, object] = {}
        if semantic:
            report["semantic"] = asdict(
                application.process_pending(worker_id=worker_id, max_jobs=max_jobs)
            )
        if index:
            report["index"] = asdict(application.sync_index(worker_id=worker_id, max_jobs=max_jobs))
        typer.echo(json.dumps(report, sort_keys=True))

    @app.command("remember")
    def remember_command(
        memory_type: Annotated[str, typer.Argument()],
        text: Annotated[str | None, typer.Argument()] = None,
        file: Annotated[Path | None, typer.Option("--file", dir_okay=False)] = None,
        stdin: Annotated[bool, typer.Option("--stdin")] = False,
        title: Annotated[str, typer.Option("--title")] = "Agent asserted memory",
        category: Annotated[str, typer.Option("--category")] = "other",
        subject_key: Annotated[str | None, typer.Option("--subject-key")] = None,
        source_fact_id: Annotated[list[str] | None, typer.Option("--source-fact-id")] = None,
        workstream_key: Annotated[str | None, typer.Option("--workstream-key")] = None,
        workstream_state: Annotated[
            Literal["open", "closed"], typer.Option("--workstream-state")
        ] = "open",
        goal: Annotated[str | None, typer.Option("--goal")] = None,
        next_step: Annotated[str | None, typer.Option("--next-step")] = None,
        terminal_outcome: Annotated[str | None, typer.Option("--terminal-outcome")] = None,
        tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        """Store Repository Knowledge, Working Preference, or Work State."""
        content = _large_text(text=text, file=file, stdin=stdin)
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        memory = application.remember_direct(
            RememberRequest(
                memory_type=cast(MemoryType, memory_type),
                repo_key=resolved.repo_key,
                title=title,
                content=content,
                category=category,
                subject_key=subject_key,
                source_fact_ids=tuple(source_fact_id or ()),
                workstream_key=workstream_key,
                workstream_state=workstream_state,
                goal=goal,
                next_step=next_step,
                terminal_outcome=terminal_outcome,
                tags=tuple(tag or ()),
            )
        )
        typer.echo(json.dumps(asdict(memory), sort_keys=True))

    @memory_app.command("show")
    def memory_show_command(
        memory_id: Annotated[str, typer.Argument()],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        memory = application.show_memory(
            repo_key=resolved.repo_key,
            memory_id=memory_id,
        )
        if memory is None:
            raise typer.BadParameter("memory ID was not found", param_hint="memory_id")
        typer.echo(json.dumps(asdict(memory), sort_keys=True))

    @memory_app.command("history")
    def memory_history_command(
        memory_id: Annotated[str, typer.Argument()],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        history = application.memory_history(
            repo_key=resolved.repo_key,
            memory_id=memory_id,
        )
        typer.echo(json.dumps(asdict(history), sort_keys=True))

    @memory_app.command("supersede")
    def memory_supersede_command(
        predecessor_id: Annotated[str, typer.Argument()],
        successor_id: Annotated[str, typer.Argument()],
        reason: Annotated[str, typer.Option("--reason")],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        record = application.supersede(
            repo_key=resolved.repo_key,
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            reason=reason,
            proposer="user",
        )
        typer.echo(json.dumps(asdict(record), sort_keys=True))

    @memory_app.command("restore")
    def memory_restore_command(
        memory_id: Annotated[str, typer.Argument()],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        application, resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        restored = application.restore(repo_key=resolved.repo_key, memory_id=memory_id)
        typer.echo(json.dumps(asdict(restored), sort_keys=True))

    @namespace_app.command("export")
    def namespace_export_command(
        output: Annotated[Path, typer.Option("--output")],
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        typer.echo(json.dumps(application.export_namespace(output), sort_keys=True))

    @namespace_app.command("reset")
    def namespace_reset_command(
        dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
        confirm: Annotated[str | None, typer.Option("--confirm")] = None,
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        typer.echo(
            json.dumps(
                application.reset_namespace(confirm=confirm, dry_run=dry_run),
                sort_keys=True,
            )
        )

    @evidence_app.command("verify")
    def evidence_verify_command(
        bundle_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    ) -> None:
        """Recompute and verify one public evidence bundle without provider access."""
        result = application_factory(Path(".codecairn")).verify_evidence_bundle(bundle_dir)
        typer.echo(json.dumps(result, sort_keys=True))

    @index_app.command("sync")
    def index_sync_command(
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
        worker_id: Annotated[str, typer.Option("--worker-id")] = "cli",
        max_jobs: Annotated[int | None, typer.Option("--max-jobs", min=1)] = None,
    ) -> None:
        """Drain the index outbox until it is idle and report queue state."""
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        health = application.sync_index(worker_id=worker_id, max_jobs=max_jobs)
        typer.echo(json.dumps(asdict(health), sort_keys=True))

    @index_app.command("rebuild")
    def index_rebuild_command(
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        """Rebuild the search index from durable truth and report truth-index parity."""
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        typer.echo(json.dumps(asdict(application.rebuild_index()), sort_keys=True))

    @index_app.command("status")
    def index_status_command(
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        """Report index outbox state without resolving retrieval providers."""
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        typer.echo(json.dumps(asdict(application.index_status()), sort_keys=True))

    @app.command("doctor")
    def doctor_command(
        repo_key: Annotated[str | None, typer.Option("--repo-key")] = None,
        root: Annotated[Path | None, typer.Option("--root")] = None,
        config: Annotated[Path | None, typer.Option("--config")] = None,
        live: Annotated[bool, typer.Option("--live")] = False,
        strict: Annotated[bool, typer.Option("--strict")] = False,
        output_format: Annotated[Literal["human", "json"], typer.Option("--format")] = "human",
    ) -> None:
        """Inspect durable truth, import state, index state, and providers."""
        application, _resolved = _resolve_application(
            application_factory, config=config, root=root, repo_key=repo_key
        )
        result = application.doctor(live=live)
        if output_format == "json":
            typer.echo(json.dumps(result, sort_keys=True))
        else:
            typer.echo(_doctor_text(result))
        if strict and result["status"] != "ok":
            raise typer.Exit(code=1)

    return app


def _resolve_application(
    application_factory: ApplicationFactory,
    *,
    config: Path | None,
    root: Path | None,
    repo_key: str | None,
) -> tuple[CodeCairnApplication, RuntimeConfig]:
    resolved = resolve_runtime_config(
        start=Path.cwd(),
        config_path=config,
        root=root,
        repo_key=repo_key,
    )
    return _application(application_factory, resolved), resolved


def _application(
    application_factory: ApplicationFactory,
    resolved: RuntimeConfig,
) -> CodeCairnApplication:
    return application_factory(
        resolved.runtime_root,
        repo_key=resolved.repo_key,
        retrieval=resolved.retrieval,
        semantic=resolved.semantic,
    )


def _doctor_text(result: dict[str, object]) -> str:
    providers = cast(dict[str, object], result["providers"])
    privacy = cast(dict[str, object], result["privacy"])
    return "\n".join(
        (
            f"CodeCairn: {result['status']}",
            f"Namespace: {result['repo_key']}",
            f"Config: {providers['retrieval']} ({providers['retrieval_state']})",
            f"Semantic: {providers['semantic']}",
            f"Privacy: storage={privacy['storage']}, embedding={privacy['embedding']}, "
            f"semantic={privacy['semantic_extraction']}",
            f"Remedy: {result['remediation'] or 'none'}",
        )
    )


def _large_text(*, text: str | None, file: Path | None, stdin: bool) -> str:
    if sum((text is not None, file is not None, stdin)) != 1:
        raise typer.BadParameter("provide exactly one text argument, --file, or --stdin")
    value = text if text is not None else file.read_text() if file is not None else sys.stdin.read()
    if not value:
        raise typer.BadParameter("memory text must not be empty")
    return value
