from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
from lancedb.db import DBConnection  # type: ignore[import-untyped]
from lancedb.index import FTS  # type: ignore[import-untyped]
from lancedb.table import LanceTable  # type: ignore[import-untyped]

from codecairn.memory.embedding import VECTOR_DIMENSION, EmbeddingProvider, HashingEmbedder
from codecairn.memory.models import (
    CodingMemory,
    IndexCandidate,
    RecallDocument,
    RecallDocumentFingerprint,
    RecallDocumentKind,
)
from codecairn.memory.projection import compute_document_sha256, project_recall_documents

_TABLE_NAME = "coding_memories"
_SCHEMA = pa.schema(
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
        records = self._records(memory, markdown=markdown)
        table.delete(
            f"repo_key = {_sql_literal(memory.repo_key)} "
            f"AND memory_id = {_sql_literal(memory.memory_id)}"
        )
        table.add(pa.Table.from_pylist(records, schema=_SCHEMA))
        self._ensure_fts(table)

    def delete(self, *, repo_key: str, memory_id: str) -> None:
        table = self._table(create=False)
        if table is None:
            return
        table.delete(
            f"repo_key = {_sql_literal(repo_key)} AND memory_id = {_sql_literal(memory_id)}"
        )

    def replace_all(self, memories: tuple[tuple[CodingMemory, str], ...]) -> None:
        records = [
            record
            for memory, markdown in memories
            for record in self._records(memory, markdown=markdown)
        ]
        data = pa.Table.from_pylist(records, schema=_SCHEMA)
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

    def fingerprint_snapshot(
        self,
    ) -> tuple[set[tuple[str, str, str]], set[RecallDocumentFingerprint]]:
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
        return memory_fingerprints, document_fingerprints

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
            .where(
                f"repo_key = {_sql_literal(repo_key)} AND document_kind = 'episode'",
                prefilter=True,
            )
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
            .where(
                f"repo_key = {_sql_literal(repo_key)} AND document_kind = 'episode'",
                prefilter=True,
            )
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
        migrated = [self._migrate_legacy_row(row) for row in rows]
        data = pa.Table.from_pylist(migrated, schema=_SCHEMA)
        migrated_table = connection.create_table(_TABLE_NAME, data=data, mode="overwrite")
        if migrated:
            self._ensure_fts(migrated_table)
        return migrated_table

    def _records(self, memory: CodingMemory, *, markdown: str) -> list[dict[str, object]]:
        documents = project_recall_documents(memory, markdown=markdown)
        return [self._record(document) for document in documents]

    def _record(self, document: RecallDocument) -> dict[str, object]:
        vector = self._embedder.embed(f"{document.title}\n{document.summary}\n{document.content}")
        _validate_vector(vector)
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
            "vector": list(vector),
        }

    def _migrate_legacy_row(self, row: dict[str, object]) -> dict[str, object]:
        if "document_id" in row:
            return {
                field.name: (
                    list(
                        self._embedder.embed(f"{row['title']}\n{row['summary']}\n{row['content']}")
                    )
                    if field.name == "vector"
                    else row[field.name]
                )
                for field in _SCHEMA
            }
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
        episode = project_recall_documents(memory, markdown=str(row["content"]))[0]
        return self._record(episode)

    def _validated_rows(
        self,
    ) -> tuple[tuple[dict[str, object], tuple[dict[str, object], ...]], ...]:
        table = self._table(create=False)
        if table is None:
            return ()
        rows = cast(list[dict[str, object]], table.to_arrow().to_pylist())
        groups: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in rows:
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


def _validate_vector(vector: tuple[float, ...]) -> None:
    if len(vector) != VECTOR_DIMENSION:
        raise ValueError(f"Embedding must have {VECTOR_DIMENSION} dimensions")


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
    return float(value)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
