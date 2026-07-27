from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "source_budget.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codecairn_source_budget", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("source-budget script cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_source(root: Path, relative: str, content: bytes) -> None:
    path = root / "src" / "codecairn" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_source_budget_uses_one_evaluation_classification(tmp_path: Path) -> None:
    module = _load_script()
    _write_source(tmp_path, "memory.py", b"one\ntwo\n")
    _write_source(tmp_path, "evaluation/report.py", b"one\ntwo\nthree\n")
    report = module.build_report(tmp_path, stage="v01-000a")

    assert report.core == 2
    assert report.evaluation == 3
    assert report.total == 5
    assert report.included_paths == (
        "src/codecairn/evaluation/report.py",
        "src/codecairn/memory.py",
    )


def test_source_budget_counts_newline_delimited_physical_lines(tmp_path: Path) -> None:
    module = _load_script()
    _write_source(tmp_path, "module.py", b"one\ntwo")
    report = module.build_report(tmp_path, stage="v01-000a")

    assert report.core == 1
    assert report.total == 1


def test_source_budget_rejects_unknown_stage(tmp_path: Path) -> None:
    module = _load_script()
    _write_source(tmp_path, "module.py", b"one\n")

    with pytest.raises(ValueError, match="Unknown source-budget stage"):
        module.build_report(tmp_path, stage="unknown")


def test_source_budget_cli_fails_a_tight_stage(tmp_path: Path) -> None:
    _write_source(tmp_path, "module.py", b"line\n" * 10_001)
    result = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--stage",
            "release",
            "--format",
            "json",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert '"passed": false' in result.stdout
    assert "core=10001 exceeds release limit=10000" in result.stdout
