"""Protocol-clean stdio MCP presentation over the shared application facade."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError, ToolError
from pydantic import Field, TypeAdapter

from codecairn.configuration import resolve_runtime_config
from codecairn.memory.config import RetrievalConfig, SemanticConfig
from codecairn.memory.errors import ConfigurationError, IndexNotReady, ProviderConfigurationError, TraceImportError
from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import CodingMemory, RecallResult
from codecairn.memory.schema import MemoryType, SchemaInvalid
from codecairn.service.application import CodeCairnApplication, ImportOutcome, MemoryDetail, MemoryPage, RememberRequest

_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}")


class ApplicationFactory(Protocol):
    def __call__(
        self,
        root: Path,
        *,
        repo_key: str | None = None,
        retrieval: RetrievalConfig | None = None,
        semantic: SemanticConfig | None = None,
    ) -> CodeCairnApplication: ...


def build_server(application_factory: ApplicationFactory, *, working_directory: Path | None = None) -> FastMCP:
    """Build the seven-tool, one-resource version 0.1 MCP server."""
    cwd = (working_directory or Path.cwd()).resolve()
    server = FastMCP(
        "CodeCairn", instructions="Explicit, auditable repository memory. Tools never execute coding work.", log_level="ERROR"
    )

    def application(repo_key: str | None) -> tuple[CodeCairnApplication, str]:
        resolved = resolve_runtime_config(start=cwd, repo_key=repo_key)
        return (
            application_factory(
                resolved.runtime_root, repo_key=resolved.repo_key, retrieval=resolved.retrieval, semantic=resolved.semantic
            ),
            resolved.repo_key,
        )

    @server.tool(structured_output=True)
    def recall(
        task: Annotated[str, Field(min_length=1, max_length=8_192)],
        repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        workstream_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        include_superseded: bool = False,
    ) -> dict[str, object]:
        """Compile bounded active repository memory for one coding task."""
        try:
            _bounded(task, 8_192, "task")
            app, resolved_key = application(repo_key)
            return asdict(
                app.recall(
                    task, repo_key=resolved_key, workstream_key=workstream_key, limit=limit, include_superseded=include_superseded
                )
            )
        except Exception as error:
            raise _tool_error(error) from None

    @server.tool(structured_output=True)
    def remember(
        memory_type: MemoryType,
        title: Annotated[str, Field(min_length=1, max_length=256)],
        content: Annotated[str, Field(min_length=1, max_length=32_768)],
        repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        category: Annotated[str, Field(min_length=1, max_length=64)] = "other",
        subject_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        workstream_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        workstream_state: Literal["open", "closed"] = "open",
        goal: Annotated[str | None, Field(min_length=1, max_length=32_768)] = None,
        next_step: Annotated[str | None, Field(min_length=1, max_length=32_768)] = None,
        terminal_outcome: Annotated[str | None, Field(min_length=1, max_length=32_768)] = None,
        tags: Annotated[list[str] | None, Field(max_length=32)] = None,
        source_fact_ids: Annotated[list[str] | None, Field(max_length=128)] = None,
    ) -> dict[str, object]:
        """Create direct Knowledge, Working Preference, or Work State memory."""
        try:
            for name, value, maximum in (("title", title, 256), ("content", content, 32_768), ("category", category, 64)):
                _bounded(value, maximum, name)
            app, resolved_key = application(repo_key)
            return asdict(
                app.remember_direct(
                    RememberRequest(
                        repo_key=resolved_key,
                        memory_type=memory_type,
                        title=title,
                        content=content,
                        category=category,
                        subject_key=subject_key,
                        source_fact_ids=tuple(source_fact_ids or ()),
                        workstream_key=workstream_key,
                        workstream_state=workstream_state,
                        goal=goal,
                        next_step=next_step,
                        terminal_outcome=terminal_outcome,
                        tags=tuple(tags or ()),
                    )
                )
            )
        except Exception as error:
            raise _tool_error(error) from None

    @server.tool(structured_output=True)
    def list_memories(
        repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Field(min_length=1, max_length=4_096)] = None,
    ) -> dict[str, object]:
        """List a compact, stable page of repository memories."""
        try:
            app, resolved_key = application(repo_key)
            return asdict(
                app.list_memory_page(repo_key=resolved_key, memory_type=memory_type, status=status, limit=limit, cursor=cursor)
            )
        except Exception as error:
            raise _tool_error(error) from None

    @server.tool(structured_output=True)
    def get_memory(
        memory_id: Annotated[str, Field(pattern=r"^mem_[0-9a-f]{64}$")],
        repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
    ) -> dict[str, object]:
        """Return one full durable memory and its resource URI."""
        try:
            _memory_id(memory_id)
            app, resolved_key = application(repo_key)
            return asdict(app.get_memory(repo_key=resolved_key, memory_id=memory_id))
        except Exception as error:
            raise _tool_error(error) from None

    @server.tool(structured_output=True)
    def memory_history(
        memory_id: Annotated[str, Field(pattern=r"^mem_[0-9a-f]{64}$")],
        repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
    ) -> dict[str, object]:
        """Return the ordered immutable lineage containing one memory."""
        try:
            _memory_id(memory_id)
            app, resolved_key = application(repo_key)
            return asdict(app.memory_history(repo_key=resolved_key, memory_id=memory_id))
        except Exception as error:
            raise _tool_error(error) from None

    @server.tool(structured_output=True)
    def import_session(
        source_path: Annotated[str, Field(min_length=1, max_length=4_096)],
        repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
    ) -> dict[str, object]:
        """Import one owned Codex or Claude session through a manual boundary."""
        try:
            _bounded(source_path, 4_096, "source_path")
            source = Path(source_path).expanduser().resolve(strict=True)
            if not source.is_file():
                raise FileNotFoundError(source)
            app, resolved_key = application(repo_key)
            return asdict(app.import_session(source, repo_key=resolved_key, boundary_kind="manual_finalize"))
        except Exception as error:
            raise _tool_error(error) from None

    @server.tool(structured_output=True)
    def doctor(repo_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None) -> dict[str, object]:
        """Return structured subsystem health and executable remedies."""
        try:
            app, _resolved_key = application(repo_key)
            return app.doctor()
        except Exception as error:
            raise _tool_error(error) from None

    @server.resource("codecairn://memory/{memory_id}", name="CodeCairn memory", mime_type="text/markdown")
    def memory_resource(memory_id: str) -> str:
        """Read canonical durable Markdown for one memory in the current namespace."""
        try:
            _memory_id(memory_id)
            app, resolved_key = application(None)
            return app.memory_resource(repo_key=resolved_key, memory_id=memory_id)
        except Exception as error:
            raise ResourceError(_error_json(error)) from None

    _configure_tool_schemas(server)
    return server


async def schema_snapshot(server: FastMCP) -> dict[str, object]:
    """Return deterministic checked-in tool and resource schema metadata."""
    tools = sorted(await server.list_tools(), key=lambda item: item.name)
    resources = sorted(await server.list_resource_templates(), key=lambda item: item.uriTemplate)
    return {
        "schema_version": 1,
        "tools": [{"name": item.name, "input_schema": item.inputSchema, "output_schema": item.outputSchema} for item in tools],
        "resources": [{"name": item.name, "uri_template": item.uriTemplate, "mime_type": item.mimeType} for item in resources],
    }


def _bounded(value: str, maximum: int, field: str) -> None:
    if not value or len(value.encode()) > maximum:
        raise SchemaInvalid(f"{field} must contain 1..{maximum} UTF-8 bytes")


def _configure_tool_schemas(server: FastMCP) -> None:
    manager = server._tool_manager
    for tool in manager._tools.values():
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema(by_alias=True)
    outputs = {
        "recall": RecallResult,
        "remember": CodingMemory,
        "list_memories": MemoryPage,
        "get_memory": MemoryDetail,
        "memory_history": MemoryHistory,
        "import_session": ImportOutcome,
    }
    for name, output in outputs.items():
        schema = TypeAdapter(output).json_schema()
        _close_schema_objects(schema)
        manager._tools[name].fn_metadata.output_schema = schema


def _close_schema_objects(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object" and "properties" in value:
            value.setdefault("additionalProperties", False)
        for child in value.values():
            _close_schema_objects(child)
    elif isinstance(value, list):
        for child in value:
            _close_schema_objects(child)


def _memory_id(value: str) -> None:
    if _MEMORY_ID.fullmatch(value) is None:
        raise SchemaInvalid("memory_id must be a mem_ typed ID")


def _tool_error(error: Exception) -> ToolError:
    return ToolError(_error_json(error))


def _error_json(error: Exception) -> str:
    if isinstance(error, IndexNotReady):
        code, retryable = "index_not_ready", True
    elif isinstance(error, ConfigurationError):
        code, retryable = error.code, False
    elif isinstance(error, ProviderConfigurationError):
        code, retryable = "provider_not_configured", False
    elif isinstance(error, TraceImportError):
        code, retryable = getattr(error, "code", "trace_invalid"), False
    elif isinstance(error, FileNotFoundError):
        code, retryable = "source_unavailable", False
    elif isinstance(error, KeyError):
        code, retryable = "memory_not_found", False
    elif str(error) in {"cursor_invalid", "foreign_namespace"}:
        code, retryable = str(error), False
    else:
        code, retryable = getattr(error, "code", "schema_invalid"), False
    message = (
        "Retrieval provider is not configured"
        if isinstance(error, ProviderConfigurationError)
        else str(error).replace("\n", " ")[:2_048] or type(error).__name__
    )
    remediation = getattr(error, "remediation", None)
    if code == "source_unavailable":
        remediation = "Pass an owned readable session JSONL path."
    elif code == "memory_not_found":
        remediation = "List memories in the resolved repository namespace."
    return json.dumps(
        {"code": code, "message": message, "remediation": remediation, "retryable": retryable},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
