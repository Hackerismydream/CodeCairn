from __future__ import annotations

import time
from typing import Protocol

from codecairn.memory.models import IndexJob, MemoryStatus, RebuildReport, RecallDocument
from codecairn.memory.projection import project_memory
from codecairn.memory.schema import CodingMemory


class CascadeState(Protocol):
    def claim_index_jobs(
        self, *, repo_key: str, worker_id: str, max_jobs: int, now_ms: int, lease_ms: int, memory_ids: tuple[str, ...] | None = None
    ) -> tuple[IndexJob, ...]: ...

    def complete_index_job(self, job: IndexJob, *, worker_id: str, profile_identity: str) -> None: ...

    def fail_index_job(self, job: IndexJob, *, worker_id: str, error_code: str) -> None: ...

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None: ...

    def memory_status(self, *, repo_key: str, memory_id: str) -> str | None: ...

    def requeue_profile(self, *, repo_key: str, profile_identity: str) -> int: ...

    def requeue_indexed_namespace(self, *, repo_key: str) -> int: ...

    def requeue_index_revisions(self, *, repo_key: str, memory_ids: tuple[str, ...]) -> int: ...

    def namespace_index_counts(self, *, repo_key: str) -> dict[str, int]: ...

    def recall_documents(self, *, repo_key: str) -> tuple[tuple[CodingMemory, MemoryStatus], ...]: ...


class CascadeIndex(Protocol):
    @property
    def profile_identity(self) -> str: ...

    def upsert(self, documents: tuple[RecallDocument, ...]) -> None: ...

    def replace_namespace(self, *, repo_key: str, documents: tuple[RecallDocument, ...]) -> None: ...

    def fingerprints(self, *, repo_key: str) -> set[tuple[str, str, str, str]]: ...


class MiniCascade:
    def __init__(self, *, state: CascadeState, index: CascadeIndex) -> None:
        self._state = state
        self._index = index

    def preflight(self, *, repo_key: str, worker_id: str = "recall", max_jobs: int = 128) -> bool:
        return self._preflight(repo_key=repo_key, worker_id=worker_id, max_jobs=max_jobs, memory_ids=None)

    def preflight_memories(self, *, repo_key: str, memory_ids: tuple[str, ...], worker_id: str = "recall", max_jobs: int = 128) -> bool:
        if not memory_ids or memory_ids != tuple(sorted(set(memory_ids))):
            raise ValueError("Targeted preflight memory IDs are invalid")
        return self._preflight(repo_key=repo_key, worker_id=worker_id, max_jobs=max_jobs, memory_ids=memory_ids)

    def _preflight(self, *, repo_key: str, worker_id: str, max_jobs: int, memory_ids: tuple[str, ...] | None) -> bool:
        if memory_ids is None:
            self._state.requeue_profile(repo_key=repo_key, profile_identity=self._index.profile_identity)
        expected = self._expected(repo_key, memory_ids)
        actual = self._selected_fingerprints(repo_key, memory_ids)
        if actual != expected:
            expected_by_id = _by_memory(expected)
            actual_by_id = _by_memory(actual)
            self._state.requeue_index_revisions(
                repo_key=repo_key,
                memory_ids=tuple(
                    sorted(
                        memory_id for memory_id, fingerprints in expected_by_id.items() if actual_by_id.get(memory_id) != fingerprints
                    )
                ),
            )
        jobs = self._state.claim_index_jobs(
            repo_key=repo_key,
            worker_id=worker_id,
            max_jobs=max_jobs,
            now_ms=time.time_ns() // 1_000_000,
            lease_ms=30_000,
            memory_ids=memory_ids,
        )
        for job in jobs:
            try:
                memory = self._state.get_memory(repo_key=job.repo_key, memory_id=job.memory_id)
                status = self._state.memory_status(repo_key=job.repo_key, memory_id=job.memory_id)
                if memory is None or status not in {"active", "superseded"}:
                    raise ValueError("index_source_missing")
                if status != job.target_status:
                    raise ValueError("index_revision_stale")
                self._index.upsert(project_memory(memory, status=status))
                self._state.complete_index_job(job, worker_id=worker_id, profile_identity=self._index.profile_identity)
            except Exception as error:
                self._state.fail_index_job(job, worker_id=worker_id, error_code=type(error).__name__)
        if memory_ids is not None:
            return self._selected_fingerprints(repo_key, memory_ids) == expected
        counts = self._state.namespace_index_counts(repo_key=repo_key)
        return (
            not any(counts[name] for name in ("pending", "leased", "failed", "stale"))
            and self._selected_fingerprints(repo_key, None) == expected
        )

    def rebuild(self, *, repo_key: str) -> RebuildReport:
        documents = tuple(
            document
            for memory, status in self._state.recall_documents(repo_key=repo_key)
            for document in project_memory(memory, status=status)
        )
        self._index.replace_namespace(repo_key=repo_key, documents=documents)
        expected = {(item.memory_id, item.document_id, item.status, item.content_sha256) for item in documents}
        actual = self._index.fingerprints(repo_key=repo_key)
        if expected == actual:
            self._state.requeue_indexed_namespace(repo_key=repo_key)
            self.preflight(repo_key=repo_key, worker_id="rebuild")
        return RebuildReport(
            truth_count=len({item.memory_id for item in documents}),
            index_count=len({item[0] for item in actual}),
            parity=expected == actual,
            truth_document_count=len(documents),
            index_document_count=len(actual),
            document_parity=expected == actual,
        )

    def _expected(self, repo_key: str, memory_ids: tuple[str, ...] | None = None) -> set[tuple[str, str, str, str]]:
        return {
            (document.memory_id, document.document_id, document.status, document.content_sha256)
            for memory, status in self._state.recall_documents(repo_key=repo_key)
            for document in project_memory(memory, status=status)
            if memory_ids is None or memory.memory_id in memory_ids
        }

    def _selected_fingerprints(self, repo_key: str, memory_ids: tuple[str, ...] | None) -> set[tuple[str, str, str, str]]:
        return {item for item in self._index.fingerprints(repo_key=repo_key) if memory_ids is None or item[0] in memory_ids}


def _by_memory(fingerprints: set[tuple[str, str, str, str]]) -> dict[str, frozenset[tuple[str, str, str, str]]]:
    return {
        memory_id: frozenset(item for item in fingerprints if item[0] == memory_id)
        for memory_id, _document_id, _status, _digest in fingerprints
    }
