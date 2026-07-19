from __future__ import annotations

import hashlib
import unicodedata

from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceReference,
    GateDecision,
    GateDecisionReason,
    MemoryProposal,
    TaskEpisode,
)
from codecairn.memory.trace import stable_id

_MAX_REPOSITORY_RULE_BYTES = 1024 * 1024
_MAX_SOURCE_PATH_CHARS = 4_096


class EvidenceGate:
    """Resolve untrusted proposal references against deterministic facts."""

    def evaluate(
        self,
        proposal: MemoryProposal,
        *,
        facts: tuple[EvidenceFact, ...],
    ) -> GateDecision:
        facts_by_id = {fact.fact_id: fact for fact in facts}
        if len(facts_by_id) != len(facts):
            return _reject(proposal, reason="duplicate_fact_id")
        resolved: list[EvidenceFact] = []
        for fact_id in proposal.fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact is None:
                return _reject(
                    proposal,
                    reason="missing_fact",
                    resolved=tuple(resolved),
                )
            resolved.append(fact)
        if any(fact.repo_key != proposal.repo_key for fact in resolved):
            return _reject(
                proposal,
                reason="cross_repository_evidence",
                resolved=tuple(resolved),
            )
        resolved_facts = tuple(resolved)
        if proposal.memory_type == "user_preference":
            reason = _preference_rejection(proposal, resolved_facts)
        elif proposal.memory_type == "repository_convention":
            reason = _convention_rejection(resolved_facts)
        else:
            reason = "unsupported_memory_type"
        if reason is not None:
            return _reject(proposal, reason=reason, resolved=resolved_facts)
        return _accept(proposal, resolved_facts)


def collect_evidence_facts(
    episodes: tuple[TaskEpisode, ...],
    *,
    repo_key: str,
) -> tuple[EvidenceFact, ...]:
    """Derive exact user quotes and repeated observations from Task Episodes."""
    quote_facts: list[EvidenceFact] = []
    by_text: dict[str, list[EvidenceFact]] = {}
    for episode in episodes:
        for event in episode.events:
            if event.kind != "message" or event.role != "user" or not event.text:
                continue
            fact = EvidenceFact(
                fact_id=stable_id(
                    "fact",
                    repo_key,
                    "user_quote",
                    episode.episode_id,
                    event.event_id,
                    event.text,
                ),
                repo_key=repo_key,
                episode_id=episode.episode_id,
                kind="user_quote",
                text=event.text,
                role="user",
                evidence=(event.evidence,),
            )
            quote_facts.append(fact)
            by_text.setdefault(event.text, []).append(fact)

    repeated_facts: list[EvidenceFact] = []
    for text, matching in by_text.items():
        if len(matching) < 2:
            continue
        episode_ids = tuple(dict.fromkeys(fact.episode_id for fact in matching))
        evidence = _unique_evidence(tuple(matching))
        if len(evidence) < 2:
            continue
        repeated_facts.append(
            EvidenceFact(
                fact_id=stable_id(
                    "fact",
                    repo_key,
                    "repeated_trace",
                    text,
                    *(fact.fact_id for fact in matching),
                ),
                repo_key=repo_key,
                episode_id=stable_id("episode-set", repo_key, *episode_ids),
                kind="repeated_trace",
                text=text,
                role="user",
                evidence=evidence,
            )
        )
    return (*quote_facts, *repeated_facts)


def collect_repository_rule_fact(
    *,
    repo_key: str,
    source_path: str,
    content: bytes,
) -> EvidenceFact:
    """Turn one configured repository rule document into immutable evidence."""
    if not repo_key.strip():
        raise ValueError("Repository rule namespace must not be empty")
    if (
        not source_path
        or len(source_path) > _MAX_SOURCE_PATH_CHARS
        or any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in source_path)
    ):
        raise ValueError("Repository rule source path is invalid")
    if not content or len(content) > _MAX_REPOSITORY_RULE_BYTES:
        raise ValueError("Repository rule document is empty or exceeds its byte limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Repository rule document must be UTF-8") from exc
    content_sha256 = hashlib.sha256(content).hexdigest()
    episode_id = stable_id(
        "repository-document",
        repo_key,
        source_path,
        content_sha256,
    )
    evidence = EvidenceReference(
        provider="repository_document",
        session_id=content_sha256,
        source_path=source_path,
        raw_event_sha256=content_sha256,
        raw_event_index=0,
        raw_event_type="repository_rule",
    )
    return EvidenceFact(
        fact_id=stable_id(
            "fact",
            repo_key,
            "repository_rule",
            source_path,
            content_sha256,
        ),
        repo_key=repo_key,
        episode_id=episode_id,
        kind="repository_rule",
        text=text,
        role=None,
        evidence=(evidence,),
    )


def _preference_rejection(
    proposal: MemoryProposal,
    facts: tuple[EvidenceFact, ...],
) -> GateDecisionReason | None:
    if not proposal.quote:
        return "preference_requires_quote"
    if proposal.quote_role != "user":
        return "preference_requires_user_role"
    if not any(
        fact.kind == "user_quote"
        and fact.role == "user"
        and bool(fact.evidence)
        and proposal.quote in fact.text
        for fact in facts
    ):
        return "quote_not_exact_source_substring"
    return None


def _convention_rejection(
    facts: tuple[EvidenceFact, ...],
) -> GateDecisionReason | None:
    grounded = any(_is_grounded_convention_fact(fact) for fact in facts)
    return None if grounded else "convention_requires_grounding"


def _is_grounded_convention_fact(fact: EvidenceFact) -> bool:
    if fact.kind == "user_quote":
        return fact.role == "user" and bool(fact.evidence)
    if fact.kind == "repository_rule":
        return (
            fact.role is None
            and bool(fact.evidence)
            and all(
                evidence.provider == "repository_document"
                and evidence.raw_event_type == "repository_rule"
                for evidence in fact.evidence
            )
        )
    if fact.kind != "repeated_trace" or fact.role != "user":
        return False
    locations = {
        (
            evidence.provider,
            evidence.session_id,
            evidence.raw_event_sha256,
            evidence.raw_event_index,
        )
        for evidence in fact.evidence
    }
    return len(locations) >= 2


def _accept(proposal: MemoryProposal, facts: tuple[EvidenceFact, ...]) -> GateDecision:
    evidence = _unique_evidence(facts)
    episode_ids = tuple(dict.fromkeys(fact.episode_id for fact in facts))
    fact_ids = tuple(fact.fact_id for fact in facts)
    memory = CodingMemory(
        memory_id=stable_id(
            "memory",
            proposal.repo_key,
            proposal.memory_type,
            proposal.title,
            proposal.summary,
            proposal.quote,
            *fact_ids,
        ),
        repo_key=proposal.repo_key,
        memory_type=proposal.memory_type,
        title=proposal.title,
        summary=proposal.summary,
        episode_id=(
            episode_ids[0]
            if len(episode_ids) == 1
            else stable_id("episode-set", proposal.repo_key, *episode_ids)
        ),
        command=None,
        exit_code=None,
        evidence=evidence,
        fact_ids=fact_ids,
    )
    return GateDecision(
        proposal_id=proposal.proposal_id,
        repo_key=proposal.repo_key,
        memory_type=proposal.memory_type,
        accepted=True,
        reason="accepted",
        proposed_fact_ids=proposal.fact_ids,
        resolved_fact_ids=fact_ids,
        memory=memory,
    )


def _reject(
    proposal: MemoryProposal,
    *,
    reason: GateDecisionReason,
    resolved: tuple[EvidenceFact, ...] = (),
) -> GateDecision:
    return GateDecision(
        proposal_id=proposal.proposal_id,
        repo_key=proposal.repo_key,
        memory_type=proposal.memory_type,
        accepted=False,
        reason=reason,
        proposed_fact_ids=proposal.fact_ids,
        resolved_fact_ids=tuple(fact.fact_id for fact in resolved),
    )


def _unique_evidence(facts: tuple[EvidenceFact, ...]) -> tuple[EvidenceReference, ...]:
    unique: dict[tuple[object, ...], EvidenceReference] = {}
    for fact in facts:
        for evidence in fact.evidence:
            key = (
                evidence.provider,
                evidence.session_id,
                evidence.raw_event_sha256,
                evidence.raw_event_index,
                evidence.raw_event_type,
                evidence.call_id,
            )
            unique.setdefault(key, evidence)
    return tuple(unique.values())
