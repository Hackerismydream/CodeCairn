from __future__ import annotations

from pathlib import Path
from typing import cast

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
from lancedb.db import DBConnection  # type: ignore[import-untyped]
from lancedb.index import FTS  # type: ignore[import-untyped]
from lancedb.table import LanceTable  # type: ignore[import-untyped]

from codecairn.memory.embedding import VECTOR_DIMENSION, EmbeddingProvider, HashingEmbedder
from codecairn.memory.models import CodingMemory, IndexCandidate

_TABLE_NAME = "coding_memories"
_SCHEMA = pa.schema(
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


class LanceMemoryIndex:
    """Disposable LanceDB projection of Coding Memory Markdown."""

    def __init__(self, path: Path, *, embedder: EmbeddingProvider | None = None) -> None:
        self._path = path
        self._embedder = embedder or HashingEmbedder()

    def upsert(self, memory: CodingMemory, *, markdown: str) -> None:
        table = self._table(create=True)
        assert table is not None
        record = self._record(memory, markdown=markdown)
        data = pa.Table.from_pylist([record], schema=_SCHEMA)
        (
            table.merge_insert(["repo_key", "memory_id"])
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(data)
        )
        self._ensure_fts(table)

    def delete(self, *, repo_key: str, memory_id: str) -> None:
        table = self._table(create=False)
        if table is None:
            return
        table.delete(
            f"repo_key = {_sql_literal(repo_key)} AND memory_id = {_sql_literal(memory_id)}"
        )

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None:
        records = [self._record(memory, markdown=markdown) for memory, markdown in memories]
        data = pa.Table.from_pylist(records, schema=_SCHEMA)
        table = self._connection().create_table(
            _TABLE_NAME,
            data=data,
            mode="overwrite",
        )
        if records:
            self._ensure_fts(table)

    def fingerprints(self) -> set[tuple[str, str, str]]:
        table = self._table(create=False)
        if table is None:
            return set()
        rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        return {
            (str(row["repo_key"]), str(row["memory_id"]), str(row["content_sha256"]))
            for row in rows
        }

    def vector_candidates(
        self,
        *,
        repo_key: str,
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        _validate_vector(vector)
        table = self._table(create=False)
        if table is None or table.count_rows() == 0:
            return ()
        rows = cast(
            list[dict[str, object]],
            table.search(list(vector), query_type="vector")
            .where(f"repo_key = {_sql_literal(repo_key)}", prefilter=True)
            .limit(limit)
            .to_list(),
        )
        return tuple(
            IndexCandidate(
                repo_key=str(row["repo_key"]),
                memory_id=str(row["memory_id"]),
                score=1.0 / (1.0 + _required_float(row["_distance"])),
            )
            for row in rows
        )

    def lexical_candidates(
        self,
        *,
        repo_key: str,
        query: str,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        table = self._table(create=False)
        if table is None or table.count_rows() == 0:
            return ()
        self._ensure_fts(table)
        rows = cast(
            list[dict[str, object]],
            table.search(query, query_type="fts", fts_columns="content")
            .where(f"repo_key = {_sql_literal(repo_key)}", prefilter=True)
            .limit(limit)
            .to_list(),
        )
        return tuple(
            IndexCandidate(
                repo_key=str(row["repo_key"]),
                memory_id=str(row["memory_id"]),
                score=_required_float(row["_score"]),
            )
            for row in rows
        )

    def _connection(self) -> DBConnection:
        self._path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(self._path)

    def _table(self, *, create: bool) -> LanceTable | None:
        connection = self._connection()
        if _TABLE_NAME not in set(connection.list_tables(limit=10_000).tables):
            if not create:
                return None
            return connection.create_table(_TABLE_NAME, schema=_SCHEMA)
        table = connection.open_table(_TABLE_NAME)
        if table.schema.names == _SCHEMA.names:
            return table
        rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        migrated = [
            {
                field.name: (
                    list(
                        self._embedder.embed(f"{row['title']}\n{row['summary']}\n{row['content']}")
                    )
                    if field.name == "vector"
                    else row[field.name]
                )
                for field in _SCHEMA
            }
            for row in rows
        ]
        data = pa.Table.from_pylist(migrated, schema=_SCHEMA)
        return connection.create_table(_TABLE_NAME, data=data, mode="overwrite")

    def _record(self, memory: CodingMemory, *, markdown: str) -> dict[str, object]:
        if memory.content_sha256 is None:
            raise ValueError("Cannot index a memory without a Markdown digest")
        vector = self._embedder.embed(f"{memory.title}\n{memory.summary}\n{markdown}")
        _validate_vector(vector)
        return {
            "repo_key": memory.repo_key,
            "memory_id": memory.memory_id,
            "content_sha256": memory.content_sha256,
            "memory_type": memory.memory_type,
            "title": memory.title,
            "summary": memory.summary,
            "content": markdown,
            "vector": list(vector),
        }

    @staticmethod
    def _ensure_fts(table: LanceTable) -> None:
        if any(index.index_type == "FTS" for index in table.list_indices()):
            return
        table.create_index("content", config=FTS())


def _validate_vector(vector: tuple[float, ...]) -> None:
    if len(vector) != VECTOR_DIMENSION:
        raise ValueError(f"Embedding must have {VECTOR_DIMENSION} dimensions")


def _required_float(value: object) -> float:
    if not isinstance(value, int | float):
        raise ValueError("LanceDB returned a non-numeric search score")
    return float(value)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
