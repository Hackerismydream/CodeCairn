from __future__ import annotations

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from codecairn.bootstrap import create_cascade, create_runtime
from codecairn.memory.embedding import VECTOR_DIMENSION
from codecairn.memory.models import CodingMemory, RebuildReport, RecallDocumentFingerprint
from codecairn.memory.projection import fingerprint, project_recall_documents
from codecairn.service.cascade import MemoryIndex, MiniCascade
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"


def test_rebuild_report_preserves_the_legacy_positional_field_order() -> None:
    report = RebuildReport(1, 1, True)

    assert report.parity is True
    assert report.truth_document_count == 0
    assert report.index_document_count == 0
    assert report.document_parity is True


class RecordingIndex(MemoryIndex):
    def __init__(self) -> None:
        self._lock = Lock()
        self.upserts: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.documents: set[RecallDocumentFingerprint] = set()

    def upsert(self, memory: CodingMemory, *, markdown: str) -> None:
        assert memory.content_sha256 is not None
        with self._lock:
            self.upserts = [
                row for row in self.upserts if row[:2] != (memory.repo_key, memory.memory_id)
            ]
            self.upserts.append((memory.repo_key, memory.memory_id, memory.content_sha256))
            self.documents = {
                item
                for item in self.documents
                if (item.repo_key, item.memory_id) != (memory.repo_key, memory.memory_id)
            }
            self.documents.update(
                fingerprint(document)
                for document in project_recall_documents(memory, markdown=markdown)
            )

    def delete(self, *, repo_key: str, memory_id: str) -> None:
        with self._lock:
            self.deletes.append((repo_key, memory_id))
            self.documents = {
                item
                for item in self.documents
                if (item.repo_key, item.memory_id) != (repo_key, memory_id)
            }

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None:
        with self._lock:
            self.upserts = [
                (memory.repo_key, memory.memory_id, memory.content_sha256 or "")
                for memory, _markdown in memories
            ]
            self.documents = {
                fingerprint(document)
                for memory, markdown in memories
                for document in project_recall_documents(memory, markdown=markdown)
            }

    def fingerprints(self) -> set[tuple[str, str, str]]:
        with self._lock:
            return set(self.upserts)

    def document_fingerprints(self) -> set[RecallDocumentFingerprint]:
        with self._lock:
            return set(self.documents)


class FailOnceIndex(RecordingIndex):
    def __init__(self) -> None:
        super().__init__()
        self._remaining_failures = 1

    def upsert(self, memory: CodingMemory, *, markdown: str) -> None:
        if self._remaining_failures:
            self._remaining_failures -= 1
            raise OSError("simulated index outage")
        super().upsert(memory, markdown=markdown)


class LegacyMemoryOnlyIndex:
    """PR0 adapter contract without document-level fingerprint support."""

    def __init__(self) -> None:
        self.rows: set[tuple[str, str, str]] = set()

    def upsert(self, memory: CodingMemory, *, markdown: str) -> None:
        assert memory.content_sha256 is not None
        self.rows = {row for row in self.rows if row[:2] != (memory.repo_key, memory.memory_id)}
        self.rows.add((memory.repo_key, memory.memory_id, memory.content_sha256))

    def delete(self, *, repo_key: str, memory_id: str) -> None:
        self.rows = {row for row in self.rows if row[:2] != (repo_key, memory_id)}

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None:
        self.rows = {
            (memory.repo_key, memory.memory_id, memory.content_sha256 or "")
            for memory, _markdown in memories
        }

    def fingerprints(self) -> set[tuple[str, str, str]]:
        return set(self.rows)


def test_import_commits_an_index_revision_and_unchanged_truth_is_a_no_op(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    index = RecordingIndex()
    cascade = create_cascade(root, index=index)

    runtime.import_session(FIXTURE, repo_key="acme/widgets")

    assert cascade.health().pending == 1
    assert cascade.run_once(worker_id="worker-a") is True
    assert cascade.health().indexed == 1
    assert len(index.upserts) == 1

    report = cascade.reconcile()

    assert report.created == 0
    assert report.modified == 0
    assert report.deleted == 0
    assert cascade.health().pending == 0
    assert cascade.run_once(worker_id="worker-b") is False
    assert len(index.upserts) == 1


def test_incremental_index_projects_facts_from_markdown_truth(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    runtime.import_session(FIXTURE, repo_key="acme/widgets")
    with sqlite3.connect(root / "state.sqlite3") as connection:
        connection.execute("UPDATE memories SET facts_json = '[]'")
    assert runtime.list_memories(repo_key="acme/widgets")[0].facts == ()
    cascade = create_cascade(root)

    assert cascade.run_until_idle(worker_id="truth-reader") == 1

    documents = cascade.index_document_fingerprints()
    assert len(documents) == 4
    assert len([item for item in documents if item.document_kind == "atomic_fact"]) == 3


def test_concurrent_workers_cannot_claim_the_same_revision(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    index = RecordingIndex()
    cascade = create_cascade(root, index=index)
    runtime.import_session(FIXTURE, repo_key="acme/widgets")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda worker: cascade.run_once(worker_id=f"worker-{worker}"),
                range(8),
            )
        )

    assert results.count(True) == 1
    assert len(index.upserts) == 1
    assert cascade.health().leased == 0
    assert cascade.health().indexed == 1


def test_expired_lease_is_replayed_after_an_interrupted_worker(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    create_runtime(root).import_session(FIXTURE, repo_key="acme/widgets")
    state = SQLiteState(root / "state.sqlite3")
    interrupted = state.claim_index_job(worker_id="stopped", now_ms=1_000, lease_ms=10)
    assert interrupted is not None
    now = [1_009]
    cascade = MiniCascade(
        truth=MarkdownMemoryStore(root),
        state=state,
        index=RecordingIndex(),
        clock_ms=lambda: now[0],
        lease_ms=10,
    )

    assert cascade.run_once(worker_id="recovery") is False
    assert cascade.health().leased == 1

    now[0] = 1_010

    assert cascade.run_once(worker_id="recovery") is True
    assert cascade.health().leased == 0
    assert cascade.health().indexed == 1


def test_failed_index_job_is_audited_and_explicitly_replayed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    create_runtime(root).import_session(FIXTURE, repo_key="acme/widgets")
    cascade = create_cascade(root, index=FailOnceIndex())

    with pytest.raises(OSError, match="simulated index outage"):
        cascade.run_once(worker_id="worker")

    assert cascade.health().failed == 1
    assert cascade.retry_failed() == 1
    assert cascade.run_until_idle(worker_id="worker") == 1
    assert cascade.health().failed == 0
    assert cascade.health().indexed == 1


def test_reconcile_uses_actual_markdown_digest_for_offline_changes(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    index = RecordingIndex()
    cascade = create_cascade(root, index=index)
    runtime.import_session(FIXTURE, repo_key="acme/widgets")
    cascade.run_until_idle(worker_id="initial")
    original = runtime.list_memories(repo_key="acme/widgets")[0]
    path = Path(original.markdown_path or "")

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'summary: "A repository command failed. '
            'Inspect both cited raw events before deciding whether to repeat it."',
            'summary: "Offline corrected summary"',
        ),
        encoding="utf-8",
    )

    report = cascade.reconcile()
    updated = runtime.list_memories(repo_key="acme/widgets")[0]

    assert report.modified == 1
    assert updated.summary == "Offline corrected summary"
    assert updated.content_sha256 != original.content_sha256
    assert cascade.run_until_idle(worker_id="modify") == 1
    assert index.fingerprints() == {
        (updated.repo_key, updated.memory_id, updated.content_sha256 or "")
    }

    path.unlink()
    report = cascade.reconcile()

    assert report.deleted == 1
    assert runtime.list_memories(repo_key="acme/widgets") == ()
    assert cascade.run_until_idle(worker_id="delete") == 1
    assert index.deletes == [(original.repo_key, original.memory_id)]


def test_reconcile_discovers_offline_creation_and_reports_corruption(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    source_runtime = create_runtime(tmp_path / "source")
    source_runtime.import_session(FIXTURE, repo_key="acme/widgets")
    source = source_runtime.list_memories(repo_key="acme/widgets")[0]
    created = MarkdownMemoryStore(root).write(
        CodingMemory(
            memory_id=source.memory_id,
            repo_key=source.repo_key,
            memory_type=source.memory_type,
            title=source.title,
            summary=source.summary,
            episode_id=source.episode_id,
            command=source.command,
            exit_code=source.exit_code,
            evidence=source.evidence,
            fact_ids=source.fact_ids,
        )
    )
    cascade = create_cascade(root, index=RecordingIndex())

    report = cascade.reconcile()

    assert report.created == 1
    assert cascade.health().pending == 1

    Path(created.markdown_path or "").write_bytes(b"not valid markdown\xff")
    report = cascade.reconcile()

    assert report.corrupt == 1
    assert cascade.health().stale == 1


def test_deleted_lancedb_rebuilds_with_full_memory_id_and_hash_parity(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    cascade = create_cascade(root)
    runtime.import_session(FIXTURE, repo_key="acme/widgets")
    cascade.run_until_idle(worker_id="initial")
    truth = {
        (memory.repo_key, memory.memory_id, memory.content_sha256 or "")
        for memory in runtime.list_memories(repo_key="acme/widgets")
    }
    assert cascade.index_fingerprints() == truth

    shutil.rmtree(root / "index.lancedb")
    rebuilt = create_cascade(root)

    report = rebuilt.rebuild()

    assert report.truth_count == len(truth)
    assert report.index_count == len(truth)
    assert report.truth_document_count == 4
    assert report.index_document_count == 4
    assert report.document_parity is True
    assert report.parity is True
    assert rebuilt.index_fingerprints() == truth
    documents = rebuilt.index_document_fingerprints()
    episode = next(item for item in documents if item.document_kind == "episode")
    atomic_facts = [item for item in documents if item.document_kind == "atomic_fact"]
    assert len(atomic_facts) == 3
    assert {item.parent_document_id for item in atomic_facts} == {episode.document_id}
    assert {item.fact_id for item in atomic_facts} == {
        fact.fact_id for fact in runtime.list_memories(repo_key="acme/widgets")[0].facts
    }


def test_failed_document_parity_keeps_the_outbox_repairable(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    create_runtime(root).import_session(FIXTURE, repo_key="acme/widgets")
    cascade = create_cascade(root, index=LegacyMemoryOnlyIndex())  # type: ignore[arg-type]

    report = cascade.rebuild()

    assert report.index_count == report.truth_count == 1
    assert report.index_document_count == 0
    assert report.document_parity is False
    assert report.parity is False
    assert cascade.health().pending == 1
    assert cascade.health().indexed == 0


def test_legacy_flat_lancedb_row_migrates_without_losing_the_memory(tmp_path: Path) -> None:
    index_path = tmp_path / "index.lancedb"
    legacy_markdown = '---\nepisode_id: "episode_legacy"\n---\n\n# Legacy Convention\n'
    schema = pa.schema(
        [
            pa.field("repo_key", pa.string(), nullable=False),
            pa.field("memory_id", pa.string(), nullable=False),
            pa.field("content_sha256", pa.string(), nullable=False),
            pa.field("memory_type", pa.string(), nullable=False),
            pa.field("title", pa.string(), nullable=False),
            pa.field("summary", pa.string(), nullable=False),
            pa.field("content", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), VECTOR_DIMENSION), nullable=False),
        ]
    )
    row = {
        "repo_key": "acme/widgets",
        "memory_id": "memory_legacy",
        "content_sha256": "e" * 64,
        "memory_type": "repository_convention",
        "title": "Legacy Convention",
        "summary": "The flat projection remains available.",
        "content": legacy_markdown,
        "vector": [0.0] * VECTOR_DIMENSION,
    }
    connection = lancedb.connect(index_path)
    connection.create_table(
        "coding_memories",
        data=pa.Table.from_pylist([row], schema=schema),
    )

    index = LanceMemoryIndex(index_path)

    assert index.fingerprints() == {("acme/widgets", "memory_legacy", "e" * 64)}
    documents = index.document_fingerprints()
    assert len(documents) == 1
    document = next(iter(documents))
    assert document.document_kind == "episode"
    assert document.parent_document_id == ""
    assert document.fact_id == ""
    expected_memory = CodingMemory(
        memory_id="memory_legacy",
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title="Legacy Convention",
        summary="The flat projection remains available.",
        episode_id="episode_legacy",
        command=None,
        exit_code=None,
        evidence=(),
        content_sha256="e" * 64,
    )
    assert documents == {
        fingerprint(item)
        for item in project_recall_documents(expected_memory, markdown=legacy_markdown)
    }


def test_document_fingerprints_reject_atomic_fact_content_tampering(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    create_runtime(root).import_session(FIXTURE, repo_key="acme/widgets")
    create_cascade(root).run_until_idle(worker_id="initial")
    index_path = root / "index.lancedb"
    connection = lancedb.connect(index_path)
    table = connection.open_table("coding_memories")
    rows = table.to_arrow().to_pylist()
    atomic_fact = next(row for row in rows if row["document_kind"] == "atomic_fact")
    atomic_fact["content"] = "tampered fact content"
    connection.create_table(
        "coding_memories",
        data=pa.Table.from_pylist(rows, schema=table.schema),
        mode="overwrite",
    )

    with pytest.raises(ValueError, match="document digest"):
        LanceMemoryIndex(index_path).document_fingerprints()
