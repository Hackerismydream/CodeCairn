from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codecairn.memory.models import CodingMemory, EvidenceReference


class SQLiteState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._initialize()

    def commit_import(
        self,
        *,
        repo_key: str,
        provider: str,
        session_id: str,
        source_path: str,
        source_sha256: str,
        raw_event_count: int,
        committed_raw_event_index: int,
        memories: tuple[CodingMemory, ...],
    ) -> int:
        created_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for memory in memories:
                if memory.markdown_path is None or memory.content_sha256 is None:
                    raise ValueError("Cannot commit a memory before Markdown persistence")
                cursor = connection.execute(
                    """
                    INSERT INTO memories (
                        repo_key, memory_id, memory_type, title, summary,
                        episode_id, command, exit_code, evidence_json,
                        markdown_path, content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repo_key, memory_id) DO NOTHING
                    """,
                    (
                        memory.repo_key,
                        memory.memory_id,
                        memory.memory_type,
                        memory.title,
                        memory.summary,
                        memory.episode_id,
                        memory.command,
                        memory.exit_code,
                        json.dumps([_evidence_dict(item) for item in memory.evidence]),
                        memory.markdown_path,
                        memory.content_sha256,
                    ),
                )
                created_count += cursor.rowcount
                if cursor.rowcount == 0:
                    stored = connection.execute(
                        """
                        SELECT content_sha256
                        FROM memories
                        WHERE repo_key = ? AND memory_id = ?
                        """,
                        (memory.repo_key, memory.memory_id),
                    ).fetchone()
                    if stored is None or stored["content_sha256"] != memory.content_sha256:
                        raise ValueError(
                            f"Committed memory conflicts with candidate: {memory.memory_id}"
                        )
            connection.execute(
                """
                INSERT INTO imports (
                    repo_key, provider, session_id, source_path, source_sha256,
                    raw_event_count, committed_raw_event_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key, provider, source_path) DO UPDATE SET
                    session_id = excluded.session_id,
                    source_sha256 = excluded.source_sha256,
                    raw_event_count = excluded.raw_event_count,
                    committed_raw_event_index = excluded.committed_raw_event_index
                """,
                (
                    repo_key,
                    provider,
                    session_id,
                    source_path,
                    source_sha256,
                    raw_event_count,
                    committed_raw_event_index,
                ),
            )
        return created_count

    def list_memories(self, *, repo_key: str) -> tuple[CodingMemory, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, memory_type, title, summary, episode_id,
                       command, exit_code, evidence_json, markdown_path,
                       content_sha256
                FROM memories
                WHERE repo_key = ?
                ORDER BY memory_id
                """,
                (repo_key,),
            ).fetchall()
        return tuple(_memory_from_row(repo_key, row) for row in rows)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS memories (
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    command TEXT,
                    exit_code INTEGER,
                    evidence_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY (repo_key, memory_id)
                );
                CREATE TABLE IF NOT EXISTS imports (
                    repo_key TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    raw_event_count INTEGER NOT NULL,
                    committed_raw_event_index INTEGER NOT NULL,
                    PRIMARY KEY (repo_key, provider, source_path)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


def _evidence_dict(evidence: EvidenceReference) -> dict[str, object]:
    return {
        "provider": evidence.provider,
        "session_id": evidence.session_id,
        "source_path": evidence.source_path,
        "raw_event_sha256": evidence.raw_event_sha256,
        "raw_event_index": evidence.raw_event_index,
        "raw_event_type": evidence.raw_event_type,
        "call_id": evidence.call_id,
    }


def _memory_from_row(repo_key: str, row: sqlite3.Row) -> CodingMemory:
    evidence = tuple(EvidenceReference(**item) for item in json.loads(row["evidence_json"]))
    return CodingMemory(
        memory_id=row["memory_id"],
        repo_key=repo_key,
        memory_type=row["memory_type"],
        title=row["title"],
        summary=row["summary"],
        episode_id=row["episode_id"],
        command=row["command"],
        exit_code=row["exit_code"],
        evidence=evidence,
        markdown_path=row["markdown_path"],
        content_sha256=row["content_sha256"],
    )
