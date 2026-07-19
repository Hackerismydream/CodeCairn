from __future__ import annotations

from pathlib import Path
from typing import cast

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
from lancedb.db import DBConnection  # type: ignore[import-untyped]
from lancedb.table import LanceTable  # type: ignore[import-untyped]

from codecairn.memory.models import CodingMemory

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
    ]
)


class LanceMemoryIndex:
    """Disposable LanceDB projection of Coding Memory Markdown."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def upsert(self, memory: CodingMemory, *, markdown: str) -> None:
        table = self._table(create=True)
        assert table is not None
        record = _record(memory, markdown=markdown)
        data = pa.Table.from_pylist([record], schema=_SCHEMA)
        (
            table.merge_insert(["repo_key", "memory_id"])
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(data)
        )

    def delete(self, *, repo_key: str, memory_id: str) -> None:
        table = self._table(create=False)
        if table is None:
            return
        table.delete(
            f"repo_key = {_sql_literal(repo_key)} AND memory_id = {_sql_literal(memory_id)}"
        )

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None:
        records = [_record(memory, markdown=markdown) for memory, markdown in memories]
        data = pa.Table.from_pylist(records, schema=_SCHEMA)
        self._connection().create_table(
            _TABLE_NAME,
            data=data,
            mode="overwrite",
        )

    def fingerprints(self) -> set[tuple[str, str, str]]:
        table = self._table(create=False)
        if table is None:
            return set()
        rows = cast(list[dict[str, str]], table.to_arrow().to_pylist())
        return {(row["repo_key"], row["memory_id"], row["content_sha256"]) for row in rows}

    def _connection(self) -> DBConnection:
        self._path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(self._path)

    def _table(self, *, create: bool) -> LanceTable | None:
        connection = self._connection()
        if _TABLE_NAME not in set(connection.list_tables(limit=10_000).tables):
            if not create:
                return None
            return connection.create_table(_TABLE_NAME, schema=_SCHEMA)
        return connection.open_table(_TABLE_NAME)


def _record(memory: CodingMemory, *, markdown: str) -> dict[str, str]:
    if memory.content_sha256 is None:
        raise ValueError("Cannot index a memory without a Markdown digest")
    return {
        "repo_key": memory.repo_key,
        "memory_id": memory.memory_id,
        "content_sha256": memory.content_sha256,
        "memory_type": memory.memory_type,
        "title": memory.title,
        "summary": memory.summary,
        "content": markdown,
    }


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
