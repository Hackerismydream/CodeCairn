from __future__ import annotations

import json

from codecairn.memory.compression import TextRedactor
from codecairn.memory.models import GateAudit


class LabelExportError(ValueError):
    """Raised when a human-label artifact exceeds its configured boundary."""


def export_gate_labels(
    audits: tuple[GateAudit, ...],
    *,
    redactor: TextRedactor,
    max_export_bytes: int,
) -> bytes:
    """Render bounded redacted JSONL for extraction-precision labeling."""
    if max_export_bytes <= 0:
        raise LabelExportError("Label export byte limit must be positive")
    payload = bytearray()
    for audit in sorted(audits, key=lambda item: item.audit_id):
        record = {
            "schema_version": 1,
            "audit_id": audit.audit_id,
            "proposal_id": audit.proposal_id,
            "repo_key": redactor.redact(audit.repo_key),
            "memory_type": audit.memory_type,
            "accepted": audit.accepted,
            "gate_reason": audit.reason,
            "proposal_title": redactor.redact(audit.proposal_title),
            "proposal_summary": redactor.redact(audit.proposal_summary),
            "proposed_quote": (
                redactor.redact(audit.proposed_quote) if audit.proposed_quote is not None else None
            ),
            "proposed_quote_role": audit.proposed_quote_role,
            "proposal_confidence": audit.proposal_confidence,
            "proposed_fact_ids": audit.proposed_fact_ids,
            "resolved_fact_ids": audit.resolved_fact_ids,
            "memory_id": audit.memory_id,
            "human_label": None,
        }
        line = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) + len(line) > max_export_bytes:
            raise LabelExportError("Label export exceeds the configured byte limit")
        payload.extend(line)
    return bytes(payload)
