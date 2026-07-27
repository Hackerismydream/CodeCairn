"""Version 0.1 coding-memory domain records and canonical identities."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import typing
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from types import NoneType, UnionType
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints

SCHEMA_VERSION = 1

Provider = Literal["codex", "claude"]
MemoryType = Literal["task_experience", "repository_knowledge", "user_preference", "work_state"]
MemoryOrigin = Literal["capture", "agent_asserted", "restored"]
ExperienceOutcome = Literal["success", "failure", "partial", "unknown"]
FactKind = Literal["message", "command", "command_result", "file_change", "tool_call", "tool_result", "verification"]
FactRole = Literal["user", "assistant", "tool", "system"]
type FactScalar = str | int
type FactAttributes = Mapping[str, FactScalar]
ActionKind = Literal["command", "file_change", "tool", "decision", "observation"]
WorkstreamState = Literal["open", "closed"]

_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_TYPED_ID = re.compile(r"[a-z][a-z_]*_[0-9a-f]{64}\Z")
_MEMORY_TYPES = frozenset({"task_experience", "repository_knowledge", "user_preference", "work_state"})
_PROVIDERS = frozenset({"codex", "claude"})
_FACT_KINDS = frozenset({"message", "command", "command_result", "file_change", "tool_call", "tool_result", "verification"})
_ROLES = frozenset({"user", "assistant", "tool", "system"})
_ORIGINS = frozenset({"capture", "agent_asserted", "restored"})
_OUTCOMES = frozenset({"success", "failure", "partial", "unknown"})
_ACTION_KINDS = frozenset({"command", "file_change", "tool", "decision", "observation"})
_FACT_ATTRIBUTE_FIELDS = {
    "message": (frozenset(), frozenset({"actor"})),
    "command": (frozenset({"command"}), frozenset({"cwd_repo_relative"})),
    "command_result": (frozenset({"command_fact_id", "outcome"}), frozenset({"exit_code"})),
    "file_change": (frozenset({"path", "change_kind"}), frozenset({"destination_path"})),
    "tool_call": (frozenset({"tool_name", "call_id"}), frozenset()),
    "tool_result": (frozenset({"tool_call_fact_id", "outcome"}), frozenset()),
    "verification": (frozenset({"check_name", "outcome"}), frozenset({"command_fact_id", "tool_call_fact_id"})),
}
_CATEGORIES = {
    "task_experience": frozenset({"implementation", "debugging", "review", "evaluation", "operations", "other"}),
    "repository_knowledge": frozenset({"architecture", "convention", "command", "constraint", "solution", "other"}),
    "user_preference": frozenset({"workflow", "output", "tooling", "style", "other"}),
    "work_state": frozenset({"issue", "branch", "task", "session", "other"}),
}


class SchemaInvalid(ValueError):
    """A v0.1 record violates the closed schema."""

    code = "schema_invalid"


class IdentityConflict(ValueError):
    """A stable identity already names different immutable content."""

    code = "identity_conflict"


class LegacyRootUnsupported(ValueError):
    """A pre-v0.1 durable root must be re-imported into a fresh root."""

    code = "legacy_root_unsupported"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    provider: Provider
    session_id: str
    source_generation: int
    event_index: int
    event_id: str
    source_path_sha256: str
    event_sha256: str

    def __post_init__(self) -> None:
        _provider(self.provider)
        _bounded(self.session_id, field="session_id", maximum=256)
        _positive(self.source_generation, field="source_generation")
        _nonnegative(self.event_index, field="event_index")
        _bounded(self.event_id, field="event_id", maximum=256)
        _digest(self.source_path_sha256, field="source_path_sha256")
        _digest(self.event_sha256, field="event_sha256")

    def reference(self, fact_id: str) -> EvidenceReference:
        return EvidenceReference(
            fact_id=fact_id,
            provider=self.provider,
            session_id=self.session_id,
            source_generation=self.source_generation,
            event_index=self.event_index,
            event_id=self.event_id,
            source_path_sha256=self.source_path_sha256,
            event_sha256=self.event_sha256,
        )


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    fact_id: str
    provider: Provider
    session_id: str
    source_generation: int
    event_index: int
    event_id: str
    source_path_sha256: str
    event_sha256: str

    def __post_init__(self) -> None:
        _typed_id(self.fact_id, prefix="fact")
        _location_from_reference(self)


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    schema_version: int
    fact_id: str
    repo_key: str
    episode_id: str | None
    reference: EvidenceReference
    fact_kind: FactKind
    role: FactRole | None
    value: str
    attributes: FactAttributes
    fact_ordinal: int

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _typed_id(self.fact_id, prefix="fact")
        _bounded(self.repo_key, field="repo_key", maximum=512)
        if self.episode_id is not None:
            _typed_id(self.episode_id, prefix="ep")
        if self.reference.fact_id != self.fact_id:
            raise SchemaInvalid("Evidence reference must name its owning fact")
        if self.fact_kind not in _FACT_KINDS:
            raise SchemaInvalid(f"Unknown Evidence Fact kind: {self.fact_kind!r}")
        if self.role is not None and self.role not in _ROLES:
            raise SchemaInvalid(f"Unknown Evidence Fact role: {self.role!r}")
        if (self.fact_kind == "message") is (self.role is None):
            raise SchemaInvalid("Only message facts require a role")
        _bounded(self.value, field="fact value", maximum=32_768)
        _nonnegative(self.fact_ordinal, field="fact_ordinal")
        _validate_fact_attributes(self.fact_kind, self.attributes)
        if self.fact_id != fact_identity(
            repo_key=self.repo_key,
            location=_location_from_reference(self.reference),
            fact_kind=self.fact_kind,
            role=self.role,
            value=self.value,
            attributes=self.attributes,
            fact_ordinal=self.fact_ordinal,
        ):
            raise SchemaInvalid("Evidence Fact identity does not match canonical content")

    @classmethod
    def create(
        cls,
        *,
        repo_key: str,
        location: SourceLocation,
        fact_kind: FactKind,
        role: FactRole | None,
        value: str,
        attributes: FactAttributes,
        fact_ordinal: int = 0,
        episode_id: str | None = None,
    ) -> EvidenceFact:
        fact_id = fact_identity(
            repo_key=repo_key,
            location=location,
            fact_kind=fact_kind,
            role=role,
            value=value,
            attributes=attributes,
            fact_ordinal=fact_ordinal,
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            fact_id=fact_id,
            repo_key=repo_key,
            episode_id=episode_id,
            reference=location.reference(fact_id),
            fact_kind=fact_kind,
            role=role,
            value=value,
            attributes=dict(attributes),
            fact_ordinal=fact_ordinal,
        )


@dataclass(frozen=True, slots=True)
class SourceOrderKey:
    trusted_timestamp_ms: int | None
    provider: Provider
    session_id: str
    source_generation: int
    event_index: int

    def __post_init__(self) -> None:
        if self.trusted_timestamp_ms is not None:
            _nonnegative(self.trusted_timestamp_ms, field="trusted_timestamp_ms")
        _provider(self.provider)
        _bounded(self.session_id, field="session_id", maximum=256)
        _positive(self.source_generation, field="source_generation")
        _nonnegative(self.event_index, field="event_index")


@dataclass(frozen=True, slots=True)
class TaskEpisode:
    schema_version: int
    episode_id: str
    repo_key: str
    provider: Provider
    session_id: str
    source_generation: int
    start_event_index: int
    end_event_index_exclusive: int
    opening_event_id: str
    boundary_kind: Literal["next_user", "codex_stop", "claude_session_end", "manual_finalize"]
    continues_episode_id: str | None
    source_order_key: SourceOrderKey
    prefix_sha256: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _typed_id(self.episode_id, prefix="ep")
        _bounded(self.repo_key, field="repo_key", maximum=512)
        _provider(self.provider)
        _bounded(self.session_id, field="session_id", maximum=256)
        _positive(self.source_generation, field="source_generation")
        _nonnegative(self.start_event_index, field="start_event_index")
        if type(self.end_event_index_exclusive) is not int or self.end_event_index_exclusive <= self.start_event_index:
            raise SchemaInvalid("Episode end must be greater than its start")
        _bounded(self.opening_event_id, field="opening_event_id", maximum=256)
        if self.boundary_kind not in {"next_user", "codex_stop", "claude_session_end", "manual_finalize"}:
            raise SchemaInvalid(f"Unknown Episode boundary: {self.boundary_kind!r}")
        if self.continues_episode_id is not None:
            _typed_id(self.continues_episode_id, prefix="ep")
        _digest(self.prefix_sha256, field="prefix_sha256")
        if self.episode_id != episode_identity(
            repo_key=self.repo_key,
            provider=self.provider,
            session_id=self.session_id,
            source_generation=self.source_generation,
            start_event_index=self.start_event_index,
            end_event_index_exclusive=self.end_event_index_exclusive,
            opening_event_id=self.opening_event_id,
        ):
            raise SchemaInvalid("Task Episode identity does not match its source span")

    @classmethod
    def create(
        cls,
        *,
        repo_key: str,
        provider: Provider,
        session_id: str,
        source_generation: int,
        start_event_index: int,
        end_event_index_exclusive: int,
        opening_event_id: str,
        boundary_kind: Literal["next_user", "codex_stop", "claude_session_end", "manual_finalize"],
        continues_episode_id: str | None,
        source_order_key: SourceOrderKey,
        prefix_sha256: str,
    ) -> TaskEpisode:
        return cls(
            schema_version=SCHEMA_VERSION,
            episode_id=episode_identity(
                repo_key=repo_key,
                provider=provider,
                session_id=session_id,
                source_generation=source_generation,
                start_event_index=start_event_index,
                end_event_index_exclusive=end_event_index_exclusive,
                opening_event_id=opening_event_id,
            ),
            repo_key=repo_key,
            provider=provider,
            session_id=session_id,
            source_generation=source_generation,
            start_event_index=start_event_index,
            end_event_index_exclusive=end_event_index_exclusive,
            opening_event_id=opening_event_id,
            boundary_kind=boundary_kind,
            continues_episode_id=continues_episode_id,
            source_order_key=source_order_key,
            prefix_sha256=prefix_sha256,
        )


@dataclass(frozen=True, slots=True)
class ActionFacet:
    kind: ActionKind
    summary: str
    fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in _ACTION_KINDS:
            raise SchemaInvalid(f"Unknown action kind: {self.kind!r}")
        _bounded(self.summary, field="action summary", maximum=4_096)
        _typed_id_set(self.fact_ids, prefix="fact", maximum=128, field="action fact_ids")


@dataclass(frozen=True, slots=True)
class TaskExperiencePayload:
    goal: str
    outcome: ExperienceOutcome
    actions: tuple[ActionFacet, ...]
    result: str
    blockers: tuple[str, ...]
    verification_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded(self.goal, field="goal", maximum=32_768)
        if self.outcome not in _OUTCOMES:
            raise SchemaInvalid(f"Unknown experience outcome: {self.outcome!r}")
        if len(self.actions) > 128:
            raise SchemaInvalid("Task Experience has too many actions")
        _bounded(self.result, field="result", maximum=32_768)
        _bounded_list(self.blockers, field="blockers", maximum=64, item_maximum=2_048)
        _typed_id_set(self.verification_fact_ids, prefix="fact", maximum=128, field="verification_fact_ids")


@dataclass(frozen=True, slots=True)
class RepositoryKnowledgePayload:
    subject_key: str
    claim: str

    def __post_init__(self) -> None:
        if self.subject_key != normalize_machine_key(self.subject_key):
            raise SchemaInvalid("Repository Knowledge subject_key is not normalized")
        _bounded(self.subject_key, field="subject_key", maximum=512)
        _bounded(self.claim, field="claim", maximum=32_768)


@dataclass(frozen=True, slots=True)
class UserPreferencePayload:
    subject_key: str
    preference: str
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.subject_key != normalize_machine_key(self.subject_key):
            raise SchemaInvalid("User Preference subject_key is not normalized")
        _bounded(self.subject_key, field="subject_key", maximum=512)
        _bounded(self.preference, field="preference", maximum=32_768)
        _typed_id_set(self.source_fact_ids, prefix="fact", maximum=128, field="source_fact_ids", allow_empty=False)


@dataclass(frozen=True, slots=True)
class WorkStatePayload:
    workstream_key: str
    workstream_state: WorkstreamState
    goal: str
    progress: str
    blockers: tuple[str, ...]
    next_step: str | None
    terminal_outcome: str | None

    def __post_init__(self) -> None:
        if self.workstream_key != normalize_machine_key(self.workstream_key):
            raise SchemaInvalid("Work State workstream_key is not normalized")
        _bounded(self.workstream_key, field="workstream_key", maximum=512)
        if self.workstream_state not in {"open", "closed"}:
            raise SchemaInvalid(f"Unknown workstream state: {self.workstream_state!r}")
        _bounded(self.goal, field="goal", maximum=32_768)
        _bounded(self.progress, field="progress", maximum=32_768)
        _bounded_list(self.blockers, field="blockers", maximum=64, item_maximum=2_048)
        if self.workstream_state == "open":
            _bounded(cast(str, self.next_step), field="next_step", maximum=32_768)
            if self.terminal_outcome is not None:
                raise SchemaInvalid("Open Work State cannot have a terminal outcome")
        else:
            _bounded(cast(str, self.terminal_outcome), field="terminal_outcome", maximum=32_768)
            if self.next_step is not None:
                raise SchemaInvalid("Closed Work State cannot have a next step")


type MemoryPayload = TaskExperiencePayload | RepositoryKnowledgePayload | UserPreferencePayload | WorkStatePayload


@dataclass(frozen=True, slots=True)
class CodingMemory:
    schema_version: int
    memory_id: str
    repo_key: str
    memory_type: MemoryType
    title: str
    content: str
    category: str
    tags: tuple[str, ...]
    created_at_ms: int
    episode_id: str | None
    evidence: tuple[EvidenceReference, ...]
    facts: tuple[EvidenceFact, ...]
    origin: MemoryOrigin
    restored_from: str | None
    restore_predecessor_id: str | None
    source_order_key: SourceOrderKey | None
    payload: MemoryPayload

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _typed_id(self.memory_id, prefix="mem")
        _bounded(self.repo_key, field="repo_key", maximum=512)
        if self.memory_type not in _MEMORY_TYPES:
            raise SchemaInvalid(f"Unknown memory type: {self.memory_type!r}")
        _bounded(self.title, field="title", maximum=256)
        _bounded(self.content, field="content", maximum=32_768)
        _bounded(self.category, field="category", maximum=64)
        if self.category not in _CATEGORIES[self.memory_type]:
            raise SchemaInvalid(f"Unknown {self.memory_type} category: {self.category!r}")
        _bounded_list(self.tags, field="tags", maximum=32, item_maximum=64, sorted_set=True)
        if any(tag != normalize_tag(tag) for tag in self.tags):
            raise SchemaInvalid("tags must be normalized")
        _nonnegative(self.created_at_ms, field="created_at_ms")
        if self.episode_id is not None:
            _typed_id(self.episode_id, prefix="ep")
        if len(self.evidence) > 128 or len(self.facts) > 128:
            raise SchemaInvalid("Memory evidence or fact limit exceeded")
        if self.origin not in _ORIGINS:
            raise SchemaInvalid(f"Unknown memory origin: {self.origin!r}")
        _validate_origin(self)
        _validate_payload(self)
        _validate_memory_evidence(self)
        if self.memory_id != memory_identity(self):
            raise SchemaInvalid("Coding Memory identity does not match canonical content")

    @classmethod
    def create(
        cls,
        *,
        repo_key: str,
        memory_type: MemoryType,
        title: str,
        content: str,
        category: str,
        tags: tuple[str, ...],
        created_at_ms: int,
        episode_id: str | None,
        evidence: tuple[EvidenceReference, ...],
        facts: tuple[EvidenceFact, ...],
        origin: MemoryOrigin,
        restored_from: str | None,
        restore_predecessor_id: str | None,
        source_order_key: SourceOrderKey | None,
        payload: MemoryPayload,
    ) -> CodingMemory:
        memory_id = typed_id(
            "mem",
            _memory_identity_parts(
                repo_key=repo_key,
                memory_type=memory_type,
                episode_id=episode_id,
                facts=facts,
                origin=origin,
                restored_from=restored_from,
                restore_predecessor_id=restore_predecessor_id,
                payload=payload,
            ),
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            memory_id=memory_id,
            repo_key=repo_key,
            memory_type=memory_type,
            title=title,
            content=content,
            category=category,
            tags=tags,
            created_at_ms=created_at_ms,
            episode_id=episode_id,
            evidence=evidence,
            facts=facts,
            origin=origin,
            restored_from=restored_from,
            restore_predecessor_id=restore_predecessor_id,
            source_order_key=source_order_key,
            payload=payload,
        )


def fact_identity(
    *,
    repo_key: str,
    location: SourceLocation,
    fact_kind: FactKind,
    role: FactRole | None,
    value: str,
    attributes: FactAttributes,
    fact_ordinal: int,
) -> str:
    return typed_id(
        "fact",
        {
            "schema_version": SCHEMA_VERSION,
            "repo_key": repo_key,
            "location": _public_fields(location),
            "fact_kind": fact_kind,
            "role": role,
            "value": value,
            "attributes": dict(attributes),
            "fact_ordinal": fact_ordinal,
        },
    )


def episode_identity(
    *,
    repo_key: str,
    provider: Provider,
    session_id: str,
    source_generation: int,
    start_event_index: int,
    end_event_index_exclusive: int,
    opening_event_id: str,
) -> str:
    return typed_id(
        "ep",
        {
            "schema_version": SCHEMA_VERSION,
            "repo_key": repo_key,
            "provider": provider,
            "session_id": session_id,
            "source_generation": source_generation,
            "start_event_index": start_event_index,
            "end_event_index_exclusive": end_event_index_exclusive,
            "opening_event_id": opening_event_id,
        },
    )


def memory_identity(memory: CodingMemory) -> str:
    return typed_id(
        "mem",
        _memory_identity_parts(
            repo_key=memory.repo_key,
            memory_type=memory.memory_type,
            episode_id=memory.episode_id,
            facts=memory.facts,
            origin=memory.origin,
            restored_from=memory.restored_from,
            restore_predecessor_id=memory.restore_predecessor_id,
            payload=memory.payload,
        ),
    )


def memory_subject_key(memory: CodingMemory) -> str:
    payload = memory.payload
    if isinstance(payload, (RepositoryKnowledgePayload, UserPreferencePayload)):
        return payload.subject_key
    if isinstance(payload, WorkStatePayload):
        return payload.workstream_key
    raise SchemaInvalid("Task Experience has no subject key")


def typed_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


def canonical_json(value: object) -> str:
    _validate_identity_value(value)
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalize_machine_key(value: str) -> str:
    normalized = normalize_text(value)
    return " ".join(normalized.split()).strip().lower()


def normalize_tag(value: str) -> str:
    return normalize_text(value).strip()


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def normalize_path_key(value: str) -> str:
    normalized = normalize_text(value).replace("\\", "/")
    if normalized.startswith("/") or normalized.endswith("/"):
        raise SchemaInvalid("Repository-relative path cannot start or end with '/'")
    components = normalized.split("/")
    if ".." in components:
        raise SchemaInvalid("Repository-relative path cannot contain '..'")
    result = posixpath.normpath(normalized)
    if result in {"", "."}:
        raise SchemaInvalid("Repository-relative path cannot be empty")
    return result


def payload_to_dict(payload: MemoryPayload) -> dict[str, object]:
    return _record_to_dict(payload)


def evidence_fact_to_dict(fact: EvidenceFact) -> dict[str, object]:
    return _record_to_dict(fact)


def task_episode_to_dict(episode: TaskEpisode) -> dict[str, object]:
    return _record_to_dict(episode)


def coding_memory_to_dict(memory: CodingMemory) -> dict[str, object]:
    return _record_to_dict(memory)


def evidence_fact_from_dict(value: object) -> EvidenceFact:
    return _record_from_dict(EvidenceFact, value)


def task_episode_from_dict(value: object) -> TaskEpisode:
    return _record_from_dict(TaskEpisode, value)


def coding_memory_from_dict(value: object) -> CodingMemory:
    data = _object(value, field="record")
    data = {**data}
    for field in ("tags", "evidence", "facts"):
        data.setdefault(field, [])
    memory_type = cast(MemoryType, _string(data, "memory_type"))
    if memory_type not in _MEMORY_TYPES:
        raise SchemaInvalid(f"Unknown memory type: {memory_type!r}")
    payload_types = {
        "task_experience": TaskExperiencePayload,
        "repository_knowledge": RepositoryKnowledgePayload,
        "user_preference": UserPreferencePayload,
        "work_state": WorkStatePayload,
    }
    payload = _object(data["payload"], field="payload")
    if memory_type == "task_experience":
        payload = {**payload}
        for field in ("actions", "blockers", "verification_fact_ids"):
            payload.setdefault(field, [])
    elif memory_type == "work_state":
        payload = {"blockers": [], **payload}
    data["payload"] = _record_from_dict(payload_types[memory_type], payload)
    return _record_from_dict(CodingMemory, data)


def _record_to_dict(value: object) -> dict[str, object]:
    encoded = _encode_value(value)
    if not isinstance(encoded, dict):
        raise SchemaInvalid("Record must encode as an object")
    return encoded


def _encode_value(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _encode_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _encode_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode_value(item) for item in value]
    return value


def _record_from_dict[T](record_type: type[T], value: object) -> T:
    data = _object(value, field="record")
    dataclass_fields = fields(cast(Any, record_type))
    record_fields = {field.name for field in dataclass_fields}
    if set(data) != record_fields:
        missing, unknown = record_fields - set(data), set(data) - record_fields
        raise SchemaInvalid(f"Record field mismatch: missing={sorted(missing)!r}, unknown={sorted(unknown)!r}")
    hints = get_type_hints(record_type)
    try:
        return record_type(**{field.name: _decode_value(hints[field.name], data[field.name]) for field in dataclass_fields})
    except SchemaInvalid:
        raise
    except (TypeError, ValueError) as error:
        raise SchemaInvalid(str(error)) from error


def _decode_value(annotation: object, value: object) -> Any:
    alias = getattr(annotation, "__value__", None)
    if alias is not None:
        return _decode_value(alias, value)
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Literal:
        if value not in arguments or not any(type(value) is type(item) for item in arguments):
            raise SchemaInvalid(f"Value is not one of {arguments!r}")
        return value
    if origin in (UnionType, typing.Union):
        for option in arguments:
            try:
                return _decode_value(option, value)
            except SchemaInvalid:
                continue
        raise SchemaInvalid("Value does not match its union")
    if annotation is NoneType:
        if value is not None:
            raise SchemaInvalid("Value must be null")
        return None
    if origin is tuple:
        if not isinstance(value, list):
            raise SchemaInvalid("Tuple field must be an array")
        item_type = arguments[0]
        return tuple(_decode_value(item_type, item) for item in value)
    if origin in (dict, Mapping):
        if not isinstance(value, dict):
            raise SchemaInvalid("Mapping field must be an object")
        key_type, item_type = arguments
        return {_decode_value(key_type, key): _decode_value(item_type, item) for key, item in value.items()}
    if isinstance(annotation, type) and is_dataclass(annotation):
        if isinstance(value, annotation):
            return value
        return _record_from_dict(annotation, value)
    if annotation is Any or annotation is object:
        return value
    if type(value) is not annotation:
        name = getattr(annotation, "__name__", repr(annotation))
        raise SchemaInvalid(f"Value must be {name}")
    return value


def _memory_identity_parts(
    *,
    repo_key: str,
    memory_type: MemoryType,
    episode_id: str | None,
    facts: tuple[EvidenceFact, ...],
    origin: MemoryOrigin,
    restored_from: str | None,
    restore_predecessor_id: str | None,
    payload: MemoryPayload,
) -> dict[str, object]:
    identity: dict[str, object] = {"schema_version": SCHEMA_VERSION, "repo_key": repo_key, "memory_type": memory_type}
    if memory_type == "task_experience":
        identity["episode_id"] = episode_id
        return identity
    if isinstance(payload, (RepositoryKnowledgePayload, UserPreferencePayload)):
        key = payload.subject_key
    elif isinstance(payload, WorkStatePayload):
        key = payload.workstream_key
    else:
        raise SchemaInvalid(f"{memory_type} has the wrong payload")
    source_fact_ids = (
        list(payload.source_fact_ids) if isinstance(payload, UserPreferencePayload) else sorted(fact.fact_id for fact in facts)
    )
    identity.update({"key": key, "source_fact_ids": source_fact_ids, "payload": payload_to_dict(payload), "origin": origin})
    if origin == "capture":
        identity["episode_id"] = episode_id
    elif origin == "restored":
        identity["restored_from"] = restored_from
        identity["restore_predecessor_id"] = restore_predecessor_id
    return identity


def _validate_origin(memory: CodingMemory) -> None:
    if memory.origin == "capture":
        if (
            memory.episode_id is None
            or not memory.evidence
            or not memory.facts
            or memory.source_order_key is None
            or memory.restored_from is not None
            or memory.restore_predecessor_id is not None
        ):
            raise SchemaInvalid("Capture memory requires source lineage only")
    elif memory.origin == "agent_asserted":
        if (
            memory.episode_id is not None
            or memory.source_order_key is not None
            or memory.restored_from is not None
            or memory.restore_predecessor_id is not None
        ):
            raise SchemaInvalid("Agent-asserted memory cannot claim capture or restore lineage")
    elif memory.restored_from is None or memory.restore_predecessor_id is None:
        raise SchemaInvalid("Restored memory requires historical and active lineage")
    if memory.restored_from is not None:
        _typed_id(memory.restored_from, prefix="mem")
    if memory.restore_predecessor_id is not None:
        _typed_id(memory.restore_predecessor_id, prefix="mem")


def _validate_payload(memory: CodingMemory) -> None:
    expected = {
        "task_experience": TaskExperiencePayload,
        "repository_knowledge": RepositoryKnowledgePayload,
        "user_preference": UserPreferencePayload,
        "work_state": WorkStatePayload,
    }[memory.memory_type]
    if not isinstance(memory.payload, expected):
        raise SchemaInvalid(f"{memory.memory_type} has the wrong payload")
    if memory.memory_type == "task_experience" and (
        memory.origin != "capture" or memory.episode_id is None or memory.restored_from is not None
    ):
        raise SchemaInvalid("Task Experience is capture-only and append-only")
    if isinstance(memory.payload, UserPreferencePayload):
        by_id = {fact.fact_id: fact for fact in memory.facts}
        embedded = tuple(sorted(by_id))
        selected = memory.payload.source_fact_ids
        if embedded and (embedded != selected or any(by_id[fact_id].role != "user" for fact_id in selected)):
            raise SchemaInvalid("User Preference requires user-authored Source Facts")
    if isinstance(memory.payload, TaskExperiencePayload):
        known = {fact.fact_id for fact in memory.facts}
        selected_facets = {fact_id for action in memory.payload.actions for fact_id in action.fact_ids} | set(
            memory.payload.verification_fact_ids
        )
        if not selected_facets <= known:
            raise SchemaInvalid("Task Experience facets must select embedded Source Facts")


def _validate_memory_evidence(memory: CodingMemory) -> None:
    fact_ids = tuple(fact.fact_id for fact in memory.facts)
    if len(fact_ids) != len(set(fact_ids)):
        raise SchemaInvalid("Memory facts must have unique IDs")
    references = tuple(fact.reference for fact in memory.facts)
    if tuple(memory.evidence) != references:
        raise SchemaInvalid("Memory evidence must exactly match selected fact references")
    if tuple(memory.evidence) != tuple(sorted(memory.evidence, key=_reference_sort_key)):
        raise SchemaInvalid("Memory evidence must use canonical source order")
    if any(fact.repo_key != memory.repo_key for fact in memory.facts):
        raise SchemaInvalid("Memory facts cannot cross namespaces")
    if memory.origin in {"capture", "restored"} and any(fact.episode_id != memory.episode_id for fact in memory.facts):
        raise SchemaInvalid("Capture lineage facts must belong to the Memory Episode")


def _validate_fact_attributes(kind: FactKind, attributes: FactAttributes) -> None:
    if not isinstance(attributes, Mapping) or not all(isinstance(key, str) for key in attributes):
        raise SchemaInvalid("Evidence Fact attributes must be an object")
    required, optional = _FACT_ATTRIBUTE_FIELDS[kind]
    keys = frozenset(attributes)
    if not required <= keys or not keys <= required | optional:
        raise SchemaInvalid(f"{kind} attributes have an invalid field set")
    if any(isinstance(value, bool) or not isinstance(value, str | int) for value in attributes.values()):
        raise SchemaInvalid("Evidence Fact attributes must contain scalar strings or integers")
    for key, value in attributes.items():
        if isinstance(value, str):
            _bounded(value, field=f"attributes.{key}", maximum=4_096)
    outcome = attributes.get("outcome")
    if outcome is not None and outcome not in {"success", "failure", "unknown"}:
        raise SchemaInvalid("Evidence Fact outcome is invalid")
    change_kind = attributes.get("change_kind")
    if change_kind is not None and change_kind not in {"add", "update", "delete", "move"}:
        raise SchemaInvalid("File change kind is invalid")
    for key in ("path", "destination_path", "cwd_repo_relative"):
        path_value = attributes.get(key)
        if isinstance(path_value, str) and path_value != normalize_path_key(path_value):
            raise SchemaInvalid(f"Evidence Fact path {key!r} is not normalized")


def _public_fields(value: SourceLocation | EvidenceReference) -> dict[str, object]:
    return {
        "provider": value.provider,
        "session_id": value.session_id,
        "source_generation": value.source_generation,
        "event_index": value.event_index,
        "event_id": value.event_id,
        "source_path_sha256": value.source_path_sha256,
        "event_sha256": value.event_sha256,
    }


def _location_from_reference(reference: EvidenceReference) -> SourceLocation:
    return SourceLocation(
        provider=reference.provider,
        session_id=reference.session_id,
        source_generation=reference.source_generation,
        event_index=reference.event_index,
        event_id=reference.event_id,
        source_path_sha256=reference.source_path_sha256,
        event_sha256=reference.event_sha256,
    )


def _reference_sort_key(reference: EvidenceReference) -> tuple[str, str, int, int, str]:
    return (reference.provider, reference.session_id, reference.source_generation, reference.event_index, reference.fact_id)


def _schema(value: int) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise SchemaInvalid(f"schema_version must be {SCHEMA_VERSION}")


def _provider(value: str) -> None:
    if value not in _PROVIDERS:
        raise SchemaInvalid(f"Unknown source provider: {value!r}")


def _typed_id(value: str, *, prefix: str) -> None:
    if not isinstance(value, str) or _TYPED_ID.fullmatch(value) is None:
        raise SchemaInvalid(f"Invalid typed ID: {value!r}")
    if not value.startswith(f"{prefix}_"):
        raise SchemaInvalid(f"Expected {prefix!r} typed ID")


def _digest(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise SchemaInvalid(f"{field} must be a lowercase SHA-256")


def _bounded(value: str, *, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise SchemaInvalid(f"{field} must contain 1..{maximum} UTF-8 bytes")
    if value != normalize_text(value):
        raise SchemaInvalid(f"{field} must use NFC text and LF newlines")


def _bounded_list(values: tuple[str, ...], *, field: str, maximum: int, item_maximum: int, sorted_set: bool = False) -> None:
    if len(values) > maximum:
        raise SchemaInvalid(f"{field} has too many values")
    for value in values:
        _bounded(value, field=field, maximum=item_maximum)
    if sorted_set and values != tuple(sorted(set(values))):
        raise SchemaInvalid(f"{field} must be unique and sorted")


def _typed_id_set(values: tuple[str, ...], *, prefix: str, maximum: int, field: str, allow_empty: bool = True) -> None:
    if (not allow_empty and not values) or len(values) > maximum:
        raise SchemaInvalid(f"{field} has an invalid cardinality")
    if values != tuple(sorted(set(values))):
        raise SchemaInvalid(f"{field} must be unique and sorted")
    for value in values:
        _typed_id(value, prefix=prefix)


def _positive(value: int, *, field: str) -> None:
    if type(value) is not int or value < 1:
        raise SchemaInvalid(f"{field} must be a positive integer")


def _nonnegative(value: int, *, field: str) -> None:
    if type(value) is not int or value < 0:
        raise SchemaInvalid(f"{field} must be a non-negative integer")


def _validate_identity_value(value: object) -> None:
    if value is None:
        return
    if isinstance(value, (bool, float)):
        raise SchemaInvalid("Canonical identity values cannot contain booleans or floats")
    if isinstance(value, (str, int)):
        if isinstance(value, str) and value != normalize_text(value):
            raise SchemaInvalid("Canonical identity text must be normalized")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_identity_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _validate_identity_value(key)
            _validate_identity_value(item)
        return
    raise SchemaInvalid(f"Unsupported canonical identity value: {type(value).__name__}")


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SchemaInvalid(f"{field} must be an object")
    return cast(dict[str, object], value)


def _string(data: Mapping[str, object], field: str) -> str:
    if field not in data:
        raise SchemaInvalid(f"Missing required field: {field}")
    value = data[field]
    if not isinstance(value, str):
        raise SchemaInvalid(f"{field} must be a string")
    return value
