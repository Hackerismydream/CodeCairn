from __future__ import annotations

import math
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkerProcessLimits:
    max_rss_bytes: int
    stall_timeout_seconds: float
    poll_interval_seconds: float = 0.25
    rss_poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_rss_bytes < 1:
            raise ValueError("worker max RSS must be positive")
        if not math.isfinite(self.stall_timeout_seconds) or self.stall_timeout_seconds <= 0:
            raise ValueError("worker stall timeout must be positive")
        if not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("worker poll interval must be positive")
        if not math.isfinite(self.rss_poll_interval_seconds) or self.rss_poll_interval_seconds <= 0:
            raise ValueError("worker RSS poll interval must be positive")


@dataclass(frozen=True, slots=True)
class WorkerProcessResult:
    pid: int
    returncode: int
    max_rss_bytes: int
    wall_time_seconds: float
    termination_reason: str | None
    monitor_error_type: str | None = None


def run_monitored_worker(
    command: tuple[str, ...],
    *,
    progress_root: Path,
    limits: WorkerProcessLimits,
    on_started: Callable[[int], None] | None = None,
) -> WorkerProcessResult:
    """Run one worker without inherited Python state and enforce live resource gates."""
    if not command:
        raise ValueError("worker command must not be empty")
    started = time.monotonic()
    progress = _progress_snapshot(progress_root)
    last_progress_at = started
    max_rss_bytes = 0
    rss_bytes = 0
    next_rss_at = started
    termination_reason: str | None = None
    monitor_error_type: str | None = None
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    try:
        if on_started is not None:
            on_started(process.pid)
        while process.poll() is None:
            now = time.monotonic()
            if now >= next_rss_at:
                rss_bytes = _process_rss_bytes(process.pid)
                max_rss_bytes = max(max_rss_bytes, rss_bytes)
                next_rss_at = now + limits.rss_poll_interval_seconds
            current_progress = _progress_snapshot(progress_root)
            if current_progress != progress:
                progress = current_progress
                last_progress_at = now
            if rss_bytes > limits.max_rss_bytes:
                termination_reason = "rss_limit"
                _terminate(process)
                break
            if now - last_progress_at > limits.stall_timeout_seconds:
                termination_reason = "stalled"
                _terminate(process)
                break
            time.sleep(limits.poll_interval_seconds)
    except Exception as error:
        termination_reason = "monitor_error"
        monitor_error_type = type(error).__name__
    finally:
        if process.poll() is None:
            _terminate(process)
    returncode = process.wait()
    max_rss_bytes = max(max_rss_bytes, _children_max_rss_bytes())
    return WorkerProcessResult(
        pid=process.pid,
        returncode=returncode,
        max_rss_bytes=max_rss_bytes,
        wall_time_seconds=round(time.monotonic() - started, 6),
        termination_reason=termination_reason,
        monitor_error_type=monitor_error_type,
    )


def _progress_snapshot(root: Path) -> tuple[tuple[str, int, int], ...]:
    if not root.is_dir():
        return ()
    return tuple(
        (path.relative_to(root).as_posix(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*.json"))
        if path.is_file()
    )


def _process_rss_bytes(pid: int) -> int:
    if sys.platform.startswith("linux"):
        status_path = Path(f"/proc/{pid}/status")
        if not status_path.is_file():
            return 0
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                return int(fields[1]) * 1024
        return 0
    completed = subprocess.run(
        ("ps", "-o", "rss=", "-p", str(pid)),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=2,
    )
    value = completed.stdout.strip()
    return int(value) * 1024 if value else 0


def _children_max_rss_bytes() -> int:
    observed = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    return observed if sys.platform == "darwin" else observed * 1024


def _terminate(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
