from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codecairn.bootstrap import create_runtime
from codecairn.importers import SessionImporter
from codecairn.memory.errors import IndexNotReady
from codecairn.memory.models import IndexCandidate
from codecairn.memory.schema import CodingMemory, RepositoryKnowledgePayload, WorkStatePayload
from codecairn.service.cascade import MiniCascade
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState
from tests.retrieval_fakes import TEST_RETRIEVAL, FusionReranker, HashingEmbedder

FIXTURES = Path(__file__).parent / "fixtures"


def _knowledge(index: int, *, content: str | None = None, subject: str | None = None) -> CodingMemory:
    claim = content or f"Run repository check command {index} before committing."
    return CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="repository_knowledge",
        title=f"Repository check {index}",
        content=claim,
        category="command",
        tags=("checks",),
        created_at_ms=index,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=RepositoryKnowledgePayload(subject_key=subject or f"repository-check-{index}", claim=claim),
    )


def _work_state(index: int, *, key: str, closed: bool = False) -> CodingMemory:
    return CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="work_state",
        title=f"Release state {index}",
        content="Release work is active and needs repository checks.",
        category="task",
        tags=(),
        created_at_ms=index,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=WorkStatePayload(
            workstream_key=key,
            workstream_state="closed" if closed else "open",
            goal="Ship release",
            progress=f"Checks are running for attempt {index}.",
            blockers=(),
            next_step=None if closed else "Finish checks.",
            terminal_outcome="completed" if closed else None,
        ),
    )


def test_default_recall_excludes_superseded_but_history_query_includes_it(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    old = runtime.store_memory(_knowledge(1, content="Run legacy repository checks.", subject="checks"))
    new = runtime.store_memory(_knowledge(2, content="Run current repository checks.", subject="checks"))
    runtime.supersede(
        repo_key=old.repo_key,
        predecessor_id=old.memory_id,
        successor_id=new.memory_id,
        reason="The check command changed.",
        proposer="user",
    )

    current = runtime.recall("repository checks", repo_key=old.repo_key)
    historical = runtime.recall("repository checks", repo_key=old.repo_key, include_superseded=True)

    assert old.memory_id not in {item.memory_id for item in current.sidecar.ranked}
    assert old.memory_id in {item.memory_id for item in historical.sidecar.ranked}
    assert {item.status for item in historical.sidecar.ranked} == {"active", "superseded"}


def test_open_work_state_is_pinned_and_ambiguity_pins_none(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    runtime.store_memory(_knowledge(1))
    first = runtime.store_memory(_work_state(1, key="release"))
    runtime.store_memory(_work_state(2, key="other", closed=True))

    recalled = runtime.recall("repository checks", repo_key="acme/widgets", workstream_key="release")
    assert recalled.sidecar.ranked[0].memory_id == first.memory_id
    assert recalled.sidecar.ranked[0].pinned is True
    closed = runtime.recall("repository checks", repo_key="acme/widgets", workstream_key="other")
    assert not any(item.pinned for item in closed.sidecar.ranked)

    runtime.store_memory(_work_state(3, key="release"))
    ambiguous = runtime.recall("repository checks", repo_key="acme/widgets", workstream_key="release")
    assert not any(item.pinned for item in ambiguous.sidecar.ranked)


def test_type_caps_and_total_token_budget_explain_omissions(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    for index in range(42):
        runtime.store_memory(_knowledge(index))
    runtime.store_memory(_knowledge(20, content="repository checks " + "X" * 8_000))

    recalled = runtime.recall("repository checks", repo_key="acme/widgets", limit=40, token_budget=512)

    assert recalled.sidecar.context_trace is not None
    assert recalled.sidecar.context_trace.token_count <= 512
    assert recalled.sidecar.context_trace.tokenizer.endswith("upper-bound-v1")
    assert {item.reason for item in recalled.sidecar.omissions} >= {"type_cap", "token_budget"}


def test_recall_renders_relevant_exact_lines_from_an_oversized_memory(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    relevant = "Joanna: I took that picture on a hike last summer near Fort Wayne."
    noise = tuple(f"Joanna: Fort Wayne background detail {index}." + " filler" * 20 for index in range(80))
    stored = runtime.store_memory(_knowledge(1, content="\n".join((*noise, relevant))))

    recalled = runtime.recall("Where did Joanna hike last summer?", repo_key=stored.repo_key, token_budget=512)

    assert relevant in recalled.markdown
    assert recalled.sidecar.context_trace is not None
    assert recalled.sidecar.context_trace.rendered_memory_ids == (stored.memory_id,)
    assert recalled.sidecar.context_trace.omitted_snippet_count > 0
    assert recalled.sidecar.context_trace.token_count <= 512
    assert recalled.sidecar.ranked[0].snippets[0].text == relevant
    assert recalled.sidecar.ranked[0].snippets[0].document_id.startswith(f"{stored.memory_id}:snippet:")


def test_repository_only_recall_can_use_the_public_forty_memory_limit(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    stored = tuple(
        runtime.store_memory(_knowledge(index, content=f"Joanna hiking clue {index} near Fort Wayne.")) for index in range(32)
    )

    recalled = runtime.recall("Joanna hiking near Fort Wayne", repo_key="acme/widgets", limit=40)

    assert {item.memory_id for item in recalled.sidecar.ranked} == {memory.memory_id for memory in stored}
    assert recalled.sidecar.context_trace is not None
    assert set(recalled.sidecar.context_trace.rendered_memory_ids) == {memory.memory_id for memory in stored}
    assert dict(recalled.sidecar.context_trace.type_caps)["repository_knowledge"] == 40


def test_recall_compiles_all_fitting_ranked_snippets_not_a_fixed_three_lines(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    relevant = tuple(f"Joanna: Fort Wayne hiking detail {index}." for index in range(6))
    stored = runtime.store_memory(_knowledge(1, content="\n".join(("unrelated conversation " * 10,) * 80 + relevant)))

    recalled = runtime.recall("Joanna Fort Wayne hiking details", repo_key=stored.repo_key, token_budget=1_024)

    assert all(line in recalled.markdown for line in relevant)
    assert recalled.sidecar.context_trace is not None
    assert recalled.sidecar.context_trace.token_count <= 1_024


def test_snippet_selection_searches_inside_admitted_memory() -> None:
    relevant = "Caroline: I adopted a rescue greyhound named Finch."
    memory = _knowledge(1, content="\n".join(("unrelated detail", relevant)))
    engine = object.__new__(RecallEngine)
    engine._reranker = None

    selected = engine._rank_snippets("What kind of dog did Caroline adopt?", memories=(memory,), candidates={}, scores={})

    assert selected[memory.memory_id][0].text == relevant


def test_snippet_selection_has_a_per_memory_bound() -> None:
    memories = tuple(_knowledge(index, content="\n".join(f"detail {line}" for line in range(20))) for index in range(20))
    engine = object.__new__(RecallEngine)
    engine._reranker = None

    selected = engine._rank_snippets("detail", memories=memories, candidates={}, scores={})

    assert sum(map(len, selected.values())) == 240


def test_searched_snippets_remain_a_priority_layer_for_negative_reranker_scores() -> None:
    memory = _knowledge(1, content="searched line\nCaroline adopted a greyhound")
    document_id = f"{memory.memory_id}:snippet:0000"
    engine = object.__new__(RecallEngine)
    engine._reranker = type(
        "NegativeReranker", (), {"rerank": lambda _self, _query, documents: tuple((key, -100.0) for key, _text, _score in documents)}
    )()

    selected = engine._rank_snippets(
        "What dog did Caroline adopt?",
        memories=(memory,),
        candidates={document_id: IndexCandidate(memory.memory_id, document_id, "searched line")},
        scores={document_id: 0.1},
    )

    assert selected[memory.memory_id][0].document_id == document_id


def test_bounded_preflight_returns_index_not_ready_without_fallback(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    ready = create_runtime(root, retrieval_adapters=TEST_RETRIEVAL)
    ready.store_memory(_knowledge(1))
    ready.store_memory(_knowledge(2))
    ready.recall("repository checks", repo_key="acme/widgets")
    shutil.rmtree(root / "index.lance")

    state = SQLiteState(root / "state.sqlite3")
    embedder = HashingEmbedder()
    index = LanceMemoryIndex(root / "index.lance", embedder=embedder)
    bounded = MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(root),
        state=state,
        recall_engine=RecallEngine(
            state=state,
            index=index,
            embedder=embedder,
            reranker=FusionReranker(),
            preflight=MiniCascade(state=state, index=index),
            preflight_job_cap=1,
        ),
    )

    with pytest.raises(IndexNotReady) as failure:
        bounded.recall("repository checks", repo_key="acme/widgets")
    assert failure.value.code == "index_not_ready"
    assert bounded.recall("repository checks", repo_key="acme/widgets").sidecar.freshness == "fresh"


def test_rebuild_projects_active_and_historical_status(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root, retrieval_adapters=TEST_RETRIEVAL)
    first = runtime.store_memory(_knowledge(1, subject="checks"))
    second = runtime.store_memory(_knowledge(2, subject="checks"))
    runtime.supersede(
        repo_key=first.repo_key, predecessor_id=first.memory_id, successor_id=second.memory_id, reason="New command.", proposer="user"
    )
    state = SQLiteState(root / "state.sqlite3")
    embedder = HashingEmbedder()
    index = LanceMemoryIndex(root / "rebuilt.lance", embedder=embedder)

    report = MiniCascade(state=state, index=index).rebuild(repo_key=first.repo_key)

    assert report.parity is True
    assert {status for _memory_id, _document_id, status, _digest in index.fingerprints(repo_key=first.repo_key)} == {
        "active",
        "superseded",
    }
    assert report.truth_document_count == 2


def test_imported_experience_is_fresh_while_semantics_remain_pending(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    runtime.import_session(FIXTURES / "codex/failed_command.jsonl", repo_key="acme/widgets", boundary_kind="manual_finalize")

    recalled = runtime.recall("failing command", repo_key="acme/widgets")

    assert recalled.sidecar.ranked[0].memory_type == "task_experience"
    assert recalled.sidecar.source_cursor == recalled.sidecar.index_cursor
    assert recalled.sidecar.semantic_state == "pending"
    assert recalled.sidecar.freshness == "semantic_pending"
