from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, cast

from codecairn.memory.models import EvidenceFact, MemoryProposal, MemoryType
from codecairn.memory.trace import stable_id

_MAX_PROPOSALS = 64
_MAX_FACT_IDS = 64
_MAX_TITLE_CHARS = 256
_MAX_SUMMARY_CHARS = 4_096
_MAX_QUOTE_CHARS = 4_096
_REQUIRED_PROPOSAL_FIELDS = frozenset({"memory_type", "title", "summary", "fact_ids"})
_OPTIONAL_PROPOSAL_FIELDS = frozenset({"quote", "quote_role", "confidence"})
_PROPOSAL_FIELDS = _REQUIRED_PROPOSAL_FIELDS | _OPTIONAL_PROPOSAL_FIELDS
_SUPPORTED_TYPES = frozenset(
    {"debug_episode", "repository_convention", "user_preference", "verified_fix"}
)


class TextRedactor(Protocol):
    def redact(self, text: str) -> str: ...


class CompressionModel(Protocol):
    def complete(self, payload: bytes) -> object: ...


class CompressionBoundaryError(ValueError):
    """Raised when a remote compression boundary is not safely bounded."""


class ProposalSchemaError(ValueError):
    """Raised when untrusted semantic-compression output has an invalid shape."""


class SemanticCompression:
    """Send redacted Evidence Facts, then parse untrusted proposal schemas."""

    def __init__(
        self,
        *,
        model: CompressionModel,
        redactor: TextRedactor,
        max_payload_bytes: int,
    ) -> None:
        if max_payload_bytes <= 0:
            raise CompressionBoundaryError("Compression payload limit must be positive")
        self._model = model
        self._redactor = redactor
        self._max_payload_bytes = max_payload_bytes

    def propose(
        self,
        facts: tuple[EvidenceFact, ...],
        *,
        repo_key: str,
    ) -> tuple[MemoryProposal, ...]:
        payload = self._payload(facts)
        raw_proposals = self._model.complete(payload)
        return _parse_proposals(raw_proposals, repo_key=repo_key)

    def _payload(self, facts: tuple[EvidenceFact, ...]) -> bytes:
        safe_facts = [
            {
                "fact_id": fact.fact_id,
                "kind": fact.kind,
                "role": fact.role,
                "actor": fact.actor,
                "occurred_at": fact.occurred_at,
                "text": self._redactor.redact(fact.text),
            }
            for fact in facts
        ]
        payload = json.dumps(
            {"schema_version": 1, "facts": safe_facts},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(payload) > self._max_payload_bytes:
            raise CompressionBoundaryError(
                "Redacted compression payload exceeds the configured byte limit"
            )
        return payload


def _parse_proposals(value: object, *, repo_key: str) -> tuple[MemoryProposal, ...]:
    if not isinstance(value, list) or len(value) > _MAX_PROPOSALS:
        raise ProposalSchemaError("Compression output must be a bounded proposal list")
    proposals: list[MemoryProposal] = []
    for position, item in enumerate(value):
        if (
            not isinstance(item, Mapping)
            or not set(item) >= _REQUIRED_PROPOSAL_FIELDS
            or not set(item) <= _PROPOSAL_FIELDS
        ):
            raise ProposalSchemaError(f"Proposal {position} has an invalid field set")
        memory_type = _required_string(item, "memory_type", position=position)
        if memory_type not in _SUPPORTED_TYPES:
            raise ProposalSchemaError(f"Proposal {position} has an unsupported memory type")
        title = _bounded_string(
            item,
            "title",
            position=position,
            max_chars=_MAX_TITLE_CHARS,
        )
        summary = _bounded_string(
            item,
            "summary",
            position=position,
            max_chars=_MAX_SUMMARY_CHARS,
        )
        fact_ids_value = item.get("fact_ids")
        if (
            not isinstance(fact_ids_value, list)
            or not fact_ids_value
            or len(fact_ids_value) > _MAX_FACT_IDS
            or not all(isinstance(fact_id, str) and fact_id for fact_id in fact_ids_value)
        ):
            raise ProposalSchemaError(f"Proposal {position} has invalid fact identifiers")
        fact_ids = tuple(fact_ids_value)
        if len(fact_ids) != len(set(fact_ids)):
            raise ProposalSchemaError(f"Proposal {position} repeats a fact identifier")
        quote = _optional_bounded_string(
            item,
            "quote",
            position=position,
            max_chars=_MAX_QUOTE_CHARS,
        )
        quote_role = _optional_bounded_string(
            item,
            "quote_role",
            position=position,
            max_chars=32,
        )
        confidence = _optional_confidence(item, position=position)
        proposals.append(
            MemoryProposal(
                proposal_id=stable_id(
                    "proposal",
                    repo_key,
                    memory_type,
                    title,
                    summary,
                    quote,
                    quote_role,
                    *fact_ids,
                ),
                repo_key=repo_key,
                memory_type=cast(MemoryType, memory_type),
                title=title,
                summary=summary,
                fact_ids=fact_ids,
                quote=quote,
                quote_role=quote_role,
                confidence=confidence,
            )
        )
    return tuple(proposals)


def _required_string(item: Mapping[object, object], key: str, *, position: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ProposalSchemaError(f"Proposal {position} field {key!r} must be a string")
    return value


def _bounded_string(
    item: Mapping[object, object],
    key: str,
    *,
    position: int,
    max_chars: int,
) -> str:
    value = _required_string(item, key, position=position)
    if len(value) > max_chars:
        raise ProposalSchemaError(f"Proposal {position} field {key!r} exceeds its limit")
    return value


def _optional_bounded_string(
    item: Mapping[object, object],
    key: str,
    *,
    position: int,
    max_chars: int,
) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ProposalSchemaError(f"Proposal {position} field {key!r} is invalid")
    return value


def _optional_confidence(item: Mapping[object, object], *, position: int) -> float | None:
    value = item.get("confidence")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProposalSchemaError(f"Proposal {position} confidence is invalid")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ProposalSchemaError(f"Proposal {position} confidence is outside [0, 1]")
    return confidence
