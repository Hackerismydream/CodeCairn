from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, cast

from codecairn.memory.models import (
    CodingMemory,
    IndexHealth,
    IndexJob,
    RebuildReport,
    RecallDocumentFingerprint,
    ReconcileReport,
    TruthScan,
)
from codecairn.memory.projection import fingerprint, project_recall_documents


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
                truth_memory, markdown = _read_projection(self._truth, memory)
                self._index.upsert(truth_memory, markdown=markdown)
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
        truth_documents = {
            fingerprint(document)
            for memory, markdown in payload
            for document in project_recall_documents(memory, markdown=markdown)
        }
        self._index.replace_all(payload)
        indexed, indexed_documents = _fingerprint_snapshot(self._index)
        document_parity = indexed_documents == truth_documents
        parity = indexed == truth and document_parity
        if parity:
            self._state.replace_index_state(indexed)
        else:
            _requeue_index_revisions(self._state, truth)
        return RebuildReport(
            truth_count=len(truth),
            index_count=len(indexed),
            truth_document_count=len(truth_documents),
            index_document_count=len(indexed_documents),
            document_parity=document_parity,
            parity=parity,
        )

    def index_fingerprints(self) -> set[tuple[str, str, str]]:
        return self._index.fingerprints()

    def index_document_fingerprints(self) -> set[RecallDocumentFingerprint]:
        return _document_fingerprints(self._index)

    def index_vector_sha256(self) -> str:
        method = getattr(self._index, "vector_sha256", None)
        if not callable(method):
            raise TypeError("Memory index does not expose a vector digest")
        result = method()
        if not isinstance(result, str) or not result:
            raise TypeError("Memory index vector digest must be a non-empty string")
        return result

    def index_corpus_snapshot(
        self,
    ) -> tuple[set[tuple[str, str, str]], set[RecallDocumentFingerprint], str]:
        method = getattr(self._index, "corpus_snapshot", None)
        if not callable(method):
            return (
                self.index_fingerprints(),
                self.index_document_fingerprints(),
                self.index_vector_sha256(),
            )
        result = method()
        if (
            not isinstance(result, tuple)
            or len(result) != 3
            or not isinstance(result[0], set)
            or not isinstance(result[1], set)
            or not isinstance(result[2], str)
            or not result[2]
        ):
            raise TypeError("Memory index corpus snapshot has an invalid shape")
        return cast(
            tuple[set[tuple[str, str, str]], set[RecallDocumentFingerprint], str],
            result,
        )


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _document_fingerprints(index: MemoryIndex) -> set[RecallDocumentFingerprint]:
    method = getattr(index, "document_fingerprints", None)
    if not callable(method):
        return set()
    result = method()
    if not isinstance(result, set):
        raise TypeError("Memory index document fingerprints must be a set")
    return cast(set[RecallDocumentFingerprint], result)


def _fingerprint_snapshot(
    index: MemoryIndex,
) -> tuple[set[tuple[str, str, str]], set[RecallDocumentFingerprint]]:
    method = getattr(index, "fingerprint_snapshot", None)
    if not callable(method):
        return index.fingerprints(), _document_fingerprints(index)
    result = method()
    if (
        not isinstance(result, tuple)
        or len(result) != 2
        or not all(isinstance(item, set) for item in result)
    ):
        raise TypeError("Memory index fingerprint snapshot must contain two sets")
    memory_fingerprints, document_fingerprints = result
    return (
        cast(set[tuple[str, str, str]], memory_fingerprints),
        cast(set[RecallDocumentFingerprint], document_fingerprints),
    )


def _requeue_index_revisions(
    state: CascadeState,
    fingerprints: set[tuple[str, str, str]],
) -> None:
    method = getattr(state, "requeue_index_revisions", None)
    if callable(method):
        method(fingerprints)


def _read_projection(
    truth: MemoryTruth,
    memory: CodingMemory,
) -> tuple[CodingMemory, str]:
    method = getattr(truth, "read_projection", None)
    if not callable(method):
        return memory, truth.read_markdown(memory)
    result = method(memory)
    if (
        not isinstance(result, tuple)
        or len(result) != 2
        or not isinstance(result[0], CodingMemory)
        or not isinstance(result[1], str)
    ):
        raise TypeError("Memory truth projection must contain a memory and Markdown")
    return result
