from __future__ import annotations

import hashlib
import shlex
import unicodedata
from pathlib import PurePath

from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceFactKind,
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
        elif proposal.memory_type == "verified_fix":
            reason = _verified_fix_rejection(resolved_facts)
        elif proposal.memory_type == "debug_episode":
            reason = _debug_episode_rejection(resolved_facts)
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
    episode_facts: list[EvidenceFact] = []
    by_text: dict[str, list[EvidenceFact]] = {}
    for episode in episodes:
        calls = {
            event.call_id: event
            for event in episode.events
            if event.kind == "tool_call" and event.call_id is not None
        }
        command_outcome_facts: list[EvidenceFact] = []
        for event in episode.events:
            if event.kind == "message" and event.role == "user" and event.text:
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
                if event.event_id == episode.opening_event_id:
                    episode_facts.append(
                        EvidenceFact(
                            fact_id=stable_id(
                                "fact",
                                repo_key,
                                "task_prompt",
                                episode.episode_id,
                                event.event_id,
                                event.text,
                            ),
                            repo_key=repo_key,
                            episode_id=episode.episode_id,
                            kind="task_prompt",
                            text=event.text,
                            role="user",
                            evidence=(event.evidence,),
                        )
                    )
            if event.kind == "tool_call":
                action_text = event.command or event.tool_name
                if action_text:
                    episode_facts.append(
                        EvidenceFact(
                            fact_id=stable_id(
                                "fact",
                                repo_key,
                                "action",
                                episode.episode_id,
                                event.event_id,
                                action_text,
                            ),
                            repo_key=repo_key,
                            episode_id=episode.episode_id,
                            kind="action",
                            text=action_text,
                            role=None,
                            evidence=(event.evidence,),
                        )
                    )
            for change in event.file_changes:
                episode_facts.append(
                    EvidenceFact(
                        fact_id=stable_id(
                            "fact",
                            repo_key,
                            "file_change",
                            episode.episode_id,
                            change.fact_id,
                        ),
                        repo_key=repo_key,
                        episode_id=episode.episode_id,
                        kind="file_change",
                        text=f"{change.operation}:{change.path}",
                        role=None,
                        evidence=(change.evidence,),
                        status="success",
                    )
                )
            if (
                event.kind == "tool_result"
                and event.is_command_result
                and event.command is not None
                and event.exit_code is not None
            ):
                call = calls.get(event.call_id) if event.call_id is not None else None
                command_evidence = (
                    (call.evidence, event.evidence)
                    if call is not None and call.command == event.command
                    else (event.evidence,)
                )
                fact_kind: EvidenceFactKind = (
                    "verification" if _is_verification_command(event.command) else "command_outcome"
                )
                command_outcome = EvidenceFact(
                    fact_id=stable_id(
                        "fact",
                        repo_key,
                        fact_kind,
                        episode.episode_id,
                        event.event_id,
                        event.command,
                        event.exit_code,
                    ),
                    repo_key=repo_key,
                    episode_id=episode.episode_id,
                    kind=fact_kind,
                    text=event.command,
                    role=None,
                    evidence=command_evidence,
                    status="success" if event.exit_code == 0 else "failed",
                )
                command_outcome_facts.append(command_outcome)
                episode_facts.append(command_outcome)
        if episode.outcome in {"success", "failed"} and command_outcome_facts:
            episode_facts.append(
                EvidenceFact(
                    fact_id=stable_id(
                        "fact",
                        repo_key,
                        "episode_outcome",
                        episode.episode_id,
                        episode.outcome,
                        *(fact.fact_id for fact in command_outcome_facts),
                    ),
                    repo_key=repo_key,
                    episode_id=episode.episode_id,
                    kind="episode_outcome",
                    text=episode.outcome,
                    role=None,
                    evidence=_unique_evidence(tuple(command_outcome_facts)),
                    status=episode.outcome,
                )
            )

    repeated_facts: list[EvidenceFact] = []
    for text, matching in by_text.items():
        if len(matching) < 2:
            continue
        episode_ids = tuple(dict.fromkeys(fact.episode_id for fact in matching))
        repeated_evidence = _unique_evidence(tuple(matching))
        if len(repeated_evidence) < 2:
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
                evidence=repeated_evidence,
            )
        )
    return (*quote_facts, *episode_facts, *repeated_facts)


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


def _verified_fix_rejection(
    facts: tuple[EvidenceFact, ...],
) -> GateDecisionReason | None:
    changes = tuple(fact for fact in facts if fact.kind == "file_change" and bool(fact.evidence))
    if not changes:
        return "verified_fix_requires_change"
    verifications = tuple(
        fact
        for fact in facts
        if fact.kind == "verification"
        and fact.status == "success"
        and bool(fact.evidence)
        and _is_verification_command(fact.text)
    )
    if not verifications:
        return "verified_fix_requires_successful_verification"
    if not any(
        change.episode_id == verification.episode_id
        and _verification_started_after_change(change, verification)
        for change in changes
        for verification in verifications
    ):
        return "verification_must_follow_change"
    return None


def _has_later_evidence(change: EvidenceFact, verification: EvidenceFact) -> bool:
    return any(
        verified.provider == changed.provider
        and verified.session_id == changed.session_id
        and verified.source_path == changed.source_path
        and verified.raw_event_index > changed.raw_event_index
        for changed in change.evidence
        for verified in verification.evidence
    )


def _verification_started_after_change(
    change: EvidenceFact,
    verification: EvidenceFact,
) -> bool:
    change_by_source: dict[tuple[str, str, str], list[int]] = {}
    for evidence in change.evidence:
        source = (evidence.provider, evidence.session_id, evidence.source_path)
        change_by_source.setdefault(source, []).append(evidence.raw_event_index)
    verification_by_source: dict[tuple[str, str, str], list[int]] = {}
    for evidence in verification.evidence:
        source = (evidence.provider, evidence.session_id, evidence.source_path)
        verification_by_source.setdefault(source, []).append(evidence.raw_event_index)
    return any(
        min(verification_by_source[source]) > max(change_indices)
        for source, change_indices in change_by_source.items()
        if source in verification_by_source
    )


def _is_verification_command(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    if tokens[:2] == ["uv", "run"]:
        tokens = tokens[2:]
    if tokens[:3] == ["python", "-m", "pytest"] or tokens[:3] == [
        "python3",
        "-m",
        "pytest",
    ]:
        return True
    executable = PurePath(tokens[0]).name
    if executable in {"pytest", "mypy", "tox", "nox"}:
        return executable != "pytest" or not any(
            token in {"--collect-only", "--fixtures", "--help", "--version"} for token in tokens[1:]
        )
    if executable == "ruff":
        return len(tokens) > 1 and (
            tokens[1] == "check" or (tokens[1] == "format" and "--check" in tokens[2:])
        )
    if executable == "make":
        return any(
            target in {"check", "ci", "integration", "lint", "test"} for target in tokens[1:]
        )
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        if len(tokens) > 1 and tokens[1] == "test":
            return True
        return (
            len(tokens) > 2
            and tokens[1] == "run"
            and any(
                marker in tokens[2].lower()
                for marker in ("build", "check", "lint", "test", "typecheck")
            )
        )
    if executable == "cargo":
        return len(tokens) > 1 and tokens[1] in {"build", "check", "test"}
    if executable == "go":
        return len(tokens) > 1 and tokens[1] in {"build", "test", "vet"}
    if executable in {"gradle", "gradlew"}:
        return any(target in {"build", "check", "test"} for target in tokens[1:])
    if executable in {"mvn", "mvnw"}:
        return any(target in {"install", "package", "test", "verify"} for target in tokens[1:])
    return False


def _debug_episode_rejection(
    facts: tuple[EvidenceFact, ...],
) -> GateDecisionReason | None:
    tasks = tuple(
        fact
        for fact in facts
        if fact.kind == "task_prompt" and fact.role == "user" and bool(fact.evidence)
    )
    if not tasks:
        return "debug_episode_requires_task_prompt"
    actions = tuple(fact for fact in facts if fact.kind == "action" and bool(fact.evidence))
    if not actions:
        return "debug_episode_requires_action"
    outcomes = tuple(
        fact
        for fact in facts
        if fact.kind == "episode_outcome"
        and fact.status in {"success", "failed"}
        and bool(fact.evidence)
    )
    if not outcomes:
        return "debug_episode_requires_observed_outcome"
    if not any(
        task.episode_id == action.episode_id == outcome.episode_id
        and _has_later_evidence(task, action)
        and _has_later_evidence(action, outcome)
        for task in tasks
        for action in actions
        for outcome in outcomes
    ):
        return "debug_episode_facts_are_disconnected"
    return None


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
