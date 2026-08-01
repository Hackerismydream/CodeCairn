from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from codecairn.memory.errors import ConfigurationError, IndexNotReady, ProviderConfigurationError
from codecairn.memory.schema import MemoryType
from codecairn.service.onboarding import OnboardingError, OnboardingModule, PreviewRequest
from codecairn_hub_api.queries import HubApplication, HubReadModule, RecallReadiness


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecallRequest(StrictRequest):
    query: str = Field(min_length=1, max_length=32_768)
    limit: int = Field(default=20, ge=1, le=100)
    include_superseded: bool = False
    workstream_key: str | None = Field(default=None, min_length=1, max_length=512)
    token_budget: int = Field(default=8_192, ge=256, le=65_536)


class OnboardingPreviewRequest(StrictRequest):
    selected_source_ids: list[Annotated[str, Field(pattern=r"^src_[0-9a-f]{64}$")]] | None = Field(default=None, max_length=256)
    install_capture_for: list[Literal["codex", "claude"]] = Field(default_factory=list, max_length=2)


class OnboardingApplyRequest(StrictRequest):
    consent_token: str = Field(min_length=32, max_length=512)


def create_hub_app(
    *,
    application: HubApplication,
    repo_key: str,
    session_token: str,
    recall_readiness: RecallReadiness,
    onboarding: OnboardingModule | None = None,
) -> FastAPI:
    """Create the loopback-only HTTP adapter for one Memory Namespace."""
    if len(session_token) < 32:
        raise ValueError("Hub session token must contain at least 32 characters")
    reads = HubReadModule(application=application, repo_key=repo_key, recall_readiness=recall_readiness)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if onboarding is not None:
            onboarding.open()
        try:
            yield
        finally:
            if onboarding is not None:
                onboarding.close()

    app = FastAPI(title="CodeCairn Hub Read Interface", version="1", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def response_policy(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.request_id = f"hubreq_{uuid.uuid4().hex}"
        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-codecairn-request-id"] = request.state.request_id
        return response

    def error_response(
        request: Request,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        remediation: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSONResponse:
        response = JSONResponse(
            status_code=status_code,
            content={
                "schema_version": 1,
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "remediation": remediation,
                    "request_id": request.state.request_id,
                },
            },
        )
        response.headers.update(headers or {})
        return response

    @app.exception_handler(HTTPException)
    async def hub_http_error(request: Request, error: HTTPException) -> JSONResponse:
        detail: dict[str, object] = error.detail if isinstance(error.detail, dict) else {}
        return error_response(
            request,
            status_code=error.status_code,
            code=str(detail.get("code", "invalid_request")),
            message=str(detail.get("message", "The Hub request is invalid.")),
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def hub_validation_error(request: Request, _error: RequestValidationError) -> JSONResponse:
        return error_response(
            request, status_code=422, code="invalid_request", message="The Hub request does not match the version 1 interface."
        )

    @app.exception_handler(KeyError)
    async def hub_missing_memory(request: Request, _error: KeyError) -> JSONResponse:
        return error_response(request, status_code=404, code="memory_not_found", message="The selected memory does not exist.")

    @app.exception_handler(IndexNotReady)
    async def hub_index_not_ready(request: Request, error: IndexNotReady) -> JSONResponse:
        return error_response(
            request,
            status_code=409,
            code=error.code,
            message="The Memory Namespace index is not ready.",
            retryable=True,
            remediation=error.remediation,
        )

    @app.exception_handler(ProviderConfigurationError)
    async def hub_provider_unavailable(request: Request, error: ProviderConfigurationError) -> JSONResponse:
        return error_response(
            request,
            status_code=503,
            code="provider_not_configured",
            message="The configured retrieval provider is unavailable.",
            remediation=error.remediation,
        )

    @app.exception_handler(ConfigurationError)
    async def hub_namespace_unavailable(request: Request, error: ConfigurationError) -> JSONResponse:
        return error_response(
            request,
            status_code=503,
            code="namespace_unavailable",
            message="The Memory Namespace is unavailable.",
            remediation=error.remediation,
        )

    @app.exception_handler(OnboardingError)
    async def hub_onboarding_error(request: Request, error: OnboardingError) -> JSONResponse:
        status = 409 if error.code in {"consent_expired", "progress_unavailable", "snapshot_stale"} else 400
        remediation = "Run onboarding preview again." if error.retryable else None
        return error_response(
            request, status_code=status, code=error.code, message=str(error), retryable=error.retryable, remediation=remediation
        )

    @app.exception_handler(ValueError)
    async def hub_value_error(request: Request, error: ValueError) -> JSONResponse:
        code = "cursor_invalid" if str(error) == "cursor_invalid" else "invalid_request"
        return error_response(
            request, status_code=400, code=code, message="The Hub request is invalid.", retryable=code == "cursor_invalid"
        )

    @app.exception_handler(Exception)
    async def hub_internal_error(request: Request, _error: Exception) -> JSONResponse:
        return error_response(
            request, status_code=500, code="internal_error", message="The Hub could not complete the request.", retryable=True
        )

    def authorize(supplied: Annotated[str | None, Header(alias="x-codecairn-hub-token")] = None) -> None:
        if supplied is None or not secrets.compare_digest(supplied, session_token):
            raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Hub session token is invalid."})

    def reject_onboarding_query(request: Request) -> None:
        if request.url.query:
            raise HTTPException(
                status_code=400, detail={"code": "invalid_query", "message": "Onboarding requests do not accept query parameters."}
            )

    @app.get("/hub-read/v1/memories", dependencies=[Depends(authorize)])
    def memories(
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
        selected_memory_id: str | None = None,
    ) -> dict[str, object]:
        return reads.memories(memory_type=memory_type, status=status, limit=limit, cursor=cursor, selected_memory_id=selected_memory_id)

    @app.post("/hub-read/v1/recall", dependencies=[Depends(authorize)])
    def recall(request: RecallRequest) -> dict[str, object]:
        return reads.recall(
            query=request.query,
            limit=request.limit,
            include_superseded=request.include_superseded,
            workstream_key=request.workstream_key,
            token_budget=request.token_budget,
        )

    @app.get("/hub-read/v1/system", dependencies=[Depends(authorize)])
    def system() -> dict[str, object]:
        return reads.system()

    if onboarding is not None:

        @app.post("/hub-onboarding/v1/preview", dependencies=[Depends(authorize), Depends(reject_onboarding_query)])
        def onboarding_preview(request: OnboardingPreviewRequest) -> dict[str, object]:
            selected = tuple(request.selected_source_ids) if request.selected_source_ids is not None else None
            return asdict(
                onboarding.preview(PreviewRequest(selected_source_ids=selected, install_capture_for=tuple(request.install_capture_for)))
            )

        @app.post("/hub-onboarding/v1/apply", dependencies=[Depends(authorize), Depends(reject_onboarding_query)])
        def onboarding_apply(request: OnboardingApplyRequest) -> dict[str, object]:
            return asdict(onboarding.apply(request.consent_token))

    return app
