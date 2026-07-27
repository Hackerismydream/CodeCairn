from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from codecairn.bootstrap import create_runtime
from codecairn.importers import SessionImporter
from codecairn.memory.evolution import EvolutionProposal, EvolutionRecord, EvolutionRejected
from codecairn.memory.schema import CodingMemory, IdentityConflict, RepositoryKnowledgePayload
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState

FIXTURES = Path(__file__).parent / "fixtures"


def _knowledge(*, claim: str, subject: str = "checks") -> CodingMemory:
    return CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="repository_knowledge",
        title="Repository checks",
        content=claim,
        category="command",
        tags=("checks",),
        created_at_ms=1,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=RepositoryKnowledgePayload(subject_key=subject, claim=claim),
    )


def _runtime(root: Path, *, fault: str | None = None) -> MemoryRuntime:
    def inject(stage: str) -> None:
        if stage == fault:
            raise RuntimeError(stage)

    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(root),
        state=SQLiteState(root / "state.sqlite3"),
        fault_injector=None if fault is None else inject,
    )


def test_supersession_is_immutable_idempotent_and_restorable(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    old = runtime.store_memory(_knowledge(claim="Run make test."))
    new = runtime.store_memory(_knowledge(claim="Run make check."))
    with sqlite3.connect(root / "state.sqlite3") as connection:
        connection.execute("UPDATE index_jobs SET status = 'indexed'")

    edge = runtime.supersede(
        repo_key=old.repo_key,
        predecessor_id=old.memory_id,
        successor_id=new.memory_id,
        reason="The repository command changed.",
        proposer="user",
    )
    retry = runtime.supersede(
        repo_key=old.repo_key,
        predecessor_id=old.memory_id,
        successor_id=new.memory_id,
        reason="The repository command changed.",
        proposer="user",
    )
    state = SQLiteState(root / "state.sqlite3")

    assert retry.evolution_id == edge.evolution_id
    assert state.memory_status(repo_key=old.repo_key, memory_id=old.memory_id) == "superseded"
    assert state.memory_status(repo_key=new.repo_key, memory_id=new.memory_id) == "active"
    with sqlite3.connect(root / "state.sqlite3") as connection:
        targets = connection.execute(
            """
            SELECT memory_id, target_status, status
            FROM index_jobs
            WHERE memory_id IN (?, ?)
            ORDER BY memory_id
            """,
            (old.memory_id, new.memory_id),
        ).fetchall()
    assert set(targets) == {(old.memory_id, "superseded", "pending"), (new.memory_id, "active", "pending")}
    assert {item.memory_id for item in runtime.memory_history(repo_key=old.repo_key, memory_id=old.memory_id).memories} == {
        old.memory_id,
        new.memory_id,
    }
    assert MarkdownMemoryStore(root).scan_evolutions().evolutions[0].record == edge

    with pytest.raises(IdentityConflict, match="immutable content"):
        runtime.supersede(
            repo_key=old.repo_key,
            predecessor_id=old.memory_id,
            successor_id=new.memory_id,
            reason="A conflicting explanation.",
            proposer="user",
        )

    restored = runtime.restore(repo_key=old.repo_key, memory_id=old.memory_id)

    assert restored.origin == "restored"
    assert restored.restored_from == old.memory_id
    assert restored.restore_predecessor_id == new.memory_id
    assert state.memory_status(repo_key=new.repo_key, memory_id=new.memory_id) == "superseded"
    assert state.memory_status(repo_key=restored.repo_key, memory_id=restored.memory_id) == "active"
    assert len(runtime.memory_history(repo_key=old.repo_key, memory_id=old.memory_id).evolutions) == 2


def test_policy_rejects_subject_mismatch_and_active_restore(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime")
    old = runtime.store_memory(_knowledge(claim="Run make test.", subject="tests"))
    unrelated = runtime.store_memory(_knowledge(claim="Use uv.", subject="environment"))

    with pytest.raises(EvolutionRejected) as mismatch:
        runtime.supersede(
            repo_key=old.repo_key,
            predecessor_id=old.memory_id,
            successor_id=unrelated.memory_id,
            reason="Unrelated claim.",
            proposer="user",
        )
    with pytest.raises(EvolutionRejected) as active:
        runtime.restore(repo_key=old.repo_key, memory_id=old.memory_id)

    assert mismatch.value.code == "different_subject"
    assert active.value.code == "already_active"


def test_restore_rejects_task_experience_and_ambiguous_lineage(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    runtime.import_session(FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize")
    task = next(memory for memory in runtime.list_memories(repo_key="acme/widgets") if memory.memory_type == "task_experience")
    with pytest.raises(EvolutionRejected) as append_only:
        runtime.restore(repo_key=task.repo_key, memory_id=task.memory_id)
    assert append_only.value.code == "append_only_experience"

    old = runtime.store_memory(_knowledge(claim="Run make test."))
    new = runtime.store_memory(_knowledge(claim="Run make check."))
    other = runtime.store_memory(_knowledge(claim="Run uv run pytest."))
    runtime.supersede(
        repo_key=old.repo_key, predecessor_id=old.memory_id, successor_id=new.memory_id, reason="First lineage.", proposer="user"
    )
    runtime.supersede(
        repo_key=other.repo_key,
        predecessor_id=other.memory_id,
        successor_id=new.memory_id,
        reason="Converged lineage.",
        proposer="user",
    )
    with sqlite3.connect(root / "state.sqlite3") as connection:
        connection.execute(
            """
            UPDATE memory_status SET status = 'active'
            WHERE memory_id = ?
            """,
            (other.memory_id,),
        )

    with pytest.raises(EvolutionRejected) as ambiguous:
        runtime.restore(repo_key=old.repo_key, memory_id=old.memory_id)
    assert ambiguous.value.code == "ambiguous_lineage"


def test_prepared_evolution_recovers_after_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = _runtime(root)
    old = runtime.store_memory(_knowledge(claim="Run make test."))
    new = runtime.store_memory(_knowledge(claim="Run make check."))

    with pytest.raises(RuntimeError, match="evolution_after_intent_prepared"):
        _runtime(root, fault="evolution_after_intent_prepared").supersede(
            repo_key=old.repo_key,
            predecessor_id=old.memory_id,
            successor_id=new.memory_id,
            reason="The command changed.",
            proposer="user",
        )

    _runtime(root).process_pending(worker_id="recovery")
    state = SQLiteState(root / "state.sqlite3")
    assert state.memory_status(repo_key=old.repo_key, memory_id=old.memory_id) == "superseded"
    assert state.operational_counts().pending_recovery_count == 0


def test_concurrent_successors_leave_one_active_tip(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    old = runtime.store_memory(_knowledge(claim="Run make test."))
    successors = (
        runtime.store_memory(_knowledge(claim="Run make check.")),
        runtime.store_memory(_knowledge(claim="Run uv run pytest.")),
    )

    def apply(successor: CodingMemory) -> str:
        try:
            create_runtime(root).supersede(
                repo_key=old.repo_key,
                predecessor_id=old.memory_id,
                successor_id=successor.memory_id,
                reason=successor.content,
                proposer="user",
            )
            return "applied"
        except EvolutionRejected:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(apply, successors))

    history = runtime.memory_history(repo_key=old.repo_key, memory_id=old.memory_id)
    assert sorted(outcomes) == ["applied", "rejected"]
    assert len(history.evolutions) == 1
    assert sum(status == "active" for _memory_id, status in history.statuses) == 1


def test_markdown_rebuild_derives_status_from_edges(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    old = runtime.store_memory(_knowledge(claim="Run make test."))
    new = runtime.store_memory(_knowledge(claim="Run make check."))
    runtime.supersede(
        repo_key=old.repo_key, predecessor_id=old.memory_id, successor_id=new.memory_id, reason="The command changed.", proposer="user"
    )
    state = SQLiteState(root / "state.sqlite3")
    state.rebuild_evolution_projection(MarkdownMemoryStore(root).scan_evolutions().evolutions)

    assert state.memory_status(repo_key=old.repo_key, memory_id=old.memory_id) == "superseded"
    assert state.memory_status(repo_key=new.repo_key, memory_id=new.memory_id) == "active"


def test_domain_detects_self_edge_before_persistence() -> None:
    memory = _knowledge(claim="Run make check.")
    proposal = EvolutionProposal.create(
        repo_key=memory.repo_key,
        decision="supersede",
        relation_kind="knowledge_contradiction",
        predecessor_id=memory.memory_id,
        successor_id=memory.memory_id,
        supporting_fact_ids=(),
        source_order_key=None,
        proposer="user",
        reason="Invalid.",
    )

    with pytest.raises(ValueError, match="cannot reference itself"):
        EvolutionRecord.from_proposal(proposal, evidence=(), created_at_ms=1)
