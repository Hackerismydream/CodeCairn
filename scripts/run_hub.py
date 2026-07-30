#!/usr/bin/env python3
"""Run the local Hub web app and loopback adapter as one foreground process."""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import suppress
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ShutdownRequested(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(f"Hub shutdown requested by signal {signum}")


def install_shutdown_handlers() -> dict[int, signal.Handlers]:
    previous: dict[int, signal.Handlers] = {}

    def request_shutdown(signum: int, _frame: object) -> None:
        raise ShutdownRequested(signum)

    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, request_shutdown)
    return previous


def restore_signal_handlers(previous: dict[int, signal.Handlers]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def available_port(preferred: int) -> int:
    candidates = (preferred, 0) if preferred else (0,)
    for candidate in candidates:
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", candidate))
                return int(listener.getsockname()[1])
        except OSError:
            if candidate == 0:
                raise
    raise RuntimeError("Could not reserve a loopback port")


def wait_for(url: str, *, headers: dict[str, str] | None = None, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status < 500:
                    return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {url}")


def terminate(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--api-port", type=int, default=0)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--production", action="store_true")
    arguments = parser.parse_args()

    api_port = available_port(arguments.api_port)
    web_port = available_port(arguments.web_port)
    token = secrets.token_urlsafe(32)
    environment = {**os.environ, "CODECAIRN_HUB_API_URL": f"http://127.0.0.1:{api_port}", "CODECAIRN_HUB_TOKEN": token}
    processes: list[subprocess.Popen[bytes]] = []
    previous_handlers = install_shutdown_handlers()
    try:
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "codecairn_hub_api.cli",
                "--repository",
                str(arguments.repository.resolve()),
                "--port",
                str(api_port),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            start_new_session=True,
        )
        processes.append(api)
        wait_for(f"http://127.0.0.1:{api_port}/hub-read/v1/system", headers={"x-codecairn-hub-token": token})

        command = "start" if arguments.production else "dev"
        web = subprocess.Popen(
            ["npm", "run", command, "--workspace", "@codecairn/hub-web", "--", "--hostname", "127.0.0.1", "--port", str(web_port)],
            cwd=REPOSITORY_ROOT,
            env=environment,
            start_new_session=True,
        )
        processes.append(web)
        wait_for(f"http://127.0.0.1:{web_port}")
        print(f"CodeCairn Hub: http://127.0.0.1:{web_port}", flush=True)

        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next((process.returncode or 1 for process in processes if process.poll() is not None), 1)
    except KeyboardInterrupt:
        return 130
    except ShutdownRequested as shutdown:
        return 128 + shutdown.signum
    finally:
        terminate(processes)
        restore_signal_handlers(previous_handlers)


if __name__ == "__main__":
    raise SystemExit(main())
