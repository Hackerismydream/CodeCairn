from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from codecairn.memory.models import (
    CodingMemory,
    EvidenceReference,
    ImportCheckpoint,
    MemoryRepairPlan,
    PendingRecoveryAudit,
)
from codecairn.memory.trace import EMPTY_RAW_PREFIX_SHA256, stable_id


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
            rows = connection.execute(
                """
                SELECT provider, session_id, committed_raw_event_index,
                       resume_raw_event_index, resume_prefix_sha256,
                       resume_call_ids_json, resume_file_change_fact_count
                FROM imports
                WHERE repo_key = ? AND source_path = ?
                """,
                (repo_key, source_path),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError(f"Import source has conflicting provider checkpoints: {source_path}")
        row = rows[0]
        return ImportCheckpoint(
            provider=row["provider"],
            session_id=row["session_id"],
            committed_raw_event_index=row["committed_raw_event_index"],
            resume_raw_event_index=row["resume_raw_event_index"],
            resume_prefix_sha256=row["resume_prefix_sha256"],
            resume_call_ids=_parse_call_ids(row["resume_call_ids_json"]),
            resume_file_change_fact_count=row["resume_file_change_fact_count"],
        )

    def list_pending_recoveries(self, *, repo_key: str) -> tuple[PendingRecoveryAudit, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT audit_id, repo_key, memory_id, reason,
                       observed_sha256, expected_sha256
                FROM recovery_audit
                WHERE repo_key = ? AND status = 'started'
                ORDER BY audit_id
                """,
                (repo_key,),
            ).fetchall()
        return tuple(
            PendingRecoveryAudit(
                audit_id=row["audit_id"],
                plan=MemoryRepairPlan(
                    repo_key=row["repo_key"],
                    memory_id=row["memory_id"],
                    reason=row["reason"],
                    observed_sha256=row["observed_sha256"],
                    expected_sha256=row["expected_sha256"],
                ),
            )
            for row in rows
        )

    def start_recovery(self, plan: MemoryRepairPlan) -> int:
        operation_key = stable_id(
            "recovery",
            plan.repo_key,
            plan.memory_id,
            plan.reason,
            plan.observed_sha256,
            plan.expected_sha256,
        )
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO recovery_audit (
                        operation_key, repo_key, memory_id, reason,
                        observed_sha256, expected_sha256, status, error_type
                    ) VALUES (?, ?, ?, ?, ?, ?, 'started', NULL)
                    """,
                    (
                        operation_key,
                        plan.repo_key,
                        plan.memory_id,
                        plan.reason,
                        plan.observed_sha256,
                        plan.expected_sha256,
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return a recovery audit id")
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT audit_id
                    FROM recovery_audit
                    WHERE operation_key = ? AND status = 'started'
                    """,
                    (operation_key,),
                ).fetchone()
                if row is None:
                    raise
                return int(row["audit_id"])

    def finish_recovery(
        self,
        audit_id: int,
        *,
        status: Literal["completed", "failed"],
        error_type: str | None = None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE recovery_audit
                SET status = ?, error_type = ?
                WHERE audit_id = ? AND status = 'started'
                """,
                (status, error_type, audit_id),
            )
            if cursor.rowcount == 1:
                return
            current = connection.execute(
                "SELECT status FROM recovery_audit WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"Recovery audit does not exist: {audit_id}")
            if current["status"] == "failed" and status == "completed":
                connection.execute(
                    """
                    UPDATE recovery_audit
                    SET status = 'completed', error_type = NULL
                    WHERE audit_id = ? AND status = 'failed'
                    """,
                    (audit_id,),
                )

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
        resume_raw_event_index: int,
        resume_prefix_sha256: str,
        resume_call_ids: tuple[str, ...],
        resume_file_change_fact_count: int,
        memories: tuple[CodingMemory, ...],
    ) -> int:
        if raw_event_count < 0 or committed_raw_event_index != raw_event_count - 1:
            raise ValueError("Committed cursor must identify the final observed raw event")
        if not 0 <= resume_raw_event_index <= raw_event_count:
            raise ValueError("Resume checkpoint must be inside the observed trace")
        if resume_file_change_fact_count < 0:
            raise ValueError("Resume file-change fact count must not be negative")
        if len(resume_call_ids) != len(set(resume_call_ids)):
            raise ValueError("Resume checkpoint call IDs must be unique")
        created_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                """
                SELECT committed_raw_event_index
                FROM imports
                WHERE repo_key = ? AND provider = ? AND source_path = ?
                """,
                (repo_key, provider, source_path),
            ).fetchone()
            if prior is not None and committed_raw_event_index < prior["committed_raw_event_index"]:
                raise ValueError("Committed import cursor must not move backwards")
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
                    raw_event_count, committed_raw_event_index,
                    resume_raw_event_index, resume_prefix_sha256,
                    resume_call_ids_json, resume_file_change_fact_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key, provider, source_path) DO UPDATE SET
                    session_id = excluded.session_id,
                    source_sha256 = excluded.source_sha256,
                    raw_event_count = excluded.raw_event_count,
                    committed_raw_event_index = excluded.committed_raw_event_index,
                    resume_raw_event_index = excluded.resume_raw_event_index,
                    resume_prefix_sha256 = excluded.resume_prefix_sha256,
                    resume_call_ids_json = excluded.resume_call_ids_json,
                    resume_file_change_fact_count = excluded.resume_file_change_fact_count
                """,
                (
                    repo_key,
                    provider,
                    session_id,
                    source_path,
                    source_sha256,
                    raw_event_count,
                    committed_raw_event_index,
                    resume_raw_event_index,
                    resume_prefix_sha256,
                    json.dumps(resume_call_ids),
                    resume_file_change_fact_count,
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
                    resume_raw_event_index INTEGER NOT NULL,
                    resume_prefix_sha256 TEXT NOT NULL,
                    resume_call_ids_json TEXT NOT NULL,
                    resume_file_change_fact_count INTEGER NOT NULL,
                    PRIMARY KEY (repo_key, provider, source_path)
                );
                CREATE TABLE IF NOT EXISTS recovery_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_key TEXT NOT NULL,
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    observed_sha256 TEXT,
                    expected_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
                    error_type TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS recovery_audit_active_operation
                ON recovery_audit(operation_key)
                WHERE status = 'started';
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(imports)").fetchall()
            }
            if "resume_raw_event_index" not in columns:
                connection.execute(
                    "ALTER TABLE imports "
                    "ADD COLUMN resume_raw_event_index INTEGER NOT NULL DEFAULT 0"
                )
            if "resume_prefix_sha256" not in columns:
                connection.execute(
                    "ALTER TABLE imports ADD COLUMN resume_prefix_sha256 "
                    f"TEXT NOT NULL DEFAULT '{EMPTY_RAW_PREFIX_SHA256}'"
                )
            if "resume_call_ids_json" not in columns:
                connection.execute(
                    "ALTER TABLE imports ADD COLUMN resume_call_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "resume_file_change_fact_count" not in columns:
                connection.execute(
                    "ALTER TABLE imports "
                    "ADD COLUMN resume_file_change_fact_count INTEGER NOT NULL DEFAULT 0"
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


def _parse_call_ids(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("Import checkpoint call IDs are invalid")
    return tuple(parsed)


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
