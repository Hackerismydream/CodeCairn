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
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from codecairn.evaluation.artifacts import write_json_exclusive

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
READY_RECEIPT_CONTRACT = "codecairn.hub.launcher-ready.v1"


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


def cli_port(requested: int | None, *, default: int) -> int:
    if requested is None:
        return available_port(default)
    if requested == 0:
        return available_port(0)
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", requested))
    except (OSError, OverflowError) as error:
        raise RuntimeError(f"Requested loopback port {requested} is unavailable") from error
    return requested


def available_port_other_than(excluded: int) -> int:
    for _attempt in range(100):
        selected = available_port(0)
        if selected != excluded:
            return selected
    raise RuntimeError("Could not select distinct loopback ports for the Hub API and Web app")


def write_ready_receipt(path: Path, *, api_port: int, web_port: int, child_process_groups: dict[str, int]) -> None:
    write_json_exclusive(
        path,
        {
            "schema_version": 1,
            "contract": READY_RECEIPT_CONTRACT,
            "web_origin": f"http://127.0.0.1:{web_port}",
            "api_port": api_port,
            "web_port": web_port,
            "launcher_pid": os.getpid(),
            "child_process_groups": child_process_groups,
        },
    )


def wait_for(url: str, *, headers: dict[str, str] | None = None, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--web-port", type=int)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--production", action="store_true")
    arguments = parser.parse_args(argv)

    if arguments.ready_file is not None and os.path.lexists(arguments.ready_file):
        parser.error(f"Ready file already exists: {arguments.ready_file}")
    try:
        api_port = cli_port(arguments.api_port, default=0)
        web_port = cli_port(arguments.web_port, default=3000)
    except RuntimeError as error:
        parser.error(str(error))
    if api_port == web_port:
        if arguments.web_port in (None, 0):
            web_port = available_port_other_than(api_port)
        elif arguments.api_port in (None, 0):
            api_port = available_port_other_than(web_port)
        else:
            parser.error("API and Web ports must be distinct")

    token = secrets.token_urlsafe(32)
    environment = {**os.environ, "CODECAIRN_HUB_API_URL": f"http://127.0.0.1:{api_port}", "CODECAIRN_HUB_TOKEN": token}
    processes: list[subprocess.Popen[bytes]] = []
    previous_handlers = install_shutdown_handlers()
    try:
        api = subprocess.Popen(
            [
                sys.executable,
                "-I",
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
        wait_for(f"http://127.0.0.1:{web_port}/api/hub-read/v1/system")
        if arguments.ready_file is not None:
            write_ready_receipt(
                arguments.ready_file, api_port=api_port, web_port=web_port, child_process_groups={"api": api.pid, "web": web.pid}
            )
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
