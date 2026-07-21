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
from codecairn.memory.models import CodingMemory
from codecairn.service.application import (
    CodeCairnApplication,
    EvaluationReportRequest,
    EvaluationRunRequest,
)

_LOGGER = logging.getLogger("codecairn.api")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class ImportRequest(BaseModel):
    source_path: Path
    repo_key: str = Field(min_length=1)


class RecallRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8_000)
    repo_key: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class EvaluationRequest(BaseModel):
    suite: Literal["locomo", "retrieval", "recovery", "coding"]
    input_path: Path
    run_id: str = Field(min_length=1, max_length=128)
    repository_commit: str = Field(min_length=1, max_length=128)
    mode: Literal["full", "smoke", "retrieval"] = "full"
    model: str | None = Field(default=None, min_length=1, max_length=128)
    judge_model: str | None = Field(default=None, min_length=1, max_length=128)
    max_workers: int = Field(default=1, ge=1, le=16)
    resume: bool = False
    question_set_path: Path | None = None
    execution_phase: Literal["all", "ingest", "questions"] = "all"
    corpus_path: Path | None = None
    query_vectors_path: Path | None = None


class _ApiError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def create_app(
    application: CodeCairnApplication,
    *,
    source_roots: tuple[Path, ...],
    artifact_root: Path,
    bind_host: str = "127.0.0.1",
) -> FastAPI:
    if not source_roots:
        raise ValueError("At least one source root is required")
    if bind_host not in _LOOPBACK_HOSTS:
        raise ValueError("HTTP bind host must be trusted loopback")
    allowed_roots = tuple(root.resolve(strict=True) for root in source_roots)
    if not all(root.is_dir() for root in allowed_roots):
        raise ValueError("Every source root must be a directory")
    resolved_artifact_root = artifact_root.resolve()
    resolved_artifact_root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="CodeCairn", version="0.1.0")
    app.state.bind_host = bind_host

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        requested_id = request.headers.get("x-request-id", "")
        request_id = requested_id if _SAFE_ID.fullmatch(requested_id) else uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        _LOGGER.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )
        return response

    @app.exception_handler(_ApiError)
    async def api_error(request: Request, error: _ApiError) -> JSONResponse:
        request_id = _request_id(request)
        _LOGGER.warning(
            "request failed",
            extra={"request_id": request_id, "error_code": error.code},
        )
        return _error_response(
            request_id=request_id,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del error
        return _error_response(
            request_id=_request_id(request),
            status_code=422,
            code="validation_error",
            message="Request validation failed",
        )

    @app.exception_handler(FileExistsError)
    async def artifact_exists(request: Request, error: FileExistsError) -> JSONResponse:
        del error
        return _error_response(
            request_id=_request_id(request),
            status_code=409,
            code="artifact_exists",
            message="Immutable evaluation artifact already exists",
        )

    @app.exception_handler(FileNotFoundError)
    async def not_found(request: Request, error: FileNotFoundError) -> JSONResponse:
        del error
        return _error_response(
            request_id=_request_id(request),
            status_code=404,
            code="not_found",
            message="Requested source or artifact was not found",
        )

    @app.exception_handler(RuntimeError)
    async def infrastructure_error(request: Request, error: RuntimeError) -> JSONResponse:
        return _error_response(
            request_id=_request_id(request),
            status_code=503,
            code="infrastructure_unavailable",
            message=str(error),
        )

    @app.exception_handler(ValueError)
    @app.exception_handler(TraceImportError)
    async def invalid_input(request: Request, error: Exception) -> JSONResponse:
        return _error_response(
            request_id=_request_id(request),
            status_code=422,
            code="invalid_input",
            message=str(error),
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        _LOGGER.exception(
            "unhandled request failure",
            extra={"request_id": _request_id(request), "error_type": type(error).__name__},
        )
        return _error_response(
            request_id=_request_id(request),
            status_code=500,
            code="internal_error",
            message="Internal server error",
        )

    @app.post("/api/v1/import")
    def import_session(request: ImportRequest) -> dict[str, Any]:
        source_path = Path(os.path.abspath(request.source_path))
        source_root = _allowed_source_root(source_path, allowed_roots=allowed_roots)
        if source_root is None:
            raise _ApiError(
                status_code=403,
                code="source_path_forbidden",
                message="Session source is outside configured roots",
            )
        result = application.import_session(
            source_path,
            repo_key=request.repo_key,
            source_root=source_root,
        )
        return asdict(result)

    @app.get("/api/v1/memories")
    def list_memories(
        repo_key: str = Query(min_length=1),
    ) -> list[dict[str, Any]]:
        return [_memory_response(memory) for memory in application.list_memories(repo_key=repo_key)]

    @app.post("/api/v1/recall")
    def recall(request: RecallRequest) -> dict[str, Any]:
        return asdict(
            application.recall(
                request.task,
                repo_key=request.repo_key,
                limit=request.limit,
            )
        )

    @app.post("/api/v1/evaluations")
    def run_evaluation(request: EvaluationRequest) -> dict[str, object]:
        observed_input_path = Path(os.path.abspath(request.input_path))
        try:
            input_path = observed_input_path.resolve(strict=True)
        except FileNotFoundError:
            raise
        if _allowed_source_root(input_path, allowed_roots=allowed_roots) is None:
            raise _ApiError(
                status_code=403,
                code="source_path_forbidden",
                message="Evaluation input is outside configured roots",
            )
        question_set_path: Path | None = None
        if request.question_set_path is not None:
            question_set_path = Path(os.path.abspath(request.question_set_path)).resolve(
                strict=True
            )
            if _allowed_source_root(question_set_path, allowed_roots=allowed_roots) is None:
                raise _ApiError(
                    status_code=403,
                    code="source_path_forbidden",
                    message="Evaluation question set is outside configured roots",
                )
        artifact_inputs: dict[str, Path | None] = {
            "corpus": request.corpus_path,
            "query_vectors": request.query_vectors_path,
        }
        resolved_artifact_inputs: dict[str, Path | None] = {}
        for name, raw_path in artifact_inputs.items():
            if raw_path is None:
                resolved_artifact_inputs[name] = None
                continue
            resolved_path = Path(os.path.abspath(raw_path)).resolve(strict=True)
            if not resolved_path.is_dir() or not resolved_path.is_relative_to(
                resolved_artifact_root
            ):
                raise _ApiError(
                    status_code=403,
                    code="artifact_path_forbidden",
                    message=f"Evaluation {name} artifact is outside the artifact root",
                )
            resolved_artifact_inputs[name] = resolved_path
        if not _SAFE_ID.fullmatch(request.run_id):
            raise _ApiError(
                status_code=422,
                code="invalid_run_id",
                message="Evaluation run id contains unsafe characters",
            )
        if request.suite != "locomo" and request.execution_phase != "all":
            raise _ApiError(
                status_code=422,
                code="invalid_execution_phase",
                message="Execution phases are supported only for LoCoMo",
            )
        if request.suite != "locomo" and any(
            path is not None for path in resolved_artifact_inputs.values()
        ):
            raise _ApiError(
                status_code=422,
                code="invalid_locomo_artifact",
                message="Corpus and query-vector artifacts are supported only for LoCoMo",
            )
        suite_root = resolved_artifact_root / request.suite
        if suite_root.exists() and not suite_root.resolve(strict=True).is_relative_to(
            resolved_artifact_root
        ):
            raise _ApiError(
                status_code=403,
                code="artifact_path_forbidden",
                message="Evaluation output escapes the configured artifact root",
            )
        execution_phase = request.execution_phase
        if resolved_artifact_inputs["corpus"] is not None and execution_phase == "all":
            execution_phase = "questions"
        return application.run_evaluation(
            EvaluationRunRequest(
                suite=request.suite,
                input_path=input_path,
                output_root=resolved_artifact_root,
                run_id=request.run_id,
                repository_commit=request.repository_commit,
                mode=request.mode,
                model=request.model,
                judge_model=request.judge_model,
                max_workers=request.max_workers,
                resume=request.resume,
                question_set_path=question_set_path,
                execution_phase=execution_phase,
                corpus_path=resolved_artifact_inputs["corpus"],
                query_vectors_path=resolved_artifact_inputs["query_vectors"],
            )
        )

    @app.get("/api/v1/evaluations/{suite}/{run_id}")
    def report_evaluation(
        suite: Literal["locomo", "retrieval", "recovery", "coding"],
        run_id: str,
    ) -> dict[str, object]:
        if not _SAFE_ID.fullmatch(run_id):
            raise _ApiError(
                status_code=422,
                code="invalid_run_id",
                message="Evaluation run id contains unsafe characters",
            )
        candidate = resolved_artifact_root / suite / run_id
        try:
            run_dir = candidate.resolve(strict=True)
        except FileNotFoundError:
            raise
        if not run_dir.is_relative_to(resolved_artifact_root):
            raise _ApiError(
                status_code=403,
                code="artifact_path_forbidden",
                message="Evaluation artifact escapes the configured artifact root",
            )
        return application.report_evaluation(
            EvaluationReportRequest(
                suite=suite,
                run_dir=run_dir,
            )
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return application.doctor()

    return app


def _allowed_source_root(source_path: Path, *, allowed_roots: tuple[Path, ...]) -> Path | None:
    return next((root for root in allowed_roots if source_path.is_relative_to(root)), None)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


def _error_response(
    *,
    request_id: str,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message},
            "request_id": request_id,
        },
        headers={"x-request-id": request_id},
    )


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
