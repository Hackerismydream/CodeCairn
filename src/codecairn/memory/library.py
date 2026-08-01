"""Person-first library records that reference unchanged Coding Memories."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, cast

from codecairn.memory.schema import (
    CodingMemory,
    SchemaInvalid,
    canonical_json,
    coding_memory_to_dict,
    normalize_machine_key,
    normalize_text,
    typed_id,
)

MemoryScope = Literal["global", "repository"]
RequestingClient = Literal["cli", "hub", "mcp", "pico"]

_PERSON_ID = re.compile(r"person_[0-9a-f]{64}\Z")
_PROMOTION_ID = re.compile(r"promotion_[0-9a-f]{64}\Z")
_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class Person:
    schema_version: int
    person_id: str
    created_at_ms: int

    def __post_init__(self) -> None:
        if self.schema_version != 1 or _PERSON_ID.fullmatch(self.person_id) is None or self.created_at_ms < 0:
            raise SchemaInvalid("Local Person is invalid")


@dataclass(frozen=True, slots=True)
class SourceContext:
    repository_key: str
    memory_id: str
    revision_sha256: str

    def __post_init__(self) -> None:
        if (
            not self.repository_key
            or self.repository_key != normalize_text(self.repository_key)
            or len(self.repository_key.encode()) > 512
            or _MEMORY_ID.fullmatch(self.memory_id) is None
            or _SHA256.fullmatch(self.revision_sha256) is None
        ):
            raise SchemaInvalid("Promotion Source Context is invalid")


@dataclass(frozen=True, slots=True)
class GlobalPreferencePromotion:
    schema_version: int
    promotion_id: str
    person_id: str
    subject_key: str
    source: SourceContext
    replaces_promotion_id: str | None
    created_at_ms: int

    def __post_init__(self) -> None:
        if (
            self.schema_version != 1
            or _PROMOTION_ID.fullmatch(self.promotion_id) is None
            or _PERSON_ID.fullmatch(self.person_id) is None
            or self.subject_key != normalize_machine_key(self.subject_key)
            or not self.subject_key
            or len(self.subject_key.encode()) > 512
            or self.created_at_ms < 0
            or (self.replaces_promotion_id is not None and _PROMOTION_ID.fullmatch(self.replaces_promotion_id) is None)
        ):
            raise SchemaInvalid("Global Preference Promotion is invalid")
        if self.promotion_id != promotion_identity(
            person_id=self.person_id, subject_key=self.subject_key, source=self.source, replaces_promotion_id=self.replaces_promotion_id
        ):
            raise SchemaInvalid("Global Preference Promotion identity is invalid")

    @classmethod
    def create(
        cls, *, person_id: str, subject_key: str, source: SourceContext, replaces_promotion_id: str | None, created_at_ms: int
    ) -> GlobalPreferencePromotion:
        normalized_subject = normalize_machine_key(subject_key)
        return cls(
            schema_version=1,
            promotion_id=promotion_identity(
                person_id=person_id, subject_key=normalized_subject, source=source, replaces_promotion_id=replaces_promotion_id
            ),
            person_id=person_id,
            subject_key=normalized_subject,
            source=source,
            replaces_promotion_id=replaces_promotion_id,
            created_at_ms=created_at_ms,
        )


def promotion_identity(*, person_id: str, subject_key: str, source: SourceContext, replaces_promotion_id: str | None) -> str:
    return typed_id(
        "promotion",
        {
            "schema_version": 1,
            "person_id": person_id,
            "subject_key": subject_key,
            "source": source_context_to_dict(source),
            "replaces_promotion_id": replaces_promotion_id,
        },
    )


def memory_revision_sha256(memory: CodingMemory) -> str:
    return hashlib.sha256(canonical_json(coding_memory_to_dict(memory)).encode()).hexdigest()


def person_to_dict(person: Person) -> dict[str, object]:
    return {"schema_version": person.schema_version, "person_id": person.person_id, "created_at_ms": person.created_at_ms}


def person_from_dict(value: object) -> Person:
    data = _closed_object(value, {"schema_version", "person_id", "created_at_ms"})
    return Person(
        schema_version=_int(data["schema_version"]), person_id=_str(data["person_id"]), created_at_ms=_int(data["created_at_ms"])
    )


def source_context_to_dict(source: SourceContext) -> dict[str, object]:
    return {"repository_key": source.repository_key, "memory_id": source.memory_id, "revision_sha256": source.revision_sha256}


def promotion_to_dict(promotion: GlobalPreferencePromotion) -> dict[str, object]:
    return {
        "schema_version": promotion.schema_version,
        "promotion_id": promotion.promotion_id,
        "person_id": promotion.person_id,
        "subject_key": promotion.subject_key,
        "source": source_context_to_dict(promotion.source),
        "replaces_promotion_id": promotion.replaces_promotion_id,
        "created_at_ms": promotion.created_at_ms,
    }


def promotion_from_dict(value: object) -> GlobalPreferencePromotion:
    data = _closed_object(
        value, {"schema_version", "promotion_id", "person_id", "subject_key", "source", "replaces_promotion_id", "created_at_ms"}
    )
    source = _closed_object(data["source"], {"repository_key", "memory_id", "revision_sha256"})
    predecessor = data["replaces_promotion_id"]
    if predecessor is not None and not isinstance(predecessor, str):
        raise SchemaInvalid("Promotion predecessor must be a string or null")
    return GlobalPreferencePromotion(
        schema_version=_int(data["schema_version"]),
        promotion_id=_str(data["promotion_id"]),
        person_id=_str(data["person_id"]),
        subject_key=_str(data["subject_key"]),
        source=SourceContext(
            repository_key=_str(source["repository_key"]),
            memory_id=_str(source["memory_id"]),
            revision_sha256=_str(source["revision_sha256"]),
        ),
        replaces_promotion_id=predecessor,
        created_at_ms=_int(data["created_at_ms"]),
    )


def _closed_object(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields or not all(isinstance(key, str) for key in value):
        raise SchemaInvalid("Library record fields are invalid")
    return cast(dict[str, object], value)


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise SchemaInvalid("Library record field must be a string")
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaInvalid("Library record field must be an integer")
    return value
