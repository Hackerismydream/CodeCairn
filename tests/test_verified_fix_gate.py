import sqlite3
from pathlib import Path

import pytest

from codecairn.bootstrap import create_runtime
from codecairn.memory.evidence import EvidenceGate, collect_evidence_facts
from codecairn.memory.models import (
    EvidenceFact,
    EvidenceReference,
    FileChangeFact,
    MemoryProposal,
    TaskEpisode,
    TraceEvent,
)


def _evidence(index: int) -> EvidenceReference:
    return EvidenceReference(
        provider="codex",
        session_id="session-fix",
        source_path="/observed/fix.jsonl",
        raw_event_sha256=f"{index:x}" * 64,
        raw_event_index=index,
        raw_event_type="response_item",
    )


def test_verified_fix_requires_a_successful_verification_after_the_change(tmp_path: Path) -> None:
    changed = EvidenceFact(
        fact_id="fact-change",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="file_change",
        text="update:src/widget.py",
        role=None,
        evidence=(_evidence(5),),
        status="success",
    )
    verified = EvidenceFact(
        fact_id="fact-verification",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text="uv run pytest",
        role=None,
        evidence=(_evidence(7),),
        status="success",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-verified-fix",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Fix widget validation",
        summary="Update widget validation and verify the test suite.",
        fact_ids=(changed.fact_id, verified.fact_id),
        confidence=1.0,
    )

    decision = EvidenceGate().evaluate(proposal, facts=(changed, verified))

    assert decision.accepted is True
    assert decision.reason == "accepted"
    assert decision.memory is not None
    assert decision.memory.memory_type == "verified_fix"
    assert decision.memory.fact_ids == (changed.fact_id, verified.fact_id)
    assert decision.memory.facts == (changed, verified)

    runtime = create_runtime(tmp_path / "runtime")
    persisted = runtime.evaluate_proposal(proposal, facts=(changed, verified))

    assert persisted.accepted is True
    assert runtime.list_memories(repo_key="acme/widgets")[0].facts == (changed, verified)


def test_verified_fix_rejects_a_failed_verification() -> None:
    changed = EvidenceFact(
        fact_id="fact-change",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="file_change",
        text="update:src/widget.py",
        role=None,
        evidence=(_evidence(5),),
        status="success",
    )
    failed = EvidenceFact(
        fact_id="fact-failed-verification",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text="uv run pytest",
        role=None,
        evidence=(_evidence(7),),
        status="failed",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-unverified-fix",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Unverified widget fix",
        summary="The test suite still fails.",
        fact_ids=(changed.fact_id, failed.fact_id),
        confidence=1.0,
    )

    decision = EvidenceGate().evaluate(proposal, facts=(changed, failed))

    assert decision.accepted is False
    assert decision.reason == "verified_fix_requires_successful_verification"


def test_verified_fix_rejects_verification_before_the_change() -> None:
    verified = EvidenceFact(
        fact_id="fact-earlier-verification",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text="uv run pytest",
        role=None,
        evidence=(_evidence(3),),
        status="success",
    )
    changed = EvidenceFact(
        fact_id="fact-later-change",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="file_change",
        text="update:src/widget.py",
        role=None,
        evidence=(_evidence(5),),
        status="success",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-backdated-verification",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Backdated widget fix",
        summary="The verification predates the change.",
        fact_ids=(verified.fact_id, changed.fact_id),
        confidence=1.0,
    )

    decision = EvidenceGate().evaluate(proposal, facts=(verified, changed))

    assert decision.accepted is False
    assert decision.reason == "verification_must_follow_change"


def test_verification_started_before_change_is_rejected_even_if_result_arrives_later() -> None:
    verified = EvidenceFact(
        fact_id="fact-overlapping-verification",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text="uv run pytest",
        role=None,
        evidence=(_evidence(3), _evidence(7)),
        status="success",
    )
    changed = EvidenceFact(
        fact_id="fact-midflight-change",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="file_change",
        text="update:src/widget.py",
        role=None,
        evidence=(_evidence(5),),
        status="success",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-overlapping-verification",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Overlapping verification",
        summary="The verification started before the change.",
        fact_ids=(verified.fact_id, changed.fact_id),
    )

    decision = EvidenceGate().evaluate(proposal, facts=(verified, changed))

    assert decision.accepted is False
    assert decision.reason == "verification_must_follow_change"


def test_high_llm_confidence_cannot_bypass_missing_change_evidence() -> None:
    verified = EvidenceFact(
        fact_id="fact-verification-only",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text="uv run pytest",
        role=None,
        evidence=(_evidence(7),),
        status="success",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-confident-without-change",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Confident but ungrounded",
        summary="The model claims the fix is verified.",
        fact_ids=(verified.fact_id,),
        confidence=1.0,
    )

    decision = EvidenceGate().evaluate(proposal, facts=(verified,))

    assert decision.accepted is False
    assert decision.reason == "verified_fix_requires_change"


@pytest.mark.parametrize("command", ["pwd", "ruff format .", "mvn"])
def test_unrelated_successful_command_cannot_verify_a_fix(command: str) -> None:
    changed = EvidenceFact(
        fact_id="fact-change",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="file_change",
        text="update:src/widget.py",
        role=None,
        evidence=(_evidence(5),),
        status="success",
    )
    unrelated = EvidenceFact(
        fact_id="fact-unrelated-success",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text=command,
        role=None,
        evidence=(_evidence(7),),
        status="success",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-pwd-verified-fix",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Unverified widget fix",
        summary="An unrelated command succeeded.",
        fact_ids=(changed.fact_id, unrelated.fact_id),
        confidence=1.0,
    )

    decision = EvidenceGate().evaluate(proposal, facts=(changed, unrelated))

    assert decision.accepted is False
    assert decision.reason == "verified_fix_requires_successful_verification"


def test_rejected_confident_proposal_is_audited_with_its_confidence(tmp_path: Path) -> None:
    verified = EvidenceFact(
        fact_id="fact-verification-only",
        repo_key="acme/widgets",
        episode_id="episode-fix",
        kind="verification",
        text="uv run pytest",
        role=None,
        evidence=(_evidence(7),),
        status="success",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-confident-rejection",
        repo_key="acme/widgets",
        memory_type="verified_fix",
        title="Confident but ungrounded",
        summary="The model claims a fix without a change.",
        fact_ids=(verified.fact_id,),
        confidence=1.0,
    )
    runtime = create_runtime(tmp_path / "runtime")

    runtime.evaluate_proposal(proposal, facts=(verified,))

    audit = runtime.list_gate_audits(repo_key="acme/widgets")[0]
    assert audit.accepted is False
    assert audit.reason == "verified_fix_requires_change"
    assert audit.proposal_confidence == 1.0


def test_existing_gate_audit_is_migrated_for_proposal_confidence(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    with sqlite3.connect(root / "state.sqlite3") as connection:
        connection.execute(
            """
            CREATE TABLE gate_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                repo_key TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                reason TEXT NOT NULL,
                proposal_title TEXT NOT NULL,
                proposal_summary TEXT NOT NULL,
                proposed_quote TEXT,
                proposed_quote_role TEXT,
                proposed_fact_ids_json TEXT NOT NULL,
                resolved_fact_ids_json TEXT NOT NULL,
                memory_id TEXT,
                UNIQUE (repo_key, proposal_id)
            )
            """
        )

    create_runtime(root)

    with sqlite3.connect(root / "state.sqlite3") as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(gate_audit)").fetchall()}
    assert "proposal_confidence" in columns


def test_debug_episode_requires_connected_task_action_and_observed_outcome() -> None:
    task = EvidenceFact(
        fact_id="fact-task",
        repo_key="acme/widgets",
        episode_id="episode-debug",
        kind="task_prompt",
        text="Find why widget validation fails.",
        role="user",
        evidence=(_evidence(1),),
    )
    action = EvidenceFact(
        fact_id="fact-action",
        repo_key="acme/widgets",
        episode_id="episode-debug",
        kind="action",
        text="uv run pytest tests/test_widget.py",
        role=None,
        evidence=(_evidence(2),),
    )
    outcome = EvidenceFact(
        fact_id="fact-outcome",
        repo_key="acme/widgets",
        episode_id="episode-debug",
        kind="episode_outcome",
        text="failed",
        role=None,
        evidence=(_evidence(3),),
        status="failed",
    )
    proposal = MemoryProposal(
        proposal_id="proposal-debug-episode",
        repo_key="acme/widgets",
        memory_type="debug_episode",
        title="Widget validation failure",
        summary="The focused widget test reproduces the failure.",
        fact_ids=(task.fact_id, action.fact_id, outcome.fact_id),
        confidence=0.9,
    )

    decision = EvidenceGate().evaluate(proposal, facts=(task, action, outcome))

    assert decision.accepted is True
    assert decision.memory is not None
    assert decision.memory.memory_type == "debug_episode"


@pytest.mark.parametrize(
    ("omitted_kind", "expected_reason"),
    [
        ("task_prompt", "debug_episode_requires_task_prompt"),
        ("action", "debug_episode_requires_action"),
        ("episode_outcome", "debug_episode_requires_observed_outcome"),
    ],
)
def test_debug_episode_rejects_each_missing_required_fact(
    omitted_kind: str,
    expected_reason: str,
) -> None:
    facts = (
        EvidenceFact(
            fact_id="fact-task",
            repo_key="acme/widgets",
            episode_id="episode-debug",
            kind="task_prompt",
            text="Find the failure.",
            role="user",
            evidence=(_evidence(1),),
        ),
        EvidenceFact(
            fact_id="fact-action",
            repo_key="acme/widgets",
            episode_id="episode-debug",
            kind="action",
            text="uv run pytest",
            role=None,
            evidence=(_evidence(2),),
        ),
        EvidenceFact(
            fact_id="fact-outcome",
            repo_key="acme/widgets",
            episode_id="episode-debug",
            kind="episode_outcome",
            text="failed",
            role=None,
            evidence=(_evidence(3),),
            status="failed",
        ),
    )
    included = tuple(fact for fact in facts if fact.kind != omitted_kind)
    proposal = MemoryProposal(
        proposal_id=f"proposal-missing-{omitted_kind}",
        repo_key="acme/widgets",
        memory_type="debug_episode",
        title="Incomplete debug episode",
        summary="A high-confidence proposal is still incomplete.",
        fact_ids=tuple(fact.fact_id for fact in included),
        confidence=1.0,
    )

    decision = EvidenceGate().evaluate(proposal, facts=included)

    assert decision.accepted is False
    assert decision.reason == expected_reason


def test_debug_episode_rejects_facts_from_different_episodes() -> None:
    facts = (
        EvidenceFact(
            fact_id="fact-task",
            repo_key="acme/widgets",
            episode_id="episode-one",
            kind="task_prompt",
            text="Find the failure.",
            role="user",
            evidence=(_evidence(1),),
        ),
        EvidenceFact(
            fact_id="fact-action",
            repo_key="acme/widgets",
            episode_id="episode-two",
            kind="action",
            text="uv run pytest",
            role=None,
            evidence=(_evidence(2),),
        ),
        EvidenceFact(
            fact_id="fact-outcome",
            repo_key="acme/widgets",
            episode_id="episode-two",
            kind="episode_outcome",
            text="failed",
            role=None,
            evidence=(_evidence(3),),
            status="failed",
        ),
    )
    proposal = MemoryProposal(
        proposal_id="proposal-disconnected-debug",
        repo_key="acme/widgets",
        memory_type="debug_episode",
        title="Disconnected debug episode",
        summary="These facts come from different episodes.",
        fact_ids=tuple(fact.fact_id for fact in facts),
    )

    decision = EvidenceGate().evaluate(proposal, facts=facts)

    assert decision.accepted is False
    assert decision.reason == "debug_episode_facts_are_disconnected"


def test_fact_collector_connects_task_action_change_verification_and_outcome() -> None:
    task_evidence = _evidence(1)
    patch_evidence = _evidence(2)
    verify_call_evidence = _evidence(3)
    verify_result_evidence = _evidence(4)
    episode = TaskEpisode(
        episode_id="episode-fix",
        trace_id="trace-fix",
        opening_event_id="event-task",
        events=(
            TraceEvent(
                event_id="event-task",
                kind="message",
                role="user",
                text="Fix widget validation and run the tests.",
                evidence=task_evidence,
            ),
            TraceEvent(
                event_id="event-patch",
                kind="tool_call",
                tool_name="apply_patch",
                call_id="call-patch",
                evidence=patch_evidence,
                file_changes=(
                    FileChangeFact(
                        fact_id="provider-file-change",
                        operation="update",
                        path="src/widget.py",
                        destination_path=None,
                        evidence=patch_evidence,
                    ),
                ),
            ),
            TraceEvent(
                event_id="event-verify-call",
                kind="tool_call",
                tool_name="exec_command",
                call_id="call-verify",
                command="uv run pytest",
                evidence=verify_call_evidence,
            ),
            TraceEvent(
                event_id="event-verify-result",
                kind="tool_result",
                tool_name="exec_command",
                call_id="call-verify",
                command="uv run pytest",
                exit_code=0,
                is_command_result=True,
                evidence=verify_result_evidence,
            ),
        ),
        outcome="success",
    )

    facts = collect_evidence_facts((episode,), repo_key="acme/widgets")

    assert {fact.kind for fact in facts} >= {
        "task_prompt",
        "action",
        "file_change",
        "verification",
        "episode_outcome",
    }
    change = next(fact for fact in facts if fact.kind == "file_change")
    verification = next(fact for fact in facts if fact.kind == "verification")
    outcome = next(fact for fact in facts if fact.kind == "episode_outcome")
    assert change.text == "update:src/widget.py"
    assert verification.status == "success"
    assert verification.text == "uv run pytest"
    assert [item.raw_event_index for item in verification.evidence] == [3, 4]
    assert outcome.status == "success"
