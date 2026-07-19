from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal, cast

from codecairn.memory.models import (
    CodingMemory,
    EvidenceReference,
    GateAudit,
    GateDecision,
    GateDecisionReason,
    ImportCheckpoint,
    IndexHealth,
    IndexJob,
    IndexOperation,
    MemoryProposal,
    MemoryRepairPlan,
    MemoryType,
    OperationalCounts,
    PendingRecoveryAudit,
    ReconcileReport,
    TruthScan,
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

    def operational_counts(self) -> OperationalCounts:
        with self._connect() as connection:
            imports = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(raw_event_count), 0) AS events FROM imports"
            ).fetchone()
            memories = connection.execute("SELECT COUNT(*) AS count FROM memories").fetchone()
            audits = connection.execute("SELECT COUNT(*) AS count FROM gate_audit").fetchone()
            recoveries = connection.execute(
                "SELECT COUNT(*) AS count FROM recovery_audit WHERE status = 'started'"
            ).fetchone()
        return OperationalCounts(
            import_count=int(imports["count"]),
            observed_event_count=int(imports["events"]),
            memory_count=int(memories["count"]),
            gate_audit_count=int(audits["count"]),
            pending_recovery_count=int(recoveries["count"]),
        )

    def commit_gate_decision(
        self,
        decision: GateDecision,
        *,
        proposal: MemoryProposal,
    ) -> None:
        if proposal.proposal_id != decision.proposal_id or proposal.repo_key != decision.repo_key:
            raise ValueError("Gate decision does not match its audited proposal")
        memory = decision.memory
        if decision.accepted != (memory is not None):
            raise ValueError("Accepted gate decisions must carry exactly one memory")
        if memory is not None and memory.repo_key != decision.repo_key:
            raise ValueError("Gate decision memory crosses repository namespace")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if memory is not None and _insert_memory(connection, memory):
                _enqueue_index_revision(connection, memory, operation="upsert")
            memory_id = memory.memory_id if memory is not None else None
            connection.execute(
                """
                INSERT INTO gate_audit (
                    proposal_id, repo_key, memory_type, accepted, reason,
                    proposal_title, proposal_summary, proposed_quote,
                    proposed_quote_role, proposal_confidence,
                    proposed_fact_ids_json, resolved_fact_ids_json, memory_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key, proposal_id) DO NOTHING
                """,
                (
                    decision.proposal_id,
                    decision.repo_key,
                    decision.memory_type,
                    int(decision.accepted),
                    decision.reason,
                    proposal.title,
                    proposal.summary,
                    proposal.quote,
                    proposal.quote_role,
                    proposal.confidence,
                    json.dumps(decision.proposed_fact_ids),
                    json.dumps(decision.resolved_fact_ids),
                    memory_id,
                ),
            )
            row = connection.execute(
                """
                SELECT memory_type, accepted, reason, proposal_title,
                       proposal_summary, proposed_quote, proposed_quote_role,
                       proposal_confidence, proposed_fact_ids_json,
                       resolved_fact_ids_json, memory_id
                FROM gate_audit
                WHERE repo_key = ? AND proposal_id = ?
                """,
                (decision.repo_key, decision.proposal_id),
            ).fetchone()
            expected = (
                decision.memory_type,
                int(decision.accepted),
                decision.reason,
                proposal.title,
                proposal.summary,
                proposal.quote,
                proposal.quote_role,
                proposal.confidence,
                json.dumps(decision.proposed_fact_ids),
                json.dumps(decision.resolved_fact_ids),
                memory_id,
            )
            if row is None or tuple(row) != expected:
                raise ValueError(f"Gate audit conflicts with proposal: {decision.proposal_id}")

    def list_gate_audits(self, *, repo_key: str) -> tuple[GateAudit, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT audit_id, proposal_id, repo_key, memory_type, accepted,
                       reason, proposal_title, proposal_summary, proposed_quote,
                       proposed_quote_role, proposal_confidence,
                       proposed_fact_ids_json,
                       resolved_fact_ids_json, memory_id
                FROM gate_audit
                WHERE repo_key = ?
                ORDER BY audit_id
                """,
                (repo_key,),
            ).fetchall()
        return tuple(
            GateAudit(
                audit_id=row["audit_id"],
                proposal_id=row["proposal_id"],
                repo_key=row["repo_key"],
                memory_type=cast(MemoryType, row["memory_type"]),
                accepted=bool(row["accepted"]),
                reason=cast(GateDecisionReason, row["reason"]),
                proposal_title=row["proposal_title"],
                proposal_summary=row["proposal_summary"],
                proposed_quote=row["proposed_quote"],
                proposed_quote_role=row["proposed_quote_role"],
                proposal_confidence=row["proposal_confidence"],
                proposed_fact_ids=_parse_string_tuple(
                    row["proposed_fact_ids_json"],
                    field="proposed fact IDs",
                ),
                resolved_fact_ids=_parse_string_tuple(
                    row["resolved_fact_ids_json"],
                    field="resolved fact IDs",
                ),
                memory_id=row["memory_id"],
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
                inserted = _insert_memory(connection, memory)
                created_count += inserted
                if inserted:
                    _enqueue_index_revision(connection, memory, operation="upsert")
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
                       command, exit_code, evidence_json, fact_ids_json, markdown_path,
                       content_sha256
                FROM memories
                WHERE repo_key = ?
                ORDER BY memory_id
                """,
                (repo_key,),
            ).fetchall()
        return tuple(_memory_from_row(repo_key, row) for row in rows)

    def get_memory(self, *, repo_key: str, memory_id: str) -> CodingMemory | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT memory_id, memory_type, title, summary, episode_id,
                       command, exit_code, evidence_json, fact_ids_json, markdown_path,
                       content_sha256
                FROM memories
                WHERE repo_key = ? AND memory_id = ?
                """,
                (repo_key, memory_id),
            ).fetchone()
        return None if row is None else _memory_from_row(repo_key, row)

    def list_all_memories(self) -> tuple[CodingMemory, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT repo_key, memory_id, memory_type, title, summary, episode_id,
                       command, exit_code, evidence_json, fact_ids_json, markdown_path,
                       content_sha256
                FROM memories
                ORDER BY repo_key, memory_id
                """
            ).fetchall()
        return tuple(_memory_from_row(row["repo_key"], row) for row in rows)

    def reconcile_truth(self, scan: TruthScan) -> ReconcileReport:
        discovered = {(memory.repo_key, memory.memory_id): memory for memory in scan.memories}
        if len(discovered) != len(scan.memories):
            raise ValueError("Markdown truth contains duplicate memory identities")
        discovered_paths = {_required_markdown_path(memory) for memory in scan.memories}
        issue_paths = {issue.markdown_path for issue in scan.issues}
        created = 0
        modified = 0
        deleted = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_rows = connection.execute(
                "SELECT repo_key, memory_id, markdown_path, content_sha256 FROM memories"
            ).fetchall()
            existing = {(row["repo_key"], row["memory_id"]): row for row in existing_rows}
            connection.execute("DELETE FROM reconcile_issues")
            connection.executemany(
                """
                INSERT INTO reconcile_issues (
                    markdown_path, observed_sha256, error_type
                ) VALUES (?, ?, ?)
                """,
                [
                    (issue.markdown_path, issue.observed_sha256, issue.error_type)
                    for issue in scan.issues
                ],
            )
            for key, memory in discovered.items():
                prior = existing.get(key)
                if prior is None:
                    _insert_truth_memory(connection, memory)
                    _enqueue_index_revision(connection, memory, operation="upsert")
                    created += 1
                elif prior["content_sha256"] != memory.content_sha256:
                    _update_truth_memory(connection, memory)
                    _enqueue_index_revision(connection, memory, operation="upsert")
                    modified += 1
            for key, prior in existing.items():
                if key in discovered:
                    continue
                markdown_path = prior["markdown_path"]
                if markdown_path in issue_paths:
                    continue
                if markdown_path in discovered_paths:
                    raise ValueError("Markdown path changed memory identity")
                connection.execute(
                    "DELETE FROM memories WHERE repo_key = ? AND memory_id = ?",
                    key,
                )
                _enqueue_index_key(
                    connection,
                    repo_key=key[0],
                    memory_id=key[1],
                    content_sha256=prior["content_sha256"],
                    operation="delete",
                )
                deleted += 1
        return ReconcileReport(
            created=created,
            modified=modified,
            deleted=deleted,
            corrupt=len(scan.issues),
        )

    def claim_index_job(
        self,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> IndexJob | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT job_id, repo_key, memory_id, content_sha256, operation
                FROM index_queue
                WHERE status = 'pending'
                   OR (status = 'leased' AND lease_expires_at_ms <= ?)
                ORDER BY job_id
                LIMIT 1
                """,
                (now_ms,),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE index_queue
                SET status = 'leased', lease_owner = ?, lease_expires_at_ms = ?,
                    attempts = attempts + 1, error_type = NULL
                WHERE job_id = ?
                  AND (status = 'pending'
                       OR (status = 'leased' AND lease_expires_at_ms <= ?))
                """,
                (worker_id, now_ms + lease_ms, row["job_id"], now_ms),
            )
            if cursor.rowcount != 1:
                return None
            return IndexJob(
                job_id=row["job_id"],
                repo_key=row["repo_key"],
                memory_id=row["memory_id"],
                content_sha256=row["content_sha256"],
                operation=cast(IndexOperation, row["operation"]),
                lease_owner=worker_id,
            )

    def complete_index_job(self, job: IndexJob, *, update_fingerprint: bool = True) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE index_queue
                SET status = 'indexed', lease_owner = NULL,
                    lease_expires_at_ms = NULL, error_type = NULL
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (job.job_id, job.lease_owner),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Index lease is no longer owned: {job.job_id}")
            if not update_fingerprint:
                return
            if job.operation == "delete":
                connection.execute(
                    "DELETE FROM index_state WHERE repo_key = ? AND memory_id = ?",
                    (job.repo_key, job.memory_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO index_state (repo_key, memory_id, content_sha256)
                    VALUES (?, ?, ?)
                    ON CONFLICT(repo_key, memory_id) DO UPDATE SET
                        content_sha256 = excluded.content_sha256
                    """,
                    (job.repo_key, job.memory_id, job.content_sha256),
                )

    def fail_index_job(self, job: IndexJob, *, error_type: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE index_queue
                SET status = 'failed', lease_owner = NULL,
                    lease_expires_at_ms = NULL, error_type = ?
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (error_type, job.job_id, job.lease_owner),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Index lease is no longer owned: {job.job_id}")

    def index_health(self, *, now_ms: int) -> IndexHealth:
        with self._connect() as connection:
            queue = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'pending'
                                  OR (status = 'leased' AND lease_expires_at_ms <= ?)
                             THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'leased' AND lease_expires_at_ms > ?
                             THEN 1 ELSE 0 END) AS leased,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM index_queue
                """,
                (now_ms, now_ms),
            ).fetchone()
            indexed = connection.execute("SELECT COUNT(*) AS count FROM index_state").fetchone()
            stale = connection.execute("SELECT COUNT(*) AS count FROM reconcile_issues").fetchone()
        return IndexHealth(
            pending=int(queue["pending"] or 0),
            leased=int(queue["leased"] or 0),
            indexed=int(indexed["count"]),
            failed=int(queue["failed"] or 0),
            stale=int(stale["count"]),
        )

    def replace_index_state(self, fingerprints: set[tuple[str, str, str]]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM index_state")
            connection.executemany(
                """
                INSERT INTO index_state (repo_key, memory_id, content_sha256)
                VALUES (?, ?, ?)
                """,
                sorted(fingerprints),
            )
            connection.execute(
                """
                UPDATE index_queue
                SET status = 'indexed', lease_owner = NULL,
                    lease_expires_at_ms = NULL, error_type = NULL
                WHERE operation = 'upsert'
                  AND EXISTS (
                    SELECT 1 FROM index_state
                    WHERE index_state.repo_key = index_queue.repo_key
                      AND index_state.memory_id = index_queue.memory_id
                      AND index_state.content_sha256 = index_queue.content_sha256
                  )
                """
            )

            connection.execute(
                """
                UPDATE index_queue
                SET status = 'indexed', lease_owner = NULL,
                    lease_expires_at_ms = NULL, error_type = NULL
                WHERE operation = 'delete'
                  AND NOT EXISTS (
                    SELECT 1 FROM index_state
                    WHERE index_state.repo_key = index_queue.repo_key
                      AND index_state.memory_id = index_queue.memory_id
                  )
                """
            )

    def retry_failed_index_jobs(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE index_queue
                SET status = 'pending', error_type = NULL
                WHERE status = 'failed'
                """
            )
        return cursor.rowcount

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
                    fact_ids_json TEXT NOT NULL DEFAULT '[]',
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
                CREATE TABLE IF NOT EXISTS gate_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    repo_key TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
                    reason TEXT NOT NULL,
                    proposal_title TEXT NOT NULL,
                    proposal_summary TEXT NOT NULL,
                    proposed_quote TEXT,
                    proposed_quote_role TEXT,
                    proposal_confidence REAL,
                    proposed_fact_ids_json TEXT NOT NULL,
                    resolved_fact_ids_json TEXT NOT NULL,
                    memory_id TEXT,
                    UNIQUE (repo_key, proposal_id)
                );
                CREATE TABLE IF NOT EXISTS index_queue (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('upsert', 'delete')),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'leased', 'indexed', 'failed')
                    ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at_ms INTEGER,
                    error_type TEXT
                );
                CREATE TABLE IF NOT EXISTS index_state (
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY (repo_key, memory_id)
                );
                CREATE TABLE IF NOT EXISTS reconcile_issues (
                    markdown_path TEXT PRIMARY KEY,
                    observed_sha256 TEXT,
                    error_type TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS recovery_audit_active_operation
                ON recovery_audit(operation_key)
                WHERE status = 'started';
                CREATE UNIQUE INDEX IF NOT EXISTS index_queue_active_revision
                ON index_queue(repo_key, memory_id, content_sha256, operation)
                WHERE status IN ('pending', 'leased');
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
            memory_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "fact_ids_json" not in memory_columns:
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN fact_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            gate_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(gate_audit)").fetchall()
            }
            if "proposal_confidence" not in gate_columns:
                connection.execute("ALTER TABLE gate_audit ADD COLUMN proposal_confidence REAL")
            connection.execute(
                """
                INSERT INTO index_queue (
                    repo_key, memory_id, content_sha256, operation, status
                )
                SELECT memories.repo_key, memories.memory_id,
                       memories.content_sha256, 'upsert', 'pending'
                FROM memories
                WHERE NOT EXISTS (
                    SELECT 1 FROM index_state
                    WHERE index_state.repo_key = memories.repo_key
                      AND index_state.memory_id = memories.memory_id
                      AND index_state.content_sha256 = memories.content_sha256
                )
                  AND NOT EXISTS (
                    SELECT 1 FROM index_queue
                    WHERE index_queue.repo_key = memories.repo_key
                      AND index_queue.memory_id = memories.memory_id
                      AND index_queue.content_sha256 = memories.content_sha256
                      AND index_queue.operation = 'upsert'
                      AND index_queue.status IN ('pending', 'leased')
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
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


def _insert_memory(connection: sqlite3.Connection, memory: CodingMemory) -> int:
    if memory.markdown_path is None or memory.content_sha256 is None:
        raise ValueError("Cannot commit a memory before Markdown persistence")
    cursor = connection.execute(
        """
        INSERT INTO memories (
            repo_key, memory_id, memory_type, title, summary,
            episode_id, command, exit_code, evidence_json, fact_ids_json,
            markdown_path, content_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            json.dumps(memory.fact_ids),
            memory.markdown_path,
            memory.content_sha256,
        ),
    )
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
            raise ValueError(f"Committed memory conflicts with candidate: {memory.memory_id}")
    return cursor.rowcount


def _insert_truth_memory(connection: sqlite3.Connection, memory: CodingMemory) -> None:
    if _insert_memory(connection, memory) != 1:
        raise ValueError(f"Markdown truth conflicts with SQLite: {memory.memory_id}")


def _update_truth_memory(connection: sqlite3.Connection, memory: CodingMemory) -> None:
    markdown_path = _required_markdown_path(memory)
    if memory.content_sha256 is None:
        raise ValueError("Markdown truth is missing its content digest")
    cursor = connection.execute(
        """
        UPDATE memories
        SET memory_type = ?, title = ?, summary = ?, episode_id = ?,
            command = ?, exit_code = ?, evidence_json = ?, fact_ids_json = ?,
            markdown_path = ?, content_sha256 = ?
        WHERE repo_key = ? AND memory_id = ?
        """,
        (
            memory.memory_type,
            memory.title,
            memory.summary,
            memory.episode_id,
            memory.command,
            memory.exit_code,
            json.dumps([_evidence_dict(item) for item in memory.evidence]),
            json.dumps(memory.fact_ids),
            markdown_path,
            memory.content_sha256,
            memory.repo_key,
            memory.memory_id,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError(f"Markdown truth memory disappeared: {memory.memory_id}")


def _enqueue_index_revision(
    connection: sqlite3.Connection,
    memory: CodingMemory,
    *,
    operation: IndexOperation,
) -> None:
    if memory.content_sha256 is None:
        raise ValueError("Cannot index Markdown without its content digest")
    _enqueue_index_key(
        connection,
        repo_key=memory.repo_key,
        memory_id=memory.memory_id,
        content_sha256=memory.content_sha256,
        operation=operation,
    )


def _enqueue_index_key(
    connection: sqlite3.Connection,
    *,
    repo_key: str,
    memory_id: str,
    content_sha256: str,
    operation: IndexOperation,
) -> None:
    connection.execute(
        """
        INSERT INTO index_queue (
            repo_key, memory_id, content_sha256, operation, status
        ) VALUES (?, ?, ?, ?, 'pending')
        ON CONFLICT(repo_key, memory_id, content_sha256, operation)
        WHERE status IN ('pending', 'leased')
        DO NOTHING
        """,
        (repo_key, memory_id, content_sha256, operation),
    )


def _required_markdown_path(memory: CodingMemory) -> str:
    if memory.markdown_path is None:
        raise ValueError("Markdown truth is missing its canonical path")
    return memory.markdown_path


def _parse_call_ids(value: str) -> tuple[str, ...]:
    return _parse_string_tuple(value, field="import checkpoint call IDs")


def _parse_string_tuple(value: str, *, field: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{field.capitalize()} are invalid")
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{field.capitalize()} must be unique")
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
        fact_ids=_parse_string_tuple(row["fact_ids_json"], field="memory fact IDs"),
        markdown_path=row["markdown_path"],
        content_sha256=row["content_sha256"],
    )
