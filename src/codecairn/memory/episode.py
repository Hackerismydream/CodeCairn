from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from codecairn.memory.models import (
    EvidenceFact,
    EvidenceReference,
    SemanticAtomicFact,
    SemanticEpisode,
)
from codecairn.memory.trace import stable_id


@dataclass(frozen=True, slots=True)
class AttributedTurn:
    """One exact source turn with deterministic attribution."""

    turn_id: str
    actor: str
    role: str
    text: str
    occurred_at: str | None
    evidence: EvidenceReference


@dataclass(frozen=True, slots=True)
class AttributedEpisode:
    """Small public input contract for writing a complete source episode."""

    repo_key: str
    source_episode_id: str
    title: str
    turns: tuple[AttributedTurn, ...]
    adjacency_group_id: str | None = None
    adjacency_index: int | None = None


class EpisodeSemanticizer(Protocol):
    semanticizer_id: str
    revision: str

    def compile(
        self,
        facts: tuple[EvidenceFact, ...],
        *,
        episode_id: str,
    ) -> SemanticEpisode: ...


class LosslessEpisodeSemanticizer:
    """Preserve the original deterministic default Episode projection contract."""

    semanticizer_id = "codecairn/lossless-episode"
    revision = "v1"

    def compile(
        self,
        facts: tuple[EvidenceFact, ...],
        *,
        episode_id: str,
    ) -> SemanticEpisode:
        ordered = tuple(sorted(facts, key=_fact_order))
        atomic_facts = tuple(
            SemanticAtomicFact(
                fact_id=stable_id(
                    "semantic-atomic-fact",
                    episode_id,
                    fact.fact_id,
                    render_attributed_fact(fact),
                ),
                text=render_attributed_fact(fact),
                source_fact_ids=(fact.fact_id,),
            )
            for fact in ordered
        )
        return SemanticEpisode(
            episode_id=episode_id,
            narrative="\n".join(item.text for item in atomic_facts),
            atomic_facts=atomic_facts,
            source_fact_ids=tuple(fact.fact_id for fact in ordered),
            semanticizer_id=self.semanticizer_id,
            revision=self.revision,
        )


def compile_source_facts(episode: AttributedEpisode) -> tuple[str, tuple[EvidenceFact, ...]]:
    """Validate an attributed episode and derive immutable source facts."""

    if not episode.repo_key.strip():
        raise ValueError("Episode repository key must not be empty")
    if not episode.source_episode_id.strip():
        raise ValueError("Episode source identity must not be empty")
    if not episode.title.strip():
        raise ValueError("Episode title must not be empty")
    if not episode.turns:
        raise ValueError("Episode must contain at least one turn")
    _validate_adjacency(episode)
    turn_ids = tuple(turn.turn_id for turn in episode.turns)
    if any(not turn_id.strip() for turn_id in turn_ids) or len(turn_ids) != len(set(turn_ids)):
        raise ValueError("Episode turn identities must be non-empty and unique")
    episode_id = stable_id(
        "attributed-episode",
        episode.repo_key,
        episode.source_episode_id,
    )
    facts: list[EvidenceFact] = []
    for turn in episode.turns:
        if not turn.actor.strip() or not turn.role.strip() or not turn.text.strip():
            raise ValueError("Episode turns require actor, role, and exact text")
        if turn.occurred_at is not None:
            try:
                parsed = datetime.fromisoformat(turn.occurred_at)
            except ValueError as error:
                raise ValueError("Episode turn timestamp must be ISO-8601") from error
            if parsed.tzinfo is None:
                raise ValueError("Episode turn timestamp must include an offset")
        facts.append(
            EvidenceFact(
                fact_id=stable_id(
                    "attributed-turn",
                    episode.repo_key,
                    episode.source_episode_id,
                    turn.turn_id,
                    turn.evidence.raw_event_sha256,
                ),
                repo_key=episode.repo_key,
                episode_id=episode_id,
                kind="conversation_turn",
                text=turn.text,
                role=turn.role,
                evidence=(turn.evidence,),
                actor=turn.actor,
                occurred_at=turn.occurred_at,
            )
        )
    return episode_id, tuple(sorted(facts, key=_fact_order))


def episode_summary(facts: tuple[EvidenceFact, ...]) -> str:
    first = min(facts, key=_fact_order)
    timestamp = first.occurred_at or "Unknown time"
    return f"{timestamp} — attributed episode with {len(facts)} turns."


def attributed_episode_attempt_id(
    episode: AttributedEpisode,
    facts: tuple[EvidenceFact, ...],
    *,
    semantic_episode: SemanticEpisode | None = None,
) -> str:
    """Identify one auditable source or semantic write attempt without changing fact IDs."""

    payload: dict[str, object] = {
        "schema": "codecairn/attributed-episode-attempt-v1",
        "stage": "source" if semantic_episode is None else "semantic",
        "repo_key": episode.repo_key,
        "source_episode_id": episode.source_episode_id,
        "title": episode.title,
        "adjacency_group_id": episode.adjacency_group_id,
        "adjacency_index": episode.adjacency_index,
        "facts": [
            {
                "fact_id": fact.fact_id,
                "repo_key": fact.repo_key,
                "episode_id": fact.episode_id,
                "kind": fact.kind,
                "text": fact.text,
                "role": fact.role,
                "status": fact.status,
                "actor": fact.actor,
                "occurred_at": fact.occurred_at,
                "evidence": [
                    {
                        "provider": reference.provider,
                        "session_id": reference.session_id,
                        "source_path": reference.source_path,
                        "raw_event_sha256": reference.raw_event_sha256,
                        "raw_event_index": reference.raw_event_index,
                        "raw_event_type": reference.raw_event_type,
                        "call_id": reference.call_id,
                    }
                    for reference in fact.evidence
                ],
            }
            for fact in facts
        ],
    }
    if semantic_episode is not None:
        payload["semantic_episode"] = {
            "episode_id": semantic_episode.episode_id,
            "narrative": semantic_episode.narrative,
            "atomic_facts": [
                {
                    "fact_id": fact.fact_id,
                    "text": fact.text,
                    "source_fact_ids": list(fact.source_fact_ids),
                }
                for fact in semantic_episode.atomic_facts
            ],
            "source_fact_ids": list(semantic_episode.source_fact_ids),
            "semanticizer_id": semantic_episode.semanticizer_id,
            "revision": semantic_episode.revision,
        }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return stable_id("attributed-episode-proposal", canonical)


def _validate_adjacency(episode: AttributedEpisode) -> None:
    group_id = episode.adjacency_group_id
    index = episode.adjacency_index
    if (group_id is None) != (index is None):
        raise ValueError("Episode adjacency group and index must be configured together")
    if group_id is None:
        return
    if not group_id.strip():
        raise ValueError("Episode adjacency group must not be empty")
    if type(index) is not int or index < 0:
        raise ValueError("Episode adjacency index must be a non-negative integer")


def render_attributed_fact(fact: EvidenceFact) -> str:
    """Render attribution for retrieval while keeping EvidenceFact.text exact."""

    actor = fact.actor
    if fact.occurred_at and actor:
        return f"{fact.occurred_at} — {actor}: {fact.text}"
    if fact.occurred_at:
        return f"{fact.occurred_at} — {fact.text}"
    if actor:
        return f"{actor}: {fact.text}"
    return fact.text


def render_episode(memory_facts: tuple[EvidenceFact, ...]) -> str:
    return "\n".join(render_attributed_fact(fact) for fact in sorted(memory_facts, key=_fact_order))


def _fact_order(fact: EvidenceFact) -> tuple[int, str]:
    return (
        min((item.raw_event_index for item in fact.evidence), default=-1),
        fact.fact_id,
    )
