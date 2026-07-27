from __future__ import annotations

import hashlib

from codecairn.memory.models import (
    AgentTrace,
    TraceEpisode,
    TraceEpisodeOutcome,
    TraceEvent,
)
from codecairn.memory.schema import episode_identity

EMPTY_RAW_PREFIX_SHA256 = hashlib.sha256(b"codecairn:raw-prefix:v1").hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:20]}"


def extend_raw_prefix_sha256(prefix_sha256: str, raw_event_sha256: str) -> str:
    """Extend the stable digest for one ordered raw-event prefix."""
    encoded = bytes.fromhex(prefix_sha256) + bytes.fromhex(raw_event_sha256)
    return hashlib.sha256(encoded).hexdigest()


def segment_tasks(trace: AgentTrace, *, repo_key: str) -> tuple[TraceEpisode, ...]:
    episodes: list[TraceEpisode] = []
    current: list[TraceEvent] | None = None

    for event in trace.events:
        starts_task = event.kind == "message" and event.role == "user"
        if starts_task:
            if current:
                episodes.append(_build_episode(trace, repo_key=repo_key, events=current))
            current = [event]
        elif current is not None:
            current.append(event)

    if current:
        episodes.append(_build_episode(trace, repo_key=repo_key, events=current))
    return tuple(episodes)


def _build_episode(trace: AgentTrace, *, repo_key: str, events: list[TraceEvent]) -> TraceEpisode:
    opening = next(
        (event for event in events if event.kind == "message" and event.role == "user"),
        events[0],
    )
    return TraceEpisode(
        episode_id=episode_identity(
            repo_key=repo_key,
            provider=trace.provider,
            session_id=trace.session_id,
            source_generation=1,
            start_event_index=opening.evidence.raw_event_index,
            end_event_index_exclusive=max(event.evidence.raw_event_index for event in events) + 1,
            opening_event_id=opening.event_id,
        ),
        trace_id=trace.trace_id,
        opening_event_id=opening.event_id,
        events=tuple(events),
        outcome=_outcome(events),
    )


def _outcome(events: list[TraceEvent]) -> TraceEpisodeOutcome:
    results = [
        event.exit_code
        for event in events
        if event.kind == "tool_result" and event.is_command_result
    ]
    if any(code is not None and code != 0 for code in results):
        return "failure"
    if any(code == 0 for code in results):
        return "success"
    return "unknown"
