from __future__ import annotations

import pytest

from codecairn.memory.schema import EvidenceFact, FactRole, SourceLocation
from codecairn.storage.sqlite import SQLiteState


def _fact(*, repo_key: str = "acme/widgets", role: FactRole = "user") -> EvidenceFact:
    return EvidenceFact.create(
        repo_key=repo_key,
        location=SourceLocation(
            provider="claude",
            session_id="session-1",
            source_generation=1,
            event_index=3,
            event_id="event-3",
            source_path_sha256="0" * 64,
            event_sha256="1" * 64,
        ),
        fact_kind="message",
        role=role,
        value="Keep the output concise.",
        attributes={},
    )


def test_source_fact_registry_is_idempotent_and_namespace_scoped(tmp_path) -> None:
    state = SQLiteState(tmp_path / "state.sqlite3")
    fact = _fact()

    state.store_source_facts((fact,))
    state.store_source_facts((fact,))

    assert state.resolve_source_facts(
        repo_key=fact.repo_key,
        fact_ids=(fact.fact_id,),
    ) == (fact,)
    with pytest.raises(KeyError):
        state.resolve_source_facts(
            repo_key="acme/other",
            fact_ids=(fact.fact_id,),
        )


def test_source_fact_registry_preserves_requested_order(tmp_path) -> None:
    state = SQLiteState(tmp_path / "state.sqlite3")
    first = _fact()
    second = EvidenceFact.create(
        repo_key=first.repo_key,
        location=SourceLocation(
            provider="claude",
            session_id="session-1",
            source_generation=1,
            event_index=4,
            event_id="event-4",
            source_path_sha256="0" * 64,
            event_sha256="2" * 64,
        ),
        fact_kind="message",
        role="user",
        value="Always run make check.",
        attributes={},
    )
    state.store_source_facts((first, second))

    assert state.resolve_source_facts(
        repo_key=first.repo_key,
        fact_ids=(second.fact_id, first.fact_id),
    ) == (second, first)
