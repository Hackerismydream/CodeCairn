from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

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
