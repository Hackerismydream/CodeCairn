from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from codecairn.evaluation.locomo import LoCoMoConversation, load_locomo_dataset
from codecairn.evaluation.locomo_oracle import (
    build_locomo_oracle_context,
    compile_locomo_source_facts,
)
from codecairn.memory.episode import AttributedEpisode, AttributedTurn, compile_source_facts
from codecairn.memory.models import EvidenceReference

FIXTURE = Path(__file__).parent / "fixtures" / "locomo" / "synthetic.json"


class WordTokenCounter:
    def count(self, text: str) -> int:
        return len(text.split())


def test_locomo_oracle_context_maps_dia_id_to_compiled_source_fact_id() -> None:
    dataset = load_locomo_dataset(FIXTURE)
    conversation = dataset.conversations[0]
    session = conversation.sessions[0]
    turn = session.turns[0]
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
    _, expected_facts = compile_source_facts(
        AttributedEpisode(
            repo_key=f"locomo/{conversation.sample_id}",
            source_episode_id=f"{conversation.sample_id}/{session.session_id}",
            title="Expected session",
            turns=(
                AttributedTurn(
                    turn_id=turn.dia_id,
                    actor=turn.speaker,
                    role="participant",
                    text=turn.text,
                    occurred_at=turn.timestamp_iso,
                    evidence=EvidenceReference(
                        provider="locomo",
                        session_id=f"{conversation.sample_id}/{session.session_id}",
                        source_path=(
                            f"locomo://{dataset.sha256}/{conversation.sample_id}/"
                            f"{session.session_id}"
                        ),
                        raw_event_sha256=digest,
                        raw_event_index=turn.turn_index,
                        raw_event_type="locomo_turn",
                    ),
                ),
            ),
        )
    )

    context = build_locomo_oracle_context(
        conversation,
        dataset_sha256=dataset.sha256,
        gold_dia_ids=(turn.dia_id,),
        token_counter=WordTokenCounter(),
    )

    assert tuple(item.source_fact_id for item in context.evidence) == (expected_facts[0].fact_id,)
    assert context.evidence[0].text.endswith(f"{turn.speaker}: {turn.text}")
    assert all(question.question not in context.markdown for question in conversation.questions)


def test_locomo_oracle_context_rejects_unknown_gold_dia_id() -> None:
    dataset = load_locomo_dataset(FIXTURE)
    conversation = dataset.conversations[0]

    with pytest.raises(ValueError, match="unknown gold dia_id"):
        build_locomo_oracle_context(
            conversation,
            dataset_sha256=dataset.sha256,
            gold_dia_ids=("D-forged",),
            token_counter=WordTokenCounter(),
        )


def test_locomo_source_fact_mapping_rejects_duplicate_dia_ids() -> None:
    dataset = load_locomo_dataset(FIXTURE)
    conversation = dataset.conversations[0]
    session = conversation.sessions[0]
    duplicate_turn = replace(session.turns[1], dia_id=session.turns[0].dia_id)
    duplicate_conversation = replace(
        conversation,
        sessions=(replace(session, turns=(session.turns[0], duplicate_turn)),),
    )

    with pytest.raises(ValueError, match="duplicate dia_id"):
        compile_locomo_source_facts(
            duplicate_conversation,
            dataset_sha256=dataset.sha256,
        )


def test_locomo_source_fact_mapping_keeps_dia_ids_when_turn_input_is_reordered() -> None:
    dataset = load_locomo_dataset(FIXTURE)
    conversation = dataset.conversations[0]
    session = conversation.sessions[0]
    reordered = replace(
        conversation,
        sessions=(replace(session, turns=tuple(reversed(session.turns))),),
    )

    expected = {
        item.dia_id: item.fact.fact_id
        for item in compile_locomo_source_facts(
            conversation,
            dataset_sha256=dataset.sha256,
        )
        if item.dia_id in {turn.dia_id for turn in session.turns}
    }
    observed = {
        item.dia_id: item.fact.fact_id
        for item in compile_locomo_source_facts(
            reordered,
            dataset_sha256=dataset.sha256,
        )
    }

    assert observed == expected


def test_locomo_oracle_context_does_not_read_question_metadata() -> None:
    dataset = load_locomo_dataset(FIXTURE)
    conversation = dataset.conversations[0]

    class QuestionMetadataGuard:
        sample_id = conversation.sample_id
        sessions = conversation.sessions

        @property
        def questions(self) -> None:
            raise AssertionError("oracle context must not read question metadata")

    context = build_locomo_oracle_context(
        cast(LoCoMoConversation, QuestionMetadataGuard()),
        dataset_sha256=dataset.sha256,
        gold_dia_ids=(conversation.sessions[0].turns[0].dia_id,),
        token_counter=WordTokenCounter(),
    )

    assert len(context.evidence) == 1
