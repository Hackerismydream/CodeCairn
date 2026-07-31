from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from codecairn.bootstrap import create_application
from codecairn.configuration import initialize_repository, resolve_runtime_config
from codecairn.integrations.pico.backend import CodeCairnPicoBackend, PicoAdapterError, make_backend
from codecairn.memory.schema import TaskExperiencePayload, canonical_json
from codecairn.service.application import RememberRequest
from tests.retrieval_fakes import TEST_RETRIEVAL


@dataclass(frozen=True)
class _PicoMemory:
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(repository), *arguments), check=True, capture_output=True)


def _fixture(tmp_path: Path, *, resolver: Any = None) -> tuple[CodeCairnPicoBackend, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    runtime = tmp_path / "runtime"
    initialize_repository(
        start=repository, root=runtime, repo_key="example/project", retrieval_profile="fastembed", semantic_profile="none"
    )
    workspace = repository / "workspace"
    workspace.mkdir()
    context = SimpleNamespace(config={}, services=SimpleNamespace(workspace=workspace))

    def application_factory(root: Path, **kwargs: Any):
        return create_application(root, retrieval_adapters=TEST_RETRIEVAL, **kwargs)

    backend = CodeCairnPicoBackend(context, application_factory=application_factory, config_resolver=resolver or resolve_runtime_config)
    return backend, repository, runtime


def _install_pico_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    pico = types.ModuleType("pico")
    memory_engine = types.ModuleType("pico.memory_engine")
    memory_engine.Memory = _PicoMemory
    pico.memory_engine = memory_engine
    monkeypatch.setitem(sys.modules, "pico", pico)
    monkeypatch.setitem(sys.modules, "pico.memory_engine", memory_engine)


def _verified_outcome() -> tuple[str, dict[str, Any]]:
    outcome = {
        "schema": "pico.coding-task.outcome.v1",
        "task_id": "openclaw-123",
        "issue_url": "https://github.com/openclaw/openclaw/issues/123",
        "issue_body": "Fix the widget regression.",
        "issue_body_full_sha256": hashlib.sha256(b"Fix the widget regression.").hexdigest(),
        "issue_body_sha256": hashlib.sha256(b"Fix the widget regression.").hexdigest(),
        "status": "verified",
        "repository": "/workspace/openclaw",
        "base_sha": "1" * 40,
        "branch": "fix/widget_regression",
        "candidate_commit_sha": "5" * 40,
        "candidate_tree_sha": "c" * 40,
        "provider": "deepseek",
        "model": "deepseek/deepseek-v4-flash",
        "observed_models": ["deepseek-v4-flash"],
        "runtime_commit": "2" * 40,
        "memory_backend": "codecairn",
        "memory_commit": "3" * 40,
        "skills": [{"id": "contribute-open-source", "sha256": "4" * 64}],
        "head_sha": "5" * 40,
        "changed_files": ["src/widget.py"],
        "file_changes": [{"kind": "file", "operation": "update", "path": "src/widget.py", "sha256": "6" * 64, "size": 12}],
        "verifications": [
            {
                "argv": ["pnpm", "test"],
                "duration_ms": 12,
                "exit_code": 0,
                "stderr_path": "verification/1.stderr",
                "stderr_sha256": "7" * 64,
                "stdout_path": "verification/1.stdout",
                "stdout_sha256": "8" * 64,
            }
        ],
        "diff_sha256": "9" * 64,
        "profile_sha256": "a" * 64,
        "verification_environment": "sandboxed_live_model",
        "final_summary": "Implemented the regression fix and passed the focused tests.",
    }
    digest = hashlib.sha256(canonical_json(outcome).encode()).hexdigest()
    return f"coding-task-outcome:{digest}", outcome


def test_store_then_recall_and_fresh_backend_use_workspace_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pico_memory(monkeypatch)
    backend, repository, runtime = _fixture(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    async def scenario() -> tuple[list[Any], list[Any]]:
        await backend.start()
        await backend.store(
            "pico-session-1",
            [{"role": "user", "content": "Remember that releases require make check."}, {"role": "assistant", "content": "Recorded."}],
        )
        hits = await backend.recall("How are releases checked?", user_id="default", top_k=5)
        assert await backend.recall("cafeteria menu typography unrelated satellite telemetry", user_id="default", top_k=5) == []
        await backend.stop()

        context = SimpleNamespace(config={}, services=SimpleNamespace(workspace=repository / "workspace"))
        fresh = CodeCairnPicoBackend(
            context, application_factory=lambda root, **kwargs: create_application(root, retrieval_adapters=TEST_RETRIEVAL, **kwargs)
        )
        await fresh.start()
        recalled = await fresh.recall("release checks", user_id="another-pico-user", top_k=5)
        await fresh.stop()
        return hits, recalled

    hits, recalled = asyncio.run(scenario())

    assert len(hits) == 1
    assert isinstance(hits[0], _PicoMemory)
    assert hits[0].score == 0.0
    assert hits[0].metadata["repo_key"] == "example/project"
    assert hits[0].metadata["score_semantics"] == "compiled_context_not_ranked"
    assert hits[0].metadata["source_cursor"] == hits[0].metadata["index_cursor"]
    assert hits[0].metadata["rendered_memory_ids"]
    assert hits[0].metadata["source_uris"]
    assert runtime.is_relative_to(tmp_path) and not runtime.is_relative_to(repository)

    assert len(recalled) == 1
    assert recalled[0].metadata["rendered_memory_ids"] == hits[0].metadata["rendered_memory_ids"]


def test_agent_track_feedback_and_invalid_track_behavior(tmp_path: Path) -> None:
    backend, _, _ = _fixture(tmp_path)

    async def scenario() -> None:
        await backend.start()
        assert await backend.recall("query", agent_id="agent", top_k=5) == []
        assert await backend.recall("query", top_k=5) == []
        assert await backend.recall("query", user_id="user", agent_id="agent", top_k=5) == []
        await backend.feedback({"kind": "anything", "secret": "must-not-be-persisted"})
        with pytest.raises(PicoAdapterError, match="top_k"):
            await backend.recall("query", user_id="user", top_k=0)
        await backend.stop()

    asyncio.run(scenario())


def test_assistant_only_subagent_slice_is_an_explicit_no_op(tmp_path: Path) -> None:
    backend, _, runtime = _fixture(tmp_path)

    async def scenario() -> None:
        await backend.start()
        await backend.store("subagent-session", [{"role": "assistant", "content": "Subagent result."}])
        await backend.stop()

    asyncio.run(scenario())
    assert tuple((runtime / "sources" / "pico").glob("*/*.jsonl")) == ()


def test_missing_initialization_and_repository_runtime_fail_closed(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    context = SimpleNamespace(config={}, services=SimpleNamespace(workspace=repository))
    missing = CodeCairnPicoBackend(context)
    with pytest.raises(PicoAdapterError, match="codecairn init"):
        asyncio.run(missing.start())

    initialize_repository(start=repository, root=repository / ".codecairn", repo_key="example/project")
    nested = CodeCairnPicoBackend(context)
    with pytest.raises(PicoAdapterError, match="external runtime root"):
        asyncio.run(nested.start())


def test_start_drains_durable_pending_index_before_readiness_check(tmp_path: Path) -> None:
    backend, _, runtime = _fixture(tmp_path)
    seed = create_application(runtime, repo_key="example/project", retrieval_adapters=TEST_RETRIEVAL)
    seed.remember_direct(
        RememberRequest(
            repo_key="example/project",
            memory_type="repository_knowledge",
            title="Release check",
            content="Releases require make check.",
            subject_key="release-check",
        )
    )
    assert seed.index_status().pending == 1

    asyncio.run(backend.start())

    assert seed.index_status().pending == 0
    asyncio.run(backend.stop())


def test_failed_start_clears_partial_application_state(tmp_path: Path) -> None:
    backend, _, _ = _fixture(tmp_path)

    class _FailingApplication:
        def import_checkpoint(self, *_: Any, **__: Any) -> None:
            return None

        def sync_index(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("injected secret path /tmp/private")

        def index_status(self) -> Any:
            return SimpleNamespace(pending=0, leased=0, failed=0, stale=0)

    backend._application_factory = lambda *_args, **_kwargs: _FailingApplication()

    with pytest.raises(PicoAdapterError) as failure:
        asyncio.run(backend.start())

    assert "secret" not in str(failure.value)
    assert "/tmp/private" not in str(failure.value)
    assert backend._config is None
    assert backend._application is None


def test_blocking_start_is_offloaded_from_event_loop(tmp_path: Path) -> None:
    def slow_resolver(**kwargs: Any):
        time.sleep(0.1)
        return resolve_runtime_config(**kwargs)

    backend, _, _ = _fixture(tmp_path, resolver=slow_resolver)

    async def scenario() -> None:
        start = asyncio.create_task(backend.start())
        await asyncio.sleep(0.02)
        assert start.done() is False
        await start
        await backend.stop()

    asyncio.run(scenario())


def test_lifecycle_store_and_recall_use_thread_offload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pico_memory(monkeypatch)
    backend, _, _ = _fixture(tmp_path)
    original = asyncio.to_thread
    operations: list[str] = []

    async def recording(operation: Any, *args: Any, **kwargs: Any) -> Any:
        operations.append(getattr(operation, "__name__", type(operation).__name__))
        return await original(operation, *args, **kwargs)

    monkeypatch.setattr("codecairn.integrations.pico.backend.asyncio.to_thread", recording)

    async def scenario() -> None:
        await backend.start()
        await backend.store(
            "session", [{"role": "user", "content": "Remember thread offload."}, {"role": "assistant", "content": "Done."}]
        )
        await backend.recall("thread offload", user_id="user", top_k=5)
        await backend.stop()

    asyncio.run(scenario())
    assert len(operations) == 5


def test_factory_rejects_pico_side_overrides_and_resource_import_is_pico_free(tmp_path: Path) -> None:
    context = SimpleNamespace(config={"runtime_root": str(tmp_path)}, services=SimpleNamespace(workspace=tmp_path))
    with pytest.raises(PicoAdapterError, match="remove Pico-side"):
        make_backend(context)

    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith(("COV_CORE", "COVERAGE"))},
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import codecairn, codecairn.memory, codecairn.service, codecairn.integrations.pico;"
            "assert not any(n == 'pico' or n.startswith('pico.') for n in __import__('sys').modules)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_plugin_identity_and_manifest_are_closed() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    manifest = tomllib.loads((root / "src" / "codecairn" / "integrations" / "pico" / "pico-plugin.toml").read_text())["plugin"]

    assert project["project"]["entry-points"]["pico.plugins"] == {"codecairn": "codecairn.integrations.pico"}
    assert manifest["id"] == "codecairn-memory"
    assert manifest["enabled_by_default"] is True
    assert manifest["contributes"] == {
        "memory_backends": [{"name": "codecairn", "factory": "codecairn.integrations.pico.backend:make_backend"}]
    }


def test_tool_shapes_are_normalized_without_inventing_outcome(tmp_path: Path) -> None:
    backend, _, runtime = _fixture(tmp_path)

    async def scenario() -> None:
        await backend.start()
        await backend.store(
            "tool-session",
            [
                {"role": "user", "content": "Inspect the repository."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'}}],
                },
                {"role": "tool", "tool_call_id": "call-1", "name": "shell", "content": "All tests passed."},
                {"role": "assistant", "content": "Done."},
            ],
        )
        await backend.stop()

    asyncio.run(scenario())

    source = next((runtime / "sources" / "pico").glob("*/*.jsonl"))
    batch = json.loads(source.read_text().splitlines()[1])
    assert batch["events"][1] == {"arguments": {"cmd": "pwd"}, "call_id": "call-1", "kind": "tool_call", "tool_name": "shell"}
    assert batch["events"][2] == {"call_id": "call-1", "kind": "tool_result", "text": "All tests passed."}


@pytest.mark.parametrize("observed_model", ("deepseek-v4-flash", "deepseek/deepseek-v4-flash"))
def test_verified_outcome_is_structured_evidence_and_exact_retry_is_idempotent(tmp_path: Path, observed_model: str) -> None:
    backend, _, runtime = _fixture(tmp_path)
    _, outcome = _verified_outcome()
    outcome["observed_models"] = [observed_model]
    key = "coding-task-outcome:" + hashlib.sha256(canonical_json(outcome).encode()).hexdigest()

    async def scenario() -> None:
        await backend.start()
        await backend.store_verified_outcome(key, outcome)
        await backend.store_verified_outcome(key, outcome)
        await backend.stop()

    asyncio.run(scenario())

    source = next((runtime / "sources" / "pico").glob("*/*.jsonl"))
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    events = records[1]["events"]
    assert [event["kind"] for event in events] == ["message", "tool_call", "tool_result", "message"]
    assert events[1]["tool_name"] == "pico_done_gate"
    assert events[2]["status"] == "success"
    assert events[2]["terminal_observation"] == {"exit_code": 0, "file_changes": [{"operation": "update", "path": "src/widget.py"}]}
    assert events[2]["untrusted_payload"] == outcome

    memories = create_application(runtime, repo_key="example/project", retrieval_adapters=TEST_RETRIEVAL).list_memories(
        repo_key="example/project"
    )
    assert len(memories) == 1
    assert isinstance(memories[0].payload, TaskExperiencePayload)
    assert memories[0].payload.outcome == "success"
    file_change_facts = [fact for fact in memories[0].facts if fact.fact_kind == "file_change"]
    assert [(fact.attributes["change_kind"], fact.attributes["path"]) for fact in file_change_facts] == [("update", "src/widget.py")]


def test_verified_outcome_rejects_wrong_digest_failed_checks_and_reserved_store_session(tmp_path: Path) -> None:
    backend, _, _ = _fixture(tmp_path)
    key, outcome = _verified_outcome()

    async def scenario() -> None:
        await backend.start()
        with pytest.raises(PicoAdapterError, match="verified_outcome_invalid"):
            await backend.store_verified_outcome("coding-task-outcome:" + "0" * 64, outcome)
        failed = json.loads(json.dumps(outcome))
        failed["verifications"][0]["exit_code"] = 1
        failed_key = "coding-task-outcome:" + hashlib.sha256(canonical_json(failed).encode()).hexdigest()
        with pytest.raises(PicoAdapterError, match="verified_outcome_invalid"):
            await backend.store_verified_outcome(failed_key, failed)
        mismatched_model = json.loads(json.dumps(outcome))
        mismatched_model["observed_models"] = ["deepseek-v3.2"]
        mismatched_model_key = "coding-task-outcome:" + hashlib.sha256(canonical_json(mismatched_model).encode()).hexdigest()
        with pytest.raises(PicoAdapterError, match="verified_outcome_invalid"):
            await backend.store_verified_outcome(mismatched_model_key, mismatched_model)
        incomplete = {
            "schema": "pico.coding-task.outcome.v1",
            "status": "verified",
            "task_id": "forged",
            "issue_body": "Pretend this passed.",
            "final_summary": "Done.",
            "verifications": [{"exit_code": 0}],
            "file_changes": [],
        }
        incomplete_key = "coding-task-outcome:" + hashlib.sha256(canonical_json(incomplete).encode()).hexdigest()
        with pytest.raises(PicoAdapterError, match="verified_outcome_invalid"):
            await backend.store_verified_outcome(incomplete_key, incomplete)
        with pytest.raises(PicoAdapterError, match="reserved"):
            await backend.store(key, [{"role": "user", "content": "Do not collide."}])
        await backend.stop()

    asyncio.run(scenario())
