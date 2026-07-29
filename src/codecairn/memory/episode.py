"""Pure Task Episode closure state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from codecairn.memory.models import AgentTrace, TraceEpisodeOutcome, TraceEvent
from codecairn.memory.schema import SourceOrderKey, TaskEpisode
from codecairn.memory.trace import extend_raw_prefix_sha256

BoundaryKind = Literal["next_user", "codex_stop", "claude_session_end", "manual_finalize", "pico_turn_end"]


@dataclass(frozen=True, slots=True)
class ClosedEpisode:
    record: TaskEpisode
    events: tuple[TraceEvent, ...]
    outcome: TraceEpisodeOutcome


def close_trace_episodes(
    trace: AgentTrace, *, repo_key: str, existing: tuple[TaskEpisode, ...] = (), final_boundary: BoundaryKind | None = None
) -> tuple[ClosedEpisode, ...]:
    """Close only next-user spans and one explicit final boundary."""
    last = max(existing, key=lambda item: item.end_event_index_exclusive, default=None)
    start_cursor = last.end_event_index_exclusive if last is not None else trace.resumed_from_raw_event_index
    events = tuple(event for event in trace.events if event.evidence.raw_event_index >= start_cursor)
    if not events:
        return ()
    closed: list[ClosedEpisode] = []
    active: list[TraceEvent] = []
    continuation_id: str | None = None
    for event in events:
        is_user = event.kind == "message" and event.role == "user"
        if is_user:
            if active:
                closed.append(
                    _close(
                        trace,
                        repo_key=repo_key,
                        events=active,
                        boundary_kind="next_user",
                        end_cursor=event.evidence.raw_event_index,
                        continues_episode_id=continuation_id,
                    )
                )
            active = [event]
            continuation_id = None
            continue
        if active:
            active.append(event)
        elif last is not None and is_episode_signal(event):
            active = [event]
            continuation_id = last.episode_id
    if active and final_boundary is not None:
        closed.append(
            _close(
                trace,
                repo_key=repo_key,
                events=active,
                boundary_kind=final_boundary,
                end_cursor=trace.raw_event_count,
                continues_episode_id=continuation_id,
            )
        )
    return tuple(closed)


def is_episode_signal(event: TraceEvent) -> bool:
    return bool(
        (event.kind == "message" and event.text)
        or (event.kind == "tool_call" and event.tool_name and event.call_id)
        or (event.kind == "tool_result" and (event.text or event.tool_status or event.exit_code is not None))
        or event.file_changes
    )


def _close(
    trace: AgentTrace,
    *,
    repo_key: str,
    events: list[TraceEvent],
    boundary_kind: BoundaryKind,
    end_cursor: int,
    continues_episode_id: str | None,
) -> ClosedEpisode:
    opening = events[0]
    start_cursor = opening.evidence.raw_event_index
    record = TaskEpisode.create(
        repo_key=repo_key,
        provider=trace.provider,
        session_id=trace.session_id,
        source_generation=1,
        start_event_index=start_cursor,
        end_event_index_exclusive=end_cursor,
        opening_event_id=opening.event_id,
        boundary_kind=boundary_kind,
        continues_episode_id=continues_episode_id,
        source_order_key=SourceOrderKey(
            trusted_timestamp_ms=None,
            provider=trace.provider,
            session_id=trace.session_id,
            source_generation=1,
            event_index=start_cursor,
        ),
        prefix_sha256=_prefix_at(trace, end_cursor),
    )
    selected = tuple(event for event in events if start_cursor <= event.evidence.raw_event_index < end_cursor)
    return ClosedEpisode(record=record, events=selected, outcome=_outcome(selected))


def _prefix_at(trace: AgentTrace, cursor: int) -> str:
    prefix = trace.raw_prefix_sha256
    for index, digest in enumerate(trace.raw_suffix_event_sha256s, start=trace.resumed_from_raw_event_index):
        if index >= cursor:
            break
        prefix = extend_raw_prefix_sha256(prefix, digest)
    return prefix


def _outcome(events: tuple[TraceEvent, ...]) -> TraceEpisodeOutcome:
    results = tuple(
        event.observed_outcome for event in events if event.kind == "tool_result" and event.observed_outcome in {"success", "failure"}
    )
    if not results:
        results = tuple(
            "success" if event.exit_code == 0 else "failure"
            for event in events
            if event.kind == "tool_result" and event.is_command_result and event.exit_code is not None
        )
    if not results:
        return "unknown"
    successes = sum(outcome == "success" for outcome in results)
    if successes == len(results):
        return "success"
    if successes == 0:
        return "failure"
    return "partial"
