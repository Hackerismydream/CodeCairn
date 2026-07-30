from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import RecallResult
from codecairn.memory.schema import MemoryType
from codecairn.service.application import MemoryDetail, MemoryPage


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


@dataclass(frozen=True, slots=True)
class RecallReadiness:
    """Configuration-only recall readiness; never claims a live provider check."""

    profile: str
    state: Literal["configuration_ready", "missing_key", "not_configured"]
    live_checked: bool
    remediation: str | None


class HubReadModule:
    """Compose view-shaped Hub reads over the Memory OS application interface."""

    def __init__(self, *, application: HubApplication, repo_key: str, recall_readiness: RecallReadiness) -> None:
        self._application = application
        self._repo_key = repo_key
        self._recall_readiness = recall_readiness

    def memories(
        self,
        *,
        memory_type: MemoryType | None,
        status: Literal["active", "superseded"] | None,
        limit: int,
        cursor: str | None,
        selected_memory_id: str | None,
    ) -> dict[str, object]:
        page = self._application.list_memory_page(
            repo_key=self._repo_key, memory_type=memory_type, status=status, limit=limit, cursor=cursor
        )
        selected_id = selected_memory_id or (page.items[0].memory_id if page.items else None)
        selected: dict[str, object] | None = None
        if selected_id is not None:
            selected = {
                "detail": asdict(self._application.get_memory(repo_key=self._repo_key, memory_id=selected_id)),
                "history": asdict(self._application.memory_history(repo_key=self._repo_key, memory_id=selected_id)),
            }
        return {"schema_version": 1, "repo_key": self._repo_key, "page": asdict(page), "selected": selected}

    def recall(
        self, *, query: str, limit: int, include_superseded: bool, workstream_key: str | None, token_budget: int
    ) -> dict[str, object]:
        result = self._application.recall(
            query,
            repo_key=self._repo_key,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )
        return {"schema_version": 1, "result": asdict(result)}

    def system(self) -> dict[str, object]:
        doctor = self._application.doctor(live=False)
        return {
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
