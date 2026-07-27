from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from codecairn.importers import SessionImporter
from codecairn.memory.schema import TaskExperiencePayload, WorkStatePayload
from codecairn.memory.semantic import (
    SemanticCandidate,
    SemanticEvolutionSuggestion,
    SemanticExtraction,
    SemanticRequest,
)
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState

FIXTURES = Path(__file__).parent / "fixtures"


def _runtime(
    root: Path,
    *,
    extractor: object | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> MemoryRuntime:
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(root),
        state=SQLiteState(root / "state.sqlite3"),
        semantic_extractor=extractor,  # type: ignore[arg-type]
        fault_injector=fault_injector,
    )


class _EmptyExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        del request
        self.calls += 1
        return SemanticExtraction(
            extractor_id="test",
            revision="1",
            candidates=(),
        )


class _RichExtractor:
    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        user = next(fact for fact in request.task_experience.facts if fact.role == "user")
        command = next(
            fact for fact in request.task_experience.facts if fact.fact_kind == "command"
        )
        task_key = next(key for key in request.allowed_workstream_keys if key.startswith("task:"))
        return SemanticExtraction(
            extractor_id="test",
            revision="rich-1",
            candidates=(
                SemanticCandidate(
                    memory_type="repository_knowledge",
                    title="Repository checks",
                    content="The repository checks run through uv.",
                    category="command",
                    source_fact_ids=(command.fact_id,),
                    subject_key="repository-checks",
                    claim="Run the checks through uv.",
                ),
                SemanticCandidate(
                    memory_type="repository_knowledge",
                    title="Failure behavior",
                    content="The observed check failed.",
                    category="constraint",
                    source_fact_ids=(command.fact_id,),
                    subject_key="check-failure",
                    claim="The observed check failed.",
                ),
                SemanticCandidate(
                    memory_type="user_preference",
                    title="Requested workflow",
                    content="The user asked to run the repository test suite.",
                    category="workflow",
                    source_fact_ids=(user.fact_id,),
                    subject_key="test-workflow",
                    preference="Run the repository test suite.",
                ),
                SemanticCandidate(
                    memory_type="work_state",
                    title="Repair failing tests",
                    content="The test failure remains unresolved.",
                    category="task",
                    source_fact_ids=(user.fact_id, command.fact_id),
                    workstream_key=task_key,
                    workstream_state="open",
                    goal=user.value,
                    progress="The failing check was observed.",
                    next_step="Repair the failing test.",
                ),
            ),
            evolution=(
                SemanticEvolutionSuggestion(
                    decision="supersede",
                    relation_kind="knowledge_obsolete",
                    predecessor_id=f"mem_{'0' * 64}",
                    successor_candidate_index=0,
                    supporting_fact_ids=(command.fact_id,),
                    reason="The new observed command replaces the older claim.",
                ),
            ),
        )


class _AssistantPreferenceExtractor:
    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        assistant = next(fact for fact in request.task_experience.facts if fact.role == "assistant")
        return SemanticExtraction(
            extractor_id="test",
            revision="invalid-preference",
            candidates=(
                SemanticCandidate(
                    memory_type="user_preference",
                    title="Invented preference",
                    content="The assistant preferred this.",
                    category="style",
                    source_fact_ids=(assistant.fact_id,),
                    subject_key="answer-style",
                    preference="Use the assistant's style.",
                ),
            ),
        )


class _IssueLifecycleExtractor:
    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        payload = request.task_experience.payload
        assert isinstance(payload, TaskExperiencePayload)
        user = next(fact for fact in request.task_experience.facts if fact.role == "user")
        issue_key = next(key for key in request.allowed_workstream_keys if key.startswith("issue:"))
        if payload.outcome == "failure":
            state = SemanticCandidate(
                memory_type="work_state",
                title="Issue 42 remains open",
                content="The repair attempt failed.",
                category="issue",
                source_fact_ids=(user.fact_id,),
                workstream_key=issue_key,
                workstream_state="open",
                goal=user.value,
                progress="The first repair failed.",
                next_step="Try the repair again.",
            )
        else:
            state = SemanticCandidate(
                memory_type="work_state",
                title="Issue 42 is complete",
                content="The follow-up repair succeeded.",
                category="issue",
                source_fact_ids=(user.fact_id,),
                workstream_key=issue_key,
                workstream_state="closed",
                goal=user.value,
                progress="The repair passed.",
                next_step=None,
                terminal_outcome="completed",
            )
        return SemanticExtraction(
            extractor_id="test",
            revision="issue-lifecycle",
            candidates=(state,),
        )


class _TimeoutThenSuccess(_EmptyExtractor):
    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("provider timeout")
        return SemanticExtraction(
            extractor_id="test",
            revision="retry-1",
            candidates=(),
        )


class _AlwaysFail:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        del request
        self.calls += 1
        raise TimeoutError("provider timeout")


def _capture(root: Path, *, fixture: str = "codex/failed_command.jsonl") -> None:
    _runtime(root).import_session(
        FIXTURES / fixture,
        repo_key="acme/widgets",
        boundary_kind="manual_finalize",
    )


def test_missing_semantic_provider_keeps_experience_and_pending_job(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    _capture(root)

    report = _runtime(root).process_pending(worker_id="test")
    state = SQLiteState(root / "state.sqlite3")

    assert report.pending == 1
    assert report.completed == 0
    assert len(state.list_memories(repo_key="acme/widgets")) == 1
    assert state.list_semantic_jobs()[0].status == "pending"
    assert state.index_health().pending == 1


def test_semantic_success_persists_multiple_optional_memories_and_one_work_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    _capture(root)

    report = _runtime(root, extractor=_RichExtractor()).process_pending(worker_id="test")
    state = SQLiteState(root / "state.sqlite3")
    memories = state.list_memories(repo_key="acme/widgets")
    batches = state.list_semantic_batches()

    assert report.completed == 1
    assert [memory.memory_type for memory in memories].count("task_experience") == 1
    assert [memory.memory_type for memory in memories].count("repository_knowledge") == 2
    assert [memory.memory_type for memory in memories].count("user_preference") == 1
    assert [memory.memory_type for memory in memories].count("work_state") == 1
    assert state.index_health().pending == 5
    assert len(batches) == 1
    evolution = batches[0]["evolution"]
    assert isinstance(evolution, list)
    assert len(evolution) == 1


def test_assistant_authored_preference_is_rejected_without_losing_experience(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    _capture(root, fixture="claude/failed_command.jsonl")

    report = _runtime(
        root,
        extractor=_AssistantPreferenceExtractor(),
    ).process_pending(worker_id="test")
    state = SQLiteState(root / "state.sqlite3")

    assert report.failed == 1
    assert len(state.list_memories(repo_key="acme/widgets")) == 1
    assert state.list_semantic_jobs()[0].status == "failed"


def test_provider_timeout_is_retryable_and_completed_job_is_not_called_again(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    extractor = _TimeoutThenSuccess()
    _capture(root)
    runtime = _runtime(root, extractor=extractor)

    first = runtime.process_pending(worker_id="test")
    second = runtime.process_pending(worker_id="test")
    third = runtime.process_pending(worker_id="test")
    job = SQLiteState(root / "state.sqlite3").list_semantic_jobs()[0]

    assert first.failed == 1
    assert second.completed == 1
    assert third.leased == 0
    assert extractor.calls == 2
    assert job.status == "completed"
    assert job.attempt_count == 2


def test_semantic_retry_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    extractor = _AlwaysFail()
    _capture(root)
    runtime = _runtime(root, extractor=extractor)

    reports = tuple(runtime.process_pending(worker_id="test") for _attempt in range(4))
    job = SQLiteState(root / "state.sqlite3").list_semantic_jobs()[0]

    assert [report.leased for report in reports] == [1, 1, 1, 0]
    assert extractor.calls == 3
    assert job.status == "failed"
    assert job.attempt_count == 3


def test_later_episode_can_close_one_existing_issue_workstream(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    source = tmp_path / "issue-session.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "issue-session"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix issue #42."}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "check"}),
                "call_id": "first",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "first",
                "output": "Process exited with code 1",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Finish issue #42."}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "check"}),
                "call_id": "second",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "second",
                "output": "Process exited with code 0",
            },
        },
    ]
    source.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )
    _runtime(root).import_session(
        source,
        repo_key="acme/widgets",
        boundary_kind="manual_finalize",
    )

    report = _runtime(
        root,
        extractor=_IssueLifecycleExtractor(),
    ).process_pending(worker_id="test")
    work_states = [
        memory
        for memory in SQLiteState(root / "state.sqlite3").list_memories(repo_key="acme/widgets")
        if isinstance(memory.payload, WorkStatePayload)
    ]
    state = SQLiteState(root / "state.sqlite3")

    assert report.completed == 2
    assert {memory.payload.workstream_state for memory in work_states} == {"closed", "open"}
    assert {memory.payload.workstream_key for memory in work_states} == {"issue:acme/widgets#42"}
    assert (
        next(
            memory.payload for memory in work_states if memory.payload.workstream_state == "closed"
        ).terminal_outcome
        == "completed"
    )
    assert sorted(
        state.memory_status(repo_key="acme/widgets", memory_id=memory.memory_id)
        for memory in work_states
    ) == ["active", "superseded"]


@pytest.mark.parametrize(
    "crash_stage",
    [
        "semantic_after_intent_prepared",
        "semantic_after_atomic_create",
        "semantic_transaction_b_start",
        "semantic_before_commit",
        "semantic_after_complete",
    ],
)
def test_semantic_commit_recovers_without_recalling_provider(
    tmp_path: Path,
    crash_stage: str,
) -> None:
    root = tmp_path / "runtime"
    extractor = _RichExtractor()
    _capture(root)
    crashed = False

    def fail_once(stage: str) -> None:
        nonlocal crashed
        if stage == crash_stage and not crashed:
            crashed = True
            raise RuntimeError(f"injected crash at {stage}")

    with pytest.raises(RuntimeError, match="injected crash"):
        _runtime(
            root,
            extractor=extractor,
            fault_injector=fail_once,
        ).process_pending(worker_id="test")

    report = _runtime(root, extractor=extractor).process_pending(worker_id="test")
    state = SQLiteState(root / "state.sqlite3")

    assert crashed is True
    assert report.leased == 0
    assert state.list_semantic_jobs()[0].status == "completed"
    assert len(state.list_memories(repo_key="acme/widgets")) == 5
