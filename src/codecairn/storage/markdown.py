from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import cast, get_args

from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    EvidenceFactKind,
    EvidenceFactStatus,
    EvidenceReference,
    MemoryRepairPlan,
    MemoryRepairReason,
    MemoryType,
    SemanticAtomicFact,
    SemanticEpisode,
    TruthIssue,
    TruthScan,
)
from codecairn.memory.trace import stable_id

_MEMORY_TYPES = frozenset(get_args(MemoryType))
_EVIDENCE_FACT_KINDS = frozenset(get_args(EvidenceFactKind))
_EVIDENCE_FACT_STATUSES = frozenset(get_args(EvidenceFactStatus))
_MAX_MARKDOWN_BYTES = 64 * 1024 * 1024
_SAFE_BODY: dict[MemoryType, tuple[str, str]] = {
    "conversation_episode": (
        "Conversation Episode",
        "An attributed conversation episode backed by exact source turns.",
    ),
    "debug_episode": ("Debug Episode", "A debugging episode backed by cited raw events."),
    "repository_convention": (
        "Repository Convention",
        "A repository convention backed by cited raw events.",
    ),
    "failed_command": (
        "Failed Command",
        "A repository command failed. Inspect the cited raw events before reuse.",
    ),
    "verified_fix": ("Verified Fix", "A verified fix backed by cited raw events."),
    "user_preference": (
        "User Preference",
        "A user preference backed by cited raw events.",
    ),
}


class MarkdownMemoryStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def prepare(self, memory: CodingMemory) -> CodingMemory:
        """Return the canonical path and full-content digest without writing."""

        prepared, _content = self._prepare(memory)
        return prepared

    def write(self, memory: CodingMemory) -> CodingMemory:
        contract_supplied = memory.markdown_path is not None or memory.content_sha256 is not None
        prepared, content = self._prepare(memory)
        path = Path(_required_markdown_path(prepared))
        content_sha256 = _required_content_sha256(prepared)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_sha256 = _file_sha256(path)
        if existing_sha256 is None:
            _atomic_create(path, content)
            existing_sha256 = _file_sha256(path)
        if existing_sha256 != content_sha256:
            if contract_supplied:
                raise ValueError(
                    f"Prepared Markdown contract conflicts with existing truth: {memory.memory_id}"
                )
            existing = self.read(path)
            if not _same_immutable_memory(existing, memory):
                raise ValueError(f"Conflicting immutable memory: {memory.memory_id}")
            return existing
        return prepared

    def plan_repair(self, memory: CodingMemory) -> MemoryRepairPlan | None:
        path = self._committed_path(memory)
        expected_sha256 = _required_content_sha256(memory)
        try:
            observed_bytes = _read_markdown_bytes(path, missing_ok=True)
        except _UnsafeMarkdownFile:
            return _repair_plan(
                memory,
                reason="unparsable",
                observed_sha256=None,
            )
        if observed_bytes is None:
            return MemoryRepairPlan(
                repo_key=memory.repo_key,
                memory_id=memory.memory_id,
                reason="missing",
                observed_sha256=None,
                expected_sha256=expected_sha256,
            )
        observed_sha256 = hashlib.sha256(observed_bytes).hexdigest()
        try:
            content = observed_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return _repair_plan(
                memory,
                reason="unparsable",
                observed_sha256=observed_sha256,
            )
        if (
            not content.startswith("---\n")
            or "\n---\n" not in content[4:]
            or not content.endswith("\n")
        ):
            return _repair_plan(
                memory,
                reason="truncated",
                observed_sha256=observed_sha256,
            )
        try:
            restored = self.read(path)
        except (OSError, ValueError):
            return _repair_plan(
                memory,
                reason="unparsable",
                observed_sha256=observed_sha256,
            )
        if observed_sha256 != expected_sha256 or not _same_immutable_memory(restored, memory):
            return _repair_plan(
                memory,
                reason="hash_mismatch",
                observed_sha256=observed_sha256,
            )
        return None

    def repair(self, memory: CodingMemory, plan: MemoryRepairPlan) -> CodingMemory:
        current_plan = self.plan_repair(memory)
        if current_plan is None:
            return self.read(self._committed_path(memory))
        if current_plan != plan:
            raise ValueError(f"Markdown changed during recovery: {memory.memory_id}")
        content = _render(memory)
        content_bytes = _validated_markdown_bytes(content)
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        if content_sha256 != plan.expected_sha256:
            raise ValueError(
                f"Committed recovery state conflicts with Markdown: {memory.memory_id}"
            )
        path = self._committed_path(memory)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace(path, content)
        restored = self.read(path)
        if restored.content_sha256 != plan.expected_sha256:
            raise ValueError(f"Markdown recovery verification failed: {memory.memory_id}")
        return restored

    def read(self, path: Path) -> CodingMemory:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self._root):
            raise ValueError("Markdown source escapes the runtime root")
        source = _read_markdown_bytes(resolved)
        if source is None:
            raise FileNotFoundError(resolved)
        content = source.decode("utf-8")
        return _memory_from_content(resolved, content)

    def read_projection(self, memory: CodingMemory) -> tuple[CodingMemory, str]:
        path = self._committed_path(memory)
        source = _read_markdown_bytes(path)
        if source is None:
            raise FileNotFoundError(path)
        observed_sha256 = hashlib.sha256(source).hexdigest()
        if observed_sha256 != _required_content_sha256(memory):
            raise ValueError(f"Markdown changed after reconciliation: {memory.memory_id}")
        content = source.decode("utf-8")
        restored = _memory_from_content(path, content)
        if (restored.repo_key, restored.memory_id) != (memory.repo_key, memory.memory_id):
            raise ValueError("Markdown truth changed its committed memory identity")
        return restored, content

    def read_markdown(self, memory: CodingMemory) -> str:
        _restored, content = self.read_projection(memory)
        return content

    def scan(self) -> TruthScan:
        memories: dict[tuple[str, str], CodingMemory] = {}
        issues: list[TruthIssue] = []
        memory_root = self._root / "repos"
        if not memory_root.exists():
            return TruthScan(memories=(), issues=())
        for path in sorted(memory_root.glob("*/memories/*/*.md")):
            observed_sha256: str | None = None
            try:
                source = _read_markdown_bytes(path)
                if source is None:
                    continue
                observed_sha256 = hashlib.sha256(source).hexdigest()
                memory = self.read(path)
                if path.resolve(strict=True) != self._path_for(memory):
                    raise ValueError("Memory Markdown is not at its canonical path")
                key = (memory.repo_key, memory.memory_id)
                if key in memories:
                    raise ValueError("Duplicate memory identity in Markdown truth")
                memories[key] = memory
            except (OSError, UnicodeError, ValueError) as exc:
                issues.append(
                    TruthIssue(
                        markdown_path=str(path.resolve(strict=False)),
                        observed_sha256=observed_sha256,
                        error_type=type(exc).__name__,
                    )
                )
        return TruthScan(
            memories=tuple(memories[key] for key in sorted(memories)),
            issues=tuple(issues),
        )

    def _path_for(self, memory: CodingMemory) -> Path:
        repo_namespace = hashlib.sha256(memory.repo_key.encode()).hexdigest()[:16]
        path = (
            self._root
            / "repos"
            / repo_namespace
            / "memories"
            / memory.memory_type
            / f"{memory.memory_id}.md"
        ).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Markdown target escapes the runtime root")
        return path

    def _prepare(self, memory: CodingMemory) -> tuple[CodingMemory, str]:
        path = self._path_for(memory)
        content = _render(memory)
        content_bytes = _validated_markdown_bytes(content)
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        supplied_path = memory.markdown_path
        supplied_sha256 = memory.content_sha256
        if (supplied_path is None) != (supplied_sha256 is None):
            raise ValueError(f"Markdown preparation contract is incomplete: {memory.memory_id}")
        if supplied_path is not None and (
            Path(supplied_path) != path or supplied_sha256 != content_sha256
        ):
            raise ValueError(f"Markdown preparation contract conflicts: {memory.memory_id}")
        return (
            replace(
                memory,
                markdown_path=str(path),
                content_sha256=content_sha256,
            ),
            content,
        )

    def _committed_path(self, memory: CodingMemory) -> Path:
        if memory.markdown_path is None:
            raise ValueError("Committed memory is missing its Markdown path")
        path = self._path_for(memory)
        if Path(memory.markdown_path) != path:
            raise ValueError(f"Committed Markdown path conflicts with memory: {memory.memory_id}")
        return path


def _memory_from_content(resolved: Path, content: str) -> CodingMemory:
    attributes = _parse_frontmatter(content)
    evidence = _parse_evidence(attributes["evidence"])
    memory_type = _required_str(attributes, "memory_type")
    if memory_type not in _MEMORY_TYPES:
        raise ValueError(f"Unknown memory type: {memory_type!r}")
    memory = CodingMemory(
        memory_id=_required_str(attributes, "memory_id"),
        repo_key=_required_str(attributes, "repo_key"),
        memory_type=cast(MemoryType, memory_type),
        title=_required_str(attributes, "title"),
        summary=_required_str(attributes, "summary"),
        episode_id=_required_str(attributes, "episode_id"),
        command=_optional_str(attributes, "command"),
        exit_code=_optional_int(attributes, "exit_code"),
        evidence=evidence,
        fact_ids=_optional_string_tuple(attributes, "fact_ids"),
        facts=_parse_facts(attributes.get("facts", [])),
        semantic_episode=_parse_semantic_episode(attributes.get("semantic_episode")),
        adjacency_group_id=_optional_str(attributes, "adjacency_group_id"),
        adjacency_index=_optional_int(attributes, "adjacency_index"),
        markdown_path=str(resolved),
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    _validate_facts(memory)
    return memory


def _render(memory: CodingMemory) -> str:
    _validate_facts(memory)
    evidence = [_evidence_dict(item) for item in memory.evidence]
    heading, description = _SAFE_BODY[memory.memory_type]
    result = (
        f"- Result: Process exited with code {memory.exit_code}\n"
        if memory.exit_code is not None
        else ""
    )
    fact_ids = f"fact_ids: {json.dumps(memory.fact_ids)}\n" if memory.fact_ids else ""
    facts = (
        f"facts: {json.dumps([_fact_dict(fact) for fact in memory.facts], sort_keys=True)}\n"
        if memory.facts
        else ""
    )
    semantic_episode = (
        "semantic_episode: "
        f"{json.dumps(_semantic_episode_dict(memory.semantic_episode), sort_keys=True)}\n"
        if memory.semantic_episode is not None
        else ""
    )
    adjacency = (
        f"adjacency_group_id: {json.dumps(memory.adjacency_group_id)}\n"
        f"adjacency_index: {json.dumps(memory.adjacency_index)}\n"
        if memory.adjacency_group_id is not None
        else ""
    )
    semantic_body = (
        "\n## Retrieval Annotation\n\n"
        "This derived text is a search aid; the cited Evidence Facts remain authoritative.\n\n"
        f"{memory.semantic_episode.narrative}\n"
        if memory.semantic_episode is not None
        else ""
    )
    return (
        "---\n"
        f"memory_id: {json.dumps(memory.memory_id)}\n"
        f"repo_key: {json.dumps(memory.repo_key)}\n"
        f"memory_type: {json.dumps(memory.memory_type)}\n"
        f"title: {json.dumps(memory.title)}\n"
        f"summary: {json.dumps(memory.summary)}\n"
        f"episode_id: {json.dumps(memory.episode_id)}\n"
        f"command: {json.dumps(memory.command)}\n"
        f"exit_code: {json.dumps(memory.exit_code)}\n"
        f"evidence: {json.dumps(evidence, sort_keys=True)}\n"
        f"{fact_ids}"
        f"{facts}"
        f"{semantic_episode}"
        f"{adjacency}"
        "---\n\n"
        f"# {heading}\n\n"
        f"{description}\n\n"
        "## Evidence\n\n"
        f"- Raw event indices: {', '.join(str(item.raw_event_index) for item in memory.evidence)}\n"
        f"- Raw event hashes: {', '.join(item.raw_event_sha256 for item in memory.evidence)}\n"
        f"{result}"
        f"{semantic_body}"
    )


def _evidence_dict(evidence: EvidenceReference) -> dict[str, object]:
    return {
        "provider": evidence.provider,
        "session_id": evidence.session_id,
        "source_path": evidence.source_path,
        "raw_event_sha256": evidence.raw_event_sha256,
        "raw_event_index": evidence.raw_event_index,
        "raw_event_type": evidence.raw_event_type,
        "call_id": evidence.call_id,
    }


def _fact_dict(fact: EvidenceFact) -> dict[str, object]:
    result: dict[str, object] = {
        "fact_id": fact.fact_id,
        "repo_key": fact.repo_key,
        "episode_id": fact.episode_id,
        "kind": fact.kind,
        "text": fact.text,
        "role": fact.role,
        "evidence": [_evidence_dict(item) for item in fact.evidence],
        "status": fact.status,
    }
    if fact.actor is not None:
        result["actor"] = fact.actor
    if fact.occurred_at is not None:
        result["occurred_at"] = fact.occurred_at
    return result


def _semantic_episode_dict(semantic_episode: SemanticEpisode) -> dict[str, object]:
    return {
        "episode_id": semantic_episode.episode_id,
        "narrative": semantic_episode.narrative,
        "atomic_facts": [
            {
                "fact_id": fact.fact_id,
                "text": fact.text,
                "source_fact_ids": list(fact.source_fact_ids),
            }
            for fact in semantic_episode.atomic_facts
        ],
        "source_fact_ids": list(semantic_episode.source_fact_ids),
        "semanticizer_id": semantic_episode.semanticizer_id,
        "revision": semantic_episode.revision,
    }


def _parse_frontmatter(content: str) -> dict[str, object]:
    if not content.startswith("---\n"):
        raise ValueError("Memory Markdown is missing frontmatter")
    frontmatter, separator, _body = content[4:].partition("\n---\n")
    if not separator:
        raise ValueError("Memory Markdown has unterminated frontmatter")
    attributes: dict[str, object] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(": ")
        if not separator:
            raise ValueError(f"Invalid memory frontmatter line: {line!r}")
        attributes[key] = json.loads(value)
    required = {
        "memory_id",
        "repo_key",
        "memory_type",
        "title",
        "summary",
        "episode_id",
        "command",
        "exit_code",
        "evidence",
    }
    if missing := required - attributes.keys():
        raise ValueError(f"Memory Markdown is missing fields: {sorted(missing)}")
    return attributes


def _parse_evidence(value: object) -> tuple[EvidenceReference, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Memory evidence must be a non-empty list")
    evidence: list[EvidenceReference] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Memory evidence at position {position} is not an object")
        evidence.append(
            EvidenceReference(
                provider=_required_str(item, "provider"),
                session_id=_required_str(item, "session_id"),
                source_path=_required_str(item, "source_path"),
                raw_event_sha256=_required_str(item, "raw_event_sha256"),
                raw_event_index=_required_int(item, "raw_event_index"),
                raw_event_type=_required_str(item, "raw_event_type"),
                call_id=_optional_str(item, "call_id"),
            )
        )
    return tuple(evidence)


def _parse_facts(value: object) -> tuple[EvidenceFact, ...]:
    if not isinstance(value, list):
        raise ValueError("Memory facts must be a list")
    facts: list[EvidenceFact] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Memory fact at position {position} is not an object")
        kind = _required_str(item, "kind")
        if kind not in _EVIDENCE_FACT_KINDS:
            raise ValueError(f"Unknown evidence fact kind: {kind!r}")
        status = _optional_str(item, "status")
        if status is not None and status not in _EVIDENCE_FACT_STATUSES:
            raise ValueError(f"Unknown evidence fact status: {status!r}")
        facts.append(
            EvidenceFact(
                fact_id=_required_str(item, "fact_id"),
                repo_key=_required_str(item, "repo_key"),
                episode_id=_required_str(item, "episode_id"),
                kind=cast(EvidenceFactKind, kind),
                text=_required_str(item, "text"),
                role=_optional_str(item, "role"),
                evidence=_parse_evidence(item.get("evidence")),
                status=cast(EvidenceFactStatus | None, status),
                actor=_optional_str(item, "actor"),
                occurred_at=_optional_str(item, "occurred_at"),
            )
        )
    return tuple(facts)


def _parse_semantic_episode(value: object) -> SemanticEpisode | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Memory semantic episode must be an object or null")
    atomic_value = value.get("atomic_facts")
    if not isinstance(atomic_value, list):
        raise ValueError("Memory semantic atomic facts must be a list")
    atomic_facts: list[SemanticAtomicFact] = []
    for position, item in enumerate(atomic_value):
        if not isinstance(item, dict):
            raise ValueError(f"Semantic atomic fact at position {position} is not an object")
        atomic_facts.append(
            SemanticAtomicFact(
                fact_id=_required_str(item, "fact_id"),
                text=_required_str(item, "text"),
                source_fact_ids=_required_string_tuple(item, "source_fact_ids"),
            )
        )
    return SemanticEpisode(
        episode_id=_required_str(value, "episode_id"),
        narrative=_required_str(value, "narrative"),
        atomic_facts=tuple(atomic_facts),
        source_fact_ids=_required_string_tuple(value, "source_fact_ids"),
        semanticizer_id=_required_str(value, "semanticizer_id"),
        revision=_required_str(value, "revision"),
    )


def _required_str(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Memory field {key!r} must be a string")
    return value


def _optional_str(values: dict[str, object], key: str) -> str | None:
    value = values.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Memory field {key!r} must be a string or null")
    return value


def _required_int(values: dict[str, object], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Memory field {key!r} must be an integer")
    return value


def _optional_int(values: dict[str, object], key: str) -> int | None:
    value = values.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"Memory field {key!r} must be an integer or null")
    return value


def _optional_string_tuple(values: dict[str, object], key: str) -> tuple[str, ...]:
    value = values.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Memory field {key!r} must be a string list")
    if len(value) != len(set(value)):
        raise ValueError(f"Memory field {key!r} must contain unique values")
    return tuple(value)


def _required_string_tuple(values: dict[str, object], key: str) -> tuple[str, ...]:
    if key not in values:
        raise ValueError(f"Memory field {key!r} is required")
    return _optional_string_tuple(values, key)


def _atomic_create(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return
        _fsync_directory(path.parent)
    except Exception:
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    directory_descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _core_semantic_identity(memory: CodingMemory) -> tuple[object, ...]:
    evidence = tuple(
        (
            item.provider,
            item.session_id,
            item.raw_event_sha256,
            item.raw_event_index,
            item.raw_event_type,
            item.call_id,
        )
        for item in memory.evidence
    )
    return (
        memory.memory_id,
        memory.repo_key,
        memory.memory_type,
        memory.title,
        memory.summary,
        memory.episode_id,
        memory.command,
        memory.exit_code,
        memory.fact_ids,
        _semantic_episode_identity(memory.semantic_episode),
        memory.adjacency_group_id,
        memory.adjacency_index,
        evidence,
    )


def _same_immutable_memory(existing: CodingMemory, candidate: CodingMemory) -> bool:
    if _core_semantic_identity(existing) != _core_semantic_identity(candidate):
        return False
    return (
        not existing.facts
        or not candidate.facts
        or _fact_semantic_identity(existing.facts) == _fact_semantic_identity(candidate.facts)
    )


def _fact_semantic_identity(facts: tuple[EvidenceFact, ...]) -> tuple[object, ...]:
    return tuple(
        (
            fact.fact_id,
            fact.repo_key,
            fact.episode_id,
            fact.kind,
            fact.text,
            fact.role,
            fact.status,
            fact.actor,
            fact.occurred_at,
            tuple(
                (
                    item.provider,
                    item.session_id,
                    item.raw_event_sha256,
                    item.raw_event_index,
                    item.raw_event_type,
                    item.call_id,
                )
                for item in fact.evidence
            ),
        )
        for fact in facts
    )


def _semantic_episode_identity(semantic_episode: SemanticEpisode | None) -> object:
    if semantic_episode is None:
        return None
    return (
        semantic_episode.episode_id,
        semantic_episode.narrative,
        tuple(
            (fact.fact_id, fact.text, fact.source_fact_ids)
            for fact in semantic_episode.atomic_facts
        ),
        semantic_episode.source_fact_ids,
        semantic_episode.semanticizer_id,
        semantic_episode.revision,
    )


def _validate_facts(memory: CodingMemory) -> None:
    if (memory.adjacency_group_id is None) != (memory.adjacency_index is None):
        raise ValueError("Memory adjacency group and index must be configured together")
    if memory.adjacency_group_id is not None and (
        not memory.adjacency_group_id.strip()
        or type(memory.adjacency_index) is not int
        or memory.adjacency_index < 0
    ):
        raise ValueError("Memory adjacency metadata is invalid")
    fact_ids = tuple(fact.fact_id for fact in memory.facts)
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("Memory facts must have unique fact IDs")
    if memory.fact_ids and memory.facts and memory.fact_ids != fact_ids:
        raise ValueError("Memory fact IDs must match the persisted fact snapshot")
    evidence = set(memory.evidence)
    for fact in memory.facts:
        if fact.repo_key != memory.repo_key:
            raise ValueError("Memory facts must belong to the same repository")
        if not fact.evidence or not set(fact.evidence).issubset(evidence):
            raise ValueError("Memory facts must cite the memory evidence")
    if memory.memory_type == "conversation_episode" and (
        not memory.facts
        or memory.fact_ids != fact_ids
        or any(
            fact.kind != "conversation_turn"
            or fact.episode_id != memory.episode_id
            or not fact.actor
            or not fact.role
            for fact in memory.facts
        )
    ):
        raise ValueError("Conversation Episodes require attributed source turns")
    semantic_episode = memory.semantic_episode
    if memory.memory_type == "conversation_episode" and semantic_episode is None:
        raise ValueError("Conversation Episodes require a grounded semantic projection")
    if semantic_episode is None:
        return
    source_fact_ids = set(fact_ids)
    semantic_fact_ids = tuple(atomic.fact_id for atomic in semantic_episode.atomic_facts)
    if (
        semantic_episode.episode_id != memory.episode_id
        or tuple(fact_ids) != semantic_episode.source_fact_ids
        or not semantic_episode.narrative.strip()
        or not semantic_episode.semanticizer_id.strip()
        or not semantic_episode.revision.strip()
        or len(semantic_fact_ids) != len(set(semantic_fact_ids))
        or any(
            not atomic.source_fact_ids
            or len(atomic.source_fact_ids) != len(set(atomic.source_fact_ids))
            or not set(atomic.source_fact_ids) <= source_fact_ids
            or not atomic.text.strip()
            or atomic.fact_id
            != stable_id(
                "semantic-atomic-fact",
                semantic_episode.episode_id,
                *atomic.source_fact_ids,
                atomic.text,
            )
            for atomic in semantic_episode.atomic_facts
        )
    ):
        raise ValueError("Memory semantic episode must be grounded in its Evidence Facts")


def _file_sha256(path: Path) -> str | None:
    source = _read_markdown_bytes(path, missing_ok=True)
    return hashlib.sha256(source).hexdigest() if source is not None else None


class _UnsafeMarkdownFile(ValueError):
    pass


def _read_markdown_bytes(path: Path, *, missing_ok: bool = False) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as exc:
        raise _UnsafeMarkdownFile(f"Unsafe Markdown source: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _UnsafeMarkdownFile(f"Markdown source is not a regular file: {path}")
        if metadata.st_size > _MAX_MARKDOWN_BYTES:
            raise _UnsafeMarkdownFile(
                f"Markdown source exceeds the {_MAX_MARKDOWN_BYTES}-byte limit: {path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            source = handle.read(_MAX_MARKDOWN_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(source) > _MAX_MARKDOWN_BYTES:
        raise _UnsafeMarkdownFile(
            f"Markdown source exceeds the {_MAX_MARKDOWN_BYTES}-byte limit: {path}"
        )
    return source


def _validated_markdown_bytes(content: str) -> bytes:
    encoded = content.encode()
    if len(encoded) > _MAX_MARKDOWN_BYTES:
        raise ValueError(f"Memory Markdown exceeds the {_MAX_MARKDOWN_BYTES}-byte limit")
    return encoded


def _required_markdown_path(memory: CodingMemory) -> str:
    if memory.markdown_path is None:
        raise ValueError("Prepared memory is missing its Markdown path")
    return memory.markdown_path


def _required_content_sha256(memory: CodingMemory) -> str:
    if memory.content_sha256 is None:
        raise ValueError("Committed memory is missing its content hash")
    return memory.content_sha256


def _repair_plan(
    memory: CodingMemory,
    *,
    reason: MemoryRepairReason,
    observed_sha256: str | None,
) -> MemoryRepairPlan:
    return MemoryRepairPlan(
        repo_key=memory.repo_key,
        memory_id=memory.memory_id,
        reason=reason,
        observed_sha256=observed_sha256,
        expected_sha256=_required_content_sha256(memory),
    )
