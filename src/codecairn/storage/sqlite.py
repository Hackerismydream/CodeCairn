"""SQLite operational state and immutable mirrors for version 0.1."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from codecairn.memory.models import (
    ImportCheckpoint,
    IndexHealth,
    MemoryArtifact,
    OperationalCounts,
)
from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    IdentityConflict,
    LegacyRootUnsupported,
    UserPreferencePayload,
    canonical_json,
    coding_memory_from_dict,
    coding_memory_to_dict,
    evidence_fact_from_dict,
    evidence_fact_to_dict,
)

_SCHEMA_REVISION = "codecairn-v01-1"


class SQLiteState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._initialize()

    def get_checkpoint(
        self,
        *,
        repo_key: str,
        source_path: str,
    ) -> ImportCheckpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT provider, session_id, committed_raw_event_index,
                       resume_raw_event_index, resume_prefix_sha256,
                       resume_call_ids_json, resume_file_change_fact_count
                FROM imports
                WHERE repo_key = ? AND source_path = ?
                """,
                (repo_key, source_path),
            ).fetchone()
        if row is None:
            return None
        return ImportCheckpoint(
            provider=row["provider"],
            session_id=row["session_id"],
            committed_raw_event_index=row["committed_raw_event_index"],
            resume_raw_event_index=row["resume_raw_event_index"],
            resume_prefix_sha256=row["resume_prefix_sha256"],
            resume_call_ids=_string_tuple(row["resume_call_ids_json"]),
            resume_file_change_fact_count=row["resume_file_change_fact_count"],
        )

    def commit_checkpoint(
        self,
        *,
        repo_key: str,
        provider: str,
        session_id: str,
        source_path: str,
        source_sha256: str,
        raw_event_count: int,
        committed_raw_event_index: int,
        resume_raw_event_index: int,
        resume_prefix_sha256: str,
        resume_call_ids: tuple[str, ...],
        resume_file_change_fact_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO imports (
                    repo_key, source_path, provider, session_id, source_sha256,
                    raw_event_count, committed_raw_event_index,
                    resume_raw_event_index, resume_prefix_sha256,
                    resume_call_ids_json, resume_file_change_fact_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key, source_path) DO UPDATE SET
                    provider = excluded.provider,
                    session_id = excluded.session_id,
                    source_sha256 = excluded.source_sha256,
                    raw_event_count = excluded.raw_event_count,
                    committed_raw_event_index = excluded.committed_raw_event_index,
                    resume_raw_event_index = excluded.resume_raw_event_index,
                    resume_prefix_sha256 = excluded.resume_prefix_sha256,
                    resume_call_ids_json = excluded.resume_call_ids_json,
                    resume_file_change_fact_count =
                        excluded.resume_file_change_fact_count
                """,
                (
                    repo_key,
                    source_path,
                    provider,
                    session_id,
                    source_sha256,
                    raw_event_count,
                    committed_raw_event_index,
                    resume_raw_event_index,
                    resume_prefix_sha256,
                    json.dumps(resume_call_ids),
                    resume_file_change_fact_count,
                ),
            )

    def store_source_facts(self, facts: tuple[EvidenceFact, ...]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for fact in facts:
                encoded = canonical_json(evidence_fact_to_dict(fact))
                existing = connection.execute(
                    """
                    SELECT canonical_fact_json
                    FROM source_fact_registry
                    WHERE repo_key = ? AND fact_id = ?
                    """,
                    (fact.repo_key, fact.fact_id),
                ).fetchone()
                if existing is not None:
                    if existing["canonical_fact_json"] != encoded:
                        raise IdentityConflict(
                            f"Source Fact identity conflicts with registry: {fact.fact_id}"
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO source_fact_registry (
                        repo_key, fact_id, provider, session_id, source_generation,
                        event_index, event_id, role, fact_kind, event_sha256,
                        source_path_sha256, canonical_fact_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact.repo_key,
                        fact.fact_id,
                        fact.reference.provider,
                        fact.reference.session_id,
                        fact.reference.source_generation,
                        fact.reference.event_index,
                        fact.reference.event_id,
                        fact.role,
                        fact.fact_kind,
                        fact.reference.event_sha256,
                        fact.reference.source_path_sha256,
                        encoded,
                    ),
                )

    def resolve_source_facts(
        self,
        *,
        repo_key: str,
        fact_ids: tuple[str, ...],
    ) -> tuple[EvidenceFact, ...]:
        if not fact_ids:
            return ()
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Source Fact IDs must be unique")
        placeholders = ",".join("?" for _item in fact_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT fact_id, canonical_fact_json
                FROM source_fact_registry
                WHERE repo_key = ? AND fact_id IN ({placeholders})
                """,
                (repo_key, *fact_ids),
            ).fetchall()
        by_id = {
            row["fact_id"]: evidence_fact_from_dict(json.loads(row["canonical_fact_json"]))
            for row in rows
        }
        missing = [fact_id for fact_id in fact_ids if fact_id not in by_id]
        if missing:
            raise KeyError(f"Unknown Source Fact IDs: {', '.join(missing)}")
        return tuple(by_id[fact_id] for fact_id in fact_ids)

    def store_memory(self, artifact: MemoryArtifact) -> bool:
        memory = artifact.memory
        encoded = canonical_json(coding_memory_to_dict(memory))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT canonical_memory_json, markdown_path, content_sha256
                FROM memories
                WHERE repo_key = ? AND memory_id = ?
                """,
                (memory.repo_key, memory.memory_id),
            ).fetchone()
            expected_metadata = (str(artifact.path), artifact.content_sha256)
            if existing is not None:
                if (
                    existing["canonical_memory_json"] != encoded
                    or (existing["markdown_path"], existing["content_sha256"]) != expected_metadata
                ):
                    raise IdentityConflict(
                        f"Coding Memory identity conflicts with state: {memory.memory_id}"
                    )
                return False
            _validate_fact_links(connection, memory)
            connection.execute(
                """
                INSERT INTO memories (
                    repo_key, memory_id, memory_type, canonical_memory_json,
                    markdown_path, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.repo_key,
                    memory.memory_id,
                    memory.memory_type,
                    encoded,
                    *expected_metadata,
                ),
            )
        return True

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT canonical_memory_json
                FROM memories
                WHERE repo_key = ?
                ORDER BY memory_id
                """,
                (repo_key,),
            ).fetchall()
        return tuple(
            coding_memory_from_dict(json.loads(row["canonical_memory_json"])) for row in rows
        )

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT canonical_memory_json
                FROM memories
                WHERE repo_key = ? AND memory_id = ?
                """,
                (repo_key, memory_id),
            ).fetchone()
        return (
            None
            if row is None
            else coding_memory_from_dict(json.loads(row["canonical_memory_json"]))
        )

    def operational_counts(self) -> OperationalCounts:
        with self._connect() as connection:
            imports = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(raw_event_count), 0) AS events FROM imports"
            ).fetchone()
            memories = connection.execute("SELECT COUNT(*) AS count FROM memories").fetchone()
        return OperationalCounts(
            import_count=int(imports["count"]),
            observed_event_count=int(imports["events"]),
            memory_count=int(memories["count"]),
            pending_recovery_count=0,
        )

    def index_health(self) -> IndexHealth:
        count = self.operational_counts().memory_count
        return IndexHealth(pending=count, leased=0, indexed=0, failed=0, stale=0)

    def _initialize(self) -> None:
        with self._connect() as connection:
            existing = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
                if row["name"] != "sqlite_sequence"
            }
            if existing and "codecairn_meta" not in existing:
                raise LegacyRootUnsupported(
                    "Pre-v0.1 SQLite state is unsupported; use a fresh root and re-import"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS codecairn_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    repo_key TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    raw_event_count INTEGER NOT NULL,
                    committed_raw_event_index INTEGER NOT NULL,
                    resume_raw_event_index INTEGER NOT NULL,
                    resume_prefix_sha256 TEXT NOT NULL,
                    resume_call_ids_json TEXT NOT NULL,
                    resume_file_change_fact_count INTEGER NOT NULL,
                    PRIMARY KEY (repo_key, source_path)
                );
                CREATE TABLE IF NOT EXISTS source_fact_registry (
                    repo_key TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_generation INTEGER NOT NULL,
                    event_index INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    role TEXT,
                    fact_kind TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    source_path_sha256 TEXT NOT NULL,
                    canonical_fact_json TEXT NOT NULL,
                    PRIMARY KEY (repo_key, fact_id),
                    UNIQUE (
                        repo_key, provider, session_id, source_generation,
                        event_index, fact_id
                    )
                );
                CREATE TABLE IF NOT EXISTS memories (
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    canonical_memory_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY (repo_key, memory_id)
                );
                """
            )
            row = connection.execute(
                "SELECT value FROM codecairn_meta WHERE key = 'schema_revision'"
            ).fetchone()
            if row is not None and row["value"] != _SCHEMA_REVISION:
                raise LegacyRootUnsupported(
                    "Unsupported SQLite schema; use a fresh root and re-import"
                )
            connection.execute(
                """
                INSERT INTO codecairn_meta (key, value)
                VALUES ('schema_revision', ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (_SCHEMA_REVISION,),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _validate_fact_links(connection: sqlite3.Connection, memory: CodingMemory) -> None:
    fact_ids = (
        memory.payload.source_fact_ids
        if isinstance(memory.payload, UserPreferencePayload)
        else tuple(fact.fact_id for fact in memory.facts)
    )
    for fact_id in fact_ids:
        row = connection.execute(
            """
            SELECT role
            FROM source_fact_registry
            WHERE repo_key = ? AND fact_id = ?
            """,
            (memory.repo_key, fact_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Source Fact ID: {fact_id}")
        if isinstance(memory.payload, UserPreferencePayload) and row["role"] != "user":
            raise ValueError("User Preference requires user-authored Source Facts")


def _string_tuple(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("Import checkpoint call IDs are invalid")
    if len(parsed) != len(set(parsed)):
        raise ValueError("Import checkpoint call IDs must be unique")
    return tuple(parsed)
