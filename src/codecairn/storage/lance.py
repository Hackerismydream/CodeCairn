from __future__ import annotations

import math
from pathlib import Path
from typing import cast

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
from filelock import FileLock
from lancedb.index import FTS  # type: ignore[import-untyped]
from lancedb.table import LanceTable  # type: ignore[import-untyped]

from codecairn.memory.models import IndexCandidate, RecallDocument
from codecairn.memory.retrieval import EmbeddingProvider

_TABLE = "coding_memories"


class LanceMemoryIndex:
    def __init__(self, path: Path, *, embedder: EmbeddingProvider) -> None:
        self._path = path
        self._embedder = embedder
        self._lock = FileLock(path.parent / f".{path.name}.lock")
        self._schema = pa.schema(
            [
                pa.field("repo_key", pa.string(), nullable=False),
                pa.field("memory_id", pa.string(), nullable=False),
                pa.field("document_id", pa.string(), nullable=False),
                pa.field("document_kind", pa.string(), nullable=False),
                pa.field("memory_type", pa.string(), nullable=False),
                pa.field("status", pa.string(), nullable=False),
                pa.field("profile_identity", pa.string(), nullable=False),
                pa.field("title", pa.string(), nullable=False),
                pa.field("content", pa.string(), nullable=False),
                pa.field("content_sha256", pa.string(), nullable=False),
                pa.field("created_at_ms", pa.int64(), nullable=False),
                pa.field("workstream_key", pa.string(), nullable=False),
                pa.field("vector", pa.list_(pa.float32(), embedder.dimension), nullable=False),
            ],
            metadata={b"codecairn.profile": embedder.index_identity.encode()},
        )

    @property
    def profile_identity(self) -> str:
        return self._embedder.index_identity

    def upsert(self, documents: tuple[RecallDocument, ...]) -> None:
        if not documents:
            raise ValueError("Index upsert requires projected documents")
        memory = documents[0]
        vectors = self._embedder.embed_documents(tuple(_embedding_text(document) for document in documents))
        with self._lock:
            table = self._table(create=True)
            assert table is not None
            table.delete(f"repo_key = {_literal(memory.repo_key)} AND memory_id = {_literal(memory.memory_id)}")
            table.add(
                pa.Table.from_pylist(
                    [self._row(document, vector) for document, vector in zip(documents, vectors, strict=True)], schema=self._schema
                )
            )
            self._ensure_fts(table)

    def replace_namespace(self, *, repo_key: str, documents: tuple[RecallDocument, ...]) -> None:
        vectors = self._embedder.embed_documents(tuple(_embedding_text(document) for document in documents))
        rows = [self._row(document, vector) for document, vector in zip(documents, vectors, strict=True)]
        with self._lock:
            table = self._table(create=bool(rows))
            if table is None:
                return
            table.delete(f"repo_key = {_literal(repo_key)}")
            if rows:
                table.add(pa.Table.from_pylist(rows, schema=self._schema))
                self._ensure_fts(table)

    def lexical_candidates(self, *, repo_key: str, query: str, include_superseded: bool, limit: int) -> tuple[IndexCandidate, ...]:
        with self._lock:
            table = self._table(create=False)
            if table is None or table.count_rows() == 0:
                return ()
            self._ensure_fts(table)
            rows = cast(
                list[dict[str, object]],
                table.search(query, query_type="fts", fts_columns="content")
                .where(_filter(repo_key, include_superseded), prefilter=True)
                .limit(limit)
                .to_list(),
            )
        return tuple(_candidate(row) for row in rows)

    def vector_candidates(
        self, *, repo_key: str, vector: tuple[float, ...], include_superseded: bool, limit: int
    ) -> tuple[IndexCandidate, ...]:
        with self._lock:
            table = self._table(create=False)
            if table is None or table.count_rows() == 0:
                return ()
            rows = cast(
                list[dict[str, object]],
                table.search(list(vector), query_type="vector")
                .metric("cosine")
                .where(_filter(repo_key, include_superseded), prefilter=True)
                .limit(limit)
                .to_list(),
            )
        return tuple(_candidate(row, vector=True) for row in rows)

    def fingerprints(self, *, repo_key: str) -> set[tuple[str, str, str, str]]:
        with self._lock:
            table = self._table(create=False)
            if table is None:
                return set()
            rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        return {
            (str(row["memory_id"]), str(row["document_id"]), str(row["status"]), str(row["content_sha256"]))
            for row in rows
            if row["repo_key"] == repo_key
        }

    def delete_namespace(self, *, repo_key: str) -> None:
        with self._lock:
            table = self._table(create=False)
            if table is not None:
                table.delete(f"repo_key = {_literal(repo_key)}")

    def _table(self, *, create: bool) -> LanceTable | None:
        connection = lancedb.connect(self._path)
        if _TABLE not in set(connection.list_tables(limit=100).tables):
            return connection.create_table(_TABLE, schema=self._schema) if create else None
        table = connection.open_table(_TABLE)
        if not table.schema.equals(self._schema, check_metadata=True):
            raise ValueError("retrieval_profile_changed")
        return table

    def _row(self, document: RecallDocument, vector: tuple[float, ...]) -> dict[str, object]:
        if len(vector) != self._embedder.dimension:
            raise ValueError("Embedding dimension does not match retrieval profile")
        return {
            "repo_key": document.repo_key,
            "memory_id": document.memory_id,
            "document_id": document.document_id,
            "document_kind": document.document_kind,
            "memory_type": document.memory_type,
            "status": document.status,
            "profile_identity": self.profile_identity,
            "title": document.title,
            "content": document.content,
            "content_sha256": document.content_sha256,
            "created_at_ms": document.created_at_ms,
            "workstream_key": document.workstream_key or "",
            "vector": list(vector),
        }

    @staticmethod
    def _ensure_fts(table: LanceTable) -> None:
        if not any(index.index_type == "FTS" for index in table.list_indices()):
            table.create_index("content", config=FTS())


def _filter(repo_key: str, include_superseded: bool) -> str:
    base = f"repo_key = {_literal(repo_key)}"
    return base if include_superseded else f"{base} AND status = 'active'"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _embedding_text(document: RecallDocument) -> str:
    return f"{document.title}\n{document.content}"


def _candidate(row: dict[str, object], *, vector: bool = False) -> IndexCandidate:
    score = None
    if vector:
        distance = row["_distance"]
        if not isinstance(distance, int | float):
            raise ValueError("Vector search returned an invalid distance")
        score = 1.0 - distance
        if not math.isfinite(score):
            raise ValueError("Vector search returned a non-finite relevance score")
    return IndexCandidate(
        memory_id=str(row["memory_id"]), document_id=str(row["document_id"]), content=str(row["content"]), relevance_score=score
    )
