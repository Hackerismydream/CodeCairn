"""Durable capture operation records used by Write Intent recovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from codecairn.memory.models import ImportCheckpoint
from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    Provider,
    TaskEpisode,
    _record_from_dict,
    _record_to_dict,
    canonical_json,
    coding_memory_to_dict,
    typed_id,
)


@dataclass(frozen=True, slots=True)
class ExpectedMemoryFile:
    relative_path: str
    content_sha256: str
    memory_id: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Expected Memory path must be safe and relative")
        _digest(self.content_sha256, field="content_sha256")
        if not self.memory_id.startswith("mem_"):
            raise ValueError("Expected Memory file has an invalid identity")


@dataclass(frozen=True, slots=True)
class CaptureCheckpoint:
    repo_key: str
    provider: Provider
    session_id: str
    source_path: str
    source_sha256: str
    raw_event_count: int
    committed_raw_event_index: int
    resume: ImportCheckpoint
    prior_source_cursor: int

    def __post_init__(self) -> None:
        if not self.repo_key or not self.session_id or not self.source_path:
            raise ValueError("Capture checkpoint identity fields must not be empty")
        _digest(self.source_sha256, field="source_sha256")
        if self.raw_event_count < 0:
            raise ValueError("Capture raw_event_count must not be negative")
        if self.committed_raw_event_index != self.raw_event_count - 1:
            raise ValueError("Capture committed cursor must match the observed source")
        if self.prior_source_cursor < -1:
            raise ValueError("Capture prior cursor must be at least -1")
        if self.resume.provider != self.provider or self.resume.session_id != self.session_id:
            raise ValueError("Capture resume checkpoint does not match the source")


@dataclass(frozen=True, slots=True)
class PreparedMemoryCommit:
    operation_id: str
    repo_key: str
    episodes: tuple[TaskEpisode, ...]
    facts: tuple[EvidenceFact, ...]
    memories: tuple[CodingMemory, ...]
    expected_files: tuple[ExpectedMemoryFile, ...]
    checkpoint: CaptureCheckpoint | None
    created_at_ms: int

    def __post_init__(self) -> None:
        if self.created_at_ms < 0 or (self.checkpoint is not None and self.repo_key != self.checkpoint.repo_key):
            raise ValueError("Write Intent time or checkpoint namespace is invalid")
        if self.checkpoint is None and (
            self.episodes or self.facts or len(self.memories) != 1 or self.memories[0].memory_type == "task_experience"
        ):
            raise ValueError("Direct Memory Write Intent is invalid")
        if any(item.repo_key != self.repo_key for item in self.episodes):
            raise ValueError("Write Intent Episodes cannot cross namespaces")
        if any(item.repo_key != self.repo_key for item in self.facts):
            raise ValueError("Write Intent Source Facts cannot cross namespaces")
        if any(item.repo_key != self.repo_key for item in self.memories):
            raise ValueError("Write Intent Memories cannot cross namespaces")
        if len({item.episode_id for item in self.episodes}) != len(self.episodes):
            raise ValueError("Write Intent Episodes must be unique")
        if len({item.fact_id for item in self.facts}) != len(self.facts):
            raise ValueError("Write Intent Source Facts must be unique")
        if len({item.memory_id for item in self.memories}) != len(self.memories):
            raise ValueError("Write Intent Memories must be unique")
        if tuple(item.memory_id for item in self.expected_files) != tuple(item.memory_id for item in self.memories):
            raise ValueError("Write Intent expected files must match Memory order")
        if self.operation_id != typed_id("op", memory_commit_payload(self)):
            raise ValueError("Write Intent identity does not match its payload")

    @classmethod
    def create(
        cls,
        *,
        repo_key: str,
        episodes: tuple[TaskEpisode, ...],
        facts: tuple[EvidenceFact, ...],
        memories: tuple[CodingMemory, ...],
        expected_files: tuple[ExpectedMemoryFile, ...],
        checkpoint: CaptureCheckpoint | None,
        created_at_ms: int,
    ) -> PreparedMemoryCommit:
        return cls(
            operation_id=typed_id(
                "op",
                _memory_commit_payload(
                    repo_key=repo_key,
                    episodes=episodes,
                    facts=facts,
                    memories=memories,
                    expected_files=expected_files,
                    checkpoint=checkpoint,
                ),
            ),
            repo_key=repo_key,
            episodes=episodes,
            facts=facts,
            memories=memories,
            expected_files=expected_files,
            checkpoint=checkpoint,
            created_at_ms=created_at_ms,
        )


def memory_commit_payload(commit: PreparedMemoryCommit) -> dict[str, object]:
    return _memory_commit_payload(
        repo_key=commit.repo_key,
        episodes=commit.episodes,
        facts=commit.facts,
        memories=commit.memories,
        expected_files=commit.expected_files,
        checkpoint=commit.checkpoint,
    )


def _memory_commit_payload(
    *,
    repo_key: str,
    episodes: tuple[TaskEpisode, ...],
    facts: tuple[EvidenceFact, ...],
    memories: tuple[CodingMemory, ...],
    expected_files: tuple[ExpectedMemoryFile, ...],
    checkpoint: CaptureCheckpoint | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_kind": "capture" if checkpoint is not None else "direct_memory",
        "repo_key": repo_key,
        "episodes": [_record_to_dict(item) for item in episodes],
        "facts": [_record_to_dict(item) for item in facts],
        "memories": [coding_memory_to_dict(item) for item in memories],
        "expected_files": [{"record_kind": "coding_memory", **_record_to_dict(item)} for item in expected_files],
        "checkpoint": None if checkpoint is None else _record_to_dict(checkpoint),
    }


def memory_commit_from_payload(value: object, *, operation_id: str, created_at_ms: int) -> PreparedMemoryCommit:
    if not isinstance(value, dict):
        raise ValueError("Write Intent payload must be an object")
    required = {"schema_version", "operation_kind", "repo_key", "episodes", "facts", "memories", "expected_files", "checkpoint"}
    if set(value) != required or value["schema_version"] != 1:
        raise ValueError("Write Intent payload fields are invalid")
    if value["operation_kind"] not in {"capture", "direct_memory"}:
        raise ValueError("Write Intent operation kind is invalid")
    episodes = _list(value["episodes"], field="episodes")
    facts = _list(value["facts"], field="facts")
    memories = _list(value["memories"], field="memories")
    expected_files = _list(value["expected_files"], field="expected_files")
    return PreparedMemoryCommit(
        operation_id=operation_id,
        repo_key=_string(value["repo_key"], field="repo_key"),
        episodes=tuple(_record_from_dict(TaskEpisode, item) for item in episodes),
        facts=tuple(_record_from_dict(EvidenceFact, item) for item in facts),
        memories=tuple(_record_from_dict(CodingMemory, item) for item in memories),
        expected_files=tuple(_expected_file_from_dict(item) for item in expected_files),
        checkpoint=None if value["checkpoint"] is None else _record_from_dict(CaptureCheckpoint, value["checkpoint"]),
        created_at_ms=created_at_ms,
    )


def capture_input_fingerprint(memory: CodingMemory) -> str:
    return hashlib.sha256(canonical_json(coding_memory_to_dict(memory)).encode()).hexdigest()


def _expected_file_from_dict(value: object) -> ExpectedMemoryFile:
    if not isinstance(value, dict) or set(value) != {"record_kind", "relative_path", "content_sha256", "memory_id"}:
        raise ValueError("Write Intent expected file is invalid")
    if value["record_kind"] != "coding_memory":
        raise ValueError("Write Intent expected record kind is invalid")
    fields = {key: item for key, item in value.items() if key != "record_kind"}
    return _record_from_dict(ExpectedMemoryFile, fields)


def _list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"Write Intent {field} must be a list")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Write Intent {field} must be a non-empty string")
    return value


def _digest(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
