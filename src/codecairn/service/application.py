from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from codecairn.memory.episode import BoundaryKind
from codecairn.memory.evolution import EvolutionProposer, EvolutionRecord, MemoryHistory
from codecairn.memory.models import CodingMemory, HookReceipt, ImportResult, IndexHealth, RebuildReport, RecallResult
from codecairn.memory.schema import (
    MemoryType,
    RepositoryKnowledgePayload,
    UserPreferencePayload,
    WorkStatePayload,
    canonical_json,
    normalize_machine_key,
    normalize_tag,
)
from codecairn.memory.semantic import SemanticProcessReport
from codecairn.service.runtime import MemoryRuntime

IMPORT_INDEX_WORKER_ID = "import"


@dataclass(frozen=True, slots=True)
class IndexSyncReport:
    """Result of the index drain that follows one import commit."""

    requested: bool
    synced: bool
    health: IndexHealth | None = None
    error_type: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """One durable import result plus the index drain attempted after it."""

    result: ImportResult
    index: IndexSyncReport


@dataclass(frozen=True, slots=True)
class RememberRequest:
    repo_key: str
    memory_type: MemoryType
    title: str
    content: str
    category: str = "other"
    subject_key: str | None = None
    source_fact_ids: tuple[str, ...] = ()
    workstream_key: str | None = None
    workstream_state: Literal["open", "closed"] = "open"
    goal: str | None = None
    next_step: str | None = None
    terminal_outcome: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemorySummary:
    memory_id: str
    memory_type: MemoryType
    title: str
    status: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class MemoryPage:
    schema_version: int
    repo_key: str
    items: tuple[MemorySummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MemoryDetail:
    memory: CodingMemory
    status: str
    resource_uri: str


class ApplicationOperations(Protocol):
    def doctor(self, *, live: bool = False) -> dict[str, object]: ...

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth: ...

    def rebuild_index(self) -> RebuildReport: ...

    def index_status(self) -> IndexHealth: ...

    def export_namespace(self, output: Path) -> dict[str, object]: ...

    def reset_namespace(self, *, confirm: str | None, dry_run: bool) -> dict[str, object]: ...

    def read_memory_markdown(self, *, repo_key: str, memory_id: str) -> str: ...

    def record_hook_receipt(self, receipt: HookReceipt) -> None: ...

    def recent_hook_receipts(self) -> tuple[HookReceipt, ...]: ...


class CodeCairnApplication:
    def __init__(self, *, runtime_factory: Callable[[], MemoryRuntime], operations: ApplicationOperations) -> None:
        self._runtime_factory = runtime_factory
        self._operations = operations
        self._runtime: MemoryRuntime | None = None

    def import_session(
        self,
        source_path: Path,
        *,
        repo_key: str,
        source_root: Path | None = None,
        index: bool = True,
        boundary_kind: BoundaryKind | None = None,
    ) -> ImportOutcome:
        result = self._memory_runtime().import_session(
            source_path, repo_key=repo_key, source_root=source_root, boundary_kind=boundary_kind
        )
        return ImportOutcome(result=result, index=self._drain_index(requested=index))

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        return self._memory_runtime().list_memories(repo_key=repo_key)

    def remember_direct(self, request: RememberRequest) -> CodingMemory:
        payload: Any
        if request.memory_type == "task_experience":
            raise ValueError("Task Experience is capture-only")
        if request.memory_type == "repository_knowledge":
            if request.subject_key is None:
                raise ValueError("Repository Knowledge requires subject_key")
            payload = RepositoryKnowledgePayload(subject_key=normalize_machine_key(request.subject_key), claim=request.content)
        elif request.memory_type == "user_preference":
            if request.subject_key is None or not request.source_fact_ids:
                raise ValueError("User Preference requires subject_key and source_fact_ids")
            payload = UserPreferencePayload(
                subject_key=normalize_machine_key(request.subject_key),
                preference=request.content,
                source_fact_ids=tuple(sorted(set(request.source_fact_ids))),
            )
        elif request.memory_type == "work_state":
            if request.workstream_key is None or request.goal is None:
                raise ValueError("Work State requires workstream_key and goal")
            if request.workstream_state == "open" and request.next_step is None:
                raise ValueError("Open Work State requires next_step")
            if request.workstream_state == "closed" and request.terminal_outcome is None:
                raise ValueError("Closed Work State requires terminal_outcome")
            payload = WorkStatePayload(
                workstream_key=normalize_machine_key(request.workstream_key),
                workstream_state=request.workstream_state,
                goal=request.goal,
                progress=request.content,
                blockers=(),
                next_step=request.next_step if request.workstream_state == "open" else None,
                terminal_outcome=(request.terminal_outcome if request.workstream_state == "closed" else None),
            )
        else:
            raise ValueError("Memory type must be repository_knowledge, user_preference, or work_state")
        memory = CodingMemory.create(
            repo_key=request.repo_key,
            memory_type=request.memory_type,
            title=request.title,
            content=request.content,
            category=request.category,
            tags=tuple(sorted({normalize_tag(tag) for tag in request.tags})),
            created_at_ms=time.time_ns() // 1_000_000,
            episode_id=None,
            evidence=(),
            facts=(),
            origin="agent_asserted",
            restored_from=None,
            restore_predecessor_id=None,
            source_order_key=None,
            payload=payload,
        )
        return self._memory_runtime().store_memory(memory)

    def list_memory_page(
        self,
        *,
        repo_key: str,
        memory_type: MemoryType | None = None,
        status: Literal["active", "superseded"] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> MemoryPage:
        if limit < 1 or limit > 100:
            raise ValueError("Memory page limit must be between 1 and 100")
        after = _decode_cursor(cursor, repo_key=repo_key) if cursor else None
        selected = []
        runtime = self._memory_runtime()
        for memory in runtime.list_memories(repo_key=repo_key):
            memory_status = runtime.memory_status(repo_key=repo_key, memory_id=memory.memory_id)
            if memory.memory_id <= (after or ""):
                continue
            if memory_type is not None and memory.memory_type != memory_type:
                continue
            if status is not None and memory_status != status:
                continue
            selected.append(
                MemorySummary(
                    memory_id=memory.memory_id,
                    memory_type=memory.memory_type,
                    title=memory.title,
                    status=memory_status or "active",
                    created_at_ms=memory.created_at_ms,
                )
            )
        page = tuple(selected[:limit])
        next_cursor = _encode_cursor(repo_key=repo_key, memory_id=page[-1].memory_id) if len(selected) > limit else None
        return MemoryPage(1, repo_key, page, next_cursor)

    def get_memory(self, *, repo_key: str, memory_id: str) -> MemoryDetail:
        memory = self._memory_runtime().get_memory(repo_key=repo_key, memory_id=memory_id)
        if memory is None:
            self._operations.read_memory_markdown(repo_key=repo_key, memory_id=memory_id)
            raise KeyError(memory_id)
        status = self._memory_runtime().memory_status(repo_key=repo_key, memory_id=memory_id)
        return MemoryDetail(memory=memory, status=status or "active", resource_uri=f"codecairn://memory/{memory_id}")

    def memory_resource(self, *, repo_key: str, memory_id: str) -> str:
        return self._operations.read_memory_markdown(repo_key=repo_key, memory_id=memory_id)

    def record_hook_receipt(self, receipt: HookReceipt) -> None:
        self._operations.record_hook_receipt(receipt)

    def recent_hook_receipts(self) -> tuple[HookReceipt, ...]:
        return self._operations.recent_hook_receipts()

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
        return self._memory_runtime().recall(
            query,
            repo_key=repo_key,
            limit=limit,
            include_superseded=include_superseded,
            workstream_key=workstream_key,
            token_budget=token_budget,
        )

    def supersede(
        self, *, repo_key: str, predecessor_id: str, successor_id: str, reason: str, proposer: EvolutionProposer
    ) -> EvolutionRecord:
        return self._memory_runtime().supersede(
            repo_key=repo_key, predecessor_id=predecessor_id, successor_id=successor_id, reason=reason, proposer=proposer
        )

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory:
        return self._memory_runtime().memory_history(repo_key=repo_key, memory_id=memory_id)

    def restore(self, *, repo_key: str, memory_id: str) -> CodingMemory:
        return self._memory_runtime().restore(repo_key=repo_key, memory_id=memory_id)

    def process_pending(self, *, worker_id: str, max_jobs: int = 8) -> SemanticProcessReport:
        return self._memory_runtime().process_pending(worker_id=worker_id, max_jobs=max_jobs)

    def doctor(self, *, live: bool = False) -> dict[str, object]:
        return self._operations.doctor(live=live)

    def sync_index(self, *, worker_id: str, max_jobs: int | None = None) -> IndexHealth:
        return self._operations.sync_index(worker_id=worker_id, max_jobs=max_jobs)

    def rebuild_index(self) -> RebuildReport:
        return self._operations.rebuild_index()

    def index_status(self) -> IndexHealth:
        return self._operations.index_status()

    def export_namespace(self, output: Path) -> dict[str, object]:
        return self._operations.export_namespace(output)

    def reset_namespace(self, *, confirm: str | None, dry_run: bool) -> dict[str, object]:
        return self._operations.reset_namespace(confirm=confirm, dry_run=dry_run)

    def _memory_runtime(self) -> MemoryRuntime:
        if self._runtime is None:
            self._runtime = self._runtime_factory()
        return self._runtime

    def _drain_index(self, *, requested: bool) -> IndexSyncReport:
        if not requested:
            return IndexSyncReport(requested=False, synced=False)
        try:
            health = self._operations.sync_index(worker_id=IMPORT_INDEX_WORKER_ID)
        except Exception as error:
            return IndexSyncReport(requested=True, synced=False, error_type=type(error).__name__, error=str(error))
        return IndexSyncReport(
            requested=True,
            synced=(health.pending == 0 and health.leased == 0 and health.failed == 0 and health.stale == 0),
            health=health,
        )


def import_response(outcome: ImportOutcome) -> dict[str, object]:
    return {**asdict(outcome.result), "index": asdict(outcome.index)}


def _encode_cursor(*, repo_key: str, memory_id: str) -> str:
    raw = canonical_json({"last_memory_id": memory_id, "repo_key": repo_key, "schema_version": 1, "sort_key": "memory_id"}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, *, repo_key: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("cursor_invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "repo_key", "sort_key", "last_memory_id"}
        or value["schema_version"] != 1
        or value["repo_key"] != repo_key
        or value["sort_key"] != "memory_id"
        or not isinstance(value["last_memory_id"], str)
    ):
        raise ValueError("cursor_invalid")
    return value["last_memory_id"]
