from __future__ import annotations

import json
import os
import shutil
import stat
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


def _owned_transcript(tmp_path: Path, *, client: HookClient, fixture: str, repository: Path) -> Path:
    relative = ".codex/sessions/2026/08/01" if client == "codex" else ".claude/projects/current"
    transcript = tmp_path / "home" / relative / f"{client}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in (TRACES / fixture).read_text().splitlines()]
    for record in records:
        if client == "claude":
            record["cwd"] = str(repository)
        elif record.get("type") == "session_meta":
            record["payload"]["cwd"] = str(repository)
    transcript.write_text("".join(f"{json.dumps(record)}\n" for record in records))
    return transcript


@pytest.mark.parametrize(
    ("client", "envelope", "trace"),
    (("claude", "claude_session_end.json", "claude/failed_command.jsonl"), ("codex", "codex_stop.json", "codex/failed_command.jsonl")),
)
def test_hook_imports_once_across_one_hundred_repeats(
    tmp_path: Path, monkeypatch: Any, client: HookClient, envelope: str, trace: str
) -> None:
    repository, runtime = _repository(tmp_path)
    transcript = _owned_transcript(tmp_path, client=client, fixture=trace, repository=repository)
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
    transcript = _owned_transcript(tmp_path, client="codex", fixture="codex/failed_command.jsonl", repository=repository)
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


@pytest.mark.parametrize("client", ("codex", "claude"))
def test_hook_rejects_mixed_repository_cwds(tmp_path: Path, monkeypatch: Any, client: HookClient) -> None:
    repository, runtime = _repository(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    subprocess.run(("git", "init", str(foreign)), check=True, capture_output=True)
    transcript = _owned_transcript(tmp_path, client=client, fixture=f"{client}/failed_command.jsonl", repository=repository)
    with transcript.open("a") as output:
        record = (
            {"type": "turn_context", "payload": {"cwd": str(foreign)}}
            if client == "codex"
            else {
                "type": "assistant",
                "sessionId": "claude-session-test-001",
                "cwd": str(foreign),
                "message": {"role": "assistant", "content": "foreign"},
            }
        )
        output.write(json.dumps(record) + "\n")
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda selected: "2.1.220" if selected == "claude" else "0.144.6")

    receipt = bootstrap.run_hook(
        client,
        _envelope("claude_session_end.json" if client == "claude" else "codex_stop.json", repository=repository, transcript=transcript),
    )

    assert receipt.outcome == "failed"
    assert receipt.error_code == "source_unavailable"
    assert bootstrap.create_application(runtime).list_memories(repo_key="acme/widgets") == ()


@pytest.mark.parametrize("client", ("codex", "claude"))
@pytest.mark.parametrize("escape", ("outside", "symlinked_parent"))
def test_hook_rejects_history_outside_the_fixed_client_root(tmp_path: Path, monkeypatch: Any, client: HookClient, escape: str) -> None:
    repository, runtime = _repository(tmp_path)
    transcript = _owned_transcript(tmp_path, client=client, fixture=f"{client}/failed_command.jsonl", repository=repository)
    if escape == "outside":
        outside = tmp_path / "outside.jsonl"
        transcript.replace(outside)
        transcript = outside
    else:
        client_dir = tmp_path / "home" / f".{client}"
        outside = tmp_path / "outside-client-dir"
        client_dir.rename(outside)
        client_dir.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(bootstrap, "detect_client_version", lambda selected: "2.1.220" if selected == "claude" else "0.144.6")

    receipt = bootstrap.run_hook(
        client,
        _envelope("claude_session_end.json" if client == "claude" else "codex_stop.json", repository=repository, transcript=transcript),
    )

    assert receipt.outcome == "failed"
    assert receipt.error_code == "source_unavailable"
    assert bootstrap.create_application(runtime).list_memories(repo_key="acme/widgets") == ()


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
    transcript = _owned_transcript(tmp_path, client="codex", fixture="codex/failed_command.jsonl", repository=repository)
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


def test_install_rejects_a_settings_fifo_without_blocking(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    os.mkfifo(target)
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")

    with pytest.raises(ValueError, match="hook_config_invalid"):
        install_hook(client="claude", target=target, executable=executable, dry_run=True)

    assert stat.S_ISFIFO(target.lstat().st_mode)


def test_capture_preview_fails_closed_when_parent_is_absent_or_becomes_a_symlink(tmp_path: Path, monkeypatch: Any) -> None:
    client_dir = tmp_path / ".claude"
    outside = tmp_path / "outside"
    outside.mkdir()
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    adapter = hook_module.LocalHookCaptureAdapter(client="claude", target=client_dir / "settings.json", executable=executable)

    with pytest.raises(ValueError, match="hook_config_invalid"):
        adapter.inspect()
    client_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="hook_config_invalid"):
        adapter.inspect()

    assert tuple(outside.iterdir()) == ()


def test_capture_apply_rejects_replaced_parent_identity_without_writing_new_directory(tmp_path: Path, monkeypatch: Any) -> None:
    client_dir = tmp_path / ".claude"
    client_dir.mkdir()
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    adapter = hook_module.LocalHookCaptureAdapter(client="claude", target=client_dir / "settings.json", executable=executable)
    plan = adapter.inspect()
    client_dir.rename(tmp_path / "original-client-dir")
    client_dir.mkdir()

    with pytest.raises(ValueError, match="hook_preview_stale"):
        adapter.apply(plan)

    assert tuple(client_dir.iterdir()) == ()
    assert tuple((tmp_path / "original-client-dir").iterdir()) == ()


@pytest.mark.parametrize("invalid_shape", ("type", "timeout", "matcher"))
def test_install_requires_the_complete_unmatched_command_handler_shape(tmp_path: Path, monkeypatch: Any, invalid_shape: str) -> None:
    target = tmp_path / "settings.json"
    executable = tmp_path / "codecairn"
    executable.write_text("")
    command = f"{executable.resolve()} hook run --client claude"
    inner: dict[str, object] = {"type": "command", "command": command, "timeout": 5}
    entry: dict[str, object] = {"hooks": [inner]}
    if invalid_shape == "type":
        inner["type"] = "prompt"
    elif invalid_shape == "timeout":
        inner["timeout"] = 0
    else:
        entry["matcher"] = "other"
    target.write_text(json.dumps({"hooks": {"SessionEnd": [entry]}}))
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")

    preview = install_hook(client="claude", target=target, executable=executable, dry_run=True)

    merged = cast(dict[str, Any], preview["merged"])
    assert preview["changed"] is True
    assert len(merged["hooks"]["SessionEnd"]) == 2


def test_existing_install_fails_closed_when_atomic_exchange_is_unavailable(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"theme":"dark"}\n')
    original = target.read_bytes()
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    monkeypatch.setattr(
        hook_module, "_rename_swap", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("atomic exchange unavailable"))
    )

    with pytest.raises(OSError, match="atomic exchange unavailable"):
        install_hook(client="claude", target=target, executable=executable, dry_run=False)
    assert target.read_bytes() == original


def test_pre_exchange_conflict_preserves_concurrent_content(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"theme":"dark"}\n')
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    exchange = hook_module._rename_swap
    calls = 0

    def overwrite_before_exchange(source: str, destination: str, *, directory_fd: int) -> None:
        nonlocal calls
        if calls == 0:
            target.write_text('{"external":true}\n')
        calls += 1
        exchange(source, destination, directory_fd=directory_fd)

    monkeypatch.setattr(hook_module, "_rename_swap", overwrite_before_exchange)

    with pytest.raises(ValueError, match="hook_preview_stale"):
        install_hook(client="claude", target=target, executable=executable, dry_run=False)
    assert json.loads(target.read_text()) == {"external": True}


def test_post_exchange_conflict_is_not_rolled_back_over_external_content(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"theme":"dark"}\n')
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    exchange = hook_module._rename_swap
    calls = 0

    def overwrite_after_exchange(source: str, destination: str, *, directory_fd: int) -> None:
        nonlocal calls
        exchange(source, destination, directory_fd=directory_fd)
        calls += 1
        if calls == 1:
            target.write_text('{"external":true}\n')

    monkeypatch.setattr(hook_module, "_rename_swap", overwrite_after_exchange)

    with pytest.raises(ValueError, match="hook_preview_stale"):
        install_hook(client="claude", target=target, executable=executable, dry_run=False)
    assert json.loads(target.read_text()) == {"external": True}


def test_absent_target_link_race_preserves_external_symlink_content(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    outside = tmp_path / "outside.json"
    outside.write_text('{"external":true}\n')
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    link = hook_module.os.link

    def replace_after_link(*args, **kwargs) -> None:
        link(*args, **kwargs)
        target.unlink()
        target.symlink_to(outside)

    monkeypatch.setattr(hook_module.os, "link", replace_after_link)

    with pytest.raises(ValueError, match="hook_preview_stale"):
        install_hook(client="claude", target=target, executable=executable, dry_run=False)
    assert target.is_symlink()
    assert outside.read_text() == '{"external":true}\n'


def test_symlink_swapped_in_after_exchange_is_not_followed(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"theme":"dark"}\n')
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":true}\n')
    executable = tmp_path / "codecairn"
    executable.write_text("")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "2.1.220")
    exchange = hook_module._rename_swap
    calls = 0

    def swap_once(source: str, destination: str, *, directory_fd: int) -> None:
        nonlocal calls
        exchange(source, destination, directory_fd=directory_fd)
        calls += 1
        if calls == 1:
            target.unlink()
            target.symlink_to(outside)

    monkeypatch.setattr(hook_module, "_rename_swap", swap_once)

    with pytest.raises(ValueError, match="hook_preview_stale"):
        install_hook(client="claude", target=target, executable=executable, dry_run=False)
    assert target.is_symlink()
    assert outside.read_text() == '{"secret":true}\n'


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
