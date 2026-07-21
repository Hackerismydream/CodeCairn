from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import cast

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
from filelock import FileLock
from lancedb.db import DBConnection  # type: ignore[import-untyped]
from lancedb.index import FTS  # type: ignore[import-untyped]
from lancedb.table import LanceTable  # type: ignore[import-untyped]

from codecairn.memory.embedding import EmbeddingProvider
from codecairn.memory.models import (
    CodingMemory,
    IndexCandidate,
    RecallDocument,
    RecallDocumentFingerprint,
    RecallDocumentKind,
)
from codecairn.memory.projection import compute_document_sha256, project_recall_documents

_TABLE_NAME = "coding_memories"
_EMBEDDING_BATCH_SIZE = 128


def _schema(
    dimension: int,
    *,
    model_id: str,
    source_id: str,
    revision: str,
    index_identity: str,
) -> pa.Schema:
    return pa.schema(
        [
            pa.field("repo_key", pa.string(), nullable=False),
            pa.field("memory_id", pa.string(), nullable=False),
            pa.field("document_id", pa.string(), nullable=False),
            pa.field("document_kind", pa.string(), nullable=False),
            pa.field("parent_document_id", pa.string(), nullable=False),
            pa.field("source_episode_id", pa.string(), nullable=False),
            pa.field("fact_id", pa.string(), nullable=False),
            pa.field("content_sha256", pa.string(), nullable=False),
            pa.field("document_sha256", pa.string(), nullable=False),
            pa.field("memory_type", pa.string(), nullable=False),
            pa.field("title", pa.string(), nullable=False),
            pa.field("summary", pa.string(), nullable=False),
            pa.field("content", pa.string(), nullable=False),
            pa.field("child_count", pa.int32(), nullable=False),
            pa.field("embedding_model_id", pa.string(), nullable=False),
            pa.field("embedding_source_id", pa.string(), nullable=False),
            pa.field("embedding_revision", pa.string(), nullable=False),
            pa.field("embedding_index_identity", pa.string(), nullable=False),
            pa.field("embedding_dimension", pa.int32(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dimension), nullable=False),
        ],
        metadata={
            b"codecairn.embedding_model_id": model_id.encode(),
            b"codecairn.embedding_source_id": source_id.encode(),
            b"codecairn.embedding_revision": revision.encode(),
            b"codecairn.embedding_index_identity": index_identity.encode(),
            b"codecairn.embedding_dimension": str(dimension).encode(),
        },
    )


class LanceMemoryIndex:
    """Disposable LanceDB projection of Coding Memory Markdown."""

    def __init__(self, path: Path, *, embedder: EmbeddingProvider) -> None:
        self._path = path
        self._embedder = embedder
        self._operation_lock = FileLock(path.parent / f".{path.name}.lock")
        self._schema = _schema(
            embedder.dimension,
            model_id=embedder.model_id,
            source_id=embedder.source_id,
            revision=embedder.revision,
            index_identity=embedder.index_identity,
        )

    @property
    def embedding_config(self) -> dict[str, object]:
        return {
            "adapter": "fastembed-compatible",
            "model": self._embedder.model_id,
            "source": self._embedder.source_id,
            "revision": self._embedder.revision,
            "index_identity": self._embedder.index_identity,
            "dimension": self._embedder.dimension,
        }

    def upsert(self, memory: CodingMemory, *, markdown: str) -> None:
        with self._operation_lock:
            table = self._table(create=True)
            assert table is not None
            records = self._records(memory, markdown=markdown)
            table.delete(
                f"repo_key = {_sql_literal(memory.repo_key)} "
                f"AND memory_id = {_sql_literal(memory.memory_id)}"
            )
            table.add(pa.Table.from_pylist(records, schema=self._schema))
            self._ensure_fts(table)

    def delete(self, *, repo_key: str, memory_id: str) -> None:
        with self._operation_lock:
            table = self._table(create=False)
            if table is None:
                return
            table.delete(
                f"repo_key = {_sql_literal(repo_key)} AND memory_id = {_sql_literal(memory_id)}"
            )

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None:
        documents = [
            document
            for memory, markdown in memories
            for document in project_recall_documents(memory, markdown=markdown)
        ]
        with self._operation_lock:
            records = self._records_for_documents(tuple(documents))
            data = pa.Table.from_pylist(records, schema=self._schema)
            table = self._connection().create_table(
                _TABLE_NAME,
                data=data,
                mode="overwrite",
            )
            if records:
                self._ensure_fts(table)

    def fingerprints(self) -> set[tuple[str, str, str]]:
        memory_fingerprints, _document_fingerprints = self.fingerprint_snapshot()
        return memory_fingerprints

    def document_fingerprints(self) -> set[RecallDocumentFingerprint]:
        _memory_fingerprints, document_fingerprints = self.fingerprint_snapshot()
        return document_fingerprints

    def vector_sha256(self) -> str:
        """Hash canonical document IDs and stored float32 vectors."""
        _memories, _documents, vector_sha256 = self.corpus_snapshot()
        return vector_sha256

    def fingerprint_snapshot(
        self,
    ) -> tuple[set[tuple[str, str, str]], set[RecallDocumentFingerprint]]:
        memory_fingerprints, document_fingerprints, _vector_sha256 = self.corpus_snapshot()
        return memory_fingerprints, document_fingerprints

    def corpus_snapshot(
        self,
    ) -> tuple[set[tuple[str, str, str]], set[RecallDocumentFingerprint], str]:
        with self._operation_lock:
            rows = self._validated_rows()
        memory_fingerprints = {
            (str(episode["repo_key"]), str(episode["memory_id"]), str(episode["content_sha256"]))
            for episode, _children in rows
        }
        document_fingerprints = {
            RecallDocumentFingerprint(
                repo_key=str(row["repo_key"]),
                memory_id=str(row["memory_id"]),
                document_id=str(row["document_id"]),
                document_kind=cast(str, row["document_kind"]),  # type: ignore[arg-type]
                parent_document_id=str(row["parent_document_id"]),
                fact_id=str(row["fact_id"]),
                document_sha256=str(row["document_sha256"]),
            )
            for episode, children in rows
            for row in (episode, *children)
        }
        return (
            memory_fingerprints,
            document_fingerprints,
            _vector_sha256(
                rows,
                dimension=self._embedder.dimension,
            ),
        )

    def vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        return self.document_vector_candidates(
            repo_key=repo_key,
            vector=vector,
            document_kind="episode",
            limit=limit,
        )

    def document_vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        document_kind: RecallDocumentKind,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        _validate_vector(vector, dimension=self._embedder.dimension)
        with self._operation_lock:
            table = self._table(create=False)
            if table is None or table.count_rows() == 0:
                return ()
            rows = cast(
                list[dict[str, object]],
                table.search(list(vector), query_type="vector")
                .where(
                    f"repo_key = {_sql_literal(repo_key)} "
                    f"AND document_kind = {_sql_literal(document_kind)}",
                    prefilter=True,
                )
                .limit(limit)
                .to_list(),
            )
        return tuple(
            _candidate(row, score=1.0 / (1.0 + _required_float(row["_distance"]))) for row in rows
        )

    def lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        return self.document_lexical_candidates(
            repo_key=repo_key,
            query=query,
            document_kind="episode",
            limit=limit,
        )

    def document_lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        document_kind: RecallDocumentKind,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        with self._operation_lock:
            table = self._table(create=False)
            if table is None or table.count_rows() == 0:
                return ()
            self._ensure_fts(table)
            rows = cast(
                list[dict[str, object]],
                table.search(query, query_type="fts", fts_columns="content")
                .where(
                    f"repo_key = {_sql_literal(repo_key)} "
                    f"AND document_kind = {_sql_literal(document_kind)}",
                    prefilter=True,
                )
                .limit(limit)
                .to_list(),
            )
        return tuple(_candidate(row, score=_required_float(row["_score"])) for row in rows)

    def _connection(self) -> DBConnection:
        self._path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(self._path)

    def _table(self, *, create: bool) -> LanceTable | None:
        connection = self._connection()
        if _TABLE_NAME not in set(connection.list_tables(limit=10_000).tables):
            if not create:
                return None
            return connection.create_table(_TABLE_NAME, schema=self._schema)
        table = connection.open_table(_TABLE_NAME)
        if table.schema.equals(self._schema, check_metadata=True):
            return table
        rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        migrated = self._migrate_legacy_rows(rows)
        data = pa.Table.from_pylist(migrated, schema=self._schema)
        migrated_table = connection.create_table(_TABLE_NAME, data=data, mode="overwrite")
        if migrated:
            self._ensure_fts(migrated_table)
        return migrated_table

    def _records(self, memory: CodingMemory, *, markdown: str) -> list[dict[str, object]]:
        documents = project_recall_documents(memory, markdown=markdown)
        return self._records_for_documents(documents)

    def _records_for_documents(
        self,
        documents: tuple[RecallDocument, ...],
    ) -> list[dict[str, object]]:
        texts = tuple(_embedding_text(document) for document in documents)
        records: list[dict[str, object]] = []
        for start in range(0, len(documents), _EMBEDDING_BATCH_SIZE):
            document_batch = documents[start : start + _EMBEDDING_BATCH_SIZE]
            vector_batch = self._embedder.embed_documents(
                texts[start : start + _EMBEDDING_BATCH_SIZE]
            )
            if len(vector_batch) != len(document_batch):
                raise ValueError("Embedding provider returned an unexpected vector count")
            records.extend(
                self._record(document, vector=vector)
                for document, vector in zip(document_batch, vector_batch, strict=True)
            )
        return records

    def _record(
        self,
        document: RecallDocument,
        *,
        vector: tuple[float, ...],
    ) -> dict[str, object]:
        _validate_vector(vector, dimension=self._embedder.dimension)
        return {
            "repo_key": document.repo_key,
            "memory_id": document.memory_id,
            "document_id": document.document_id,
            "document_kind": document.document_kind,
            "parent_document_id": document.parent_document_id,
            "source_episode_id": document.source_episode_id,
            "fact_id": document.fact_id,
            "content_sha256": document.content_sha256,
            "document_sha256": document.document_sha256,
            "memory_type": document.memory_type,
            "title": document.title,
            "summary": document.summary,
            "content": document.content,
            "child_count": document.child_count,
            "embedding_model_id": self._embedder.model_id,
            "embedding_source_id": self._embedder.source_id,
            "embedding_revision": self._embedder.revision,
            "embedding_index_identity": self._embedder.index_identity,
            "embedding_dimension": self._embedder.dimension,
            "vector": list(vector),
        }

    def _migrate_legacy_rows(
        self,
        rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        documents: list[RecallDocument] = []
        for row in rows:
            if "document_id" in row:
                documents.append(_document_from_row(row))
                continue
            memory = CodingMemory(
                memory_id=str(row["memory_id"]),
                repo_key=str(row["repo_key"]),
                memory_type=cast(str, row["memory_type"]),  # type: ignore[arg-type]
                title=str(row["title"]),
                summary=str(row["summary"]),
                episode_id=_legacy_episode_id(str(row["content"])),
                command=None,
                exit_code=None,
                evidence=(),
                content_sha256=str(row["content_sha256"]),
            )
            documents.append(project_recall_documents(memory, markdown=str(row["content"]))[0])
        return self._records_for_documents(tuple(documents))

    def _validated_rows(
        self,
    ) -> tuple[tuple[dict[str, object], tuple[dict[str, object], ...]], ...]:
        table = self._table(create=False)
        if table is None:
            return ()
        rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        groups: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in rows:
            if (
                row["embedding_model_id"] != self._embedder.model_id
                or row["embedding_source_id"] != self._embedder.source_id
                or row["embedding_revision"] != self._embedder.revision
                or row["embedding_index_identity"] != self._embedder.index_identity
                or row["embedding_dimension"] != self._embedder.dimension
            ):
                raise ValueError("LanceDB embedding configuration is inconsistent")
            document_kind = cast(RecallDocumentKind, str(row["document_kind"]))
            expected_document_sha256 = compute_document_sha256(
                document_id=str(row["document_id"]),
                repo_key=str(row["repo_key"]),
                memory_id=str(row["memory_id"]),
                document_kind=document_kind,
                parent_document_id=str(row["parent_document_id"]),
                source_episode_id=str(row["source_episode_id"]),
                fact_id=str(row["fact_id"]),
                content_sha256=str(row["content_sha256"]),
                memory_type=str(row["memory_type"]),
                title=str(row["title"]),
                summary=str(row["summary"]),
                content=str(row["content"]),
                child_count=int(cast(int, row["child_count"])),
            )
            if row["document_sha256"] != expected_document_sha256:
                raise ValueError("LanceDB recall document digest is inconsistent")
            key = (str(row["repo_key"]), str(row["memory_id"]))
            groups.setdefault(key, []).append(row)
        validated: list[tuple[dict[str, object], tuple[dict[str, object], ...]]] = []
        for key in sorted(groups):
            group = groups[key]
            episodes = [row for row in group if row["document_kind"] == "episode"]
            children = tuple(row for row in group if row["document_kind"] == "atomic_fact")
            if len(episodes) != 1 or len(children) + 1 != len(group):
                raise ValueError("LanceDB recall projection has invalid document kinds")
            episode = episodes[0]
            if episode["parent_document_id"] or episode["fact_id"]:
                raise ValueError("LanceDB Episode document has an invalid parent or fact ID")
            if int(cast(int, episode["child_count"])) != len(children):
                raise ValueError("LanceDB Episode child count does not match AtomicFacts")
            if any(
                child["parent_document_id"] != episode["document_id"]
                or not child["fact_id"]
                or child["content_sha256"] != episode["content_sha256"]
                for child in children
            ):
                raise ValueError("LanceDB AtomicFact parent projection is inconsistent")
            document_ids = {str(row["document_id"]) for row in group}
            if len(document_ids) != len(group):
                raise ValueError("LanceDB recall document IDs are not unique")
            validated.append((episode, children))
        return tuple(validated)

    @staticmethod
    def _ensure_fts(table: LanceTable) -> None:
        if any(index.index_type == "FTS" for index in table.list_indices()):
            return
        table.create_index("content", config=FTS())


def _vector_sha256(
    rows: tuple[tuple[dict[str, object], tuple[dict[str, object], ...]], ...],
    *,
    dimension: int,
) -> str:
    documents = sorted(
        (row for episode, children in rows for row in (episode, *children)),
        key=lambda row: (str(row["repo_key"]), str(row["document_id"])),
    )
    digest = hashlib.sha256()
    for row in documents:
        identity = json.dumps(
            [str(row["repo_key"]), str(row["document_id"])],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        raw_vector = row.get("vector")
        if not isinstance(raw_vector, list):
            raise ValueError("LanceDB vector payload is invalid")
        vector = tuple(float(item) for item in raw_vector)
        _validate_vector(vector, dimension=dimension)
        digest.update(len(identity).to_bytes(4, "big"))
        digest.update(identity)
        digest.update(struct.pack(f"<{dimension}f", *vector))
    return digest.hexdigest()


def _document_from_row(row: dict[str, object]) -> RecallDocument:
    return RecallDocument(
        document_id=str(row["document_id"]),
        repo_key=str(row["repo_key"]),
        memory_id=str(row["memory_id"]),
        document_kind=cast(RecallDocumentKind, str(row["document_kind"])),
        parent_document_id=str(row["parent_document_id"]),
        source_episode_id=str(row["source_episode_id"]),
        fact_id=str(row["fact_id"]),
        content_sha256=str(row["content_sha256"]),
        document_sha256=str(row["document_sha256"]),
        memory_type=cast(str, row["memory_type"]),  # type: ignore[arg-type]
        title=str(row["title"]),
        summary=str(row["summary"]),
        content=str(row["content"]),
        child_count=int(cast(int, row["child_count"])),
    )


def _embedding_text(document: RecallDocument) -> str:
    return f"{document.title}\n{document.summary}\n{document.content}"


def _validate_vector(vector: tuple[float, ...], *, dimension: int) -> None:
    if len(vector) != dimension:
        raise ValueError(f"Embedding must have {dimension} dimensions")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("Embedding must contain only finite values")


def _legacy_episode_id(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return ""
    frontmatter, separator, _body = markdown[4:].partition("\n---\n")
    if not separator:
        return ""
    for line in frontmatter.splitlines():
        key, field_separator, value = line.partition(": ")
        if key != "episode_id" or not field_separator:
            continue
        parsed = json.loads(value)
        if not isinstance(parsed, str):
            raise ValueError("Legacy LanceDB episode_id must be a string")
        return parsed
    return ""


def _required_float(value: object) -> float:
    if not isinstance(value, int | float):
        raise ValueError("LanceDB returned a non-numeric search score")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("LanceDB returned a non-finite search score")
    return result


def _candidate(row: dict[str, object], *, score: float) -> IndexCandidate:
    return IndexCandidate(
        repo_key=str(row["repo_key"]),
        memory_id=str(row["memory_id"]),
        score=score,
        document_id=str(row["document_id"]),
        document_kind=cast(RecallDocumentKind, str(row["document_kind"])),
        parent_document_id=str(row["parent_document_id"]),
        fact_id=str(row["fact_id"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        content=str(row["content"]),
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
