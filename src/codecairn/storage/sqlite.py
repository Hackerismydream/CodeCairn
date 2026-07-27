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
from codecairn.memory.evolution import (
    EvolutionArtifact,
    EvolutionProposal,
    EvolutionRecord,
    EvolutionRejected,
    MemoryHistory,
    MemoryStatus,
    PreparedEvolutionCommit,
    ProposalResolution,
    evaluate_proposal,
    evolution_commit_from_payload,
    evolution_commit_payload,
    evolution_from_dict,
    evolution_to_dict,
    proposal_to_dict,
    require_applied,
)
from codecairn.memory.models import (
    ImportCheckpoint,
    IndexHealth,
    IndexJob,
    MemoryArtifact,
    OperationalCounts,
)
from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    IdentityConflict,
    LegacyRootUnsupported,
    RepositoryKnowledgePayload,
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

_SCHEMA_REVISION = "codecairn-v01-4"


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

    def prepare_evolution(self, commit: PreparedEvolutionCommit) -> str:
        payload = evolution_commit_payload(commit)
        payload_json = canonical_json(payload)
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
                    raise IdentityConflict("Evolution Write Intent conflicts with state")
                return str(existing["status"])
            claim = connection.execute(
                """
                SELECT operation_id, evolution_id
                FROM evolution_claims
                WHERE repo_key = ? AND predecessor_id = ?
                """,
                (commit.repo_key, commit.record.predecessor_id),
            ).fetchone()
            if claim is not None:
                if claim["evolution_id"] == commit.record.evolution_id:
                    existing_edge = connection.execute(
                        """
                        SELECT canonical_evolution_json
                        FROM evolutions
                        WHERE repo_key = ? AND evolution_id = ?
                        """,
                        (commit.repo_key, commit.record.evolution_id),
                    ).fetchone()
                    if existing_edge is not None:
                        stored = evolution_from_dict(
                            json.loads(existing_edge["canonical_evolution_json"])
                        )
                        if _same_evolution_retry(stored, commit.record):
                            return "completed"
                        raise IdentityConflict("Evolution edge conflicts with immutable content")
                raise EvolutionRejected(
                    "conflicting_successor",
                    "Active predecessor already has a claimed successor",
                )
            predecessor = _get_memory(
                connection,
                repo_key=commit.repo_key,
                memory_id=commit.record.predecessor_id,
            )
            successor = commit.new_memory or _get_memory(
                connection,
                repo_key=commit.repo_key,
                memory_id=commit.record.successor_id,
            )
            if successor is None:
                raise EvolutionRejected("unknown_successor", "Successor memory does not exist")
            status = cast(
                MemoryStatus | None,
                _memory_status(
                    connection,
                    repo_key=commit.repo_key,
                    memory_id=commit.record.predecessor_id,
                ),
            )
            require_applied(
                evaluate_proposal(
                    commit.proposal,
                    predecessor=predecessor,
                    successor=successor,
                    predecessor_status=status,
                )
            )
            if _would_cycle(connection, commit.record):
                raise EvolutionRejected("cycle", "Evolution would create a cycle")
            connection.execute(
                """
                INSERT INTO write_intents (
                    operation_id, repo_key, operation_kind, status,
                    expected_files_json, memory_ids_json,
                    prior_source_cursor, target_source_cursor,
                    prepared_payload_json, error_code, created_at_ms,
                    completed_at_ms
                ) VALUES (
                    ?, ?, ?, 'prepared', ?, ?,
                    NULL, NULL, ?, NULL, ?, NULL
                )
                """,
                (
                    commit.operation_id,
                    commit.repo_key,
                    payload["operation_kind"],
                    canonical_json(
                        [
                            payload["expected_memory_file"],
                            payload["expected_evolution_file"],
                        ]
                    ),
                    canonical_json([commit.record.predecessor_id, commit.record.successor_id]),
                    payload_json,
                    commit.created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO evolution_claims (
                    repo_key, predecessor_id, evolution_id, operation_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    commit.repo_key,
                    commit.record.predecessor_id,
                    commit.record.evolution_id,
                    commit.operation_id,
                ),
            )
        return "prepared"

    def list_prepared_evolutions(self) -> tuple[PreparedEvolutionCommit, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT operation_id, prepared_payload_json, created_at_ms
                FROM write_intents
                WHERE operation_kind IN ('evolution', 'restore')
                  AND status = 'prepared'
                ORDER BY created_at_ms, operation_id
                """
            ).fetchall()
        return tuple(
            evolution_commit_from_payload(
                json.loads(row["prepared_payload_json"]),
                operation_id=row["operation_id"],
                created_at_ms=row["created_at_ms"],
            )
            for row in rows
        )

    def complete_evolution(
        self,
        commit: PreparedEvolutionCommit,
        evolution_artifact: EvolutionArtifact,
        memory_artifact: MemoryArtifact | None,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> bool:
        _validate_evolution_artifacts(commit, evolution_artifact, memory_artifact)
        payload_json = canonical_json(evolution_commit_payload(commit))
        encoded = canonical_json(evolution_to_dict(commit.record))
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
                raise IdentityConflict("Evolution Write Intent is missing or conflicting")
            if intent["status"] == "completed":
                return False
            if intent["status"] != "prepared":
                raise IdentityConflict("Evolution Write Intent is not recoverable")
            if memory_artifact is not None:
                _insert_memory(connection, memory_artifact)
            predecessor = _get_memory(
                connection,
                repo_key=commit.repo_key,
                memory_id=commit.record.predecessor_id,
            )
            successor = _get_memory(
                connection,
                repo_key=commit.repo_key,
                memory_id=commit.record.successor_id,
            )
            assert successor is not None
            require_applied(
                evaluate_proposal(
                    commit.proposal,
                    predecessor=predecessor,
                    successor=successor,
                    predecessor_status=cast(
                        MemoryStatus | None,
                        _memory_status(
                            connection,
                            repo_key=commit.repo_key,
                            memory_id=commit.record.predecessor_id,
                        ),
                    ),
                )
            )
            if _would_cycle(connection, commit.record):
                raise EvolutionRejected("cycle", "Evolution would create a cycle")
            existing = connection.execute(
                """
                SELECT canonical_evolution_json, markdown_path, content_sha256
                FROM evolutions
                WHERE repo_key = ? AND evolution_id = ?
                """,
                (commit.repo_key, commit.record.evolution_id),
            ).fetchone()
            metadata = (
                str(evolution_artifact.path),
                evolution_artifact.content_sha256,
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO evolutions (
                        repo_key, evolution_id, predecessor_id, successor_id,
                        canonical_evolution_json, markdown_path, content_sha256,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit.repo_key,
                        commit.record.evolution_id,
                        commit.record.predecessor_id,
                        commit.record.successor_id,
                        encoded,
                        *metadata,
                        commit.record.created_at_ms,
                    ),
                )
            elif (
                existing["canonical_evolution_json"] != encoded
                or (existing["markdown_path"], existing["content_sha256"]) != metadata
            ):
                raise IdentityConflict("Evolution identity conflicts with state")
            connection.execute(
                """
                UPDATE memory_status
                SET status = 'superseded'
                WHERE repo_key = ? AND memory_id = ? AND status = 'active'
                """,
                (commit.repo_key, commit.record.predecessor_id),
            )
            connection.execute(
                """
                UPDATE memory_status
                SET status = 'active'
                WHERE repo_key = ? AND memory_id = ?
                """,
                (commit.repo_key, commit.record.successor_id),
            )
            _requeue_index_job(connection, predecessor, status="superseded")
            _requeue_index_job(connection, successor, status="active")
            _store_proposal_outcome(
                connection,
                proposal=commit.proposal,
                resolution=ProposalResolution("applied"),
                evolution_id=commit.record.evolution_id,
                created_at_ms=commit.created_at_ms,
            )
            connection.execute(
                """
                UPDATE write_intents
                SET status = 'completed', completed_at_ms = ?
                WHERE operation_id = ? AND status = 'prepared'
                """,
                (commit.created_at_ms, commit.operation_id),
            )
            _stage(on_stage, "evolution_before_commit")
        return existing is None

    def record_proposal_outcome(
        self,
        proposal: EvolutionProposal,
        resolution: ProposalResolution,
        *,
        created_at_ms: int,
    ) -> None:
        if resolution.outcome == "applied":
            raise ValueError("Applied proposal requires an Evolution Record")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _store_proposal_outcome(
                connection,
                proposal=proposal,
                resolution=resolution,
                evolution_id=None,
                created_at_ms=created_at_ms,
            )

    def memory_status(self, *, repo_key: str, memory_id: str) -> str | None:
        with self._connect() as connection:
            return _memory_status(connection, repo_key=repo_key, memory_id=memory_id)

    def memory_history(self, *, repo_key: str, memory_id: str) -> MemoryHistory:
        with self._connect() as connection:
            seed = _get_memory(connection, repo_key=repo_key, memory_id=memory_id)
            if seed is None:
                raise KeyError(memory_id)
            ids = _lineage_ids(connection, repo_key=repo_key, memory_id=memory_id)
            memories = tuple(
                item
                for item_id in ids
                if (item := _get_memory(connection, repo_key=repo_key, memory_id=item_id))
                is not None
            )
            rows = connection.execute(
                """
                SELECT canonical_evolution_json
                FROM evolutions
                WHERE repo_key = ?
                  AND predecessor_id IN ({})
                  AND successor_id IN ({})
                ORDER BY created_at_ms, evolution_id
                """.format(
                    ",".join("?" for _ in ids),
                    ",".join("?" for _ in ids),
                ),
                (repo_key, *ids, *ids),
            ).fetchall()
            evolutions = tuple(
                evolution_from_dict(json.loads(row["canonical_evolution_json"])) for row in rows
            )
            statuses = tuple(
                (
                    item_id,
                    cast(
                        MemoryStatus,
                        _memory_status(
                            connection,
                            repo_key=repo_key,
                            memory_id=item_id,
                        ),
                    ),
                )
                for item_id in ids
            )
        return MemoryHistory(memories=memories, evolutions=evolutions, statuses=statuses)

    def active_lineage_tips(self, *, repo_key: str, memory_id: str) -> tuple[CodingMemory, ...]:
        with self._connect() as connection:
            ids = _lineage_ids(connection, repo_key=repo_key, memory_id=memory_id)
            return tuple(
                memory
                for item_id in ids
                if _memory_status(connection, repo_key=repo_key, memory_id=item_id) == "active"
                and (memory := _get_memory(connection, repo_key=repo_key, memory_id=item_id))
                is not None
            )

    def rebuild_evolution_projection(
        self,
        evolutions: tuple[EvolutionArtifact, ...],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM evolutions")
            connection.execute("DELETE FROM evolution_claims")
            connection.execute("UPDATE memory_status SET status = 'active'")
            for artifact in sorted(
                evolutions,
                key=lambda item: (item.record.created_at_ms, item.record.evolution_id),
            ):
                record = artifact.record
                predecessor = _get_memory(
                    connection, repo_key=record.repo_key, memory_id=record.predecessor_id
                )
                successor = _get_memory(
                    connection, repo_key=record.repo_key, memory_id=record.successor_id
                )
                if predecessor is None or successor is None or _would_cycle(connection, record):
                    raise IdentityConflict("Evolution Markdown cannot rebuild projection")
                connection.execute(
                    """
                    INSERT INTO evolutions (
                        repo_key, evolution_id, predecessor_id, successor_id,
                        canonical_evolution_json, markdown_path, content_sha256,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.repo_key,
                        record.evolution_id,
                        record.predecessor_id,
                        record.successor_id,
                        canonical_json(evolution_to_dict(record)),
                        str(artifact.path),
                        artifact.content_sha256,
                        record.created_at_ms,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO evolution_claims (
                        repo_key, predecessor_id, evolution_id, operation_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (record.repo_key, record.predecessor_id, record.evolution_id, "rebuild"),
                )
                connection.execute(
                    """
                    UPDATE memory_status SET status = 'superseded'
                    WHERE repo_key = ? AND memory_id = ?
                    """,
                    (record.repo_key, record.predecessor_id),
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
        return tuple(key for key, _memory_id in self.active_workstream_heads(repo_key=repo_key))

    def active_workstream_heads(
        self,
        *,
        repo_key: str,
    ) -> tuple[tuple[str, str], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.canonical_memory_json
                FROM memories m
                JOIN memory_status s
                  ON s.repo_key = m.repo_key AND s.memory_id = m.memory_id
                WHERE m.repo_key = ? AND m.memory_type = 'work_state'
                  AND s.status = 'active'
                ORDER BY m.memory_id
                """,
                (repo_key,),
            ).fetchall()
        heads = []
        for row in rows:
            memory = coding_memory_from_dict(json.loads(row["canonical_memory_json"]))
            assert isinstance(memory.payload, WorkStatePayload)
            if memory.payload.workstream_state == "open":
                heads.append((memory.payload.workstream_key, memory.memory_id))
        return tuple(sorted(heads))

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

    def claim_index_jobs(
        self,
        *,
        repo_key: str,
        worker_id: str,
        max_jobs: int,
        now_ms: int,
        lease_ms: int,
    ) -> tuple[IndexJob, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT job_id
                FROM index_jobs
                WHERE repo_key = ? AND attempt_count < 3
                  AND (
                    status IN ('pending', 'failed', 'stale')
                    OR (
                        status = 'leased'
                        AND lease_expires_at_ms IS NOT NULL
                        AND lease_expires_at_ms <= ?
                    )
                  )
                ORDER BY created_at_ms, job_id
                LIMIT ?
                """,
                (repo_key, now_ms, max_jobs),
            ).fetchall()
            jobs: list[IndexJob] = []
            for row in rows:
                connection.execute(
                    """
                    UPDATE index_jobs
                    SET status = 'leased', attempt_count = attempt_count + 1,
                        lease_owner = ?, lease_expires_at_ms = ?,
                        error_code = NULL
                    WHERE job_id = ?
                    """,
                    (worker_id, now_ms + lease_ms, row["job_id"]),
                )
                job = connection.execute(
                    """
                    SELECT job_id, repo_key, memory_id, target_status,
                           attempt_count
                    FROM index_jobs
                    WHERE job_id = ?
                    """,
                    (row["job_id"],),
                ).fetchone()
                assert job is not None
                jobs.append(
                    IndexJob(
                        job_id=job["job_id"],
                        repo_key=job["repo_key"],
                        memory_id=job["memory_id"],
                        target_status=cast(MemoryStatus, job["target_status"]),
                        attempt_count=job["attempt_count"],
                    )
                )
        return tuple(jobs)

    def complete_index_job(
        self,
        job: IndexJob,
        *,
        worker_id: str,
        profile_identity: str,
    ) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE index_jobs
                SET status = 'indexed', indexed_profile = ?,
                    lease_owner = NULL, lease_expires_at_ms = NULL,
                    error_code = NULL
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (profile_identity, job.job_id, worker_id),
            )
            if updated.rowcount != 1:
                raise IdentityConflict("Index job lease is not owned by this worker")

    def fail_index_job(
        self,
        job: IndexJob,
        *,
        worker_id: str,
        error_code: str,
    ) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE index_jobs
                SET status = 'failed', lease_owner = NULL,
                    lease_expires_at_ms = NULL, error_code = ?
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (error_code[:128], job.job_id, worker_id),
            )
            if updated.rowcount != 1:
                raise IdentityConflict("Index job lease is not owned by this worker")

    def requeue_profile(self, *, repo_key: str, profile_identity: str) -> int:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE index_jobs
                SET status = 'pending', attempt_count = 0,
                    lease_owner = NULL, lease_expires_at_ms = NULL,
                    error_code = NULL
                WHERE repo_key = ? AND status = 'indexed'
                  AND indexed_profile != ?
                """,
                (repo_key, profile_identity),
            )
        return updated.rowcount

    def requeue_indexed_namespace(self, *, repo_key: str) -> int:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE index_jobs
                SET status = 'pending', attempt_count = 0,
                    lease_owner = NULL, lease_expires_at_ms = NULL
                WHERE repo_key = ? AND status = 'indexed'
                """,
                (repo_key,),
            )
        return updated.rowcount

    def requeue_index_revisions(
        self,
        *,
        repo_key: str,
        memory_ids: tuple[str, ...],
    ) -> int:
        if not memory_ids:
            return 0
        placeholders = ",".join("?" for _item in memory_ids)
        with self._connect() as connection:
            updated = connection.execute(
                f"""
                UPDATE index_jobs
                SET status = 'pending', attempt_count = 0,
                    lease_owner = NULL, lease_expires_at_ms = NULL
                WHERE repo_key = ? AND status = 'indexed'
                  AND memory_id IN ({placeholders})
                """,
                (repo_key, *memory_ids),
            )
        return updated.rowcount

    def namespace_index_counts(self, *, repo_key: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM index_jobs
                WHERE repo_key = ?
                GROUP BY status
                """,
                (repo_key,),
            ).fetchall()
        counts = {"pending": 0, "leased": 0, "indexed": 0, "failed": 0, "stale": 0}
        counts.update({str(row["status"]): int(row["count"]) for row in rows})
        return counts

    def recall_documents(
        self,
        *,
        repo_key: str,
    ) -> tuple[tuple[CodingMemory, MemoryStatus], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.canonical_memory_json, s.status
                FROM memories m
                JOIN memory_status s
                  ON s.repo_key = m.repo_key AND s.memory_id = m.memory_id
                WHERE m.repo_key = ?
                ORDER BY m.memory_id
                """,
                (repo_key,),
            ).fetchall()
        return tuple(
            (
                coding_memory_from_dict(json.loads(row["canonical_memory_json"])),
                cast(MemoryStatus, row["status"]),
            )
            for row in rows
        )

    def recall_cursors(self, *, repo_key: str) -> tuple[int, int, str]:
        with self._connect() as connection:
            source = connection.execute(
                """
                SELECT COALESCE(MAX(committed_raw_event_index), -1) AS cursor
                FROM imports
                WHERE repo_key = ?
                """,
                (repo_key,),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM index_jobs
                WHERE repo_key = ? AND status != 'indexed'
                """,
                (repo_key,),
            ).fetchone()
            semantic = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM semantic_jobs
                WHERE repo_key = ?
                GROUP BY status
                """,
                (repo_key,),
            ).fetchall()
        source_cursor = int(source["cursor"])
        index_cursor = source_cursor if int(pending["count"]) == 0 else -1
        states = {str(row["status"]): int(row["count"]) for row in semantic}
        semantic_state = (
            "failed"
            if states.get("failed", 0)
            else "pending"
            if states.get("pending", 0) or states.get("leased", 0)
            else "complete"
        )
        return source_cursor, index_cursor, semantic_state

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
                        operation_kind IN (
                            'capture', 'semantic_commit', 'evolution', 'restore'
                        )
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
                    target_status TEXT NOT NULL CHECK (
                        target_status IN ('active', 'superseded')
                    ),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'leased', 'indexed', 'failed', 'stale')
                    ),
                    indexed_profile TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at_ms INTEGER,
                    error_code TEXT,
                    created_at_ms INTEGER NOT NULL,
                    UNIQUE (repo_key, memory_id, operation)
                );
                CREATE TABLE IF NOT EXISTS memory_status (
                    repo_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    subject_key TEXT,
                    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
                    PRIMARY KEY (repo_key, memory_id)
                );
                CREATE TABLE IF NOT EXISTS evolutions (
                    repo_key TEXT NOT NULL,
                    evolution_id TEXT NOT NULL,
                    predecessor_id TEXT NOT NULL,
                    successor_id TEXT NOT NULL,
                    canonical_evolution_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (repo_key, evolution_id),
                    UNIQUE (repo_key, predecessor_id)
                );
                CREATE TABLE IF NOT EXISTS evolution_claims (
                    repo_key TEXT NOT NULL,
                    predecessor_id TEXT NOT NULL,
                    evolution_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    PRIMARY KEY (repo_key, predecessor_id)
                );
                CREATE TABLE IF NOT EXISTS evolution_proposals (
                    repo_key TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (
                        outcome IN ('applied', 'kept_both', 'rejected')
                    ),
                    error_code TEXT,
                    evolution_id TEXT,
                    canonical_proposal_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (repo_key, proposal_id)
                );
                """
            )
            _ensure_write_intent_evolution_kind(connection)
            _ensure_index_target_status(connection)
            _ensure_index_lifecycle_columns(connection)
            _backfill_memory_status(connection)
            row = connection.execute(
                "SELECT value FROM codecairn_meta WHERE key = 'schema_revision'"
            ).fetchone()
            if row is not None and row["value"] not in (
                _SCHEMA_REVISION,
                *(f"codecairn-v01-{number}" for number in range(1, 4)),
            ):
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


def _ensure_write_intent_evolution_kind(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'write_intents'
        """
    ).fetchone()
    if row is None or ("'evolution'" in str(row["sql"]) and "'restore'" in str(row["sql"])):
        return
    connection.executescript(
        """
        ALTER TABLE write_intents RENAME TO write_intents_v012;
        CREATE TABLE write_intents (
            operation_id TEXT PRIMARY KEY,
            repo_key TEXT NOT NULL,
            operation_kind TEXT NOT NULL CHECK (
                operation_kind IN (
                    'capture', 'semantic_commit', 'evolution', 'restore'
                )
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
        INSERT INTO write_intents
        SELECT * FROM write_intents_v012;
        DROP TABLE write_intents_v012;
        """
    )


def _ensure_index_target_status(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(index_jobs)").fetchall()
    }
    if "target_status" not in columns:
        connection.execute(
            """
            ALTER TABLE index_jobs
            ADD COLUMN target_status TEXT NOT NULL DEFAULT 'active'
            """
        )


def _ensure_index_lifecycle_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(index_jobs)").fetchall()
    }
    additions = {
        "indexed_profile": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "lease_owner": "TEXT",
        "lease_expires_at_ms": "INTEGER",
        "error_code": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE index_jobs ADD COLUMN {name} {declaration}")


def _backfill_memory_status(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT repo_key, memory_id, memory_type, canonical_memory_json
        FROM memories
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_status s
            WHERE s.repo_key = memories.repo_key
              AND s.memory_id = memories.memory_id
        )
        """
    ).fetchall()
    for row in rows:
        memory = coding_memory_from_dict(json.loads(row["canonical_memory_json"]))
        connection.execute(
            """
            INSERT INTO memory_status (
                repo_key, memory_id, memory_type, subject_key, status
            ) VALUES (?, ?, ?, ?, 'active')
            """,
            (
                memory.repo_key,
                memory.memory_id,
                memory.memory_type,
                None if memory.memory_type == "task_experience" else _subject_key(memory),
            ),
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
    connection.execute(
        """
        INSERT INTO memory_status (
            repo_key, memory_id, memory_type, subject_key, status
        ) VALUES (?, ?, ?, ?, 'active')
        ON CONFLICT(repo_key, memory_id) DO NOTHING
        """,
        (
            memory.repo_key,
            memory.memory_id,
            memory.memory_type,
            (None if memory.memory_type == "task_experience" else _subject_key(memory)),
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
            job_id, repo_key, memory_id, operation, target_status, status,
            indexed_profile, attempt_count, lease_owner,
            lease_expires_at_ms, error_code, created_at_ms
        ) VALUES (
            ?, ?, ?, 'upsert', 'active', 'pending',
            NULL, 0, NULL, NULL, NULL, ?
        )
        ON CONFLICT(repo_key, memory_id, operation) DO NOTHING
        """,
        (job_id, memory.repo_key, memory.memory_id, memory.created_at_ms),
    )


def _requeue_index_job(
    connection: sqlite3.Connection,
    memory: CodingMemory | None,
    *,
    status: MemoryStatus,
) -> None:
    if memory is None:
        return
    _insert_index_job(connection, memory)
    connection.execute(
        """
        UPDATE index_jobs
        SET status = 'pending', target_status = ?, indexed_profile = NULL,
            attempt_count = 0, lease_owner = NULL,
            lease_expires_at_ms = NULL, error_code = NULL, created_at_ms = ?
        WHERE repo_key = ? AND memory_id = ? AND operation = 'upsert'
        """,
        (status, memory.created_at_ms, memory.repo_key, memory.memory_id),
    )


def _get_memory(
    connection: sqlite3.Connection,
    *,
    repo_key: str,
    memory_id: str,
) -> CodingMemory | None:
    row = connection.execute(
        """
        SELECT canonical_memory_json
        FROM memories
        WHERE repo_key = ? AND memory_id = ?
        """,
        (repo_key, memory_id),
    ).fetchone()
    return (
        None if row is None else coding_memory_from_dict(json.loads(row["canonical_memory_json"]))
    )


def _memory_status(
    connection: sqlite3.Connection,
    *,
    repo_key: str,
    memory_id: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT status
        FROM memory_status
        WHERE repo_key = ? AND memory_id = ?
        """,
        (repo_key, memory_id),
    ).fetchone()
    return None if row is None else str(row["status"])


def _would_cycle(
    connection: sqlite3.Connection,
    record: EvolutionRecord,
) -> bool:
    cursor = record.successor_id
    visited = {record.predecessor_id}
    while True:
        if cursor in visited:
            return True
        visited.add(cursor)
        row = connection.execute(
            """
            SELECT successor_id
            FROM evolutions
            WHERE repo_key = ? AND predecessor_id = ?
            """,
            (record.repo_key, cursor),
        ).fetchone()
        if row is None:
            return False
        cursor = str(row["successor_id"])


def _same_evolution_retry(
    left: EvolutionRecord,
    right: EvolutionRecord,
) -> bool:
    left_value = evolution_to_dict(left)
    right_value = evolution_to_dict(right)
    left_value.pop("created_at_ms")
    right_value.pop("created_at_ms")
    return left_value == right_value


def _lineage_ids(
    connection: sqlite3.Connection,
    *,
    repo_key: str,
    memory_id: str,
) -> tuple[str, ...]:
    rows = connection.execute(
        """
        WITH RECURSIVE lineage(memory_id) AS (
            VALUES (?)
            UNION
            SELECT e.predecessor_id
            FROM evolutions e JOIN lineage l ON e.successor_id = l.memory_id
            WHERE e.repo_key = ?
            UNION
            SELECT e.successor_id
            FROM evolutions e JOIN lineage l ON e.predecessor_id = l.memory_id
            WHERE e.repo_key = ?
        )
        SELECT memory_id FROM lineage
        """,
        (memory_id, repo_key, repo_key),
    ).fetchall()
    ids = {str(row["memory_id"]) for row in rows}
    edges = connection.execute(
        """
        SELECT predecessor_id, successor_id
        FROM evolutions
        WHERE repo_key = ?
        """,
        (repo_key,),
    ).fetchall()
    successor_by_predecessor = {
        str(row["predecessor_id"]): str(row["successor_id"])
        for row in edges
        if row["predecessor_id"] in ids and row["successor_id"] in ids
    }
    incoming = {
        str(row["successor_id"])
        for row in edges
        if row["predecessor_id"] in ids and row["successor_id"] in ids
    }
    roots = sorted(ids - incoming)
    ordered: list[str] = []
    for root in roots:
        cursor: str | None = root
        while cursor is not None and cursor not in ordered:
            ordered.append(cursor)
            cursor = successor_by_predecessor.get(cursor)
    ordered.extend(sorted(ids - set(ordered)))
    return tuple(ordered)


def _store_proposal_outcome(
    connection: sqlite3.Connection,
    *,
    proposal: EvolutionProposal,
    resolution: ProposalResolution,
    evolution_id: str | None,
    created_at_ms: int,
) -> None:
    encoded = canonical_json(proposal_to_dict(proposal))
    existing = connection.execute(
        """
        SELECT outcome, error_code, evolution_id, canonical_proposal_json
        FROM evolution_proposals
        WHERE repo_key = ? AND proposal_id = ?
        """,
        (proposal.repo_key, proposal.proposal_id),
    ).fetchone()
    expected = (
        resolution.outcome,
        resolution.error_code,
        evolution_id,
        encoded,
    )
    if existing is not None:
        actual = (
            existing["outcome"],
            existing["error_code"],
            existing["evolution_id"],
            existing["canonical_proposal_json"],
        )
        if actual != expected:
            raise IdentityConflict("Evolution Proposal outcome conflicts with state")
        return
    connection.execute(
        """
        INSERT INTO evolution_proposals (
            repo_key, proposal_id, outcome, error_code, evolution_id,
            canonical_proposal_json, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal.repo_key,
            proposal.proposal_id,
            resolution.outcome,
            resolution.error_code,
            evolution_id,
            encoded,
            created_at_ms,
        ),
    )


def _subject_key(memory: CodingMemory) -> str:
    payload = memory.payload
    if isinstance(payload, (RepositoryKnowledgePayload, UserPreferencePayload)):
        return payload.subject_key
    if isinstance(payload, WorkStatePayload):
        return payload.workstream_key
    raise ValueError("Task Experience has no subject key")


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


def _validate_evolution_artifacts(
    commit: PreparedEvolutionCommit,
    evolution_artifact: EvolutionArtifact,
    memory_artifact: MemoryArtifact | None,
) -> None:
    expected = commit.expected_evolution_file
    if (
        evolution_artifact.record != commit.record
        or evolution_artifact.record.evolution_id != expected.evolution_id
        or evolution_artifact.content_sha256 != expected.content_sha256
        or not evolution_artifact.path.as_posix().endswith(expected.relative_path)
    ):
        raise IdentityConflict("Evolution artifact conflicts with Write Intent")
    expected_memory = commit.expected_memory_file
    if expected_memory is None:
        if memory_artifact is not None:
            raise IdentityConflict("Unexpected restored Memory artifact")
        return
    if (
        memory_artifact is None
        or memory_artifact.memory != commit.new_memory
        or memory_artifact.memory.memory_id != expected_memory.memory_id
        or memory_artifact.content_sha256 != expected_memory.content_sha256
        or not memory_artifact.path.as_posix().endswith(expected_memory.relative_path)
    ):
        raise IdentityConflict("Restored Memory artifact conflicts with Write Intent")


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
