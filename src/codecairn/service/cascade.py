from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from codecairn.memory.models import (
    CodingMemory,
    IndexHealth,
    IndexJob,
    RebuildReport,
    ReconcileReport,
    TruthScan,
)


class MemoryTruth(Protocol):
    def scan(self) -> TruthScan: ...

    def read_markdown(self, memory: CodingMemory) -> str: ...


class CascadeState(Protocol):
    def reconcile_truth(self, scan: TruthScan) -> ReconcileReport: ...

    def claim_index_job(
        self,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> IndexJob | None: ...

    def complete_index_job(self, job: IndexJob, *, update_fingerprint: bool = True) -> None: ...

    def fail_index_job(self, job: IndexJob, *, error_type: str) -> None: ...

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...

    def index_health(self, *, now_ms: int) -> IndexHealth: ...

    def replace_index_state(self, fingerprints: set[tuple[str, str, str]]) -> None: ...

    def retry_failed_index_jobs(self) -> int: ...


class MemoryIndex(Protocol):
    def upsert(self, memory: CodingMemory, *, markdown: str) -> None: ...

    def delete(self, *, repo_key: str, memory_id: str) -> None: ...

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None: ...

    def fingerprints(self) -> set[tuple[str, str, str]]: ...


class MiniCascade:
    """Synchronize disposable search state from authoritative Markdown truth."""

    def __init__(
        self,
        *,
        truth: MemoryTruth,
        state: CascadeState,
        index: MemoryIndex,
        clock_ms: Callable[[], int] | None = None,
        lease_ms: int = 30_000,
    ) -> None:
        self._truth = truth
        self._state = state
        self._index = index
        self._clock_ms = clock_ms or _system_clock_ms
        self._lease_ms = lease_ms

    def reconcile(self) -> ReconcileReport:
        return self._state.reconcile_truth(self._truth.scan())

    def run_once(self, *, worker_id: str) -> bool:
        job = self._state.claim_index_job(
            worker_id=worker_id,
            now_ms=self._clock_ms(),
            lease_ms=self._lease_ms,
        )
        if job is None:
            return False
        try:
            if job.operation == "delete":
                self._index.delete(repo_key=job.repo_key, memory_id=job.memory_id)
            else:
                memory = self._state.get_memory(
                    repo_key=job.repo_key,
                    memory_id=job.memory_id,
                )
                if memory is None or memory.content_sha256 != job.content_sha256:
                    self._state.complete_index_job(job, update_fingerprint=False)
                    return True
                markdown = self._truth.read_markdown(memory)
                self._index.upsert(memory, markdown=markdown)
            self._state.complete_index_job(job)
        except Exception as exc:
            self._state.fail_index_job(job, error_type=type(exc).__name__)
            raise
        return True

    def run_until_idle(self, *, worker_id: str, max_jobs: int = 10_000) -> int:
        if max_jobs <= 0:
            raise ValueError("max_jobs must be positive")
        processed = 0
        while processed < max_jobs and self.run_once(worker_id=worker_id):
            processed += 1
        return processed

    def health(self) -> IndexHealth:
        return self._state.index_health(now_ms=self._clock_ms())

    def retry_failed(self) -> int:
        return self._state.retry_failed_index_jobs()

    def rebuild(self) -> RebuildReport:
        scan = self._truth.scan()
        if scan.issues:
            raise ValueError("Cannot rebuild an index from corrupt Markdown truth")
        self._state.reconcile_truth(scan)
        payload = tuple((memory, self._truth.read_markdown(memory)) for memory in scan.memories)
        truth = {
            (memory.repo_key, memory.memory_id, memory.content_sha256 or "")
            for memory in scan.memories
        }
        self._index.replace_all(payload)
        indexed = self._index.fingerprints()
        self._state.replace_index_state(indexed)
        return RebuildReport(
            truth_count=len(truth),
            index_count=len(indexed),
            parity=indexed == truth,
        )

    def index_fingerprints(self) -> set[tuple[str, str, str]]:
        return self._index.fingerprints()


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000
