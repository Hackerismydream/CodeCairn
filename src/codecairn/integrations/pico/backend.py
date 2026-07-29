"""Installed Pico MemoryBackend translation over CodeCairn's public service."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from codecairn.bootstrap import create_application
from codecairn.configuration import discover_repository, resolve_runtime_config
from codecairn.integrations.pico.journal import PicoJournalImporter, PicoSourceJournal
from codecairn.memory.config import RuntimeConfig
from codecairn.service.application import CodeCairnApplication, ImportOutcome


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
