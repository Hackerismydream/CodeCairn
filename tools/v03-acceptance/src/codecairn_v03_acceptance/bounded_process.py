"""Bounded subprocess execution with process-group termination."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast

ProcessTerminal = Literal["exited", "timeout", "stdout_limit", "stderr_limit", "descendant_pipe_leak"]


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    """Exact bounded output and terminal state for one child process group."""

    pid: int
    exit_code: int
    terminal: ProcessTerminal
    duration_ms: int
    stdout: bytes
    stderr: bytes


def run_bounded_process(
    command: tuple[str, ...], *, cwd: Path, environment: Mapping[str, str], timeout_seconds: float, stdout_limit: int, stderr_limit: int
) -> BoundedProcessResult:
    """Run a command while enforcing timeout and output caps during execution."""
    if not command or timeout_seconds <= 0 or stdout_limit < 1 or stderr_limit < 1:
        raise ValueError("bounded process command, timeout, and stream limits must be positive")
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd.resolve(),
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        bufsize=0,
    )
    stdout_stream = cast(BinaryIO, process.stdout)
    stderr_stream = cast(BinaryIO, process.stderr)
    stdout = bytearray()
    stderr = bytearray()
    stdout_exceeded = threading.Event()
    stderr_exceeded = threading.Event()
    readers = (
        threading.Thread(target=_read_bounded, args=(stdout_stream, stdout, stdout_limit, stdout_exceeded), daemon=True),
        threading.Thread(target=_read_bounded, args=(stderr_stream, stderr, stderr_limit, stderr_exceeded), daemon=True),
    )
    for reader in readers:
        reader.start()

    terminal: ProcessTerminal | None = None
    deadline = started + timeout_seconds
    while process.poll() is None:
        limit_terminal = _limit_terminal(stdout_exceeded, stderr_exceeded)
        if limit_terminal is not None:
            terminal = limit_terminal
            _stop_process_group(process)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminal = "timeout"
            _stop_process_group(process)
            break
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=min(0.02, remaining))

    for reader in readers:
        reader.join(timeout=1)
    terminal = terminal or _limit_terminal(stdout_exceeded, stderr_exceeded)
    if any(reader.is_alive() for reader in readers):
        terminal = terminal or "descendant_pipe_leak"
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        for reader in readers:
            reader.join(timeout=1)
    stdout_stream.close()
    stderr_stream.close()
    if process.poll() is None:
        _stop_process_group(process)
    return BoundedProcessResult(
        pid=process.pid,
        exit_code=process.wait(timeout=1),
        terminal=terminal or "exited",
        duration_ms=max(0, round((time.monotonic() - started) * 1_000)),
        stdout=bytes(stdout),
        stderr=bytes(stderr),
    )


def _read_bounded(stream: BinaryIO, output: bytearray, limit: int, exceeded: threading.Event) -> None:
    while True:
        try:
            chunk = os.read(stream.fileno(), 8_192)
        except OSError:
            return
        if not chunk:
            return
        remaining = max(0, limit - len(output))
        output.extend(chunk[:remaining])
        if len(chunk) > remaining:
            exceeded.set()


def _limit_terminal(stdout: threading.Event, stderr: threading.Event) -> ProcessTerminal | None:
    if stdout.is_set():
        return "stdout_limit"
    if stderr.is_set():
        return "stderr_limit"
    return None


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=1)
