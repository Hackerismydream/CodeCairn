import json

import pytest

from codecairn.memory.labels import LabelExportError, export_gate_labels
from codecairn.memory.models import GateAudit


class _SecretRedactor:
    def redact(self, text: str) -> str:
        return text.replace("sk-secret", "[REDACTED]")


def test_human_label_export_includes_accepts_and_rejections_without_secrets() -> None:
    audits = (
        GateAudit(
            audit_id=1,
            proposal_id="proposal-accepted",
            repo_key="acme/widgets",
            memory_type="verified_fix",
            accepted=True,
            reason="accepted",
            proposal_title="Remove sk-secret from logs",
            proposal_summary="The fix removes the credential from output.",
            proposed_quote=None,
            proposed_quote_role=None,
            proposal_confidence=0.99,
            proposed_fact_ids=("fact-change", "fact-verification"),
            resolved_fact_ids=("fact-change", "fact-verification"),
            memory_id="memory-accepted",
        ),
        GateAudit(
            audit_id=2,
            proposal_id="proposal-rejected",
            repo_key="acme/widgets",
            memory_type="verified_fix",
            accepted=False,
            reason="verified_fix_requires_successful_verification",
            proposal_title="Claim sk-secret is fixed",
            proposal_summary="No successful verification exists.",
            proposed_quote=None,
            proposed_quote_role=None,
            proposal_confidence=1.0,
            proposed_fact_ids=("fact-change",),
            resolved_fact_ids=("fact-change",),
            memory_id=None,
        ),
    )

    payload = export_gate_labels(
        audits,
        redactor=_SecretRedactor(),
        max_export_bytes=4_096,
    )

    assert b"sk-secret" not in payload
    assert b"[REDACTED]" in payload
    records = [json.loads(line) for line in payload.splitlines()]
    assert [record["accepted"] for record in records] == [True, False]
    assert records[0]["human_label"] is None
    assert records[1]["gate_reason"] == "verified_fix_requires_successful_verification"
    assert all("source_path" not in record for record in records)


def test_human_label_export_rejects_output_over_its_byte_limit() -> None:
    audit = GateAudit(
        audit_id=1,
        proposal_id="proposal-large",
        repo_key="acme/widgets",
        memory_type="debug_episode",
        accepted=False,
        reason="debug_episode_requires_action",
        proposal_title="Large candidate",
        proposal_summary="x" * 512,
        proposed_quote=None,
        proposed_quote_role=None,
        proposal_confidence=1.0,
        proposed_fact_ids=("fact-task",),
        resolved_fact_ids=("fact-task",),
        memory_id=None,
    )

    with pytest.raises(LabelExportError, match="configured byte limit"):
        export_gate_labels(
            (audit,),
            redactor=_SecretRedactor(),
            max_export_bytes=128,
        )
