"""SQLite operational state and immutable mirrors for version 0.1."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from codecairn.memory.capture import (
    CaptureCheckpoint,
    PreparedCapture,
    capture_input_fingerprint,
    prepared_capture_from_payload,
    prepared_capture_payload,
)
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
    TaskEpisode,
    UserPreferencePayload,
    WorkStatePayload,
    canonical_json,
    coding_memory_from_dict,
    coding_memory_to_dict,
    evidence_fact_from_dict,
    evidence_fact_to_dict,
    task_episode_from_dict,
    task_episode_to_dict,
    typed_id,
)
from codecairn.memory.semantic import (
    PreparedSemanticCommit,
    SemanticJob,
    SemanticJobStatus,
    semantic_commit_from_payload,
    semantic_commit_payload,
)

_SCHEMA_REVISION = "codecairn-v01-2"


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

    def store_source_facts(self, facts: tuple[EvidenceFact, ...]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _insert_source_facts(connection, facts)

    def list_episodes(
        self,
        *,
        repo_key: str,
        provider: str,
        session_id: str,
        source_generation: int = 1,
    ) -> tuple[TaskEpisode, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT canonical_episode_json
                FROM episodes
                WHERE repo_key = ? AND provider = ? AND session_id = ?
                  AND source_generation = ?
                ORDER BY start_event_index, end_event_index_exclusive
                """,
                (repo_key, provider, session_id, source_generation),
            ).fetchall()
        return tuple(
            task_episode_from_dict(json.loads(row["canonical_episode_json"])) for row in rows
        )

    def store_episode(self, episode: TaskEpisode) -> TaskEpisode:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _insert_episode(connection, episode)

    def prepare_capture(self, capture: PreparedCapture) -> str:
        payload_json = canonical_json(prepared_capture_payload(capture))
        expected_json = canonical_json(prepared_capture_payload(capture)["expected_files"])
        memory_ids_json = canonical_json(sorted(memory.memory_id for memory in capture.memories))
        checkpoint = capture.checkpoint
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for episode in capture.episodes:
                committed = _insert_episode(connection, episode)
                if (
                    committed.boundary_kind != episode.boundary_kind
                    or not _same_episode_except_boundary(committed, episode)
                ):
                    return "closure_lost"
            existing = connection.execute(
                """
                SELECT status, repo_key, prepared_payload_json
                FROM write_intents
                WHERE operation_id = ?
                """,
                (capture.operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["repo_key"] != capture.repo_key
                    or existing["prepared_payload_json"] != payload_json
                ):
                    raise IdentityConflict(
                        f"Write Intent identity conflicts with state: {capture.operation_id}"
                    )
                return str(existing["status"])
            connection.execute(
                """
                INSERT INTO write_intents (
                    operation_id, repo_key, operation_kind, status,
                    expected_files_json, memory_ids_json,
                    prior_source_cursor, target_source_cursor,
                    prepared_payload_json, error_code, created_at_ms,
                    completed_at_ms
                ) VALUES (?, ?, 'capture', 'prepared', ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    capture.operation_id,
                    capture.repo_key,
                    expected_json,
                    memory_ids_json,
                    checkpoint.prior_source_cursor,
                    checkpoint.committed_raw_event_index,
                    payload_json,
                    capture.created_at_ms,
                ),
            )
        return "prepared"

    def list_prepared_captures(self) -> tuple[PreparedCapture, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT operation_id, prepared_payload_json, created_at_ms
                FROM write_intents
                WHERE operation_kind = 'capture' AND status = 'prepared'
                ORDER BY created_at_ms, operation_id
                """
            ).fetchall()
        return tuple(
            prepared_capture_from_payload(
                json.loads(row["prepared_payload_json"]),
                operation_id=row["operation_id"],
                created_at_ms=row["created_at_ms"],
            )
            for row in rows
        )

    def conflict_write_intent(
        self,
        *,
        operation_id: str,
        error_code: str,
    ) -> None:
        if not error_code:
            raise ValueError("Write Intent conflict code must not be empty")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE write_intents
                SET status = 'conflicted', error_code = ?
                WHERE operation_id = ? AND status = 'prepared'
                """,
                (error_code, operation_id),
            )
            if updated.rowcount == 1:
                return
            row = connection.execute(
                """
                SELECT status, error_code
                FROM write_intents
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None or row["status"] != "conflicted" or row["error_code"] != error_code:
                raise IdentityConflict("Write Intent cannot be marked conflicted")

    def complete_capture(
        self,
        capture: PreparedCapture,
        artifacts: tuple[MemoryArtifact, ...],
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> int:
        _validate_capture_artifacts(capture, artifacts)
        payload_json = canonical_json(prepared_capture_payload(capture))
        checkpoint = capture.checkpoint
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, prepared_payload_json
                FROM write_intents
                WHERE operation_id = ?
                """,
                (capture.operation_id,),
            ).fetchone()
            if row is None:
                raise IdentityConflict(f"Write Intent is not prepared: {capture.operation_id}")
            if row["prepared_payload_json"] != payload_json:
                raise IdentityConflict(
                    f"Write Intent payload conflicts with state: {capture.operation_id}"
                )
            if row["status"] == "completed":
                return 0
            if row["status"] != "prepared":
                raise IdentityConflict(f"Write Intent is not recoverable: {capture.operation_id}")
            _stage(on_stage, "capture_transaction_b_start")
            for episode in capture.episodes:
                committed = _insert_episode(connection, episode)
                if committed.episode_id != episode.episode_id:
                    raise IdentityConflict(
                        "Episode closure cursor already names a different source span"
                    )
            _stage(on_stage, "capture_after_episodes")
            _insert_source_facts(connection, capture.facts)
            _stage(on_stage, "capture_after_facts")
            created = sum(int(_insert_memory(connection, artifact)) for artifact in artifacts)
            _stage(on_stage, "capture_after_memories")
            for memory in capture.memories:
                if memory.memory_type == "task_experience":
                    _insert_semantic_job(
                        connection,
                        memory=memory,
                        created_at_ms=capture.created_at_ms,
                    )
            _stage(on_stage, "capture_after_semantic_jobs")
            _commit_capture_checkpoint(connection, checkpoint)
            _stage(on_stage, "capture_after_checkpoint")
            connection.execute(
                """
                UPDATE write_intents
                SET status = 'completed', completed_at_ms = ?
                WHERE operation_id = ? AND status = 'prepared'
                """,
                (capture.created_at_ms, capture.operation_id),
            )
            _stage(on_stage, "capture_before_commit")
        return created

    def lease_semantic_jobs(
        self,
        *,
        worker_id: str,
        max_jobs: int,
        now_ms: int,
        lease_duration_ms: int,
        max_attempts: int,
    ) -> tuple[SemanticJob, ...]:
        if not worker_id or max_jobs < 1 or now_ms < 0 or lease_duration_ms < 1 or max_attempts < 1:
            raise ValueError("Semantic lease parameters are invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT job_id
                FROM semantic_jobs
                WHERE attempt_count < ?
                  AND (
                    status IN ('pending', 'failed')
                    OR (
                        status = 'leased'
                        AND lease_expires_at_ms IS NOT NULL
                        AND lease_expires_at_ms <= ?
                    )
                  )
                ORDER BY source_event_index, created_at_ms, job_id
                LIMIT ?
                """,
                (max_attempts, now_ms, max_jobs),
            ).fetchall()
            jobs: list[SemanticJob] = []
            for row in rows:
                connection.execute(
                    """
                    UPDATE semantic_jobs
                    SET status = 'leased', attempt_count = attempt_count + 1,
                        lease_owner = ?, lease_expires_at_ms = ?,
                        error_code = NULL, error_detail = NULL, updated_at_ms = ?
                    WHERE job_id = ?
                    """,
                    (
                        worker_id,
                        now_ms + lease_duration_ms,
                        now_ms,
                        row["job_id"],
                    ),
                )
                leased = connection.execute(
                    """
                    SELECT job_id, repo_key, episode_id, memory_id, status,
                           input_fingerprint, attempt_count
                    FROM semantic_jobs
                    WHERE job_id = ?
                    """,
                    (row["job_id"],),
                ).fetchone()
                assert leased is not None
                jobs.append(_semantic_job(leased))
        return tuple(jobs)

    def fail_semantic_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_detail: str,
        now_ms: int,
    ) -> None:
        if not error_code or len(error_detail) > 512:
            raise ValueError("Semantic failure fields are invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE semantic_jobs
                SET status = 'failed', lease_owner = NULL,
                    lease_expires_at_ms = NULL, error_code = ?,
                    error_detail = ?, updated_at_ms = ?
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (error_code, error_detail, now_ms, job_id, worker_id),
            )
            if updated.rowcount != 1:
                raise IdentityConflict("Semantic job lease is not owned by this worker")

    def semantic_job_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM semantic_jobs
                GROUP BY status
                """
            ).fetchall()
        counts = {"pending": 0, "leased": 0, "completed": 0, "failed": 0}
        counts.update({str(row["status"]): int(row["count"]) for row in rows})
        return counts

    def list_semantic_jobs(self) -> tuple[SemanticJob, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, repo_key, episode_id, memory_id, status,
                       input_fingerprint, attempt_count
                FROM semantic_jobs
                ORDER BY source_event_index, created_at_ms, job_id
                """
            ).fetchall()
        return tuple(_semantic_job(row) for row in rows)

    def list_semantic_batches(self) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT canonical_batch_json
                FROM semantic_batches
                ORDER BY job_id
                """
            ).fetchall()
        values = tuple(json.loads(row["canonical_batch_json"]) for row in rows)
        if not all(isinstance(value, dict) for value in values):
            raise ValueError("Semantic batch state is invalid")
        return cast(tuple[dict[str, object], ...], values)

    def prepare_semantic_commit(self, commit: PreparedSemanticCommit) -> str:
        payload_json = canonical_json(semantic_commit_payload(commit))
        payload = semantic_commit_payload(commit)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT status, repo_key, prepared_payload_json
                FROM write_intents
                WHERE operation_id = ?
                """,
                (commit.operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["repo_key"] != commit.repo_key
                    or existing["prepared_payload_json"] != payload_json
                ):
                    raise IdentityConflict(
                        f"Write Intent identity conflicts with state: {commit.operation_id}"
                    )
                return str(existing["status"])
            connection.execute(
                """
                INSERT INTO write_intents (
                    operation_id, repo_key, operation_kind, status,
                    expected_files_json, memory_ids_json,
                    prior_source_cursor, target_source_cursor,
                    prepared_payload_json, error_code, created_at_ms,
                    completed_at_ms
                ) VALUES (
                    ?, ?, 'semantic_commit', 'prepared', ?, ?,
                    NULL, NULL, ?, NULL, ?, NULL
                )
                """,
                (
                    commit.operation_id,
                    commit.repo_key,
                    canonical_json(payload["expected_files"]),
                    canonical_json(sorted(memory.memory_id for memory in commit.memories)),
                    payload_json,
                    commit.created_at_ms,
                ),
            )
        return "prepared"

    def list_prepared_semantic_commits(
        self,
    ) -> tuple[PreparedSemanticCommit, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT operation_id, prepared_payload_json, created_at_ms
                FROM write_intents
                WHERE operation_kind = 'semantic_commit' AND status = 'prepared'
                ORDER BY created_at_ms, operation_id
                """
            ).fetchall()
        return tuple(
            semantic_commit_from_payload(
                json.loads(row["prepared_payload_json"]),
                operation_id=row["operation_id"],
                created_at_ms=row["created_at_ms"],
            )
            for row in rows
        )

    def complete_semantic_commit(
        self,
        commit: PreparedSemanticCommit,
        artifacts: tuple[MemoryArtifact, ...],
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> int:
        _validate_semantic_artifacts(commit, artifacts)
        payload_json = canonical_json(semantic_commit_payload(commit))
        batch_json = canonical_json(commit.canonical_batch)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                """
                SELECT status, prepared_payload_json
                FROM write_intents
                WHERE operation_id = ?
                """,
                (commit.operation_id,),
            ).fetchone()
            if intent is None or intent["prepared_payload_json"] != payload_json:
                raise IdentityConflict("Semantic Write Intent is missing or conflicting")
            if intent["status"] == "completed":
                return 0
            if intent["status"] != "prepared":
                raise IdentityConflict("Semantic Write Intent is not recoverable")
            _stage(on_stage, "semantic_transaction_b_start")
            created = sum(int(_insert_memory(connection, artifact)) for artifact in artifacts)
            existing_batch = connection.execute(
                """
                SELECT output_fingerprint, canonical_batch_json
                FROM semantic_batches
                WHERE job_id = ?
                """,
                (commit.job_id,),
            ).fetchone()
            if existing_batch is None:
                connection.execute(
                    """
                    INSERT INTO semantic_batches (
                        job_id, output_fingerprint, canonical_batch_json
                    ) VALUES (?, ?, ?)
                    """,
                    (commit.job_id, commit.output_fingerprint, batch_json),
                )
            elif (
                existing_batch["output_fingerprint"] != commit.output_fingerprint
                or existing_batch["canonical_batch_json"] != batch_json
            ):
                raise IdentityConflict("Semantic output conflicts with completed batch")
            job = connection.execute(
                """
                SELECT status, output_fingerprint
                FROM semantic_jobs
                WHERE job_id = ?
                """,
                (commit.job_id,),
            ).fetchone()
            if job is None:
                raise IdentityConflict("Semantic job is missing")
            if job["status"] == "completed":
                if job["output_fingerprint"] != commit.output_fingerprint:
                    raise IdentityConflict("Semantic job output fingerprint conflicts")
            else:
                connection.execute(
                    """
                    UPDATE semantic_jobs
                    SET status = 'completed', output_fingerprint = ?,
                        lease_owner = NULL, lease_expires_at_ms = NULL,
                        error_code = NULL, error_detail = NULL, updated_at_ms = ?
                    WHERE job_id = ?
                    """,
                    (
                        commit.output_fingerprint,
                        commit.created_at_ms,
                        commit.job_id,
                    ),
                )
            connection.execute(
                """
                UPDATE write_intents
                SET status = 'completed', completed_at_ms = ?
                WHERE operation_id = ?
                """,
                (commit.created_at_ms, commit.operation_id),
            )
            _stage(on_stage, "semantic_before_commit")
        return created

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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _insert_memory(connection, artifact)

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

    def open_workstream_keys(self, *, repo_key: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    memory.payload.workstream_key
                    for memory in self.list_memories(repo_key=repo_key)
                    if isinstance(memory.payload, WorkStatePayload)
                    and memory.payload.workstream_state == "open"
                }
            )
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
            recoveries = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM write_intents
                WHERE status = 'prepared'
                """
            ).fetchone()
            conflicts = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM write_intents
                WHERE status = 'conflicted'
                """
            ).fetchone()
        return OperationalCounts(
            import_count=int(imports["count"]),
            observed_event_count=int(imports["events"]),
            memory_count=int(memories["count"]),
            pending_recovery_count=int(recoveries["count"]),
            conflicted_recovery_count=int(conflicts["count"]),
        )

    def index_health(self) -> IndexHealth:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM index_jobs
                GROUP BY status
                """
            ).fetchall()
        counts = {"pending": 0, "leased": 0, "indexed": 0, "failed": 0, "stale": 0}
        counts.update({str(row["status"]): int(row["count"]) for row in rows})
        return IndexHealth(**counts)

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
            if "memories" in existing:
                _ensure_memory_episode_column(connection)
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
                    episode_id TEXT,
                    canonical_memory_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY (repo_key, memory_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    one_task_experience_per_episode
                ON memories (repo_key, episode_id)
                WHERE memory_type = 'task_experience';
                CREATE TABLE IF NOT EXISTS episodes (
                    repo_key TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_generation INTEGER NOT NULL,
                    start_event_index INTEGER NOT NULL,
                    end_event_index_exclusive INTEGER NOT NULL,
                    canonical_episode_json TEXT NOT NULL,
                    PRIMARY KEY (repo_key, episode_id),
                    UNIQUE (
                        repo_key, provider, session_id, source_generation,
                        start_event_index, end_event_index_exclusive
                    ),
                    UNIQUE (
                        repo_key, provider, session_id, source_generation,
                        end_event_index_exclusive
                    )
                );
                CREATE TABLE IF NOT EXISTS write_intents (
                    operation_id TEXT PRIMARY KEY,
                    repo_key TEXT NOT NULL,
                    operation_kind TEXT NOT NULL CHECK (
                        operation_kind IN ('capture', 'semantic_commit')
                    ),
                    status TEXT NOT NULL CHECK (
                        status IN ('prepared', 'completed', 'conflicted')
                    ),
                    expected_files_json TEXT NOT NULL,
                    memory_ids_json TEXT NOT NULL,
                    prior_source_cursor INTEGER,
                    target_source_cursor INTEGER,
                    prepared_payload_json TEXT NOT NULL,
                    error_code TEXT,
                    created_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER
                );
                CREATE TABLE IF NOT EXISTS semantic_jobs (
                    job_id TEXT PRIMARY KEY,
                    repo_key TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'leased', 'completed', 'failed')
                    ),
                    input_fingerprint TEXT NOT NULL,
                    source_event_index INTEGER NOT NULL,
                    output_fingerprint TEXT,
                    attempt_count INTEGER NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at_ms INTEGER,
                    error_code TEXT,
                    error_detail TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE (repo_key, episode_id),
                    UNIQUE (repo_key, memory_id)
                );
                CREATE TABLE IF NOT EXISTS semantic_batches (
                    job_id TEXT PRIMARY KEY,
                    output_fingerprint TEXT NOT NULL,
                    canonical_batch_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS index_jobs (
                    job_id TEXT PRIMARY KEY,
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('upsert', 'delete')),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'leased', 'indexed', 'failed', 'stale')
                    ),
                    created_at_ms INTEGER NOT NULL,
                    UNIQUE (repo_key, memory_id, operation)
                );
                """
            )
            row = connection.execute(
                "SELECT value FROM codecairn_meta WHERE key = 'schema_revision'"
            ).fetchone()
            if row is not None and row["value"] not in {
                "codecairn-v01-1",
                _SCHEMA_REVISION,
            }:
                raise LegacyRootUnsupported(
                    "Unsupported SQLite schema; use a fresh root and re-import"
                )
            connection.execute(
                """
                INSERT INTO codecairn_meta (key, value)
                VALUES ('schema_revision', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
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


def _ensure_memory_episode_column(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
    if "episode_id" in columns:
        return
    connection.execute("ALTER TABLE memories ADD COLUMN episode_id TEXT")
    rows = connection.execute(
        """
        SELECT repo_key, memory_id, canonical_memory_json
        FROM memories
        """
    ).fetchall()
    for row in rows:
        memory = coding_memory_from_dict(json.loads(row["canonical_memory_json"]))
        connection.execute(
            """
            UPDATE memories
            SET episode_id = ?
            WHERE repo_key = ? AND memory_id = ?
            """,
            (memory.episode_id, row["repo_key"], row["memory_id"]),
        )


def _insert_episode(
    connection: sqlite3.Connection,
    episode: TaskEpisode,
) -> TaskEpisode:
    encoded = canonical_json(task_episode_to_dict(episode))
    closure = connection.execute(
        """
        SELECT canonical_episode_json
        FROM episodes
        WHERE repo_key = ? AND provider = ? AND session_id = ?
          AND source_generation = ? AND end_event_index_exclusive = ?
        """,
        (
            episode.repo_key,
            episode.provider,
            episode.session_id,
            episode.source_generation,
            episode.end_event_index_exclusive,
        ),
    ).fetchone()
    if closure is not None:
        return task_episode_from_dict(json.loads(closure["canonical_episode_json"]))
    existing = connection.execute(
        """
        SELECT canonical_episode_json
        FROM episodes
        WHERE repo_key = ? AND episode_id = ?
        """,
        (episode.repo_key, episode.episode_id),
    ).fetchone()
    if existing is not None:
        if existing["canonical_episode_json"] != encoded:
            raise IdentityConflict(
                f"Task Episode identity conflicts with state: {episode.episode_id}"
            )
        return episode
    connection.execute(
        """
        INSERT INTO episodes (
            repo_key, episode_id, provider, session_id, source_generation,
            start_event_index, end_event_index_exclusive,
            canonical_episode_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            episode.repo_key,
            episode.episode_id,
            episode.provider,
            episode.session_id,
            episode.source_generation,
            episode.start_event_index,
            episode.end_event_index_exclusive,
            encoded,
        ),
    )
    return episode


def _same_episode_except_boundary(
    left: TaskEpisode,
    right: TaskEpisode,
) -> bool:
    left_value = task_episode_to_dict(left)
    right_value = task_episode_to_dict(right)
    left_value.pop("boundary_kind")
    right_value.pop("boundary_kind")
    return left_value == right_value


def _insert_source_facts(
    connection: sqlite3.Connection,
    facts: tuple[EvidenceFact, ...],
) -> None:
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


def _insert_memory(
    connection: sqlite3.Connection,
    artifact: MemoryArtifact,
) -> bool:
    memory = artifact.memory
    encoded = canonical_json(coding_memory_to_dict(memory))
    if memory.memory_type == "task_experience" and memory.episode_id is not None:
        episode_memory = connection.execute(
            """
            SELECT memory_id, canonical_memory_json, markdown_path, content_sha256
            FROM memories
            WHERE repo_key = ? AND episode_id = ?
              AND memory_type = 'task_experience'
            """,
            (memory.repo_key, memory.episode_id),
        ).fetchone()
        if episode_memory is not None:
            if (
                episode_memory["memory_id"] != memory.memory_id
                or episode_memory["canonical_memory_json"] != encoded
                or (
                    episode_memory["markdown_path"],
                    episode_memory["content_sha256"],
                )
                != (str(artifact.path), artifact.content_sha256)
            ):
                raise IdentityConflict(
                    f"Task Experience conflicts with Episode: {memory.episode_id}"
                )
            return False
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
            repo_key, memory_id, memory_type, episode_id, canonical_memory_json,
            markdown_path, content_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory.repo_key,
            memory.memory_id,
            memory.memory_type,
            memory.episode_id,
            encoded,
            *expected_metadata,
        ),
    )
    _insert_index_job(connection, memory)
    return True


def _insert_semantic_job(
    connection: sqlite3.Connection,
    *,
    memory: CodingMemory,
    created_at_ms: int,
) -> None:
    if memory.episode_id is None:
        raise ValueError("Semantic capture job requires an Episode")
    if memory.source_order_key is None:
        raise ValueError("Semantic capture job requires source order")
    fingerprint = capture_input_fingerprint(memory)
    job_id = typed_id(
        "job",
        {
            "schema_version": 1,
            "job_kind": "semantic_extract",
            "repo_key": memory.repo_key,
            "episode_id": memory.episode_id,
            "input_fingerprint": fingerprint,
        },
    )
    existing = connection.execute(
        """
        SELECT job_id, input_fingerprint
        FROM semantic_jobs
        WHERE repo_key = ? AND episode_id = ?
        """,
        (memory.repo_key, memory.episode_id),
    ).fetchone()
    if existing is not None:
        if existing["job_id"] != job_id or existing["input_fingerprint"] != fingerprint:
            raise IdentityConflict(f"Semantic job conflicts with Episode: {memory.episode_id}")
        return
    connection.execute(
        """
        INSERT INTO semantic_jobs (
            job_id, repo_key, episode_id, memory_id, status,
            input_fingerprint, source_event_index,
            output_fingerprint, attempt_count,
            lease_owner, lease_expires_at_ms, error_code, error_detail,
            created_at_ms, updated_at_ms
        ) VALUES (
            ?, ?, ?, ?, 'pending', ?, ?, NULL, 0,
            NULL, NULL, NULL, NULL, ?, ?
        )
        """,
        (
            job_id,
            memory.repo_key,
            memory.episode_id,
            memory.memory_id,
            fingerprint,
            memory.source_order_key.event_index,
            created_at_ms,
            created_at_ms,
        ),
    )


def _insert_index_job(
    connection: sqlite3.Connection,
    memory: CodingMemory,
) -> None:
    job_id = typed_id(
        "job",
        {
            "schema_version": 1,
            "job_kind": "index_project",
            "repo_key": memory.repo_key,
            "memory_id": memory.memory_id,
            "operation": "upsert",
        },
    )
    connection.execute(
        """
        INSERT INTO index_jobs (
            job_id, repo_key, memory_id, operation, status, created_at_ms
        ) VALUES (?, ?, ?, 'upsert', 'pending', ?)
        ON CONFLICT(repo_key, memory_id, operation) DO NOTHING
        """,
        (job_id, memory.repo_key, memory.memory_id, memory.created_at_ms),
    )


def _validate_capture_artifacts(
    capture: PreparedCapture,
    artifacts: tuple[MemoryArtifact, ...],
) -> None:
    if len(artifacts) != len(capture.expected_files):
        raise IdentityConflict("Capture artifact count does not match Write Intent")
    for expected, memory, artifact in zip(
        capture.expected_files,
        capture.memories,
        artifacts,
        strict=True,
    ):
        if (
            artifact.memory != memory
            or artifact.memory.memory_id != expected.memory_id
            or artifact.content_sha256 != expected.content_sha256
            or artifact.path.as_posix().endswith(expected.relative_path) is False
        ):
            raise IdentityConflict(
                f"Capture artifact conflicts with Write Intent: {expected.memory_id}"
            )


def _validate_semantic_artifacts(
    commit: PreparedSemanticCommit,
    artifacts: tuple[MemoryArtifact, ...],
) -> None:
    if len(artifacts) != len(commit.expected_files):
        raise IdentityConflict("Semantic artifact count does not match Write Intent")
    for expected, memory, artifact in zip(
        commit.expected_files,
        commit.memories,
        artifacts,
        strict=True,
    ):
        if (
            artifact.memory != memory
            or artifact.memory.memory_id != expected.memory_id
            or artifact.content_sha256 != expected.content_sha256
            or not artifact.path.as_posix().endswith(expected.relative_path)
        ):
            raise IdentityConflict(
                f"Semantic artifact conflicts with Write Intent: {expected.memory_id}"
            )


def _semantic_job(row: sqlite3.Row) -> SemanticJob:
    return SemanticJob(
        job_id=row["job_id"],
        repo_key=row["repo_key"],
        episode_id=row["episode_id"],
        memory_id=row["memory_id"],
        status=cast(SemanticJobStatus, row["status"]),
        input_fingerprint=row["input_fingerprint"],
        attempt_count=row["attempt_count"],
    )


def _commit_capture_checkpoint(
    connection: sqlite3.Connection,
    checkpoint: CaptureCheckpoint,
) -> None:
    existing = connection.execute(
        """
        SELECT committed_raw_event_index
        FROM imports
        WHERE repo_key = ? AND source_path = ?
        """,
        (checkpoint.repo_key, checkpoint.source_path),
    ).fetchone()
    current = int(existing["committed_raw_event_index"]) if existing is not None else -1
    target = checkpoint.committed_raw_event_index
    if current not in {checkpoint.prior_source_cursor, target}:
        if current > target:
            return
        raise IdentityConflict("Capture source cursor compare-and-swap failed")
    if current == target and checkpoint.prior_source_cursor < target:
        return
    resume = checkpoint.resume
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
            checkpoint.repo_key,
            checkpoint.source_path,
            checkpoint.provider,
            checkpoint.session_id,
            checkpoint.source_sha256,
            checkpoint.raw_event_count,
            target,
            resume.resume_raw_event_index,
            resume.resume_prefix_sha256,
            json.dumps(resume.resume_call_ids),
            resume.resume_file_change_fact_count,
        ),
    )


def _stage(callback: Callable[[str], None] | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


def _string_tuple(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("Import checkpoint call IDs are invalid")
    if len(parsed) != len(set(parsed)):
        raise ValueError("Import checkpoint call IDs must be unique")
    return tuple(parsed)
