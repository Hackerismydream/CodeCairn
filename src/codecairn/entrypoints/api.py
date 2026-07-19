from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from codecairn.memory.errors import TraceImportError
from codecairn.memory.models import CodingMemory
from codecairn.service.runtime import MemoryRuntime


class ImportRequest(BaseModel):
    source_path: Path
    repo_key: str = Field(min_length=1)


class RecallRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8_000)
    repo_key: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


def create_app(runtime: MemoryRuntime, *, source_roots: tuple[Path, ...]) -> FastAPI:
    if not source_roots:
        raise ValueError("At least one source root is required")
    allowed_roots = tuple(root.resolve(strict=True) for root in source_roots)
    if not all(root.is_dir() for root in allowed_roots):
        raise ValueError("Every source root must be a directory")
    app = FastAPI(title="CodeCairn", version="0.1.0")

    @app.post("/api/v1/import")
    def import_session(request: ImportRequest) -> dict[str, Any]:
        try:
            source_path = Path(os.path.abspath(request.source_path))
            source_root = next(
                (root for root in allowed_roots if source_path.is_relative_to(root)),
                None,
            )
            if source_root is None:
                raise HTTPException(
                    status_code=403,
                    detail="Session source is outside configured roots",
                )
            result = runtime.import_session(
                source_path,
                repo_key=request.repo_key,
                source_root=source_root,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session source not found") from exc
        except TraceImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return asdict(result)

    @app.get("/api/v1/memories")
    def list_memories(
        repo_key: str = Query(min_length=1),
    ) -> list[dict[str, Any]]:
        return [_memory_response(memory) for memory in runtime.list_memories(repo_key=repo_key)]

    @app.post("/api/v1/recall")
    def recall(request: RecallRequest) -> dict[str, Any]:
        return asdict(
            runtime.recall(
                request.task,
                repo_key=request.repo_key,
                limit=request.limit,
            )
        )

    return app


def _memory_response(memory: CodingMemory) -> dict[str, Any]:
    return {
        "memory_id": memory.memory_id,
        "repo_key": memory.repo_key,
        "memory_type": memory.memory_type,
        "title": memory.title,
        "summary": memory.summary,
        "episode_id": memory.episode_id,
        "command": memory.command,
        "exit_code": memory.exit_code,
        "evidence": [
            {
                "provider": item.provider,
                "session_id": item.session_id,
                "raw_event_sha256": item.raw_event_sha256,
                "raw_event_index": item.raw_event_index,
                "raw_event_type": item.raw_event_type,
                "call_id": item.call_id,
            }
            for item in memory.evidence
        ],
        "content_sha256": memory.content_sha256,
    }
