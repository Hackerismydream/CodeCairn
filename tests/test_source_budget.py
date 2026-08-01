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


def _write_path(root: Path, relative: str, content: bytes) -> None:
    path = root / relative
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
    assert report.included_paths == ("src/codecairn/evaluation/report.py", "src/codecairn/memory.py")


def test_source_budget_counts_newline_delimited_physical_lines(tmp_path: Path) -> None:
    module = _load_script()
    _write_source(tmp_path, "module.py", b"one\ntwo")
    report = module.build_report(tmp_path, stage="v01-000a")

    assert report.core == 1
    assert report.total == 1


@pytest.mark.parametrize("stage", ("v03-acceptance", "v04-onboarding"))
def test_product_budget_counts_hub_launcher_api_and_acceptance_tool(tmp_path: Path, stage: str) -> None:
    module = _load_script()
    _write_source(tmp_path, "memory.py", b"core\n")
    _write_source(tmp_path, "evaluation/report.py", b"evaluation\n")
    _write_path(tmp_path, "apps/hub-api/src/codecairn_hub_api/app.py", b"hub\n")
    _write_path(tmp_path, "apps/hub-web/app/page.tsx", b"page\n")
    _write_path(tmp_path, "apps/hub-web/app/globals.css", b"style\n")
    _write_path(tmp_path, "apps/hub-web/lib/client.ts", b"client\n")
    _write_path(tmp_path, "apps/hub-web/worker/index.ts", b"worker\n")
    _write_path(tmp_path, "apps/hub-web/next.config.ts", b"next\n")
    _write_path(tmp_path, "apps/hub-web/vite.config.ts", b"vite\n")
    _write_path(tmp_path, "scripts/run_hub.py", b"launcher\n")
    _write_path(tmp_path, "tools/v03-acceptance/src/codecairn_v03_acceptance/campaign.py", b"acceptance\n")

    report = module.build_report(tmp_path, stage=stage)

    assert report.core == 9
    assert report.evaluation == 2
    assert report.total == 11
    assert report.included_paths == (
        "apps/hub-api/src/codecairn_hub_api/app.py",
        "apps/hub-web/app/globals.css",
        "apps/hub-web/app/page.tsx",
        "apps/hub-web/lib/client.ts",
        "apps/hub-web/next.config.ts",
        "apps/hub-web/vite.config.ts",
        "apps/hub-web/worker/index.ts",
        "scripts/run_hub.py",
        "src/codecairn/evaluation/report.py",
        "src/codecairn/memory.py",
        "tools/v03-acceptance/src/codecairn_v03_acceptance/campaign.py",
    )


def test_source_budget_rejects_unknown_stage(tmp_path: Path) -> None:
    module = _load_script()
    _write_source(tmp_path, "module.py", b"one\n")

    with pytest.raises(ValueError, match="Unknown source-budget stage"):
        module.build_report(tmp_path, stage="unknown")


def test_source_budget_cli_fails_a_tight_stage(tmp_path: Path) -> None:
    _write_source(tmp_path, "module.py", b"line\n" * 10_001)
    result = subprocess.run(
        (sys.executable, str(SCRIPT), "--root", str(tmp_path), "--stage", "release", "--format", "json"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert '"passed": false' in result.stdout
    assert "core=10001 exceeds release limit=9700" in result.stdout
