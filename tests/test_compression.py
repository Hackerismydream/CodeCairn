import json

import pytest

from codecairn.memory.compression import (
    CompressionBoundaryError,
    ProposalSchemaError,
    SemanticCompression,
)
from codecairn.memory.models import EvidenceFact, EvidenceReference


class _SecretRedactor:
    def redact(self, text: str) -> str:
        return text.replace("sk-secret", "[REDACTED]")


class _RecordingModel:
    def __init__(self) -> None:
        self.payload: bytes | None = None

    def complete(self, payload: bytes) -> object:
        self.payload = payload
        fact_id = json.loads(payload)["facts"][0]["fact_id"]
        return [
            {
                "memory_type": "user_preference",
                "title": "Protect credentials",
                "summary": "Redact credentials before remote calls.",
                "fact_ids": [fact_id],
                "quote": "Never send [REDACTED] to a remote model",
                "quote_role": "user",
            }
        ]


class _IdentityRedactor:
    def redact(self, text: str) -> str:
        return text


class _StaticModel:
    def __init__(self, result: object) -> None:
        self._result = result

    def complete(self, payload: bytes) -> object:
        return self._result


def test_semantic_compression_sends_only_redacted_bounded_fact_payloads() -> None:
    fact = EvidenceFact(
        fact_id="fact-user-secret",
        repo_key="acme/widgets",
        episode_id="episode-test",
        kind="user_quote",
        text="Never send sk-secret to a remote model.",
        role="user",
        evidence=(
            EvidenceReference(
                provider="codex",
                session_id="session-private",
                source_path="/private/raw-session.jsonl",
                raw_event_sha256="f" * 64,
                raw_event_index=9,
                raw_event_type="event_msg",
            ),
        ),
    )
    model = _RecordingModel()
    compression = SemanticCompression(
        model=model,
        redactor=_SecretRedactor(),
        max_payload_bytes=1_024,
    )

    proposals = compression.propose((fact,), repo_key="acme/widgets")

    assert len(proposals) == 1
    assert proposals[0].fact_ids == (fact.fact_id,)
    assert model.payload is not None
    assert len(model.payload) <= 1_024
    assert b"sk-secret" not in model.payload
    assert b"[REDACTED]" in model.payload
    assert b"raw-session" not in model.payload
    assert b"raw_event_sha256" not in model.payload


def test_semantic_compression_rejects_a_payload_over_its_configured_limit() -> None:
    fact = EvidenceFact(
        fact_id="fact-too-large",
        repo_key="acme/widgets",
        episode_id="episode-test",
        kind="user_quote",
        text="x" * 512,
        role="user",
        evidence=(),
    )
    compression = SemanticCompression(
        model=_StaticModel([]),
        redactor=_IdentityRedactor(),
        max_payload_bytes=128,
    )

    with pytest.raises(CompressionBoundaryError, match="configured byte limit"):
        compression.propose((fact,), repo_key="acme/widgets")


def test_semantic_compression_rejects_unknown_proposal_fields() -> None:
    compression = SemanticCompression(
        model=_StaticModel(
            [
                {
                    "memory_type": "user_preference",
                    "title": "Unsafe",
                    "summary": "Unsafe",
                    "fact_ids": ["fact-user-secret"],
                    "quote": "Unsafe",
                    "quote_role": "user",
                    "evidence": ["invented-source"],
                }
            ]
        ),
        redactor=_IdentityRedactor(),
        max_payload_bytes=1_024,
    )

    with pytest.raises(ProposalSchemaError, match="invalid field set"):
        compression.propose((), repo_key="acme/widgets")


def test_semantic_compression_parses_verified_fix_confidence_as_untrusted_metadata() -> None:
    compression = SemanticCompression(
        model=_StaticModel(
            [
                {
                    "memory_type": "verified_fix",
                    "title": "Fix widget validation",
                    "summary": "The focused test passes after the change.",
                    "fact_ids": ["fact-change", "fact-verification"],
                    "confidence": 1.0,
                }
            ]
        ),
        redactor=_IdentityRedactor(),
        max_payload_bytes=1_024,
    )

    proposals = compression.propose((), repo_key="acme/widgets")

    assert proposals[0].memory_type == "verified_fix"
    assert proposals[0].confidence == 1.0
    assert proposals[0].quote is None
