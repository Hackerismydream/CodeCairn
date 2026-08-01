"""Installed Pico subprocess planning and trace-evidence validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json, write_json_exclusive
from codecairn_v03_acceptance.bounded_process import run_bounded_process

_SESSION_ID = re.compile(r"cli:v03-(?:learn|recall)-[a-z0-9][a-z0-9._-]{0,63}\Z")
_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}\Z")
_MAX_PROCESS_OUTPUT_BYTES = 5 * 1024 * 1024
_MAX_TRACE_LOG_BYTES = 20 * 1024 * 1024
_MAX_TRACE_ARTIFACT_BYTES = 10 * 1024 * 1024
_MAX_SESSION_EXPORT_BYTES = 20 * 1024 * 1024
_FORBIDDEN_RECALL_TOOLS = frozenset(
    {
        "ask_user",
        "cron",
        "edit_file",
        "exec",
        "find",
        "grep",
        "list_dir",
        "message",
        "read_file",
        "spawn",
        "understand_media",
        "web_fetch",
        "web_search",
        "write_file",
    }
)
_FORBIDDEN_LEARN_TOOLS = frozenset(
    {"ask_user", "cron", "message", "spawn", "tool_call", "tool_search", "understand_media", "web_fetch", "web_search"}
)


class PicoAdapterError(RuntimeError):
    """A stable Pico installation, process, or evidence failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class PicoTurnSpec:
    executable: Path
    message: str
    session_id: str
    workspace: Path
    operator_dir: Path
    config: Path
    pico_home: Path
    trace_dir: Path
    timeout_seconds: int

    def __post_init__(self) -> None:
        executable = self.executable.resolve()
        workspace = self.workspace.resolve()
        operator_dir = self.operator_dir.resolve()
        config = self.config.resolve()
        pico_home = self.pico_home.resolve()
        trace_dir = self.trace_dir.resolve()
        if not executable.is_file() or not config.is_file() or not workspace.is_dir() or not operator_dir.is_dir():
            raise ValueError("Pico executable, config, workspace, and operator directory must exist")
        if workspace != pico_home / "workspace":
            raise ValueError("Pico acceptance workspace must be PICO_HOME/workspace")
        if operator_dir == workspace or operator_dir.is_relative_to(workspace) or (operator_dir / ".pico").exists():
            raise ValueError("Pico acceptance operator directory must not discover workspace-local plugins")
        if not self.message.strip() or not _SESSION_ID.fullmatch(self.session_id):
            raise ValueError("Pico acceptance message or session identity is invalid")
        if trace_dir == workspace or trace_dir.is_relative_to(workspace):
            raise ValueError("Pico trace state must remain outside the task workspace")
        if self.timeout_seconds < 1:
            raise ValueError("Pico timeout must be positive")

    @property
    def command(self) -> tuple[str, ...]:
        return (
            str(self.executable.resolve()),
            "run",
            "--message",
            self.message,
            "--session",
            self.session_id,
            "--workspace",
            str(self.workspace.resolve()),
            "--config",
            str(self.config.resolve()),
            "--no-markdown",
            "--no-logs",
        )

    def environment(self, source: Mapping[str, str]) -> dict[str, str]:
        allowed = {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "AZURE_API_KEY",
            "AZURE_API_BASE",
            "CODECAIRN_EMBEDDING_API_KEY",
            "DASHSCOPE_API_KEY",
            "GEMINI_API_KEY",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "LANG",
            "LC_ALL",
            "NO_PROXY",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "PATH",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_FILE",
            "TMPDIR",
        }
        environment = {key: value for key, value in source.items() if key in allowed}
        environment.setdefault("LANG", "C.UTF-8")
        existing_path = environment.get("PATH", "")
        environment.update(
            {
                "HOME": str(self.pico_home.resolve()),
                "NO_PROXY": "127.0.0.1,localhost",
                "PATH": os.pathsep.join(value for value in (str(self.executable.resolve().parent), existing_path) if value),
                "PICO_HOME": str(self.pico_home.resolve()),
                "PICO_TRACING": "1",
                "PICO_TRACING_DIR": str(self.trace_dir.resolve()),
                "PYTHONPATH": "",
                "PYTHONNOUSERSITE": "1",
            }
        )
        return environment


@dataclass(frozen=True, slots=True)
class PicoConfigPair:
    learn: Path
    recall: Path
    public_receipt: dict[str, object]


def execute_pico_turn(
    spec: PicoTurnSpec, *, artifact_dir: Path, source_environment: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Run one installed Pico process and preserve its output and public Session export."""
    artifact_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(artifact_dir, 0o700)
    spec.trace_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(spec.trace_dir, 0o700)
    stdout_path = artifact_dir / "stdout.txt"
    stderr_path = artifact_dir / "stderr.txt"
    environment = spec.environment(os.environ if source_environment is None else source_environment)
    with _exclusive_binary_writer(stdout_path) as stdout, _exclusive_binary_writer(stderr_path) as stderr:
        result = run_bounded_process(
            spec.command,
            cwd=spec.operator_dir.resolve(),
            environment=environment,
            timeout_seconds=spec.timeout_seconds,
            stdout_limit=_MAX_PROCESS_OUTPUT_BYTES,
            stderr_limit=_MAX_PROCESS_OUTPUT_BYTES,
        )
        stdout.write(result.stdout)
        stderr.write(result.stderr)
    if result.terminal in {"stdout_limit", "stderr_limit"}:
        raise PicoAdapterError("pico_output_too_large", "Pico process output exceeds the evidence limit")
    terminal_class = (
        "timeout"
        if result.terminal == "timeout"
        else ("completed" if result.terminal == "exited" and result.exit_code == 0 else "process_failure")
    )
    session_export: dict[str, object] | None = None
    if terminal_class == "completed":
        export_path = artifact_dir / "session.pico-session.json"
        export_command = (str(spec.executable.resolve()), "sessions", "export", spec.session_id, "--output", str(export_path))
        try:
            exported = run_bounded_process(
                export_command,
                cwd=spec.operator_dir.resolve(),
                environment=environment,
                timeout_seconds=min(spec.timeout_seconds, 60),
                stdout_limit=1_048_576,
                stderr_limit=1_048_576,
            )
        except OSError as error:
            raise PicoAdapterError("session_export_failed", "Pico Session export could not start") from error
        if exported.terminal != "exited" or exported.exit_code != 0:
            raise PicoAdapterError("session_export_failed", f"Pico Session export ended as {exported.terminal}")
        if export_path.is_symlink() or not export_path.is_file():
            raise PicoAdapterError("session_export_failed", "Pico Session export did not create a regular artifact")
        os.chmod(export_path, 0o600)
        session_export = _session_export_receipt(export_path, session_id=spec.session_id)
    return {
        "schema_version": 1,
        "contract": "codecairn.v03-acceptance.pico-process.v1",
        "terminal_class": terminal_class,
        "process_id": f"pid:{result.pid}",
        "session_id": spec.session_id,
        "command_sha256": canonical_sha256(list(spec.command)),
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "stdout": {"path": stdout_path.name, "sha256": file_sha256(stdout_path), "bytes": stdout_path.stat().st_size},
        "stderr": {"path": stderr_path.name, "sha256": file_sha256(stderr_path), "bytes": stderr_path.stat().st_size},
        "session_export": session_export,
    }


def prepare_pico_configs(*, base_config: Path, output_dir: Path) -> PicoConfigPair:
    """Create private learn/recall configs while publishing only their digests."""
    raw = read_json(base_config)
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ValueError("Pico base config must be a JSON object")
    base = cast(dict[str, object], raw)
    learn = deepcopy(base)
    recall = deepcopy(base)
    for config in (learn, recall):
        agents = _config_block(config, "agents")
        defaults = _config_block(agents, "defaults")
        defaults["enablePersonalization"] = False
        defaults.pop("enable_personalization", None)
        config["routing"] = {"enabled": False}
        config["memory"] = {"backend": "codecairn", "memoryTopK": 5}
        config["plugins"] = _plugins(config.get("plugins"))
        config["skillForge"] = {"enabled": False, "router": {"enabled": False}}
        config.pop("skill_forge", None)
        config["runtime"] = {"checkpoint": {"policy": "never"}}
        config["tracing"] = {"enabled": True}
        tools = _config_block(config, "tools")
        tools["restrictToWorkspace"] = True
        tools["mcpServers"] = {}
        tools["toolSearch"] = {"enabled": False}
        for alias in ("restrict_to_workspace", "mcp_servers", "tool_search"):
            tools.pop(alias, None)
    recall_tools = _config_block(recall, "tools")
    recall_tools["disabledTools"] = sorted(_FORBIDDEN_RECALL_TOOLS | {"tool_call", "tool_search"})
    recall_tools.pop("disabled_tools", None)
    learn_tools = _config_block(learn, "tools")
    learn_tools["disabledTools"] = sorted(_FORBIDDEN_LEARN_TOOLS)
    learn_tools.pop("disabled_tools", None)
    output_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(output_dir, 0o700)
    learn_path = output_dir / "learn.json"
    recall_path = output_dir / "recall.json"
    write_json_exclusive(learn_path, learn)
    write_json_exclusive(recall_path, recall)
    receipt = {
        "schema_version": 1,
        "contract": "codecairn.v03-acceptance.pico-config-receipt.v1",
        "base_config_sha256": canonical_sha256(base),
        "learn_config_sha256": file_sha256(learn_path),
        "recall_config_sha256": file_sha256(recall_path),
        "memory_backend": "codecairn",
        "recall_side_channels_disabled": True,
    }
    return PicoConfigPair(learn=learn_path, recall=recall_path, public_receipt=receipt)


def collect_learn_trace(*, trace_dir: Path, session_id: str) -> dict[str, object]:
    """Validate the terminal spine of one real Pico coding turn."""
    selected, root = _session_trace(trace_dir, session_id=session_id)
    trace_id = cast(str, root["traceId"])
    joined = [span for span in selected if span["traceId"] == trace_id]
    return {
        "trace_contract": "audit.span.v1",
        "trace_id": trace_id,
        "session_id": session_id,
        "terminal_outcome": "completed",
        "llm_call_count": sum(span["name"] == "llm.call" and _status(span) == "OK" for span in joined),
        "tool_call_count": sum(span["name"] == "tool.call" for span in joined),
    }


def collect_recall_trace(
    *, trace_dir: Path, session_id: str, expected_memory_ids: set[str], expected_repo_key: str, decision_marker: str
) -> dict[str, object]:
    """Join one fresh Pico turn's terminal, recall, and first LLM input evidence."""
    if not expected_memory_ids or any(not _MEMORY_ID.fullmatch(memory_id) for memory_id in expected_memory_ids):
        raise ValueError("expected_memory_ids must contain CodeCairn Memory IDs")
    selected, root = _session_trace(trace_dir, session_id=session_id)
    trace_id = cast(str, root["traceId"])
    outcome = _attributes(root).get("spine.outcome")
    joined = [span for span in selected if span["traceId"] == trace_id]
    memory_spans = [span for span in joined if span["name"] == "memory.recall"]
    if len(memory_spans) != 1 or _status(memory_spans[0]) != "OK":
        raise PicoAdapterError("memory_recall_failure", "Pico trace lacks one successful Memory recall")
    memory_attributes = _attributes(memory_spans[0])
    if not isinstance(memory_attributes.get("memory.hits"), int) or cast(int, memory_attributes["memory.hits"]) < 1:
        raise PicoAdapterError("memory_recall_failure", "Pico Memory recall returned no hits")
    hits = _artifact(trace_dir, memory_attributes, prefix="memory.recall", expected_type=list)
    rendered: set[str] = set()
    source_uris: set[str] = set()
    marked_contexts: list[str] = []
    source_cursor: int | None = None
    index_cursor: int | None = None
    for raw_hit in cast(list[object], hits):
        hit = _object(raw_hit, field="Pico Memory hit")
        metadata = _object(hit.get("metadata"), field="Pico Memory metadata")
        text = hit.get("text")
        ids = metadata.get("rendered_memory_ids")
        uris = metadata.get("source_uris")
        if (
            metadata.get("backend") != "codecairn"
            or not isinstance(text, str)
            or not text
            or metadata.get("repo_key") != expected_repo_key
            or metadata.get("freshness") != "fresh"
            or not isinstance(ids, list)
            or any(not isinstance(memory_id, str) for memory_id in ids)
            or not isinstance(uris, list)
            or any(not isinstance(uri, str) or not uri for uri in uris)
            or not isinstance(metadata.get("source_cursor"), int)
            or isinstance(metadata.get("source_cursor"), bool)
            or not isinstance(metadata.get("index_cursor"), int)
            or isinstance(metadata.get("index_cursor"), bool)
        ):
            raise PicoAdapterError("provenance_mismatch", "Pico Memory metadata does not match CodeCairn")
        rendered.update(cast(list[str], ids))
        source_uris.update(cast(list[str], uris))
        if decision_marker in text and set(cast(list[str], ids)) & expected_memory_ids:
            marked_contexts.append(text)
        source_cursor = cast(int, metadata["source_cursor"])
        index_cursor = cast(int, metadata["index_cursor"])
    if not rendered or not rendered <= expected_memory_ids or not source_uris:
        raise PicoAdapterError("provenance_mismatch", "Pico recalled Memory IDs or sources do not match durable truth")
    llm_spans = [span for span in joined if span["name"] == "llm.call" and _status(span) == "OK"]
    if not llm_spans:
        raise PicoAdapterError("evidence_incomplete", "Pico trace lacks the LLM input joined to Memory recall")
    llm_input = _artifact(trace_dir, _attributes(llm_spans[0]), prefix="llm.input", expected_type=dict)
    llm_input_object = cast(dict[str, object], llm_input)
    if not isinstance(llm_input_object.get("tools"), list) or llm_input_object["tools"]:
        raise PicoAdapterError("side_channel_enabled", "Pico recall LLM input retained a callable tool")
    llm_text = json.dumps(llm_input, ensure_ascii=False, sort_keys=True)
    if (
        not marked_contexts
        or not any(_contains_text(llm_input, context) for context in marked_contexts)
        or any(memory_id not in llm_text for memory_id in rendered)
    ):
        raise PicoAdapterError("evidence_incomplete", "Pico LLM input does not contain the recalled Memory context")
    forbidden = sum(
        1 for span in joined if span["name"] == "tool.call" and _attributes(span).get("tool.name") in _FORBIDDEN_RECALL_TOOLS
    )
    return {
        "trace_contract": "audit.span.v1",
        "trace_id": trace_id,
        "session_id": session_id,
        "terminal_outcome": outcome,
        "recalled_memory_ids": sorted(rendered),
        "llm_input_memory_ids": sorted(rendered),
        "source_uris": sorted(source_uris),
        "source_cursor": source_cursor,
        "index_cursor": index_cursor,
        "forbidden_tool_calls": forbidden,
    }


def _session_trace(trace_dir: Path, *, session_id: str) -> tuple[list[dict[str, object]], dict[str, object]]:
    spans_path = trace_dir / "logs" / "audit-spans.log"
    try:
        if spans_path.is_symlink() or not spans_path.is_file() or spans_path.stat().st_size > _MAX_TRACE_LOG_BYTES:
            raise OSError("trace log is not a bounded regular file")
        spans = [_span(json.loads(line)) for line in spans_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PicoAdapterError("evidence_incomplete", "Pico trace log is missing or invalid") from error
    selected = [span for span in spans if _attributes(span).get("session.id") == session_id]
    roots = [span for span in selected if span["name"] == "spine.turn"]
    if len(roots) != 1:
        raise PicoAdapterError("evidence_incomplete", "Pico trace must contain one terminal Turn")
    root = roots[0]
    outcome = _attributes(root).get("spine.outcome")
    if _status(root) != "OK":
        raise PicoAdapterError("pico_runtime_failure", "Pico Turn trace closed with an error")
    if outcome == "provider_failed":
        raise PicoAdapterError("provider_failure", "Pico provider failed before a complete Turn")
    if outcome != "completed":
        raise PicoAdapterError("task_failed", f"Pico Turn ended as {outcome}")
    if not any(span["name"] == "llm.call" and _status(span) == "OK" for span in selected if span["traceId"] == root["traceId"]):
        raise PicoAdapterError("evidence_incomplete", "Pico Turn trace lacks a successful LLM call")
    return selected, root


def _config_block(parent: dict[str, object], name: str) -> dict[str, object]:
    value = parent.get(name)
    if value is None:
        block: dict[str, object] = {}
        parent[name] = block
        return block
    return _object(value, field=f"Pico {name} config")


def _exclusive_binary_writer(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return cast(BinaryIO, os.fdopen(descriptor, "wb"))


def _session_export_receipt(path: Path, *, session_id: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_SESSION_EXPORT_BYTES:
            raise OSError("session export is not a bounded regular file")
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PicoAdapterError("session_export_failed", "Pico Session export is unreadable") from error
    if not isinstance(envelope, dict) or set(envelope) != {"schema", "payload", "sha256"}:
        raise PicoAdapterError("session_export_failed", "Pico Session export envelope is invalid")
    payload = envelope.get("payload")
    digest = envelope.get("sha256")
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload) or not isinstance(digest, str):
        raise PicoAdapterError("session_export_failed", "Pico Session export identity is invalid")
    messages = payload.get("messages")
    required = {
        "key",
        "created_at",
        "updated_at",
        "metadata",
        "last_consolidated",
        "pending_clarification",
        "messages",
        "message_count",
        "transcript_markdown",
    }
    if (
        envelope["schema"] != "pico.session.export.v1"
        or payload.get("key") != session_id
        or not required <= set(payload)
        or not isinstance(messages, list)
        or payload.get("message_count") != len(messages)
        or hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest() != digest
    ):
        raise PicoAdapterError("session_export_failed", "Pico Session export failed verification")
    return {
        "schema": envelope["schema"],
        "session_id": session_id,
        "payload_sha256": digest,
        "artifact_sha256": file_sha256(path),
        "bytes": path.stat().st_size,
    }


def _plugins(value: object) -> dict[str, object]:
    if value is None:
        return {"disabled": [], "config": {}}
    plugins = deepcopy(_object(value, field="Pico plugins config"))
    disabled = plugins.get("disabled", [])
    if not isinstance(disabled, list) or any(not isinstance(plugin_id, str) for plugin_id in disabled):
        raise ValueError("Pico disabled plugin list is invalid")
    plugins["disabled"] = [plugin_id for plugin_id in disabled if plugin_id != "codecairn-memory"]
    plugins.setdefault("config", {})
    return plugins


def _artifact(
    trace_dir: Path, attributes: dict[str, object], *, prefix: str, expected_type: type[object] | tuple[type[object], ...]
) -> object:
    raw_path = attributes.get(f"{prefix}.artifact_path")
    expected_sha1 = attributes.get(f"{prefix}.artifact_sha1")
    expected_bytes = attributes.get(f"{prefix}.artifact_bytes")
    if (
        not isinstance(raw_path, str)
        or not isinstance(expected_sha1, str)
        or not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
        or expected_bytes > _MAX_TRACE_ARTIFACT_BYTES
    ):
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact identity is missing")
    path = Path(raw_path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact is missing") from error
    if path.is_symlink() or not resolved.is_relative_to(trace_dir.resolve()) or not resolved.is_file():
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact escapes its trace root")
    if resolved.stat().st_size != expected_bytes:
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact size does not match")
    raw = resolved.read_bytes()
    if len(raw) != expected_bytes or hashlib.sha1(raw).hexdigest() != expected_sha1:
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact digest does not match")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact is not valid JSON") from error
    if not isinstance(value, expected_type):
        raise PicoAdapterError("evidence_incomplete", f"{prefix} artifact shape is invalid")
    return value


def _span(value: object) -> dict[str, object]:
    span = _object(value, field="Pico span")
    required = {
        "schemaVersion",
        "traceId",
        "spanId",
        "parentSpanId",
        "name",
        "kind",
        "startTime",
        "endTime",
        "status",
        "events",
        "attributes",
    }
    if set(span) != required or span["schemaVersion"] != "audit.span.v1":
        raise ValueError("Pico span contract is invalid")
    if any(not isinstance(span[name], str) or not span[name] for name in ("traceId", "spanId", "name")):
        raise ValueError("Pico span identity is invalid")
    _attributes(span)
    _status(span)
    return span


def _attributes(span: dict[str, object]) -> dict[str, object]:
    return _object(span.get("attributes"), field="Pico span attributes")


def _contains_text(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value
    if isinstance(value, dict):
        value = list(value.values())
    return isinstance(value, list) and any(_contains_text(item, expected) for item in value)


def _status(span: dict[str, object]) -> str:
    status = _object(span.get("status"), field="Pico span status")
    code = status.get("code")
    if not isinstance(code, str):
        raise ValueError("Pico span status code is invalid")
    return code


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)
