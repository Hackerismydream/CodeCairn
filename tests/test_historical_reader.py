from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from codecairn.evaluation.artifacts import read_json
from codecairn.evaluation.historical_reader import read_historical_bundle

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "evidence" / "benchmark-v3"
READER = ROOT / "src" / "codecairn" / "evaluation" / "historical_reader.py"


def test_historical_reader_recomputes_v3_reports_without_mutation() -> None:
    tracked = (
        BUNDLE / "raw" / "locomo" / "summary.json",
        BUNDLE / "raw" / "retrieval" / "summary.json",
        BUNDLE / "raw" / "recovery" / "summary.json",
        BUNDLE / "raw" / "coding" / "summary.json",
    )
    before = tuple(path.stat().st_mtime_ns for path in tracked)

    reports = read_historical_bundle(BUNDLE / "raw")

    assert reports.locomo == read_json(tracked[0])
    assert reports.retrieval == read_json(tracked[1])
    assert reports.recovery == read_json(tracked[2])
    assert reports.coding == read_json(tracked[3])
    assert tuple(path.stat().st_mtime_ns for path in tracked) == before


def test_historical_reader_has_only_evaluation_artifact_dependency() -> None:
    tree = ast.parse(READER.read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None}

    assert {name for name in imports if name.startswith("codecairn.")} == {"codecairn.evaluation.artifacts"}


def test_v3_verification_does_not_import_product_runtime() -> None:
    program = """
import json
import sys
from pathlib import Path
from codecairn.evaluation.evidence_bundle import verify_evidence_bundle
result = verify_evidence_bundle(Path("evidence/benchmark-v3"))
blocked = sorted(
    name for name in sys.modules
    if name.startswith((
        "codecairn.bootstrap",
        "codecairn.memory",
        "codecairn.service",
        "codecairn.storage",
    ))
)
print(json.dumps({"result": result, "blocked": blocked}, sort_keys=True))
"""
    completed = subprocess.run((sys.executable, "-c", program), cwd=ROOT, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["result"]["verified"] is True
    assert payload["result"]["verified_file_count"] == 4411
    assert payload["blocked"] == []
