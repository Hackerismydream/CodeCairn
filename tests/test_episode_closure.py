from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from codecairn.memory.episode import close_trace_episodes
from codecairn.memory.evidence import collect_evidence_facts
from codecairn.memory.models import AgentTrace, TraceEvent, TraceReference
from codecairn.memory.trace import EMPTY_RAW_PREFIX_SHA256
from codecairn.storage.sqlite import SQLiteState


def _event(
    index: int,
    *,
    kind: str = "message",
    role: str | None = None,
    text: str | None = None,
    exit_code: int | None = None,
) -> TraceEvent:
    digest = hashlib.sha256(f"event-{index}".encode()).hexdigest()
    return TraceEvent(
        event_id=f"event-{index}",
        kind=kind,  # type: ignore[arg-type]
        evidence=TraceReference(
            provider="codex",
            session_id="session-1",
            source_path="/tmp/session.jsonl",
            raw_event_sha256=digest,
            raw_event_index=index,
            raw_event_type=kind,
        ),
        role=role,
        text=text,
        exit_code=exit_code,
        is_command_result=kind == "tool_result",
    )


def _trace(events: tuple[TraceEvent, ...]) -> AgentTrace:
    return AgentTrace(
        trace_id="trace-1",
        provider="codex",
        session_id="session-1",
        source_path="/tmp/session.jsonl",
        source_sha256="f" * 64,
        raw_event_count=len(events),
        resumed_from_raw_event_index=0,
        raw_prefix_sha256=EMPTY_RAW_PREFIX_SHA256,
        raw_prefix_call_ids=(),
        raw_prefix_file_change_fact_count=0,
        raw_suffix_event_sha256s=tuple(event.evidence.raw_event_sha256 for event in events),
        events=events,
    )


def test_next_user_closes_previous_and_leaves_final_suffix_open() -> None:
    trace = _trace(
        (
            _event(0, role="user", text="First task"),
            _event(1, role="assistant", text="Done"),
            _event(2, role="user", text="Second task"),
        )
    )

    closed = close_trace_episodes(trace, repo_key="acme/widgets")

    assert len(closed) == 1
    assert closed[0].record.start_event_index == 0
    assert closed[0].record.end_event_index_exclusive == 2
    assert closed[0].record.boundary_kind == "next_user"


def test_explicit_boundary_reports_partial_outcome() -> None:
    trace = _trace(
        (
            _event(0, role="user", text="Run both checks"),
            _event(1, kind="tool_result", exit_code=0),
            _event(2, kind="tool_result", exit_code=1),
        )
    )

    closed = close_trace_episodes(
        trace,
        repo_key="acme/widgets",
        final_boundary="manual_finalize",
    )

    assert len(closed) == 1
    assert closed[0].outcome == "partial"
    assert closed[0].record.end_event_index_exclusive == 3


def test_same_cursor_boundary_is_first_close_wins_under_concurrency(
    tmp_path: Path,
) -> None:
    trace = _trace((_event(0, role="user", text="Ship it"),))
    manual = close_trace_episodes(
        trace,
        repo_key="acme/widgets",
        final_boundary="manual_finalize",
    )[0].record
    stop = replace(manual, boundary_kind="codex_stop")
    database = tmp_path / "state.sqlite3"

    def store(record: object) -> str:
        committed = SQLiteState(database).store_episode(record)  # type: ignore[arg-type]
        return committed.episode_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = tuple(executor.map(store, (manual, stop)))

    episodes = SQLiteState(database).list_episodes(
        repo_key="acme/widgets",
        provider="codex",
        session_id="session-1",
    )
    assert ids == (manual.episode_id, manual.episode_id)
    assert len(episodes) == 1
    assert episodes[0].boundary_kind in {"manual_finalize", "codex_stop"}


def test_late_unpaired_tool_result_becomes_auditable_continuation() -> None:
    first_trace = _trace((_event(0, role="user", text="Run the check"),))
    first = close_trace_episodes(
        first_trace,
        repo_key="acme/widgets",
        final_boundary="codex_stop",
    )[0]
    full_trace = _trace(
        (
            _event(0, role="user", text="Run the check"),
            replace(
                _event(1, kind="tool_result", exit_code=1),
                call_id="late-result",
                text="Process exited with code 1",
            ),
        )
    )

    continuation = close_trace_episodes(
        full_trace,
        repo_key="acme/widgets",
        existing=(first.record,),
        final_boundary="codex_stop",
    )
    facts = collect_evidence_facts(continuation, repo_key="acme/widgets")

    assert len(continuation) == 1
    assert continuation[0].record.continues_episode_id == first.record.episode_id
    assert continuation[0].outcome == "failure"
    assert len(facts) == 1
    assert facts[0].fact_kind == "message"
    assert facts[0].role == "tool"
