from __future__ import annotations

import json

from scripts.verify_hub_contract import FIXTURE_PATH, build_snapshot, render_snapshot


def test_hub_example_matches_current_read_module_contract() -> None:
    observed = build_snapshot()

    assert FIXTURE_PATH.is_file()
    assert FIXTURE_PATH.read_text(encoding="utf-8") == render_snapshot(observed)
    assert json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["evidence_boundary"]["browser_connected_to_runtime"] is False
