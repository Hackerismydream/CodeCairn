"""Loopback HTTP compatibility adapter."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from codecairn.memory.errors import TraceImportError
from codecairn.memory.schema import coding_memory_to_dict
from codecairn.service.application import CodeCairnApplication, import_response

_LOGGER = logging.getLogger("codecairn.api")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class ImportRequest(BaseModel):
    source_path: Path
    repo_key: str = Field(min_length=1)
    index: bool = True
    finalize: bool = False


class RecallRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8_192)
    repo_key: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=100)
    include_superseded: bool = False
    workstream_key: str | None = Field(default=None, min_length=1, max_length=512)
    token_budget: int = Field(default=8_192, ge=256, le=32_768)


class IndexSyncRequest(BaseModel):
    worker_id: str = Field(default="http", min_length=1, max_length=128)
    max_jobs: int | None = Field(default=None, ge=1)


class _ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def create_app(
    application: CodeCairnApplication, *, source_roots: tuple[Path, ...], artifact_root: Path, bind_host: str = "127.0.0.1"
) -> FastAPI:
    if not source_roots:
        raise ValueError("At least one source root is required")
    if bind_host not in _LOOPBACK:
        raise ValueError("HTTP bind host must be trusted loopback")
    roots = tuple(root.resolve(strict=True) for root in source_roots)
    if not all(root.is_dir() for root in roots):
        raise ValueError("Every source root must be a directory")
    artifact_root.resolve().mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="CodeCairn", version="0.1.0")
    app.state.bind_host = bind_host

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("x-request-id", "")
        request.state.request_id = supplied if _SAFE_ID.fullmatch(supplied) else uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        _LOGGER.info(
            "request completed",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )
        return response

    async def render_error(request: Request, error: Exception) -> JSONResponse:
        status, code, message = _error_details(error)
        if status == 500:
            _LOGGER.exception(
                "unhandled request failure", extra={"request_id": _request_id(request), "error_type": type(error).__name__}
            )
        return _error_response(_request_id(request), status, code, message)

    app.add_exception_handler(Exception, render_error)
    app.add_exception_handler(RequestValidationError, render_error)
    app.add_exception_handler(TraceImportError, render_error)
    app.add_exception_handler(FileNotFoundError, render_error)
    app.add_exception_handler(RuntimeError, render_error)
    app.add_exception_handler(ValueError, render_error)
    app.add_exception_handler(_ApiError, render_error)

    @app.post("/api/v1/import")
    def import_session(request: ImportRequest) -> dict[str, Any]:
        source = Path(os.path.abspath(request.source_path))
        root = next((item for item in roots if source.is_relative_to(item)), None)
        if root is None:
            raise _ApiError(403, "source_path_forbidden", "Source is outside configured roots")
        return import_response(
            application.import_session(
                source,
                repo_key=request.repo_key,
                source_root=root,
                index=request.index,
                boundary_kind="manual_finalize" if request.finalize else None,
            )
        )

    @app.get("/api/v1/memories")
    def list_memories(repo_key: str = Query(min_length=1)) -> list[dict[str, object]]:
        return [coding_memory_to_dict(memory) for memory in application.list_memories(repo_key=repo_key)]

    @app.post("/api/v1/recall")
    def recall(request: RecallRequest) -> dict[str, Any]:
        return asdict(
            application.recall(
                request.task,
                repo_key=request.repo_key,
                limit=request.limit,
                include_superseded=request.include_superseded,
                workstream_key=request.workstream_key,
                token_budget=request.token_budget,
            )
        )

    @app.post("/api/v1/evaluations")
    def run_evaluation(request: dict[str, object]) -> None:
        del request
        raise _ApiError(503, "evaluation_cli_required", "Use the v0.1 evaluation Make targets")

    @app.get("/api/v1/evaluations/{suite}/{run_id}")
    def report_evaluation(suite: Literal["locomo", "retrieval", "recovery", "coding"], run_id: str) -> None:
        del suite, run_id
        raise _ApiError(503, "evaluation_cli_required", "Use the v0.1 evaluation Make targets")

    @app.post("/api/v1/index/sync")
    def sync_index(request: IndexSyncRequest) -> dict[str, Any]:
        return asdict(application.sync_index(worker_id=request.worker_id, max_jobs=request.max_jobs))

    @app.post("/api/v1/index/rebuild")
    def rebuild_index() -> dict[str, Any]:
        return asdict(application.rebuild_index())

    @app.get("/api/v1/index")
    def index_status() -> dict[str, Any]:
        return asdict(application.index_status())

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return application.doctor()

    return app


def _error_details(error: Exception) -> tuple[int, str, str]:
    if isinstance(error, _ApiError):
        return error.status, error.code, str(error)
    if isinstance(error, RequestValidationError):
        return 422, "validation_error", "Request validation failed"
    if isinstance(error, TraceImportError):
        return 422, getattr(error, "code", "trace_invalid"), str(error)
    if isinstance(error, FileNotFoundError):
        return 404, "not_found", "Requested source or artifact was not found"
    if isinstance(error, RuntimeError):
        return 503, "infrastructure_unavailable", str(error)
    if isinstance(error, ValueError):
        return 422, "invalid_input", str(error)
    return 500, "internal_error", "Internal server error"


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


def _error_response(request_id: str, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}, "request_id": request_id},
        headers={"x-request-id": request_id},
    )
