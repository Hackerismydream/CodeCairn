from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import RecallResult
from codecairn.memory.schema import MemoryType
from codecairn.service.application import MemoryDetail, MemoryPage
from codecairn.service.myna import (
    LibraryMemorySelection,
    LibrarySnapshot,
    MynaRecallResult,
    PreferenceGovernance,
    PromotionReceipt,
    RecallForRequest,
    ScopedMemoryPage,
)


class HubApplication(Protocol):
    def doctor(self, *, live: bool = False) -> dict[str, object]: ...

    def list_memory_page(
        self,
        *,
        repo_key: str,
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> MemoryPage: ...

    def get_memory(self, *, repo_key: str, memory_id: str) -> MemoryDetail: ...

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory: ...

    def recall(
        self,
        query: str,
        *,
        repo_key: str,
        limit: int = 20,
        include_superseded: bool = False,
        workstream_key: str | None = None,
        token_budget: int = 8_192,
    ) -> RecallResult: ...


class HubLibraryApplication(Protocol):
    def library(self) -> LibrarySnapshot: ...

    def preference_governance(self, memory_id: str) -> PreferenceGovernance: ...

    def promote_preference(self, memory_id: str) -> PromotionReceipt: ...

    def recall_for(self, request: RecallForRequest) -> MynaRecallResult: ...

    def browse_library(
        self,
        *,
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        scope: Literal["all", "global", "repository"] = "all",
        limit: int = 20,
        cursor: str | None = None,
    ) -> ScopedMemoryPage: ...

    def library_memory(self, memory_id: str) -> LibraryMemorySelection: ...


@dataclass(frozen=True, slots=True)
class RecallReadiness:
    """Configuration-only recall readiness; never claims a live provider check."""

    profile: str
    state: Literal["configuration_ready", "missing_key", "not_configured"]
    live_checked: bool
    remediation: str | None


class HubReadModule:
    """Compose view-shaped Hub reads over the Memory OS application interface."""

    def __init__(
        self,
        *,
        application: HubApplication,
        repo_key: str,
        recall_readiness: RecallReadiness,
        library: HubLibraryApplication | None = None,
    ) -> None:
        self._application = application
        self._repo_key = repo_key
        self._recall_readiness = recall_readiness
        self._library = library

    def memories(
        self,
        *,
        memory_type: MemoryType | None,
        status: Literal["active", "superseded"] | None,
        limit: int,
        cursor: str | None,
        selected_memory_id: str | None,
        scope: Literal["all", "global", "repository"] = "all",
    ) -> dict[str, object]:
        if self._library is None:
            if scope == "global":
                raise ValueError("Global scope requires Myna")
            legacy_page = self._application.list_memory_page(
                repo_key=self._repo_key, memory_type=memory_type, status=status, limit=limit, cursor=cursor
            )
            selected_id = selected_memory_id or (legacy_page.items[0].memory_id if legacy_page.items else None)
            selected: dict[str, object] | None = None
            if selected_id is not None:
                selected = {
                    "detail": asdict(self._application.get_memory(repo_key=self._repo_key, memory_id=selected_id)),
                    "history": asdict(self._application.memory_history(repo_key=self._repo_key, memory_id=selected_id)),
                }
            page_value = asdict(legacy_page)
        else:
            library_page = self._library.browse_library(memory_type=memory_type, status=status, scope=scope, limit=limit, cursor=cursor)
            selected_id = selected_memory_id or (library_page.items[0].memory_id if library_page.items else None)
            page_value = {
                "schema_version": library_page.schema_version,
                "repo_key": library_page.repository_key,
                "items": [{**asdict(item), "source_repository_key": item.source.repository_key} for item in library_page.items],
                "next_cursor": library_page.next_cursor,
            }
            selected = None
            if selected_id is not None:
                selection = self._library.library_memory(selected_id)
                selected = {
                    "detail": asdict(selection.detail),
                    "history": asdict(selection.history),
                    "effective_scope": selection.effective_scope,
                    "source": asdict(selection.source),
                    "source_repository_key": selection.source.repository_key,
                }
                if selection.governance is not None:
                    selected["governance"] = asdict(selection.governance)
        result = {"schema_version": 1, "repo_key": self._repo_key, "page": page_value, "selected": selected}
        if self._library is not None:
            result["library_context"] = self._library_context()
        return result

    def recall(
        self, *, query: str, limit: int, include_superseded: bool, workstream_key: str | None, token_budget: int
    ) -> dict[str, object]:
        if self._library is None or include_superseded:
            legacy_result = self._application.recall(
                query,
                repo_key=self._repo_key,
                limit=limit,
                include_superseded=include_superseded,
                workstream_key=workstream_key,
                token_budget=token_budget,
            )
            return {"schema_version": 1, "result": asdict(legacy_result)}
        library_result = self._library.recall_for(
            RecallForRequest(
                query=query, requesting_client="hub", limit=limit, workstream_key=workstream_key, token_budget=token_budget
            )
        )
        legacy = asdict(library_result.sidecar.repository_trace)
        legacy.update(
            {
                "ranked": [asdict(item) for item in library_result.sidecar.ranked],
                "person_id": library_result.sidecar.person_id,
                "repository_key": library_result.sidecar.repository_key,
                "requesting_client": library_result.sidecar.requesting_client,
                "active_scopes": library_result.sidecar.active_scopes,
                "shadowed": [asdict(item) for item in library_result.sidecar.shadowed],
            }
        )
        return {"schema_version": 1, "result": {"markdown": library_result.markdown, "sidecar": legacy}}

    def system(self) -> dict[str, object]:
        doctor = self._application.doctor(live=False)
        result = {
            "schema_version": 1,
            "observed_at_ms": time.time_ns() // 1_000_000,
            "repo_key": self._repo_key,
            "status": doctor["status"],
            "runtime_schema": doctor["schema"],
            "counts": {
                "imports": doctor["imports"],
                "observed_events": doctor["observed_events"],
                "memories": doctor["memories"],
                "pending_recovery": doctor["pending_recovery"],
                "conflicted_recovery": doctor["conflicted_recovery"],
            },
            "semantic_jobs": doctor["semantic_jobs"],
            "hook_receipts": doctor["hook_receipts"],
            "index_jobs": doctor["index_jobs"],
            "subsystems": doctor["subsystems"],
            "providers": doctor["providers"],
            "recall_readiness": asdict(self._recall_readiness),
            "privacy": doctor["privacy"],
            "remediation": doctor["remediation"],
        }
        if self._library is not None:
            result["library_context"] = self._library_context()
        return result

    def _library_context(self) -> dict[str, object]:
        if self._library is None:
            raise RuntimeError("Myna library is unavailable")
        snapshot = self._library.library()
        return {
            "person_id": snapshot.person.person_id,
            "current_repository_key": snapshot.repository_key,
            "active_scopes": snapshot.active_scopes,
            "promotion_count": len(snapshot.promotions),
        }
