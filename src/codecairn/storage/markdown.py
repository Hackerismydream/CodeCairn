from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import cast, get_args

from codecairn.memory.models import CodingMemory, EvidenceReference, MemoryType

_MEMORY_TYPES = frozenset(get_args(MemoryType))
_SAFE_BODY: dict[MemoryType, tuple[str, str]] = {
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

    def write(self, memory: CodingMemory) -> CodingMemory:
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
        content = _render(memory)
        content_sha256 = hashlib.sha256(content.encode()).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_sha256 = _file_sha256(path)
        if existing_sha256 is None:
            _atomic_create(path, content)
            existing_sha256 = _file_sha256(path)
        if existing_sha256 != content_sha256:
            existing = self.read(path)
            if _semantic_identity(existing) != _semantic_identity(memory):
                raise ValueError(f"Conflicting immutable memory: {memory.memory_id}")
            return existing
        return replace(
            memory,
            markdown_path=str(path),
            content_sha256=content_sha256,
        )

    def read(self, path: Path) -> CodingMemory:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self._root):
            raise ValueError("Markdown source escapes the runtime root")
        content = resolved.read_text(encoding="utf-8")
        attributes = _parse_frontmatter(content)
        evidence = _parse_evidence(attributes["evidence"])
        memory_type = _required_str(attributes, "memory_type")
        if memory_type not in _MEMORY_TYPES:
            raise ValueError(f"Unknown memory type: {memory_type!r}")
        return CodingMemory(
            memory_id=_required_str(attributes, "memory_id"),
            repo_key=_required_str(attributes, "repo_key"),
            memory_type=cast(MemoryType, memory_type),
            title=_required_str(attributes, "title"),
            summary=_required_str(attributes, "summary"),
            episode_id=_required_str(attributes, "episode_id"),
            command=_optional_str(attributes, "command"),
            exit_code=_optional_int(attributes, "exit_code"),
            evidence=evidence,
            markdown_path=str(resolved),
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )


def _render(memory: CodingMemory) -> str:
    evidence = [_evidence_dict(item) for item in memory.evidence]
    heading, description = _SAFE_BODY[memory.memory_type]
    result = (
        f"- Result: Process exited with code {memory.exit_code}\n"
        if memory.exit_code is not None
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
        "---\n\n"
        f"# {heading}\n\n"
        f"{description}\n\n"
        "## Evidence\n\n"
        f"- Raw event indices: {', '.join(str(item.raw_event_index) for item in memory.evidence)}\n"
        f"- Raw event hashes: {', '.join(item.raw_event_sha256 for item in memory.evidence)}\n"
        f"{result}"
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


def _fsync_directory(path: Path) -> None:
    directory_descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _semantic_identity(memory: CodingMemory) -> tuple[object, ...]:
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
        evidence,
    )


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
