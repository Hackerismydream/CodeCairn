from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

import codecairn.bootstrap as bootstrap
import codecairn.entrypoints.hooks as hook_module
from codecairn.configuration import initialize_repository
from codecairn.entrypoints.cli import build_app
from codecairn.entrypoints.hooks import HookClient, install_hook, parse_hook_event
from tests.retrieval_fakes import TEST_RETRIEVAL

ROOT = Path(__file__).parents[1]
HOOKS = ROOT / "tests" / "fixtures" / "hooks"
TRACES = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_hook_home(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CODECAIRN_HOME", str(tmp_path / "home"))


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    initialize_repository(start=repository, root=runtime, repo_key="acme/widgets")
    return repository, runtime


def _envelope(name: str, *, repository: Path, transcript: Path | None) -> bytes:
    payload = json.loads((HOOKS / name).read_text())
    payload["cwd"] = str(repository)
    payload["transcript_path"] = None if transcript is None else str(transcript)
    return json.dumps(payload).encode()


@pytest.mark.parametrize(
    ("client", "envelope", "trace"),
    (("claude", "claude_session_end.json", "claude/failed_command.jsonl"), ("codex", "codex_stop.json", "codex/failed_command.jsonl")),
)
def test_hook_imports_once_across_one_hundred_repeats(
    tmp_path: Path, monkeypatch: Any, client: HookClient, envelope: str, trace: str
) -> None:
    repository, runtime = _repository(tmp_path)
    transcript = tmp_path / f"{client}.jsonl"
    shutil.copyfile(TRACES / trace, transcript)
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda selected: "2.1.220" if selected == "claude" else "0.144.6")
    raw = _envelope(envelope, repository=repository, transcript=transcript)

    receipts = [bootstrap.run_hook(client, raw) for _ in range(100)]

    application = bootstrap.create_application(runtime, repo_key="acme/widgets")
    memories = application.list_memories(repo_key="acme/widgets")
    assert len(memories) == 1
    assert memories[0].memory_type == "task_experience"
    assert receipts[0].outcome == "imported"
    assert receipts[0].duration_ms < 4_000
    assert {receipt.outcome for receipt in receipts[1:]} == {"noop"}
    assert len(application.recent_hook_receipts()) == 20
    assert max(receipt.duration_ms for receipt in receipts[1:]) < 1_000
    recalled = bootstrap.create_application(runtime, repo_key="acme/widgets", retrieval_adapters=TEST_RETRIEVAL).recall(
        "repository test failure", repo_key="acme/widgets"
    )
    assert recalled.sidecar.ranked


def test_nullable_codex_source_uses_one_supported_local_match(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    source = tmp_path / ".codex/sessions/2026/07/28"
    source.mkdir(parents=True)
    transcript = source / "rollout-session-test-001.jsonl"
    transcript.write_text("{}\n")

    event = parse_hook_event(
        "codex", _envelope("codex_stop_nullable.json", repository=repository, transcript=None), client_version="0.144.6", home=tmp_path
    )

    assert event.source_path == transcript.resolve()
    assert event.session_id == "session-test-001"


def test_repeated_codex_stop_imports_only_the_appended_turn(tmp_path: Path, monkeypatch: Any) -> None:
    repository, runtime = _repository(tmp_path)
    transcript = tmp_path / "codex.jsonl"
    shutil.copyfile(TRACES / "codex/failed_command.jsonl", transcript)
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda _client: "0.144.6")
    raw = _envelope("codex_stop.json", repository=repository, transcript=transcript)

    first = bootstrap.run_hook("codex", raw)
    with transcript.open("a") as output:
        output.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run lint next."}]},
                }
            )
            + "\n"
        )
    second = bootstrap.run_hook("codex", raw)

    memories = bootstrap.create_application(runtime).list_memories(repo_key="acme/widgets")
    assert first.outcome == second.outcome == "imported"
    assert len(memories) == 2
    assert len({memory.episode_id for memory in memories}) == 2


@pytest.mark.parametrize(
    "raw",
    (
        b"",
        b"{",
        b"[]",
        b"x" * (64 * 1024 + 1),
        json.dumps({"session_id": "missing", "transcript_path": "/not/owned.jsonl", "cwd": "/tmp", "hook_event_name": "Stop"}).encode(),
    ),
)
def test_hook_run_never_blocks_client_or_writes_stdout(tmp_path: Path, monkeypatch: Any, raw: bytes) -> None:
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda _client: "0.144.6")
    runner = CliRunner()
    app = build_app(bootstrap.create_application, hook_runner=bootstrap.run_hook)

    result = runner.invoke(app, ["hook", "run", "--client", "codex"], input=raw)

    assert result.exit_code == 0
    assert result.stdout == ""


def test_source_inside_runtime_root_is_noop(tmp_path: Path, monkeypatch: Any) -> None:
    repository = tmp_path / "repo"
    runtime = repository / ".codecairn"
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    initialize_repository(start=repository, root=runtime, repo_key="acme/widgets")
    transcript = runtime / "owned.jsonl"
    runtime.mkdir(exist_ok=True)
    shutil.copyfile(TRACES / "codex/failed_command.jsonl", transcript)
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda _client: "0.144.6")
    raw = _envelope("codex_stop.json", repository=runtime, transcript=transcript)

    receipt = bootstrap.run_hook("codex", raw)

    assert receipt.outcome == "noop"
    assert bootstrap.create_application(runtime).list_memories(repo_key="acme/widgets") == ()
    assert repository.is_dir()


def test_uninitialized_repository_is_visible_failure(tmp_path: Path, monkeypatch: Any) -> None:
    transcript = tmp_path / "trace.jsonl"
    transcript.write_text("{}\n")
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda _client: "0.144.6")

    receipt = bootstrap.run_hook("codex", _envelope("codex_stop.json", repository=repository, transcript=transcript))

    assert receipt.outcome == "failed"
    assert receipt.error_code
    assert receipt.retry_command == "codecairn import <owned-session.jsonl>"


def test_storage_failure_is_non_blocking_and_visible(tmp_path: Path, monkeypatch: Any) -> None:
    repository, _runtime = _repository(tmp_path)
    transcript = tmp_path / "trace.jsonl"
    shutil.copyfile(TRACES / "codex/failed_command.jsonl", transcript)
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda _client: "0.144.6")

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("storage unavailable")

    monkeypatch.setattr(bootstrap, "create_application", unavailable)
    receipt = bootstrap.run_hook("codex", _envelope("codex_stop.json", repository=repository, transcript=transcript))

    assert receipt.outcome == "failed"
    assert receipt.error_code == "hook_failed"
    assert receipt.retry_command == "codecairn import <owned-session.jsonl>"


def test_install_preserves_settings_mode_and_is_byte_idempotent(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps({"theme": "dark", "hooks": {"SessionEnd": [{"matcher": "other", "hooks": [{"type": "command", "command": "x"}]}]}})
        + "\n"
    )
    target.chmod(0o640)
    executable = tmp_path / "codecairn"
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")

    first = install_hook(client="claude", target=target, executable=executable, dry_run=False)
    after_first = target.read_bytes()
    second = install_hook(client="claude", target=target, executable=executable, dry_run=False)

    assert first["changed"] is True
    assert second["changed"] is False
    assert target.read_bytes() == after_first
    assert target.stat().st_mode & 0o777 == 0o640
    merged = json.loads(after_first)
    assert merged["theme"] == "dark"
    assert len(merged["hooks"]["SessionEnd"]) == 2


def test_install_dry_run_and_unsupported_client_do_not_write(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "hooks.json"
    target.write_text('{"unrelated":true}\n')
    original = target.read_bytes()
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "0.144.6")

    preview = install_hook(client="codex", target=target, executable=executable, dry_run=True)

    assert preview["changed"] is True
    assert target.read_bytes() == original
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: (_ for _ in ()).throw(ValueError("unsupported_client")))
    with pytest.raises(ValueError, match="unsupported_client"):
        install_hook(client="codex", target=target, executable=executable, dry_run=False)
    assert target.read_bytes() == original


def test_failed_install_readback_restores_original(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"theme":"dark"}\n')
    original = target.read_bytes()
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    hook_os = cast(Any, hook_module).os
    replace = hook_os.replace
    calls = 0

    def corrupt_once(source: str, destination: str | Path) -> None:
        nonlocal calls
        replace(source, destination)
        calls += 1
        if calls == 1:
            target.write_text("{}\n")

    monkeypatch.setattr(hook_os, "replace", corrupt_once)

    with pytest.raises(OSError, match="hook_config_readback_failed"):
        install_hook(client="claude", target=target, executable=executable, dry_run=False)
    assert target.read_bytes() == original


def test_failed_hook_degrades_doctor_with_retry(tmp_path: Path, monkeypatch: Any) -> None:
    repository, root = _repository(tmp_path)
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda _client: "0.144.6")
    receipt = bootstrap.run_hook("codex", _envelope("codex_stop.json", repository=repository, transcript=tmp_path / "missing.jsonl"))
    application = bootstrap.create_application(root, repo_key="acme/widgets")
    doctor = application.doctor()

    assert receipt.outcome == "failed"
    assert receipt.error_code == "source_unavailable"
    assert doctor["status"] == "degraded"
    subsystems = cast(dict[str, dict[str, object]], doctor["subsystems"])
    receipts = cast(dict[str, object], doctor["hook_receipts"])
    assert subsystems["hooks"]["status"] == "degraded"
    assert receipts["latest_retry"] == "codecairn import <owned-session.jsonl>"
