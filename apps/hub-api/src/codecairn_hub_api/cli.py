from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI

from codecairn.bootstrap import create_application
from codecairn.configuration import resolve_runtime_config
from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import RecallResult
from codecairn.memory.schema import MemoryType
from codecairn.service.application import CodeCairnApplication, MemoryDetail, MemoryPage
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

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory:
        return self._reads.memory_history(repo_key=repo_key, memory_id=memory_id)

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

    def doctor(self, *, live: bool = False) -> dict[str, object]:
        return self._configured.doctor(live=live)


def build_live_hub(repository: Path, *, session_token: str) -> FastAPI:
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
    return create_hub_app(
        application=LiveHubApplication(reads=reads, configured=configured),
        repo_key=config.repo_key,
        session_token=session_token,
        recall_readiness=recall_readiness,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the foreground CodeCairn Hub loopback adapter.")
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
