from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from codecairn_v03_acceptance.bounded_process import run_bounded_process


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_cap_kills_a_writer_before_its_sleep_or_timeout(tmp_path: Path, stream: str) -> None:
    command = (sys.executable, "-I", "-c", f"import os,sys,time;os.write(sys.{stream}.fileno(),b'x'*4097);time.sleep(30)")
    started = time.monotonic()

    result = run_bounded_process(
        command, cwd=tmp_path, environment={"LANG": "C.UTF-8"}, timeout_seconds=20, stdout_limit=1_024, stderr_limit=1_024
    )

    assert result.terminal == f"{stream}_limit"
    assert len(getattr(result, stream)) == 1_024
    assert result.exit_code != 0
    assert time.monotonic() - started < 5
