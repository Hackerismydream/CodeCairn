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

from codecairn.memory.evolution import (
    EvolutionArtifact,
    EvolutionRecord,
    evolution_from_dict,
    evolution_to_dict,
)
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
_EVOLUTION_KEYS = (
    "schema_version",
    "record_kind",
    "evolution_id",
    "repo_key",
    "relation_kind",
    "predecessor_id",
    "successor_id",
    "proposal_id",
    "supporting_fact_ids",
    "source_order_key",
    "proposer",
    "evidence",
    "created_at_ms",
)


@dataclass(frozen=True, slots=True)
class TruthIssue:
    path: Path
    error_code: str


@dataclass(frozen=True, slots=True)
class TruthScan:
    memories: tuple[MemoryArtifact, ...]
    issues: tuple[TruthIssue, ...]


@dataclass(frozen=True, slots=True)
class EvolutionTruthScan:
    evolutions: tuple[EvolutionArtifact, ...]
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

    def prepare_evolution(self, record: EvolutionRecord) -> EvolutionArtifact:
        content = _render_evolution(record)
        return EvolutionArtifact(
            record=record,
            path=self.evolution_path_for(record),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )

    def write_evolution(
        self,
        record: EvolutionRecord,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> EvolutionArtifact:
        self._reject_legacy_root()
        artifact = self.prepare_evolution(record)
        content = _render_evolution(record)
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_bytes(artifact.path, missing_ok=True)
        if existing is None:
            _atomic_create(
                artifact.path,
                content,
                on_stage=on_stage,
                stage_prefix="evolution",
            )
            existing = _read_bytes(artifact.path)
        assert existing is not None
        if hashlib.sha256(existing).hexdigest() != artifact.content_sha256:
            try:
                stored = self.read_evolution(artifact.path)
            except (OSError, UnicodeError, SchemaInvalid, ValueError) as exc:
                raise IdentityConflict(
                    f"Conflicting Evolution truth: {record.evolution_id}"
                ) from exc
            if evolution_to_dict(stored.record) != evolution_to_dict(record):
                raise IdentityConflict(f"Conflicting immutable Evolution: {record.evolution_id}")
            return stored
        return artifact

    def read_evolution(self, path: Path) -> EvolutionArtifact:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self._root):
            raise SchemaInvalid("Evolution Markdown source escapes the runtime root")
        source = _read_bytes(resolved)
        assert source is not None
        record = _parse_evolution(source)
        if resolved != self.evolution_path_for(record):
            raise SchemaInvalid("Evolution Markdown is not at its canonical path")
        return EvolutionArtifact(
            record=record,
            path=resolved,
            content_sha256=hashlib.sha256(source).hexdigest(),
        )

    def scan_evolutions(self) -> EvolutionTruthScan:
        evolutions: list[EvolutionArtifact] = []
        issues: list[TruthIssue] = []
        root = self._root / "evolution"
        if not root.exists():
            return EvolutionTruthScan(evolutions=(), issues=())
        for path in sorted(root.glob("*/*.md")):
            try:
                evolutions.append(self.read_evolution(path))
            except (OSError, UnicodeError, SchemaInvalid, ValueError) as exc:
                issues.append(TruthIssue(path=path, error_code=_error_code(exc)))
        return EvolutionTruthScan(evolutions=tuple(evolutions), issues=tuple(issues))

    def evolution_path_for(self, record: EvolutionRecord) -> Path:
        repo_slug = hashlib.sha256(record.repo_key.encode()).hexdigest()[:16]
        path = (self._root / "evolution" / repo_slug / f"{record.evolution_id}.md").resolve()
        if not path.is_relative_to(self._root):
            raise SchemaInvalid("Evolution target escapes the runtime root")
        return path

    def relative_evolution_path_for(self, record: EvolutionRecord) -> str:
        return self.evolution_path_for(record).relative_to(self._root).as_posix()

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


def _render_evolution(record: EvolutionRecord) -> bytes:
    value = evolution_to_dict(record)
    reason = value.pop("reason")
    envelope = {"record_kind": "evolution", **value}
    lines = ["---"]
    for key in _EVOLUTION_KEYS:
        lines.append(f"{key}: {canonical_json(envelope[key])}")
    lines.extend(("---", str(reason), ""))
    encoded = "\n".join(lines).encode()
    if len(encoded) > _MAX_MARKDOWN_BYTES:
        raise SchemaInvalid("Evolution Markdown exceeds its byte limit")
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


def _parse_evolution(source: bytes) -> EvolutionRecord:
    if not source or len(source) > _MAX_MARKDOWN_BYTES:
        raise SchemaInvalid("Evolution Markdown is empty or exceeds its byte limit")
    content = source.decode()
    if not content.startswith("---\n") or not content.endswith("\n"):
        raise SchemaInvalid("Evolution Markdown envelope is invalid")
    frontmatter, separator, body = content[4:].partition("\n---\n")
    if not separator:
        raise SchemaInvalid("Evolution Markdown frontmatter is unterminated")
    keys: list[str] = []
    record: dict[str, object] = {}
    for line in frontmatter.splitlines():
        key, marker, raw_value = line.partition(": ")
        if not marker or key in record:
            raise SchemaInvalid("Evolution Markdown frontmatter line is invalid")
        keys.append(key)
        try:
            record[key] = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise SchemaInvalid("Evolution Markdown frontmatter JSON is invalid") from exc
    if tuple(keys) != _EVOLUTION_KEYS:
        raise SchemaInvalid("Evolution Markdown frontmatter fields or order are invalid")
    if record.pop("record_kind") != "evolution":
        raise SchemaInvalid("Markdown record_kind is not evolution")
    record["reason"] = body[:-1]
    return evolution_from_dict(record)


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
