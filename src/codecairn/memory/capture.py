"""Durable capture operation records used by Write Intent recovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from codecairn.memory.models import ImportCheckpoint
from codecairn.memory.schema import (
    CodingMemory,
    EvidenceFact,
    Provider,
    TaskEpisode,
    canonical_json,
    coding_memory_from_dict,
    coding_memory_to_dict,
    evidence_fact_from_dict,
    evidence_fact_to_dict,
    task_episode_from_dict,
    task_episode_to_dict,
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
class PreparedCapture:
    operation_id: str
    repo_key: str
    episodes: tuple[TaskEpisode, ...]
    facts: tuple[EvidenceFact, ...]
    memories: tuple[CodingMemory, ...]
    expected_files: tuple[ExpectedMemoryFile, ...]
    checkpoint: CaptureCheckpoint
    created_at_ms: int

    def __post_init__(self) -> None:
        if self.created_at_ms < 0:
            raise ValueError("Write Intent created_at_ms must not be negative")
        if self.repo_key != self.checkpoint.repo_key:
            raise ValueError("Write Intent namespace does not match its checkpoint")
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
        if tuple(item.memory_id for item in self.expected_files) != tuple(
            item.memory_id for item in self.memories
        ):
            raise ValueError("Write Intent expected files must match Memory order")
        expected_id = typed_id("op", prepared_capture_payload(self))
        if self.operation_id != expected_id:
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
        checkpoint: CaptureCheckpoint,
        created_at_ms: int,
    ) -> PreparedCapture:
        operation_id = typed_id(
            "op",
            _capture_payload(
                repo_key=repo_key,
                episodes=episodes,
                facts=facts,
                memories=memories,
                expected_files=expected_files,
                checkpoint=checkpoint,
            ),
        )
        return cls(
            operation_id=operation_id,
            repo_key=repo_key,
            episodes=episodes,
            facts=facts,
            memories=memories,
            expected_files=expected_files,
            checkpoint=checkpoint,
            created_at_ms=created_at_ms,
        )


def prepared_capture_payload(capture: PreparedCapture) -> dict[str, object]:
    return _capture_payload(
        repo_key=capture.repo_key,
        episodes=capture.episodes,
        facts=capture.facts,
        memories=capture.memories,
        expected_files=capture.expected_files,
        checkpoint=capture.checkpoint,
    )


def _capture_payload(
    *,
    repo_key: str,
    episodes: tuple[TaskEpisode, ...],
    facts: tuple[EvidenceFact, ...],
    memories: tuple[CodingMemory, ...],
    expected_files: tuple[ExpectedMemoryFile, ...],
    checkpoint: CaptureCheckpoint,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_kind": "capture",
        "repo_key": repo_key,
        "episodes": [task_episode_to_dict(item) for item in episodes],
        "facts": [evidence_fact_to_dict(item) for item in facts],
        "memories": [coding_memory_to_dict(item) for item in memories],
        "expected_files": [
            {
                "record_kind": "coding_memory",
                "relative_path": item.relative_path,
                "content_sha256": item.content_sha256,
                "memory_id": item.memory_id,
            }
            for item in expected_files
        ],
        "checkpoint": _checkpoint_to_dict(checkpoint),
    }


def prepared_capture_from_payload(
    value: object,
    *,
    operation_id: str,
    created_at_ms: int,
) -> PreparedCapture:
    if not isinstance(value, dict):
        raise ValueError("Write Intent payload must be an object")
    required = {
        "schema_version",
        "operation_kind",
        "repo_key",
        "episodes",
        "facts",
        "memories",
        "expected_files",
        "checkpoint",
    }
    if set(value) != required or value["schema_version"] != 1:
        raise ValueError("Write Intent payload fields are invalid")
    if value["operation_kind"] != "capture":
        raise ValueError("Write Intent operation kind is invalid")
    episodes = _object_list(value["episodes"], field="episodes")
    facts = _object_list(value["facts"], field="facts")
    memories = _object_list(value["memories"], field="memories")
    expected_files = _object_list(value["expected_files"], field="expected_files")
    capture = PreparedCapture(
        operation_id=operation_id,
        repo_key=_string(value["repo_key"], field="repo_key"),
        episodes=tuple(task_episode_from_dict(item) for item in episodes),
        facts=tuple(evidence_fact_from_dict(item) for item in facts),
        memories=tuple(coding_memory_from_dict(item) for item in memories),
        expected_files=tuple(_expected_file_from_dict(item) for item in expected_files),
        checkpoint=_checkpoint_from_dict(value["checkpoint"]),
        created_at_ms=created_at_ms,
    )
    return capture


def capture_input_fingerprint(memory: CodingMemory) -> str:
    return hashlib.sha256(canonical_json(coding_memory_to_dict(memory)).encode()).hexdigest()


def _checkpoint_to_dict(checkpoint: CaptureCheckpoint) -> dict[str, object]:
    resume = checkpoint.resume
    return {
        "repo_key": checkpoint.repo_key,
        "provider": checkpoint.provider,
        "session_id": checkpoint.session_id,
        "source_path": checkpoint.source_path,
        "source_sha256": checkpoint.source_sha256,
        "raw_event_count": checkpoint.raw_event_count,
        "committed_raw_event_index": checkpoint.committed_raw_event_index,
        "resume_raw_event_index": resume.resume_raw_event_index,
        "resume_prefix_sha256": resume.resume_prefix_sha256,
        "resume_call_ids": list(resume.resume_call_ids),
        "resume_file_change_fact_count": resume.resume_file_change_fact_count,
        "prior_source_cursor": checkpoint.prior_source_cursor,
    }


def _checkpoint_from_dict(value: object) -> CaptureCheckpoint:
    if not isinstance(value, dict):
        raise ValueError("Write Intent checkpoint must be an object")
    required = {
        "repo_key",
        "provider",
        "session_id",
        "source_path",
        "source_sha256",
        "raw_event_count",
        "committed_raw_event_index",
        "resume_raw_event_index",
        "resume_prefix_sha256",
        "resume_call_ids",
        "resume_file_change_fact_count",
        "prior_source_cursor",
    }
    if set(value) != required:
        raise ValueError("Write Intent checkpoint fields are invalid")
    provider = _string(value["provider"], field="provider")
    if provider not in {"codex", "claude"}:
        raise ValueError("Write Intent provider is invalid")
    session_id = _string(value["session_id"], field="session_id")
    committed = _integer(value["committed_raw_event_index"], field="committed cursor")
    resume = ImportCheckpoint(
        provider=provider,
        session_id=session_id,
        committed_raw_event_index=committed,
        resume_raw_event_index=_integer(value["resume_raw_event_index"], field="resume cursor"),
        resume_prefix_sha256=_string(value["resume_prefix_sha256"], field="resume prefix"),
        resume_call_ids=tuple(
            _string(item, field="resume call ID")
            for item in _list(value["resume_call_ids"], field="resume_call_ids")
        ),
        resume_file_change_fact_count=_integer(
            value["resume_file_change_fact_count"],
            field="resume file-change count",
        ),
    )
    return CaptureCheckpoint(
        repo_key=_string(value["repo_key"], field="repo_key"),
        provider=cast(Provider, provider),
        session_id=session_id,
        source_path=_string(value["source_path"], field="source_path"),
        source_sha256=_string(value["source_sha256"], field="source_sha256"),
        raw_event_count=_integer(value["raw_event_count"], field="raw_event_count"),
        committed_raw_event_index=committed,
        resume=resume,
        prior_source_cursor=_integer(value["prior_source_cursor"], field="prior cursor"),
    )


def _expected_file_from_dict(value: object) -> ExpectedMemoryFile:
    if not isinstance(value, dict) or set(value) != {
        "record_kind",
        "relative_path",
        "content_sha256",
        "memory_id",
    }:
        raise ValueError("Write Intent expected file is invalid")
    if value["record_kind"] != "coding_memory":
        raise ValueError("Write Intent expected record kind is invalid")
    return ExpectedMemoryFile(
        relative_path=_string(value["relative_path"], field="relative_path"),
        content_sha256=_string(value["content_sha256"], field="content_sha256"),
        memory_id=_string(value["memory_id"], field="memory_id"),
    )


def _object_list(value: object, *, field: str) -> list[dict[str, object]]:
    items = _list(value, field=field)
    if not all(isinstance(item, dict) for item in items):
        raise ValueError(f"Write Intent {field} must contain objects")
    return cast(list[dict[str, object]], items)


def _list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"Write Intent {field} must be a list")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Write Intent {field} must be a non-empty string")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Write Intent {field} must be an integer")
    return value


def _digest(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
