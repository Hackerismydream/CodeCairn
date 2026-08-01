"""Installed CodeCairn CLI evidence collection through public commands only."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import write_bytes_exclusive
from codecairn_v03_acceptance.bounded_process import run_bounded_process

_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}\Z")
_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
_PUBLIC_ENVIRONMENT_KEYS = frozenset(
    {
        "CODECAIRN_EMBEDDING_API_KEY",
        "DASHSCOPE_API_KEY",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)


class CodeCairnAdapterError(RuntimeError):
    """A stable installed-CLI process or public-contract failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class PublicJSONArtifact:
    """One exclusively created copy of exact public CLI stdout."""

    path: Path
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class ListSnapshot:
    """Validated public ``codecairn list`` output."""

    artifact: PublicJSONArtifact
    repo_key: str
    memories: tuple[dict[str, object], ...]

    @property
    def memory_ids(self) -> tuple[str, ...]:
        return tuple(cast(str, memory["memory_id"]) for memory in self.memories)


@dataclass(frozen=True, slots=True)
class RecallReceipt:
    """Public facts needed to prove fresh admission and rendering."""

    artifact: PublicJSONArtifact
    repo_key: str
    query: str
    source_cursor: int
    index_cursor: int
    ranked_memory_ids: tuple[str, ...]
    rendered_memory_ids: tuple[str, ...]
    recalled_memory_ids: tuple[str, ...]
    source_uris: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeCairnPublicCLI:
    """Run an installed CodeCairn console script from an isolated operator cwd."""

    executable: Path
    operator_dir: Path
    config: Path
    runtime_root: Path
    repo_key: str
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        executable = self.executable.resolve()
        operator_dir = self.operator_dir.resolve()
        config = self.config.resolve()
        runtime_root = self.runtime_root.resolve()
        if (
            not executable.is_file()
            or not os.access(executable, os.X_OK)
            or not operator_dir.is_dir()
            or not config.is_file()
            or not runtime_root.is_dir()
        ):
            raise ValueError("CodeCairn executable, operator cwd, config, and runtime root must exist")
        if operator_dir == runtime_root or operator_dir.is_relative_to(runtime_root):
            raise ValueError("CodeCairn operator cwd must remain outside the runtime root")
        _assert_clean_operator(operator_dir)
        if not self.repo_key.strip() or len(self.repo_key.encode("utf-8")) > 512:
            raise ValueError("CodeCairn repository key is invalid")
        if self.timeout_seconds < 1:
            raise ValueError("CodeCairn timeout must be positive")

    @property
    def list_command(self) -> tuple[str, ...]:
        return (
            str(self.executable.resolve()),
            "list",
            "--repo-key",
            self.repo_key,
            "--root",
            str(self.runtime_root.resolve()),
            "--config",
            str(self.config.resolve()),
        )

    def recall_command(self, query: str) -> tuple[str, ...]:
        if not query.strip():
            raise ValueError("CodeCairn recall query must not be empty")
        return (
            str(self.executable.resolve()),
            "recall",
            query,
            "--repo-key",
            self.repo_key,
            "--root",
            str(self.runtime_root.resolve()),
            "--config",
            str(self.config.resolve()),
            "--limit",
            "5",
            "--format",
            "json",
        )

    def environment(self, source: Mapping[str, str]) -> dict[str, str]:
        environment = {key: value for key, value in source.items() if key in _PUBLIC_ENVIRONMENT_KEYS}
        environment.setdefault("LANG", "C.UTF-8")
        environment.update({"PYTHONNOUSERSITE": "1", "PYTHONPATH": ""})
        return environment

    def list_memories(self, *, artifact_path: Path, source_environment: Mapping[str, str] | None = None) -> ListSnapshot:
        """Collect and validate one full public namespace list."""
        value, artifact = self._run_json(self.list_command, artifact_path=artifact_path, source_environment=source_environment)
        if not isinstance(value, list):
            raise CodeCairnAdapterError("list_contract_incompatible", "CodeCairn list output is not a JSON array")
        memories: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw_memory in value:
            if not isinstance(raw_memory, dict):
                raise CodeCairnAdapterError("list_contract_incompatible", "CodeCairn list contains a non-object memory")
            memory = cast(dict[str, object], raw_memory)
            memory_id = _memory_id(memory.get("memory_id"), code="list_contract_incompatible")
            if memory_id in seen:
                raise CodeCairnAdapterError("list_contract_incompatible", "CodeCairn list contains a duplicate memory ID")
            if memory.get("repo_key") != self.repo_key:
                raise CodeCairnAdapterError("list_namespace_mismatch", "CodeCairn list returned a foreign repository namespace")
            seen.add(memory_id)
            memories.append(memory)
        return ListSnapshot(artifact=artifact, repo_key=self.repo_key, memories=tuple(memories))

    def recall(
        self,
        query: str,
        *,
        expected_memory_ids: set[str] | frozenset[str],
        artifact_path: Path,
        source_environment: Mapping[str, str] | None = None,
    ) -> RecallReceipt:
        """Collect public Recall output and prove that an expected memory was used."""
        expected = {_memory_id(value, code="recall_expectation_invalid") for value in expected_memory_ids}
        if not expected:
            raise ValueError("CodeCairn recall requires at least one expected memory ID")
        command = self.recall_command(query)
        value, artifact = self._run_json(command, artifact_path=artifact_path, source_environment=source_environment)
        return _recall_receipt(
            value, artifact=artifact, expected_memory_ids=expected, expected_query=query, expected_repo_key=self.repo_key
        )

    def _run_json(
        self, command: tuple[str, ...], *, artifact_path: Path, source_environment: Mapping[str, str] | None
    ) -> tuple[object, PublicJSONArtifact]:
        operator_dir = self.operator_dir.resolve()
        _assert_clean_operator(operator_dir)
        resolved_artifact = artifact_path.resolve()
        if resolved_artifact == operator_dir or resolved_artifact.is_relative_to(operator_dir):
            raise ValueError("CodeCairn evidence artifacts must remain outside the clean operator cwd")
        if resolved_artifact.exists():
            raise FileExistsError(f"CodeCairn evidence artifact already exists: {resolved_artifact}")
        try:
            result = run_bounded_process(
                command,
                cwd=operator_dir,
                environment=self.environment(os.environ if source_environment is None else source_environment),
                timeout_seconds=self.timeout_seconds,
                stdout_limit=_MAX_OUTPUT_BYTES,
                stderr_limit=_MAX_OUTPUT_BYTES,
            )
        except OSError as error:
            raise CodeCairnAdapterError("codecairn_process_failed", "Installed CodeCairn CLI could not start") from error
        if result.terminal == "timeout":
            raise CodeCairnAdapterError("codecairn_timeout", "Installed CodeCairn CLI timed out")
        if result.terminal in {"stdout_limit", "stderr_limit"}:
            raise CodeCairnAdapterError("codecairn_output_invalid", "Installed CodeCairn CLI output size is invalid")
        if result.terminal != "exited" or result.exit_code != 0:
            raise CodeCairnAdapterError("codecairn_process_failed", f"Installed CodeCairn CLI exited with {result.exit_code}")
        raw = result.stdout
        if not raw:
            raise CodeCairnAdapterError("codecairn_output_invalid", "Installed CodeCairn CLI output size is invalid")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CodeCairnAdapterError("codecairn_output_invalid", "Installed CodeCairn CLI returned non-JSON output") from error
        write_bytes_exclusive(resolved_artifact, raw)
        artifact = PublicJSONArtifact(path=resolved_artifact, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        return value, artifact


def derive_new_pico_task_experience_ids(
    before: ListSnapshot, after: ListSnapshot, *, repo_key: str, learn_session_id: str
) -> tuple[str, ...]:
    """Find new capture-derived Task Experiences owned by one exact Pico turn."""
    if before.repo_key != repo_key or after.repo_key != repo_key or not learn_session_id or len(learn_session_id.encode("utf-8")) > 256:
        raise CodeCairnAdapterError("capture_identity_invalid", "List snapshots or Pico learn session do not match")
    before_ids = set(before.memory_ids)
    captured: list[str] = []
    for memory in after.memories:
        memory_id = cast(str, memory["memory_id"])
        if memory_id in before_ids or memory.get("memory_type") != "task_experience" or memory.get("origin") != "capture":
            continue
        evidence = memory.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            continue
        if all(
            isinstance(reference, dict) and reference.get("provider") == "pico" and reference.get("session_id") == learn_session_id
            for reference in evidence
        ):
            captured.append(memory_id)
    if not captured:
        raise CodeCairnAdapterError(
            "capture_not_observed", "No new capture-derived Pico Task Experience belongs to the exact learn session"
        )
    return tuple(sorted(captured))


def _recall_receipt(
    value: object, *, artifact: PublicJSONArtifact, expected_memory_ids: set[str], expected_query: str, expected_repo_key: str
) -> RecallReceipt:
    if not isinstance(value, dict) or not isinstance(value.get("sidecar"), dict):
        raise CodeCairnAdapterError("recall_contract_incompatible", "CodeCairn Recall output has no sidecar")
    sidecar = cast(dict[str, object], value["sidecar"])
    source_cursor = sidecar.get("source_cursor")
    index_cursor = sidecar.get("index_cursor")
    admission = sidecar.get("admission_trace")
    context = sidecar.get("context_trace")
    ranked = sidecar.get("ranked")
    if (
        sidecar.get("query") != expected_query
        or sidecar.get("repo_key") != expected_repo_key
        or sidecar.get("limit") != 5
        or sidecar.get("include_superseded") is not False
        or sidecar.get("freshness") != "fresh"
        or not isinstance(source_cursor, int)
        or isinstance(source_cursor, bool)
        or source_cursor < 0
        or not isinstance(index_cursor, int)
        or isinstance(index_cursor, bool)
        or index_cursor != source_cursor
        or not isinstance(admission, dict)
        or admission.get("outcome") != "admitted"
        or not isinstance(context, dict)
        or not isinstance(ranked, list)
        or not ranked
    ):
        raise CodeCairnAdapterError("recall_evidence_invalid", "CodeCairn Recall is not fresh, admitted, or cursor-complete")
    rendered_raw = context.get("rendered_memory_ids")
    if not isinstance(rendered_raw, list) or not rendered_raw:
        raise CodeCairnAdapterError("recall_evidence_invalid", "CodeCairn Recall rendered no memory IDs")
    rendered = tuple(_memory_id(memory_id, code="recall_contract_incompatible") for memory_id in rendered_raw)
    if len(rendered) != len(set(rendered)):
        raise CodeCairnAdapterError("recall_contract_incompatible", "CodeCairn Recall rendered duplicate memory IDs")
    ranked_ids: list[str] = []
    source_uris: list[str] = []
    for raw_ranked in ranked:
        if not isinstance(raw_ranked, dict):
            raise CodeCairnAdapterError("recall_contract_incompatible", "CodeCairn ranked evidence contains a non-object")
        memory_id = _memory_id(raw_ranked.get("memory_id"), code="recall_contract_incompatible")
        source_uri = raw_ranked.get("source_uri")
        if source_uri != f"codecairn://memory/{memory_id}":
            raise CodeCairnAdapterError("recall_evidence_invalid", "CodeCairn ranked memory has a mismatched source URI")
        ranked_ids.append(memory_id)
        source_uris.append(cast(str, source_uri))
    if len(ranked_ids) != len(set(ranked_ids)) or not set(rendered).issubset(ranked_ids):
        raise CodeCairnAdapterError("recall_evidence_invalid", "Rendered and ranked memory identities are inconsistent")
    recalled = tuple(sorted(expected_memory_ids & set(rendered) & set(ranked_ids)))
    if not recalled:
        raise CodeCairnAdapterError(
            "expected_memory_not_recalled", "No expected memory appears in both ranked and rendered public Recall evidence"
        )
    return RecallReceipt(
        artifact=artifact,
        repo_key=expected_repo_key,
        query=expected_query,
        source_cursor=source_cursor,
        index_cursor=source_cursor,
        ranked_memory_ids=tuple(ranked_ids),
        rendered_memory_ids=rendered,
        recalled_memory_ids=recalled,
        source_uris=tuple(source_uris),
    )


def _memory_id(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _MEMORY_ID.fullmatch(value) is None:
        raise CodeCairnAdapterError(code, "Memory identity is invalid")
    return value


def _assert_clean_operator(operator_dir: Path) -> None:
    if any(entry.name != ".git" for entry in operator_dir.iterdir()):
        raise ValueError("CodeCairn operator cwd must be empty except for Git discovery state")
