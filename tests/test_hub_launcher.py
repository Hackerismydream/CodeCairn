from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import run_hub
from scripts.run_hub import available_port


def test_available_port_falls_back_when_the_preferred_port_is_busy() -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        preferred = int(occupied.getsockname()[1])

        selected = available_port(preferred)

    assert selected > 0
    assert selected != preferred


def test_available_port_selects_an_ephemeral_loopback_port() -> None:
    assert available_port(0) > 0


def test_cli_rejects_a_busy_explicit_port_without_starting_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_popen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("The launcher must reject the port before starting children")

    monkeypatch.setattr(run_hub.subprocess, "Popen", unexpected_popen)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        requested = int(occupied.getsockname()[1])

        with pytest.raises(SystemExit) as raised:
            run_hub.main(["--web-port", str(requested), "--ready-file", str(tmp_path / "ready.json")])

    assert raised.value.code == 2
    assert f"Requested loopback port {requested} is unavailable" in capsys.readouterr().err


def test_cli_rejects_the_same_explicit_port_for_api_and_web(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requested = available_port(0)

    def unexpected_popen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("The launcher must reject duplicate ports before starting children")

    monkeypatch.setattr(run_hub.subprocess, "Popen", unexpected_popen)

    with pytest.raises(SystemExit) as raised:
        run_hub.main(["--api-port", str(requested), "--web-port", str(requested)])

    assert raised.value.code == 2
    assert "API and Web ports must be distinct" in capsys.readouterr().err


def test_ready_receipt_is_exclusive_private_and_contains_only_public_launcher_state(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "hub-ready.json"

    run_hub.write_ready_receipt(target, api_port=40101, web_port=40102, child_process_groups={"api": 51001, "web": 51002})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "api_port": 40101,
        "child_process_groups": {"api": 51001, "web": 51002},
        "contract": "codecairn.hub.launcher-ready.v1",
        "launcher_pid": os.getpid(),
        "schema_version": 1,
        "web_origin": "http://127.0.0.1:40102",
        "web_port": 40102,
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        run_hub.write_ready_receipt(target, api_port=49901, web_port=49902, child_process_groups={"api": 59001, "web": 59002})

    assert json.loads(target.read_text(encoding="utf-8"))["web_port"] == 40102


def test_wait_for_requires_an_exact_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status = status_code

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    statuses = iter((403, 503, 200))
    observed: list[int] = []

    def fake_urlopen(_request: object, *, timeout: float) -> FakeResponse:
        assert timeout == 1
        status_code = next(statuses)
        observed.append(status_code)
        return FakeResponse(status_code)

    monkeypatch.setattr(run_hub.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(run_hub.time, "sleep", lambda _seconds: None)

    run_hub.wait_for("http://127.0.0.1:40102/health", timeout=1)

    assert observed == [403, 503, 200]


def test_launcher_publishes_ready_receipt_only_after_both_services_are_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.returncode = 17

        def poll(self) -> int:
            return self.returncode

    started: list[FakeProcess] = []
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> FakeProcess:
        commands.append(command)
        process = FakeProcess(61001 + len(started))
        started.append(process)
        return process

    readiness_checks: list[str] = []
    original_writer = run_hub.write_ready_receipt

    def observing_writer(path: Path, *, api_port: int, web_port: int, child_process_groups: dict[str, int]) -> None:
        assert len(readiness_checks) == 2
        original_writer(path, api_port=api_port, web_port=web_port, child_process_groups=child_process_groups)

    monkeypatch.setattr(run_hub.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_hub, "wait_for", lambda url, **_kwargs: readiness_checks.append(url))
    monkeypatch.setattr(run_hub, "write_ready_receipt", observing_writer)
    monkeypatch.setattr(run_hub, "install_shutdown_handlers", lambda: {})
    monkeypatch.setattr(run_hub, "restore_signal_handlers", lambda _handlers: None)
    monkeypatch.setattr(run_hub, "terminate", lambda _processes: None)
    api_port = available_port(0)
    web_port = available_port(0)
    while web_port == api_port:
        web_port = available_port(0)
    target = tmp_path / "ready.json"

    result = run_hub.main(["--api-port", str(api_port), "--web-port", str(web_port), "--ready-file", str(target)])

    assert result == 17
    assert commands[0][:4] == [sys.executable, "-I", "-m", "codecairn_hub_api.cli"]
    assert readiness_checks == [
        f"http://127.0.0.1:{api_port}/hub-read/v1/system",
        f"http://127.0.0.1:{web_port}/api/hub-read/v1/system",
    ]
    assert json.loads(target.read_text(encoding="utf-8"))["child_process_groups"] == {"api": 61001, "web": 61002}


def test_launcher_rejects_an_existing_ready_file_before_starting_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "ready.json"
    target.write_text("do not replace", encoding="utf-8")

    def unexpected_popen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("The launcher must reject an existing receipt before starting children")

    monkeypatch.setattr(run_hub.subprocess, "Popen", unexpected_popen)

    with pytest.raises(SystemExit) as raised:
        run_hub.main(["--ready-file", str(target)])

    assert raised.value.code == 2
    assert "already exists" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "do not replace"


def test_sigterm_handler_reaps_a_child_process_group() -> None:
    runner_code = """
import subprocess
import sys
import time
from scripts.run_hub import ShutdownRequested, install_shutdown_handlers, terminate

processes = []
install_shutdown_handlers()
try:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print(grandchild.pid, flush=True); "
            "time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    processes.append(child)
    assert child.stdout is not None
    print(f"{child.pid} {child.stdout.readline().strip()}", flush=True)
    while True:
        time.sleep(1)
except ShutdownRequested as shutdown:
    exit_code = 128 + shutdown.signum
finally:
    terminate(processes)
raise SystemExit(exit_code)
"""
    runner = subprocess.Popen([sys.executable, "-c", runner_code], cwd=os.getcwd(), stdout=subprocess.PIPE, text=True)
    assert runner.stdout is not None
    child_pid, grandchild_pid = (int(value) for value in runner.stdout.readline().split())

    runner.send_signal(signal.SIGTERM)

    assert runner.wait(timeout=10) == 128 + signal.SIGTERM
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (_process_exists(child_pid) or _process_exists(grandchild_pid)):
        time.sleep(0.05)
    assert not _process_exists(child_pid)
    assert not _process_exists(grandchild_pid)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
