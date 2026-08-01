from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI

from codecairn.bootstrap import create_application
from codecairn.configuration import discover_repository, resolve_runtime_config
from codecairn.entrypoints.hooks import LocalHookCaptureAdapter
from codecairn.importers.history import LocalAgentHistory
from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import RecallResult, RecallSource
from codecairn.memory.schema import CodingMemory, MemoryType
from codecairn.service.application import CodeCairnApplication, MemoryDetail, MemoryPage
from codecairn.service.myna import MemoryLibraryApplication
from codecairn.service.onboarding import OnboardingModule
from codecairn.storage.library_markdown import MarkdownLibraryStore
from codecairn.storage.sqlite import SQLiteImportProgress
from codecairn_hub_api.app import create_hub_app
from codecairn_hub_api.queries import RecallReadiness


class LiveHubApplication:
    """Keep provider-free reads separate from the provider-backed recall path."""

    def __init__(self, *, reads: CodeCairnApplication, configured: CodeCairnApplication) -> None:
        self._reads = reads
        self._configured = configured

    def list_memory_page(
        self,
        *,
        repo_key: str,
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> MemoryPage:
        return self._reads.list_memory_page(repo_key=repo_key, memory_type=memory_type, status=status, limit=limit, cursor=cursor)

    def get_memory(self, *, repo_key: str, memory_id: str) -> MemoryDetail:
        return self._reads.get_memory(repo_key=repo_key, memory_id=memory_id)

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._reads.list_memories(repo_key=repo_key)

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory:
        return self._reads.memory_history(repo_key=repo_key, memory_id=memory_id)

    def memory_resource(self, *, repo_key: str, memory_id: str) -> str:
        return self._reads.memory_resource(repo_key=repo_key, memory_id=memory_id)

    def memory_truth(self, *, repo_key: str, memory_id: str) -> CodingMemory:
        return self._reads.memory_truth(repo_key=repo_key, memory_id=memory_id)

    def has_supersession(self, *, repo_key: str, predecessor_id: str, successor_id: str) -> bool:
        return self._reads.has_supersession(repo_key=repo_key, predecessor_id=predecessor_id, successor_id=successor_id)

    def has_durable_successor(self, *, repo_key: str, memory_id: str) -> bool:
        return self._reads.has_durable_successor(repo_key=repo_key, memory_id=memory_id)

    def recall(
        self,
        query: str,
        *,
        repo_key: str,
        limit: int = 20,
        include_superseded: bool = False,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult:
        return self._configured.recall(
            query,
            repo_key=repo_key,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )

    def recall_across(
        self,
        query: str,
        *,
        current_repo_key: str,
        sources: tuple[RecallSource, ...],
        limit: int = 20,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult:
        return self._configured.recall_across(
            query,
            current_repo_key=current_repo_key,
            sources=sources,
            limit=limit,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )

    def doctor(self, *, live: bool = False) -> dict[str, object]:
        return self._configured.doctor(live=live)


def build_live_hub(repository: Path, *, session_token: str, client_home: Path | None = None, executable: Path | None = None) -> FastAPI:
    config = resolve_runtime_config(start=repository)
    reads = create_application(config.runtime_root, repo_key=config.repo_key)
    configured = create_application(config.runtime_root, repo_key=config.repo_key, retrieval=config.retrieval, semantic=config.semantic)
    missing_key = bool(
        config.retrieval.network and not (os.environ.get("CODECAIRN_EMBEDDING_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"))
    )
    recall_readiness = RecallReadiness(
        profile=config.retrieval.profile,
        state="missing_key" if missing_key else "configuration_ready",
        live_checked=False,
        remediation=("Set CODECAIRN_EMBEDDING_API_KEY or DASHSCOPE_API_KEY and restart the Hub." if missing_key else None),
    )
    home = (client_home or Path.home()).resolve()
    discovered_executable = shutil.which("codecairn") if executable is None else None
    selected_executable = executable or (Path(discovered_executable).resolve() if discovered_executable is not None else None)
    captures: tuple[LocalHookCaptureAdapter, ...] = ()
    if selected_executable is not None and selected_executable.is_file():
        captures = (
            LocalHookCaptureAdapter(client="codex", target=home / ".codex/hooks.json", executable=selected_executable),
            LocalHookCaptureAdapter(client="claude", target=home / ".claude/settings.json", executable=selected_executable),
        )
    onboarding = OnboardingModule(
        application=configured,
        repo_key=config.repo_key,
        repository_common_dir=discover_repository(repository).common_dir,
        history=LocalAgentHistory(
            home=home, identity_secret=hashlib.sha256(f"codecairn-onboarding\0{session_token}".encode()).digest()
        ),
        captures=captures,
        import_progress=SQLiteImportProgress(path=config.runtime_root / "state.sqlite3", repo_key=config.repo_key),
        source_content_egress="memory_text_to_embedding" if config.retrieval.network else "none",
    )
    live = LiveHubApplication(reads=reads, configured=configured)
    library = MemoryLibraryApplication(memory=live, truth=MarkdownLibraryStore(config.runtime_root), repository_key=config.repo_key)
    return create_hub_app(
        application=live,
        repo_key=config.repo_key,
        session_token=session_token,
        recall_readiness=recall_readiness,
        onboarding=onboarding,
        library=library,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the foreground Myna Hub loopback adapter.")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    token = os.environ.get("CODECAIRN_HUB_TOKEN", "")
    if len(token) < 32:
        parser.error("CODECAIRN_HUB_TOKEN must contain at least 32 characters")
    if arguments.port < 1 or arguments.port > 65_535:
        parser.error("--port must be between 1 and 65535")
    app = build_live_hub(arguments.repository.resolve(), session_token=token)
    uvicorn.run(app, host="127.0.0.1", port=arguments.port, access_log=False, server_header=False)


if __name__ == "__main__":
    main()
