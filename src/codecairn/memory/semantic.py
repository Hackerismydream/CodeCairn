from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from codecairn.memory.models import EvidenceFact, SemanticAtomicFact, SemanticEpisode
from codecairn.memory.trace import stable_id

_MAX_CLAUSES = 8_192
_MAX_CLAUSE_CHARS = 4_096
_MAX_SOURCE_FACTS = 8_192


@dataclass(frozen=True, slots=True)
class ProjectionIdentity:
    adapter_id: str
    revision: str
    model_id: str | None = None
    config_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionFact:
    fact_id: str
    text: str
    actor: str | None
    role: str | None
    occurred_at: str | None
    source_order: int


@dataclass(frozen=True, slots=True)
class ProjectionSource:
    episode_id: str
    source_digest: str
    facts: tuple[ProjectionFact, ...]


@dataclass(frozen=True, slots=True)
class ClauseDraft:
    text: str
    source_fact_ids: tuple[str, ...]


class ClauseProjectionAdapter(Protocol):
    identity: ProjectionIdentity

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]: ...


class ProjectionCache(Protocol):
    def get(self, cache_key: str) -> tuple[ClauseDraft, ...] | None: ...

    def put(self, cache_key: str, drafts: tuple[ClauseDraft, ...]) -> None: ...


class InMemoryProjectionCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[ClauseDraft, ...]] = {}

    def get(self, cache_key: str) -> tuple[ClauseDraft, ...] | None:
        return self._entries.get(cache_key)

    def put(self, cache_key: str, drafts: tuple[ClauseDraft, ...]) -> None:
        self._entries[cache_key] = drafts


class LosslessClauseProjectionAdapter:
    identity = ProjectionIdentity(
        adapter_id="codecairn/lossless-clause",
        revision="v1",
    )

    def propose(self, source: ProjectionSource) -> tuple[ClauseDraft, ...]:
        return tuple(
            ClauseDraft(
                text=_render_projection_fact(fact),
                source_fact_ids=(fact.fact_id,),
            )
            for fact in source.facts
        )


class GroundedClauseSemanticizer:
    def __init__(
        self,
        *,
        adapter: ClauseProjectionAdapter,
        cache: ProjectionCache | None = None,
        max_source_facts: int = _MAX_SOURCE_FACTS,
        max_clauses: int = _MAX_CLAUSES,
        max_clause_chars: int = _MAX_CLAUSE_CHARS,
    ) -> None:
        if max_source_facts < 1:
            raise ValueError("max_source_facts must be positive")
        if max_clauses < 1:
            raise ValueError("max_clauses must be positive")
        if max_clause_chars < 1:
            raise ValueError("max_clause_chars must be positive")
        self._adapter = adapter
        self._cache = cache
        self._max_source_facts = max_source_facts
        self._max_clauses = max_clauses
        self._max_clause_chars = max_clause_chars
        self.semanticizer_id = adapter.identity.adapter_id
        self.revision = adapter.identity.revision

    def compile(
        self,
        facts: tuple[EvidenceFact, ...],
        *,
        episode_id: str,
    ) -> SemanticEpisode:
        if not facts:
            raise ValueError("Semantic projection requires at least one source fact")
        if len(facts) > self._max_source_facts:
            raise ValueError("Semantic projection source fact count exceeds the configured limit")
        ordered = tuple(sorted(facts, key=_fact_order))
        fact_ids = tuple(fact.fact_id for fact in ordered)
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Semantic projection source fact IDs must be unique")
        source = ProjectionSource(
            episode_id=episode_id,
            source_digest=_source_digest(ordered, episode_id=episode_id),
            facts=tuple(
                ProjectionFact(
                    fact_id=fact.fact_id,
                    text=fact.text,
                    actor=fact.actor,
                    role=fact.role,
                    occurred_at=fact.occurred_at,
                    source_order=_fact_order(fact)[0],
                )
                for fact in ordered
            ),
        )
        cache_key = _cache_key(source.source_digest, identity=self._adapter.identity)
        cached = None if self._cache is None else self._cache.get(cache_key)
        proposed = self._adapter.propose(source) if cached is None else cached
        if len(proposed) > self._max_clauses:
            raise ValueError("Semantic projection clause count exceeds the configured limit")
        positions = {fact.fact_id: position for position, fact in enumerate(ordered)}
        drafts = tuple(
            sorted(
                (_canonical_draft(draft, positions=positions) for draft in proposed),
                key=lambda draft: _draft_order(draft, positions=positions),
            )
        )
        _validate_drafts(
            drafts,
            source_fact_ids=set(positions),
            max_clause_chars=self._max_clause_chars,
        )
        if cached is None and self._cache is not None:
            self._cache.put(cache_key, drafts)
        atomic_facts = tuple(
            SemanticAtomicFact(
                fact_id=stable_id(
                    "semantic-atomic-fact",
                    episode_id,
                    *draft.source_fact_ids,
                    draft.text,
                ),
                text=draft.text,
                source_fact_ids=draft.source_fact_ids,
            )
            for draft in drafts
        )
        narrative = "\n".join(fact.text for fact in atomic_facts)
        if not narrative:
            narrative = "\n".join(_render_projection_fact(fact) for fact in source.facts)
        return SemanticEpisode(
            episode_id=episode_id,
            narrative=narrative,
            atomic_facts=atomic_facts,
            source_fact_ids=tuple(fact.fact_id for fact in ordered),
            semanticizer_id=self.semanticizer_id,
            revision=self.revision,
        )


def semantic_episode_is_grounded(
    semantic_episode: SemanticEpisode,
    *,
    facts: tuple[EvidenceFact, ...],
) -> bool:
    """Validate a derived retrieval projection against authoritative source facts."""

    source_fact_ids = tuple(fact.fact_id for fact in facts)
    source_fact_set = set(source_fact_ids)
    if (
        not facts
        or len({fact.episode_id for fact in facts}) != 1
        or semantic_episode.episode_id != facts[0].episode_id
        or not semantic_episode.narrative.strip()
        or not semantic_episode.semanticizer_id.strip()
        or not semantic_episode.revision.strip()
        or semantic_episode.source_fact_ids != source_fact_ids
        or len(source_fact_ids) != len(source_fact_set)
    ):
        return False
    semantic_ids: set[str] = set()
    for atomic_fact in semantic_episode.atomic_facts:
        references = atomic_fact.source_fact_ids
        if (
            not atomic_fact.fact_id
            or atomic_fact.fact_id in semantic_ids
            or not atomic_fact.text.strip()
            or not references
            or len(references) != len(set(references))
            or not set(references) <= source_fact_set
            or atomic_fact.fact_id
            != stable_id(
                "semantic-atomic-fact",
                semantic_episode.episode_id,
                *references,
                atomic_fact.text,
            )
        ):
            return False
        semantic_ids.add(atomic_fact.fact_id)
    return True


def _render_projection_fact(fact: ProjectionFact) -> str:
    if fact.occurred_at and fact.actor:
        return f"{fact.occurred_at} — {fact.actor}: {fact.text}"
    if fact.occurred_at:
        return f"{fact.occurred_at} — {fact.text}"
    if fact.actor:
        return f"{fact.actor}: {fact.text}"
    return fact.text


def _canonical_draft(
    draft: ClauseDraft,
    *,
    positions: dict[str, int],
) -> ClauseDraft:
    text = " ".join(unicodedata.normalize("NFC", draft.text).split())
    source_fact_ids = tuple(
        sorted(
            draft.source_fact_ids,
            key=lambda fact_id: (positions.get(fact_id, len(positions)), fact_id),
        )
    )
    return ClauseDraft(text=text, source_fact_ids=source_fact_ids)


def _draft_order(
    draft: ClauseDraft,
    *,
    positions: dict[str, int],
) -> tuple[int, tuple[int, ...], tuple[str, ...], str]:
    source_positions = tuple(
        positions.get(fact_id, len(positions)) for fact_id in draft.source_fact_ids
    )
    return (
        min(source_positions, default=len(positions)),
        source_positions,
        draft.source_fact_ids,
        draft.text,
    )


def _validate_drafts(
    drafts: tuple[ClauseDraft, ...],
    *,
    source_fact_ids: set[str],
    max_clause_chars: int,
) -> None:
    if any(not draft.text for draft in drafts):
        raise ValueError("Semantic clause text must not be empty")
    if any(len(draft.text) > max_clause_chars for draft in drafts):
        raise ValueError("Semantic clause text exceeds the configured limit")
    if any(not draft.source_fact_ids for draft in drafts):
        raise ValueError("Semantic clause must cite at least one source fact")
    if any(len(draft.source_fact_ids) != len(set(draft.source_fact_ids)) for draft in drafts):
        raise ValueError("Semantic clause contains a duplicate source fact reference")
    clause_keys = {(draft.text, draft.source_fact_ids) for draft in drafts}
    if len(clause_keys) != len(drafts):
        raise ValueError("Projection contains a duplicate semantic clause")
    if any(fact_id not in source_fact_ids for draft in drafts for fact_id in draft.source_fact_ids):
        raise ValueError("Semantic clause references an unknown source fact")


def _source_digest(facts: tuple[EvidenceFact, ...], *, episode_id: str) -> str:
    payload = {
        "schema": "codecairn/projection-source-v1",
        "episode_id": episode_id,
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
                        "provider": item.provider,
                        "session_id": item.session_id,
                        "source_path": item.source_path,
                        "raw_event_sha256": item.raw_event_sha256,
                        "raw_event_index": item.raw_event_index,
                        "raw_event_type": item.raw_event_type,
                        "call_id": item.call_id,
                    }
                    for item in fact.evidence
                ],
            }
            for fact in facts
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _cache_key(source_digest: str, *, identity: ProjectionIdentity) -> str:
    payload = {
        "schema": "codecairn/projection-cache-key-v2",
        "source_digest": source_digest,
        "adapter": {
            "adapter_id": identity.adapter_id,
            "revision": identity.revision,
            "model_id": identity.model_id,
            "config_sha256": identity.config_sha256,
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fact_order(fact: EvidenceFact) -> tuple[int, str]:
    return (
        min((item.raw_event_index for item in fact.evidence), default=-1),
        fact.fact_id,
    )
