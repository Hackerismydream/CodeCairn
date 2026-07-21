from __future__ import annotations

import os
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from codecairn.evaluation import worker_process
from codecairn.evaluation.worker_process import WorkerProcessLimits, run_monitored_worker


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "field",
    ["stall_timeout_seconds", "poll_interval_seconds", "rss_poll_interval_seconds"],
)
def test_worker_process_limits_reject_non_finite_durations(field: str, non_finite: float) -> None:
    durations = {
        "stall_timeout_seconds": 1.0,
        "poll_interval_seconds": 0.1,
        "rss_poll_interval_seconds": 0.1,
    }
    durations[field] = non_finite

    with pytest.raises(ValueError, match="must be positive"):
        WorkerProcessLimits(
            max_rss_bytes=1024,
            stall_timeout_seconds=durations["stall_timeout_seconds"],
            poll_interval_seconds=durations["poll_interval_seconds"],
            rss_poll_interval_seconds=durations["rss_poll_interval_seconds"],
        )


def test_monitored_worker_records_process_rss_and_progress(tmp_path: Path) -> None:
    progress_root = tmp_path / "progress"
    progress_root.mkdir()
    result = run_monitored_worker(
        (
            sys.executable,
            "-c",
            (
                "import pathlib,time; "
                f"pathlib.Path({str(progress_root / 'done.json')!r}).write_text('{{}}'); "
                "time.sleep(0.1)"
            ),
        ),
        progress_root=progress_root,
        limits=WorkerProcessLimits(
            max_rss_bytes=1024 * 1024 * 1024,
            stall_timeout_seconds=2,
            poll_interval_seconds=0.02,
        ),
    )

    assert result.returncode == 0
    assert result.termination_reason is None
    assert result.max_rss_bytes > 0


def test_monitored_worker_terminates_on_rss_limit(tmp_path: Path) -> None:
    result = run_monitored_worker(
        (
            sys.executable,
            "-c",
            "import time; payload = bytearray(64 * 1024 * 1024); time.sleep(5)",
        ),
        progress_root=tmp_path / "progress",
        limits=WorkerProcessLimits(
            max_rss_bytes=32 * 1024 * 1024,
            stall_timeout_seconds=2,
            poll_interval_seconds=0.02,
        ),
    )

    assert result.returncode != 0
    assert result.termination_reason == "rss_limit"
    assert result.max_rss_bytes > 32 * 1024 * 1024


def test_monitored_worker_terminates_when_no_checkpoint_progress_is_made(tmp_path: Path) -> None:
    result = run_monitored_worker(
        (sys.executable, "-c", "import time; time.sleep(5)"),
        progress_root=tmp_path / "progress",
        limits=WorkerProcessLimits(
            max_rss_bytes=1024 * 1024 * 1024,
            stall_timeout_seconds=0.1,
            poll_interval_seconds=0.02,
        ),
    )

    assert result.returncode != 0
    assert result.termination_reason == "stalled"


def test_monitored_worker_reaps_child_when_rss_sampler_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path = tmp_path / "worker.pid"
    observed_pid: int | None = None

    def fail_after_worker_starts(pid: int) -> int:
        nonlocal observed_pid
        deadline = time.monotonic() + 2
        while not pid_path.is_file():
            if time.monotonic() >= deadline:
                raise AssertionError("worker did not start before RSS sampling failed")
            time.sleep(0.01)
        observed_pid = pid
        raise RuntimeError("RSS sampler failed")

    monkeypatch.setattr(worker_process, "_process_rss_bytes", fail_after_worker_starts)
    try:
        result = run_monitored_worker(
            (
                sys.executable,
                "-c",
                (
                    "import os,pathlib,time; "
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                    "time.sleep(30)"
                ),
            ),
            progress_root=tmp_path / "progress",
            limits=WorkerProcessLimits(
                max_rss_bytes=1024 * 1024 * 1024,
                stall_timeout_seconds=5,
                poll_interval_seconds=0.02,
            ),
        )

        assert observed_pid is not None
        assert result.returncode != 0
        assert result.termination_reason == "monitor_error"
        assert result.monitor_error_type == "RuntimeError"
        with pytest.raises(ChildProcessError):
            os.waitpid(observed_pid, os.WNOHANG)
    finally:
        if observed_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(observed_pid, signal.SIGKILL)
            with suppress(ChildProcessError):
                os.waitpid(observed_pid, 0)
