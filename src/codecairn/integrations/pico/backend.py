"""Installed Pico MemoryBackend translation over CodeCairn's public service."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from codecairn.bootstrap import create_application
from codecairn.configuration import discover_repository, resolve_runtime_config
from codecairn.integrations.pico.journal import PicoJournalImporter, PicoSourceJournal
from codecairn.memory.config import RuntimeConfig
from codecairn.memory.schema import SchemaInvalid, canonical_json
from codecairn.service.application import CodeCairnApplication, ImportOutcome

_VERIFIED_OUTCOME_PREFIX = "coding-task-outcome:"
_VERIFIED_OUTCOME_FIELDS = {
    "base_sha",
    "branch",
    "candidate_commit_sha",
    "candidate_tree_sha",
    "changed_files",
    "diff_sha256",
    "file_changes",
    "final_summary",
    "head_sha",
    "issue_body",
    "issue_body_full_sha256",
    "issue_body_sha256",
    "issue_url",
    "memory_backend",
    "memory_commit",
    "model",
    "observed_models",
    "profile_sha256",
    "provider",
    "repository",
    "runtime_commit",
    "schema",
    "skills",
    "status",
    "task_id",
    "verification_environment",
    "verifications",
}
_VERIFICATION_FIELDS = {"argv", "duration_ms", "exit_code", "stderr_path", "stderr_sha256", "stdout_path", "stdout_sha256"}


class PicoAdapterError(RuntimeError):
    """Stable, path-free failure returned across the Pico plugin boundary."""

    def __init__(self, code: str, remediation: str) -> None:
        self.code = code
        super().__init__(f"{code}: {remediation}")


class CodeCairnPicoBackend:
    """Structural Pico MemoryBackend with no Pico import at module load."""

    def __init__(
        self,
        context: Any,
        *,
        application_factory: Callable[..., CodeCairnApplication] | None = None,
        config_resolver: Callable[..., RuntimeConfig] | None = None,
    ) -> None:
        config = getattr(context, "config", None)
        services = getattr(context, "services", None)
        workspace = getattr(services, "workspace", None)
        if config not in ({}, None) or not isinstance(workspace, Path):
            raise PicoAdapterError("codecairn_plugin_config_invalid", "remove Pico-side CodeCairn overrides")
        self._workspace = workspace.resolve()
        self._application_factory = application_factory or create_application
        self._config_resolver = config_resolver or resolve_runtime_config
        self._config: RuntimeConfig | None = None
        self._application: CodeCairnApplication | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        try:
            await self._blocking("startup", self._start_sync)
        except Exception:
            self._application = None
            self._config = None
            raise
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await self._blocking("stop", self._recover_sync)
        finally:
            self._started = False
            self._application = None
            self._config = None

    async def recall(self, query: str, *, user_id: str | None = None, agent_id: str | None = None, top_k: int) -> list[Any]:
        self._require_started()
        if (user_id is None) == (agent_id is None):
            return []
        if agent_id is not None:
            return []
        if type(top_k) is not int or not 1 <= top_k <= 100:
            raise PicoAdapterError("codecairn_recall_invalid", "top_k must be between 1 and 100")
        result = await self._blocking(
            "recall", lambda: self._application_required().recall(query, repo_key=self._config_required().repo_key, limit=top_k)
        )
        trace = result.sidecar.context_trace
        if trace is None or not trace.rendered_memory_ids:
            return []
        rendered = set(trace.rendered_memory_ids)
        metadata = {
            "backend": "codecairn",
            "freshness": result.sidecar.freshness,
            "index_cursor": result.sidecar.index_cursor,
            "rendered_memory_ids": list(trace.rendered_memory_ids),
            "repo_key": result.sidecar.repo_key,
            "retrieval_profile": result.sidecar.retrieval_profile,
            "score_semantics": "compiled_context_not_ranked",
            "source_cursor": result.sidecar.source_cursor,
            "source_uris": [item.source_uri for item in result.sidecar.ranked if item.memory_id in rendered],
        }
        Memory = importlib.import_module("pico.memory_engine").Memory
        return [Memory(text=result.markdown, score=0.0, metadata=metadata)]

    async def store(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self._require_started()
        if session_id.startswith(_VERIFIED_OUTCOME_PREFIX):
            raise PicoAdapterError("codecairn_store_session_reserved", "use the verified-outcome delivery operation")
        events = _pico_events(messages)
        if not events or not any(event.get("kind") == "message" and event.get("role") == "user" for event in events):
            return
        config = self._config_required()
        journal = PicoSourceJournal(config.runtime_root, repo_key=config.repo_key, session_id=session_id)
        outcome = await self._blocking(
            "store", lambda: journal.commit(events, importer=cast(PicoJournalImporter[ImportOutcome], self._application_required()))
        )
        if not outcome.index.synced:
            raise PicoAdapterError("index_not_ready", "repair the CodeCairn index before retrying")
        await self._blocking("store", self._assert_ready)

    async def store_verified_outcome(self, idempotency_key: str, outcome: Mapping[str, object]) -> None:
        self._require_started()
        events = _verified_outcome_events(idempotency_key, outcome)
        config = self._config_required()
        journal = PicoSourceJournal(config.runtime_root, repo_key=config.repo_key, session_id=idempotency_key)
        imported = await self._blocking(
            "verified_outcome",
            lambda: journal.commit(
                events, importer=cast(PicoJournalImporter[ImportOutcome], self._application_required()), idempotency_key=idempotency_key
            ),
        )
        if not imported.index.synced:
            raise PicoAdapterError("index_not_ready", "repair the CodeCairn index before retrying")
        await self._blocking("verified_outcome", self._assert_ready)

    async def feedback(self, signals: dict[str, Any]) -> None:
        del signals

    def _start_sync(self) -> None:
        try:
            repository = discover_repository(self._workspace)
            config = self._config_resolver(start=self._workspace)
        except Exception:
            raise PicoAdapterError("codecairn_not_initialized", "run 'codecairn init' in the Pico workspace repository") from None
        if config.binding_path != repository.binding_path or config.runtime_root.is_relative_to(repository.root):
            raise PicoAdapterError("codecairn_repository_mismatch", "re-run 'codecairn init' with an external runtime root")
        application = self._application_factory(
            config.runtime_root, repo_key=config.repo_key, retrieval=config.retrieval, semantic=config.semantic
        )
        self._config, self._application = config, application
        self._recover_sync(require_ready=False)
        application.sync_index(worker_id="pico-start", max_jobs=128)
        self._assert_ready()
        doctor = application.doctor(live=True)
        if doctor.get("status") != "ok":
            raise PicoAdapterError("codecairn_startup_invalid", "run 'codecairn doctor --live' and repair degraded subsystems")

    def _recover_sync(self, *, require_ready: bool = True) -> None:
        config = self._config_required()
        PicoSourceJournal.recover_pending(
            config.runtime_root, repo_key=config.repo_key, importer=cast(PicoJournalImporter[Any], self._application_required())
        )
        if require_ready:
            self._assert_ready()

    def _assert_ready(self) -> None:
        health = self._application_required().index_status()
        if health.pending or health.leased or health.failed or health.stale:
            raise PicoAdapterError("index_not_ready", "repair the CodeCairn index before retrying")

    async def _blocking(self, phase: str, operation: Callable[[], Any]) -> Any:
        try:
            return await asyncio.to_thread(operation)
        except PicoAdapterError:
            raise
        except Exception as error:
            code = getattr(error, "code", f"codecairn_{phase}_failed")
            raise PicoAdapterError(str(code), f"CodeCairn {phase} failed; run 'codecairn doctor --live'") from None

    def _require_started(self) -> None:
        if not self._started:
            raise PicoAdapterError("codecairn_not_started", "start the configured Pico memory backend")

    def _config_required(self) -> RuntimeConfig:
        if self._config is None:
            raise PicoAdapterError("codecairn_not_started", "start the configured Pico memory backend")
        return self._config

    def _application_required(self) -> CodeCairnApplication:
        if self._application is None:
            raise PicoAdapterError("codecairn_not_started", "start the configured Pico memory backend")
        return self._application


def make_backend(context: Any) -> CodeCairnPicoBackend:
    return CodeCairnPicoBackend(context)


def _pico_events(messages: Sequence[Mapping[str, Any]]) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    for message in messages:
        role = message.get("role")
        content = _content(message.get("content"))
        if role in {"user", "assistant", "system"} and content:
            events.append({"kind": "message", "role": role, "text": content})
        if role == "assistant":
            for call in message.get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    raise PicoAdapterError("codecairn_store_invalid", "Pico tool calls must be structured objects")
                function = call.get("function")
                function = function if isinstance(function, Mapping) else call
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        raise PicoAdapterError("codecairn_store_invalid", "Pico tool arguments must contain valid JSON") from None
                events.append(
                    {"arguments": arguments, "call_id": call.get("id"), "kind": "tool_call", "tool_name": function.get("name")}
                )
        elif role == "tool":
            events.append({"call_id": message.get("tool_call_id"), "kind": "tool_result", "text": content})
        elif role not in {"user", "system"}:
            raise PicoAdapterError("codecairn_store_invalid", "Pico messages contain an unsupported role")
    return tuple(events)


def _content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                text.append(cast(str, item["text"]))
        return "\n".join(text)
    return ""


def _verified_outcome_events(idempotency_key: str, outcome: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    try:
        if not isinstance(idempotency_key, str) or not idempotency_key.startswith(_VERIFIED_OUTCOME_PREFIX):
            raise ValueError
        digest = idempotency_key.removeprefix(_VERIFIED_OUTCOME_PREFIX)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError
        encoded = canonical_json(dict(outcome))
        if hashlib.sha256(encoded.encode()).hexdigest() != digest:
            raise ValueError
        payload = cast(dict[str, object], json.loads(encoded))
        if not _valid_verified_outcome(payload):
            raise ValueError
        task_id, issue_body, final_summary = (payload[name] for name in ("task_id", "issue_body", "final_summary"))
        file_changes = cast(list[Mapping[str, object]], payload["file_changes"])
    except (KeyError, SchemaInvalid, TypeError, ValueError):
        raise PicoAdapterError(
            "codecairn_verified_outcome_invalid", "provide one canonical, sandbox-verified Coding Task outcome"
        ) from None
    terminal_changes = [
        {name: change[name] for name in ("operation", "path", "destination_path") if name in change} for change in file_changes
    ]
    call_id = f"pico_done_gate_{digest}"
    return (
        {"kind": "message", "role": "user", "text": issue_body},
        {
            "arguments": {"idempotency_key": idempotency_key, "task_id": task_id},
            "call_id": call_id,
            "kind": "tool_call",
            "tool_name": "pico_done_gate",
        },
        {
            "call_id": call_id,
            "kind": "tool_result",
            "status": "success",
            "terminal_observation": {"exit_code": 0, "file_changes": terminal_changes},
            "untrusted_payload": payload,
        },
        {"kind": "message", "role": "assistant", "text": cast(str, final_summary)},
    )


def _valid_verified_outcome(payload: Mapping[str, object]) -> bool:
    strings = ("task_id", "issue_url", "issue_body", "repository", "branch", "provider", "model", "final_summary")
    if (
        set(payload) != _VERIFIED_OUTCOME_FIELDS
        or payload.get("schema") != "pico.coding-task.outcome.v1"
        or payload.get("status") != "verified"
        or any(not isinstance(payload.get(name), str) or not payload[name] for name in strings)
        or not cast(str, payload["issue_url"]).startswith("https://github.com/")
        or payload.get("memory_backend") != "codecairn"
        or payload.get("verification_environment") not in {"sandboxed_live_model", "sandboxed_test_double"}
    ):
        return False
    if any(
        not _hex(payload.get(name), size)
        for name, size in (
            ("base_sha", 40),
            ("head_sha", 40),
            ("candidate_tree_sha", 40),
            ("candidate_commit_sha", 40),
            ("runtime_commit", 40),
            ("memory_commit", 40),
            ("issue_body_sha256", 64),
            ("issue_body_full_sha256", 64),
            ("diff_sha256", 64),
            ("profile_sha256", 64),
        )
    ):
        return False
    if (
        payload["issue_body_sha256"] != hashlib.sha256(cast(str, payload["issue_body"]).encode()).hexdigest()
        or payload["head_sha"] != payload["candidate_commit_sha"]
    ):
        return False
    observed = payload.get("observed_models")
    skills = payload.get("skills")
    changed = payload.get("changed_files")
    changes = payload.get("file_changes")
    verifications = payload.get("verifications")
    return (
        isinstance(observed, list)
        and bool(observed)
        and all(isinstance(model, str) and payload["model"] in {model, f"{payload['provider']}/{model}"} for model in observed)
        and isinstance(skills, list)
        and all(
            isinstance(skill, Mapping) and set(skill) == {"id", "sha256"} and isinstance(skill["id"], str) and _hex(skill["sha256"], 64)
            for skill in skills
        )
        and isinstance(changed, list)
        and all(isinstance(path, str) and path for path in changed)
        and isinstance(changes, list)
        and all(
            isinstance(change, Mapping)
            and set(change) <= {"operation", "path", "destination_path", "kind", "size", "sha256"}
            and {"operation", "path"} <= set(change)
            for change in changes
        )
        and [change["path"] for change in changes] == changed
        and isinstance(verifications, list)
        and bool(verifications)
        and all(_valid_verification(item) for item in verifications)
    )


def _valid_verification(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != _VERIFICATION_FIELDS:
        return False
    argv = value.get("argv")
    return (
        isinstance(argv, list)
        and bool(argv)
        and all(isinstance(argument, str) and argument for argument in argv)
        and type(value.get("exit_code")) is int
        and value["exit_code"] == 0
        and type(value.get("duration_ms")) is int
        and cast(int, value["duration_ms"]) >= 0
        and all(isinstance(value.get(name), str) and bool(value[name]) for name in ("stdout_path", "stderr_path"))
        and _hex(value.get("stdout_sha256"), 64)
        and _hex(value.get("stderr_sha256"), 64)
    )


def _hex(value: object, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and all(character in "0123456789abcdef" for character in value)
