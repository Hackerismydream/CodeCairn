from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codecairn.evaluation.evidence_bundle import verify_evidence_bundle

ROOT = Path(__file__).parents[1]


def test_checked_in_v3_bundle_verifies_offline() -> None:
    result = verify_evidence_bundle(ROOT / "evidence" / "benchmark-v3")

    assert result["verified"] is True
    assert result["bundle_id"] == "benchmark-v3"


def test_bundle_verifier_rejects_tampering(tmp_path: Path) -> None:
    target = tmp_path / "benchmark-v3"
    shutil.copytree(ROOT / "evidence" / "benchmark-v3", target)
    metrics = target / "metrics.json"
    metrics.write_text(metrics.read_text() + "\n")

    with pytest.raises(ValueError, match="hash mismatch"):
        verify_evidence_bundle(target)
