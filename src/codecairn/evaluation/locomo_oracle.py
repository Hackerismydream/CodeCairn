from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from codecairn.evaluation.grounded_answer import GroundedContext
from codecairn.evaluation.oracle_ceiling import TokenCounter, build_oracle_context
from codecairn.memory.episode import AttributedEpisode, AttributedTurn, compile_source_facts
from codecairn.memory.models import EvidenceFact, EvidenceReference

if TYPE_CHECKING:
    from codecairn.evaluation.locomo import LoCoMoConversation, LoCoMoSession, LoCoMoTurn


@dataclass(frozen=True, slots=True)
class LoCoMoSourceFact:
    dia_id: str
    fact: EvidenceFact


def compile_locomo_source_facts(
    conversation: LoCoMoConversation,
    *,
    dataset_sha256: str,
) -> tuple[LoCoMoSourceFact, ...]:
    dia_ids = tuple(turn.dia_id for session in conversation.sessions for turn in session.turns)
    seen_dia_ids: set[str] = set()
    duplicate_dia_ids: list[str] = []
    for dia_id in dia_ids:
        if dia_id in seen_dia_ids and dia_id not in duplicate_dia_ids:
            duplicate_dia_ids.append(dia_id)
        seen_dia_ids.add(dia_id)
    if duplicate_dia_ids:
        raise ValueError(
            "LoCoMo conversation contains duplicate dia_id values: " + ", ".join(duplicate_dia_ids)
        )

    compiled: list[LoCoMoSourceFact] = []
    repo_key = f"locomo/{conversation.sample_id}"
    for session in conversation.sessions:
        if not session.turns:
            continue
        turns = tuple(
            AttributedTurn(
                turn_id=turn.dia_id,
                actor=turn.speaker,
                role="participant",
                text=turn.text,
                occurred_at=turn.timestamp_iso,
                evidence=_turn_evidence(
                    conversation,
                    session,
                    turn,
                    dataset_sha256=dataset_sha256,
                ),
            )
            for turn in session.turns
        )
        _episode_id, facts = compile_source_facts(
            AttributedEpisode(
                repo_key=repo_key,
                source_episode_id=f"{conversation.sample_id}/{session.session_id}",
                title=f"Conversation {session.session_id} on {session.turns[0].timestamp_iso[:10]}",
                turns=turns,
            )
        )
        facts_by_source_digest = {fact.evidence[0].raw_event_sha256: fact for fact in facts}
        compiled.extend(
            LoCoMoSourceFact(
                dia_id=turn.turn_id,
                fact=facts_by_source_digest[turn.evidence.raw_event_sha256],
            )
            for turn in turns
        )
    return tuple(compiled)


def build_locomo_oracle_context(
    conversation: LoCoMoConversation,
    *,
    dataset_sha256: str,
    gold_dia_ids: tuple[str, ...],
    token_counter: TokenCounter,
    max_tokens: int = 4_000,
) -> GroundedContext:
    compiled = compile_locomo_source_facts(
        conversation,
        dataset_sha256=dataset_sha256,
    )
    by_dia_id = {item.dia_id: item.fact.fact_id for item in compiled}
    unknown_dia_ids = tuple(dia_id for dia_id in gold_dia_ids if dia_id not in by_dia_id)
    if unknown_dia_ids:
        raise ValueError(
            "LoCoMo oracle context contains unknown gold dia_id values: "
            + ", ".join(unknown_dia_ids)
        )
    gold_source_fact_ids = tuple(by_dia_id[dia_id] for dia_id in gold_dia_ids)
    return build_oracle_context(
        source_facts=tuple(item.fact for item in compiled),
        gold_source_fact_ids=gold_source_fact_ids,
        token_counter=token_counter,
        max_tokens=max_tokens,
    )


def _turn_evidence(
    conversation: LoCoMoConversation,
    session: LoCoMoSession,
    turn: LoCoMoTurn,
    *,
    dataset_sha256: str,
) -> EvidenceReference:
    raw = {
        "sample_id": conversation.sample_id,
        "session_id": session.session_id,
        "dia_id": turn.dia_id,
        "speaker": turn.speaker,
        "text": turn.text,
        "timestamp": turn.timestamp,
    }
    digest = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return EvidenceReference(
        provider="locomo",
        session_id=f"{conversation.sample_id}/{session.session_id}",
        source_path=f"locomo://{dataset_sha256}/{conversation.sample_id}/{session.session_id}",
        raw_event_sha256=digest,
        raw_event_index=turn.turn_index,
        raw_event_type="locomo_turn",
    )
