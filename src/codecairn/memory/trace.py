from __future__ import annotations

import hashlib

from codecairn.memory.models import (
    AgentTrace,
    CodingMemory,
    EpisodeOutcome,
    TaskEpisode,
    TraceEvent,
)


def stable_id(prefix: str, *parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:20]}"


def segment_tasks(trace: AgentTrace, *, repo_key: str) -> tuple[TaskEpisode, ...]:
    episodes: list[TaskEpisode] = []
    current: list[TraceEvent] = []

    for event in trace.events:
        starts_task = event.kind == "message" and event.role == "user"
        if starts_task and current:
            episodes.append(_build_episode(trace, repo_key=repo_key, events=current))
            current = []
        current.append(event)

    if current:
        episodes.append(_build_episode(trace, repo_key=repo_key, events=current))
    return tuple(episodes)


def extract_failed_commands(
    episodes: tuple[TaskEpisode, ...], *, repo_key: str
) -> tuple[CodingMemory, ...]:
    memories: list[CodingMemory] = []
    for episode in episodes:
        calls = {
            event.call_id: event
            for event in episode.events
            if event.kind == "tool_call" and event.call_id is not None
        }
        for event in episode.events:
            if event.kind != "tool_result" or event.exit_code in {None, 0}:
                continue
            if event.command is None:
                continue
            call = calls.get(event.call_id) if event.call_id is not None else None
            if call is None or call.command != event.command:
                continue
            memory_id = stable_id(
                "memory",
                repo_key,
                "failed_command",
                episode.episode_id,
                call.event_id,
                event.event_id,
            )
            memories.append(
                CodingMemory(
                    memory_id=memory_id,
                    repo_key=repo_key,
                    memory_type="failed_command",
                    title="Failed Command",
                    summary=(
                        "A repository command failed. Inspect both cited raw events "
                        "before deciding whether to repeat it."
                    ),
                    episode_id=episode.episode_id,
                    command=event.command,
                    exit_code=event.exit_code,
                    evidence=(call.evidence, event.evidence),
                )
            )
    return tuple(memories)


def _build_episode(trace: AgentTrace, *, repo_key: str, events: list[TraceEvent]) -> TaskEpisode:
    opening = next(
        (event for event in events if event.kind == "message" and event.role == "user"),
        events[0],
    )
    return TaskEpisode(
        episode_id=stable_id(
            "episode",
            repo_key,
            trace.provider,
            trace.session_id,
            opening.event_id,
        ),
        trace_id=trace.trace_id,
        opening_event_id=opening.event_id,
        events=tuple(events),
        outcome=_outcome(events),
    )


def _outcome(events: list[TraceEvent]) -> EpisodeOutcome:
    results = [event.exit_code for event in events if event.kind == "tool_result"]
    if any(code is not None and code != 0 for code in results):
        return "failed"
    if any(code == 0 for code in results):
        return "success"
    return "unknown"
