"""Canonical Markdown truth for version 0.1 Coding Memories."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codecairn.memory.models import MemoryArtifact
from codecairn.memory.schema import (
    CodingMemory,
    IdentityConflict,
    LegacyRootUnsupported,
    SchemaInvalid,
    canonical_json,
    coding_memory_from_dict,
    coding_memory_to_dict,
)

_MAX_MARKDOWN_BYTES = 64 * 1024 * 1024
_FRONTMATTER_KEYS = (
    "schema_version",
    "record_kind",
    "memory_id",
    "repo_key",
    "memory_type",
    "title",
    "category",
    "tags",
    "created_at_ms",
    "episode_id",
    "origin",
    "restored_from",
    "restore_predecessor_id",
    "source_order_key",
    "payload",
    "evidence",
    "facts",
)


@dataclass(frozen=True, slots=True)
class TruthIssue:
    path: Path
    error_code: str


@dataclass(frozen=True, slots=True)
class TruthScan:
    memories: tuple[MemoryArtifact, ...]
    issues: tuple[TruthIssue, ...]


class MarkdownMemoryStore:
    """Store immutable memories without leaking storage metadata into the domain."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def prepare(self, memory: CodingMemory) -> MemoryArtifact:
        content = _render(memory)
        return MemoryArtifact(
            memory=memory,
            path=self.path_for(memory),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )

    def write(
        self,
        memory: CodingMemory,
        *,
        on_stage: Callable[[str], None] | None = None,
        stage_prefix: str = "capture",
    ) -> MemoryArtifact:
        self._reject_legacy_root()
        artifact = self.prepare(memory)
        content = _render(memory)
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_bytes(artifact.path, missing_ok=True)
        if existing is None:
            _atomic_create(
                artifact.path,
                content,
                on_stage=on_stage,
                stage_prefix=stage_prefix,
            )
            existing = _read_bytes(artifact.path)
        assert existing is not None
        if hashlib.sha256(existing).hexdigest() != artifact.content_sha256:
            try:
                stored = self.read(artifact.path)
            except (OSError, UnicodeError, SchemaInvalid, ValueError) as exc:
                raise IdentityConflict(f"Conflicting Markdown truth: {memory.memory_id}") from exc
            if coding_memory_to_dict(stored.memory) != coding_memory_to_dict(memory):
                raise IdentityConflict(f"Conflicting immutable memory: {memory.memory_id}")
            return stored
        return artifact

    def read(self, path: Path) -> MemoryArtifact:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self._root):
            raise SchemaInvalid("Markdown source escapes the runtime root")
        source = _read_bytes(resolved)
        assert source is not None
        memory = _parse(source)
        expected = self.path_for(memory)
        if resolved != expected:
            raise SchemaInvalid("Memory Markdown is not at its canonical path")
        return MemoryArtifact(
            memory=memory,
            path=resolved,
            content_sha256=hashlib.sha256(source).hexdigest(),
        )

    def scan(self) -> TruthScan:
        memories: list[MemoryArtifact] = []
        issues: list[TruthIssue] = []
        memory_root = self._root / "memory"
        if not memory_root.exists():
            return TruthScan(memories=(), issues=())
        for path in sorted(memory_root.glob("*/*/*.md")):
            try:
                memories.append(self.read(path))
            except (OSError, UnicodeError, SchemaInvalid, ValueError) as exc:
                issues.append(TruthIssue(path=path, error_code=_error_code(exc)))
        identities = [(item.memory.repo_key, item.memory.memory_id) for item in memories]
        if len(identities) != len(set(identities)):
            raise IdentityConflict("Duplicate Coding Memory identity in Markdown truth")
        return TruthScan(memories=tuple(memories), issues=tuple(issues))

    def path_for(self, memory: CodingMemory) -> Path:
        repo_slug = hashlib.sha256(memory.repo_key.encode("utf-8")).hexdigest()[:16]
        path = (
            self._root / "memory" / repo_slug / memory.memory_type / f"{memory.memory_id}.md"
        ).resolve()
        if not path.is_relative_to(self._root):
            raise SchemaInvalid("Markdown target escapes the runtime root")
        return path

    def relative_path_for(self, memory: CodingMemory) -> str:
        return self.path_for(memory).relative_to(self._root).as_posix()

    def _reject_legacy_root(self) -> None:
        if (self._root / "repos").exists():
            raise LegacyRootUnsupported(
                "Pre-v0.1 runtime root is unsupported; use a fresh root and re-import"
            )


def _render(memory: CodingMemory) -> bytes:
    record = coding_memory_to_dict(memory)
    content = record.pop("content")
    envelope = {"record_kind": "coding_memory", **record}
    lines = ["---"]
    for key in _FRONTMATTER_KEYS:
        lines.append(f"{key}: {canonical_json(envelope[key])}")
    lines.extend(("---", str(content), ""))
    encoded = "\n".join(lines).encode("utf-8")
    if len(encoded) > _MAX_MARKDOWN_BYTES:
        raise SchemaInvalid("Memory Markdown exceeds its byte limit")
    return encoded


def _parse(source: bytes) -> CodingMemory:
    if not source or len(source) > _MAX_MARKDOWN_BYTES:
        raise SchemaInvalid("Memory Markdown is empty or exceeds its byte limit")
    content = source.decode("utf-8")
    if not content.startswith("---\n") or not content.endswith("\n"):
        raise SchemaInvalid("Memory Markdown envelope is invalid")
    frontmatter, separator, body = content[4:].partition("\n---\n")
    if not separator:
        raise SchemaInvalid("Memory Markdown frontmatter is unterminated")
    lines = frontmatter.splitlines()
    keys: list[str] = []
    record: dict[str, object] = {}
    for line in lines:
        key, marker, raw_value = line.partition(": ")
        if not marker or key in record:
            raise SchemaInvalid("Memory Markdown frontmatter line is invalid")
        keys.append(key)
        try:
            record[key] = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise SchemaInvalid("Memory Markdown frontmatter JSON is invalid") from exc
    if tuple(keys) != _FRONTMATTER_KEYS:
        raise SchemaInvalid("Memory Markdown frontmatter fields or order are invalid")
    if record.pop("record_kind") != "coding_memory":
        raise SchemaInvalid("Markdown record_kind is not coding_memory")
    record["content"] = body[:-1]
    return coding_memory_from_dict(record)


def _atomic_create(
    path: Path,
    content: bytes,
    *,
    on_stage: Callable[[str], None] | None = None,
    stage_prefix: str,
) -> None:
    descriptor: int | None = None
    temporary: Path | None = None
    created = False
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            _stage(on_stage, f"{stage_prefix}_after_temp_write")
            handle.flush()
            os.fsync(handle.fileno())
            _stage(on_stage, f"{stage_prefix}_after_file_fsync")
        try:
            os.link(temporary, path)
            created = True
            _stage(on_stage, f"{stage_prefix}_after_atomic_create")
        except FileExistsError:
            return
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if created:
        _fsync_directory(path.parent)
        _stage(on_stage, f"{stage_prefix}_after_directory_fsync")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage(callback: Callable[[str], None] | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


def _read_bytes(path: Path, *, missing_ok: bool = False) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SchemaInvalid("Markdown truth must be a regular single-link file")
    with path.open("rb") as handle:
        source = handle.read(_MAX_MARKDOWN_BYTES + 1)
    if len(source) > _MAX_MARKDOWN_BYTES:
        raise SchemaInvalid("Memory Markdown exceeds its byte limit")
    return source


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else type(error).__name__
