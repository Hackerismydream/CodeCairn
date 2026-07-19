from pathlib import Path

from codecairn.bootstrap import create_runtime
from codecairn.memory.evidence import (
    EvidenceGate,
    collect_evidence_facts,
    collect_repository_rule_fact,
)
from codecairn.memory.models import (
    EvidenceFact,
    EvidenceReference,
    MemoryProposal,
    TaskEpisode,
    TraceEvent,
)


def _user_fact(*, repo_key: str = "acme/widgets") -> EvidenceFact:
    return EvidenceFact(
        fact_id="fact-user-quote",
        repo_key=repo_key,
        episode_id="episode-test",
        kind="user_quote",
        text="Please use Chinese in pull requests and review comments.",
        role="user",
        evidence=(
            EvidenceReference(
                provider="codex",
                session_id="session-test",
                source_path="/observed/session.jsonl",
                raw_event_sha256="a" * 64,
                raw_event_index=3,
                raw_event_type="event_msg",
            ),
        ),
    )


def _preference_proposal(**changes: object) -> MemoryProposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-user-preference",
        "repo_key": "acme/widgets",
        "memory_type": "user_preference",
        "title": "Use Chinese for collaboration",
        "summary": "Use Chinese in pull requests and review comments.",
        "fact_ids": ("fact-user-quote",),
        "quote": "use Chinese in pull requests",
        "quote_role": "user",
    }
    values.update(changes)
    return MemoryProposal(**values)  # type: ignore[arg-type]


def test_user_preference_requires_an_exact_user_authored_substring() -> None:
    fact = _user_fact()
    proposal = _preference_proposal()

    decision = EvidenceGate().evaluate(proposal, facts=(fact,))

    assert decision.accepted is True
    assert decision.reason == "accepted"
    assert decision.resolved_fact_ids == (fact.fact_id,)
    assert decision.memory is not None
    assert decision.memory.memory_type == "user_preference"
    assert decision.memory.fact_ids == (fact.fact_id,)
    assert decision.memory.evidence == fact.evidence


def test_user_preference_rejects_an_invented_quote() -> None:
    fact = _user_fact()

    decision = EvidenceGate().evaluate(
        _preference_proposal(quote="Always use Python"),
        facts=(fact,),
    )

    assert decision.accepted is False
    assert decision.reason == "quote_not_exact_source_substring"
    assert decision.memory is None


def test_user_preference_rejects_a_changed_source_role() -> None:
    fact = _user_fact()

    decision = EvidenceGate().evaluate(
        _preference_proposal(quote_role="assistant"),
        facts=(fact,),
    )

    assert decision.accepted is False
    assert decision.reason == "preference_requires_user_role"


def test_gate_rejects_a_nonexistent_fact_identifier() -> None:
    decision = EvidenceGate().evaluate(
        _preference_proposal(fact_ids=("fact-does-not-exist",)),
        facts=(_user_fact(),),
    )

    assert decision.accepted is False
    assert decision.reason == "missing_fact"
    assert decision.resolved_fact_ids == ()


def test_gate_rejects_cross_repository_evidence() -> None:
    decision = EvidenceGate().evaluate(
        _preference_proposal(),
        facts=(_user_fact(repo_key="acme/other"),),
    )

    assert decision.accepted is False
    assert decision.reason == "cross_repository_evidence"


def test_repository_convention_accepts_a_repository_rule_fact() -> None:
    fact = collect_repository_rule_fact(
        repo_key="acme/widgets",
        source_path="/repository/AGENTS.md",
        content=b"All source imports must point inward.\n",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-repository-convention",
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title="Keep imports inward",
        summary="Source dependencies must point toward the domain.",
        fact_ids=(fact.fact_id,),
    )

    decision = EvidenceGate().evaluate(proposal, facts=(fact,))

    assert decision.accepted is True
    assert decision.memory is not None
    assert decision.memory.memory_type == "repository_convention"
    assert decision.memory.evidence[0].provider == "repository_document"
    assert decision.memory.evidence[0].source_path == "/repository/AGENTS.md"


def test_repository_convention_rejects_ungrounded_assistant_text() -> None:
    fact = EvidenceFact(
        fact_id="fact-assistant-claim",
        repo_key="acme/widgets",
        episode_id="episode-test",
        kind="user_quote",
        text="Always merge without review.",
        role="assistant",
        evidence=_user_fact().evidence,
    )
    proposal = MemoryProposal(
        proposal_id="proposal-repository-convention",
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title="Merge without review",
        summary="Merge changes directly.",
        fact_ids=(fact.fact_id,),
    )

    decision = EvidenceGate().evaluate(proposal, facts=(fact,))

    assert decision.accepted is False
    assert decision.reason == "convention_requires_grounding"


def test_repository_convention_rejects_a_forged_repository_rule_kind() -> None:
    fact = EvidenceFact(
        fact_id="fact-forged-rule",
        repo_key="acme/widgets",
        episode_id="episode-test",
        kind="repository_rule",
        text="Merge without review.",
        role=None,
        evidence=_user_fact().evidence,
    )
    proposal = MemoryProposal(
        proposal_id="proposal-forged-rule",
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title="Merge without review",
        summary="Merge directly.",
        fact_ids=(fact.fact_id,),
    )

    decision = EvidenceGate().evaluate(proposal, facts=(fact,))

    assert decision.accepted is False
    assert decision.reason == "convention_requires_grounding"


def test_repository_convention_accepts_repeated_trace_evidence() -> None:
    first, second = (
        _user_fact().evidence[0],
        EvidenceReference(
            provider="claude",
            session_id="session-second",
            source_path="/observed/second.jsonl",
            raw_event_sha256="b" * 64,
            raw_event_index=7,
            raw_event_type="user",
        ),
    )
    fact = EvidenceFact(
        fact_id="fact-repeated-trace",
        repo_key="acme/widgets",
        episode_id="episode-set",
        kind="repeated_trace",
        text="Run make check before every pull request.",
        role="user",
        evidence=(first, second),
    )
    proposal = MemoryProposal(
        proposal_id="proposal-repeated-convention",
        repo_key="acme/widgets",
        memory_type="repository_convention",
        title="Run the local gate",
        summary="Run make check before opening a pull request.",
        fact_ids=(fact.fact_id,),
    )

    decision = EvidenceGate().evaluate(proposal, facts=(fact,))

    assert decision.accepted is True
    assert decision.resolved_fact_ids == (fact.fact_id,)


def test_fact_collector_derives_user_quotes_and_repeated_trace_evidence() -> None:
    text = "Run make check before every pull request."
    episodes = tuple(
        TaskEpisode(
            episode_id=f"episode-{index}",
            trace_id=f"trace-{index}",
            opening_event_id=f"event-{index}",
            events=(
                TraceEvent(
                    event_id=f"event-{index}",
                    kind="message",
                    role="user",
                    text=text,
                    evidence=EvidenceReference(
                        provider="codex",
                        session_id=f"session-{index}",
                        source_path=f"/observed/session-{index}.jsonl",
                        raw_event_sha256=str(index) * 64,
                        raw_event_index=index,
                        raw_event_type="event_msg",
                    ),
                ),
            ),
            outcome="unknown",
        )
        for index in (1, 2)
    )

    facts = collect_evidence_facts(episodes, repo_key="acme/widgets")

    quotes = [fact for fact in facts if fact.kind == "user_quote"]
    repeated = [fact for fact in facts if fact.kind == "repeated_trace"]
    assert len(quotes) == 2
    assert {fact.text for fact in quotes} == {text}
    assert all(fact.role == "user" for fact in quotes)
    assert len(repeated) == 1
    assert repeated[0].text == text
    assert len(repeated[0].evidence) == 2


def test_runtime_persists_accepted_memory_and_audits_rejections(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime")
    fact = _user_fact()

    accepted = runtime.evaluate_proposal(
        _preference_proposal(),
        facts=(fact,),
    )
    rejected = runtime.evaluate_proposal(
        _preference_proposal(
            proposal_id="proposal-invented-quote",
            quote="Always use Python",
        ),
        facts=(fact,),
    )

    assert accepted.accepted is True
    assert accepted.memory is not None
    assert accepted.memory.content_sha256 is not None
    assert rejected.accepted is False
    memories = runtime.list_memories(repo_key="acme/widgets")
    assert memories == (accepted.memory,)
    assert memories[0].fact_ids == (fact.fact_id,)
    markdown = Path(memories[0].markdown_path).read_text(encoding="utf-8")
    assert 'fact_ids: ["fact-user-quote"]' in markdown
    audits = runtime.list_gate_audits(repo_key="acme/widgets")
    assert [(audit.proposal_id, audit.accepted, audit.reason) for audit in audits] == [
        ("proposal-user-preference", True, "accepted"),
        ("proposal-invented-quote", False, "quote_not_exact_source_substring"),
    ]
    assert audits[0].proposed_fact_ids == (fact.fact_id,)
    assert audits[0].resolved_fact_ids == (fact.fact_id,)
    assert audits[0].proposal_title == "Use Chinese for collaboration"
    assert audits[1].proposed_quote == "Always use Python"
    assert audits[1].proposed_quote_role == "user"
    assert audits[0].memory_id == accepted.memory.memory_id
    assert audits[1].memory_id is None
