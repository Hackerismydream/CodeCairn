"""Deterministic Source Fact derivation from normalized trace events."""

from __future__ import annotations

import hashlib
import shlex
from pathlib import PurePath
from typing import cast

from codecairn.memory.episode import ClosedEpisode
from codecairn.memory.models import TraceEvent
from codecairn.memory.schema import EvidenceFact, FactAttributes, FactKind, FactRole, Provider, SourceLocation


def collect_evidence_facts(episodes: tuple[ClosedEpisode, ...], *, repo_key: str) -> tuple[EvidenceFact, ...]:
    """Project normalized events into closed, immutable Source Facts."""
    facts: list[EvidenceFact] = []
    for episode in episodes:
        command_fact_by_call: dict[str, str] = {}
        tool_fact_by_call: dict[str, str] = {}
        for event in episode.events:
            event_facts = _event_facts(
                event,
                repo_key=repo_key,
                episode_id=episode.record.episode_id,
                command_fact_by_call=command_fact_by_call,
                tool_fact_by_call=tool_fact_by_call,
            )
            facts.extend(event_facts)
            for fact in event_facts:
                call_id = event.call_id
                if call_id and fact.fact_kind == "command":
                    command_fact_by_call[call_id] = fact.fact_id
                elif call_id and fact.fact_kind == "tool_call":
                    tool_fact_by_call[call_id] = fact.fact_id
    return tuple(facts)


def _event_facts(
    event: TraceEvent, *, repo_key: str, episode_id: str, command_fact_by_call: dict[str, str], tool_fact_by_call: dict[str, str]
) -> tuple[EvidenceFact, ...]:
    location = _source_location(event)
    specifications: list[tuple[FactKind, FactRole | None, str, FactAttributes]] = []
    if event.kind == "message" and event.text and event.role in {"user", "assistant", "tool", "system"}:
        specifications.append(("message", cast(FactRole, event.role), event.text, {}))
    if event.kind == "tool_call" and event.tool_name and event.call_id:
        specifications.append(("tool_call", None, event.tool_name, {"tool_name": event.tool_name, "call_id": event.call_id}))
        if event.command:
            specifications.append(("command", None, event.command, {"command": event.command}))
    for change in event.file_changes:
        attributes: dict[str, str] = {"path": change.path, "change_kind": change.operation}
        if change.destination_path is not None:
            attributes["destination_path"] = change.destination_path
        specifications.append(("file_change", None, change.path, attributes))
    if event.kind == "tool_result" and event.call_id:
        outcome = _event_outcome(event)
        tool_fact_id = tool_fact_by_call.get(event.call_id)
        if tool_fact_id is not None:
            specifications.append(
                ("tool_result", None, event.tool_status or outcome, {"tool_call_fact_id": tool_fact_id, "outcome": outcome})
            )
        command_fact_id = command_fact_by_call.get(event.call_id)
        if event.is_command_result and command_fact_id is not None:
            attributes_result: dict[str, str | int] = {"command_fact_id": command_fact_id, "outcome": outcome}
            if event.exit_code is not None:
                attributes_result["exit_code"] = event.exit_code
            specifications.append(("command_result", None, event.command or event.tool_status or outcome, attributes_result))
            if event.command and _is_verification_command(event.command):
                specifications.append(
                    (
                        "verification",
                        None,
                        event.command,
                        {"check_name": _verification_name(event.command), "outcome": outcome, "command_fact_id": command_fact_id},
                    )
                )
        if tool_fact_id is None and command_fact_id is None and event.text:
            specifications.append(("message", "tool", event.text, {}))
    return tuple(
        EvidenceFact.create(
            repo_key=repo_key,
            location=location,
            fact_kind=kind,
            role=role,
            value=value,
            attributes=attributes,
            fact_ordinal=ordinal,
            episode_id=episode_id,
        )
        for ordinal, (kind, role, value, attributes) in enumerate(specifications)
    )


def _source_location(event: TraceEvent) -> SourceLocation:
    reference = event.evidence
    return SourceLocation(
        provider=cast(Provider, reference.provider),
        session_id=reference.session_id,
        source_generation=1,
        event_index=reference.raw_event_index,
        event_id=event.event_id,
        source_path_sha256=hashlib.sha256(reference.source_path.encode("utf-8")).hexdigest(),
        event_sha256=reference.raw_event_sha256,
    )


def _event_outcome(event: TraceEvent) -> str:
    if event.observed_outcome is not None:
        return event.observed_outcome
    if event.exit_code is not None:
        return "success" if event.exit_code == 0 else "failure"
    if event.tool_status in {"success", "completed"}:
        return "success"
    if event.tool_status in {"failure", "failed", "error"}:
        return "failure"
    return "unknown"


def _verification_name(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "repository-check"
    if tokens[:2] == ["uv", "run"]:
        tokens = tokens[2:]
    return PurePath(tokens[0]).name if tokens else "repository-check"


def _is_verification_command(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if tokens[:2] == ["uv", "run"]:
        tokens = tokens[2:]
    if not tokens:
        return False
    executable = PurePath(tokens[0]).name
    if executable in {"pytest", "mypy", "tox", "nox"}:
        return True
    if executable == "ruff":
        return len(tokens) > 1 and tokens[1] in {"check", "format"}
    if executable == "make":
        return any(target in {"check", "ci", "lint", "test"} for target in tokens[1:])
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        return len(tokens) > 1 and (tokens[1] == "test" or (tokens[1] == "run" and len(tokens) > 2 and tokens[2] in {"build", "test"}))
    if executable == "cargo":
        return len(tokens) > 1 and tokens[1] in {"build", "check", "test"}
    if executable == "go":
        return len(tokens) > 1 and tokens[1] in {"build", "test", "vet"}
    return executable in {"gradle", "gradlew", "mvn", "mvnw"}
