from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import verify_hub_contract as hub_contract
from scripts.verify_hub_contract import (
    FIXTURE_PATH,
    GOVERNANCE_FIXTURE_PATH,
    build_governance_snapshot,
    build_snapshot,
    render_snapshot,
)


def test_hub_example_matches_current_read_module_contract() -> None:
    observed = build_snapshot()

    assert FIXTURE_PATH.is_file()
    assert FIXTURE_PATH.read_text(encoding="utf-8") == render_snapshot(observed)
    assert json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["evidence_boundary"]["browser_connected_to_runtime"] is False


def test_hub_governance_example_matches_closed_person_bound_contract() -> None:
    observed = build_governance_snapshot()

    assert GOVERNANCE_FIXTURE_PATH.is_file()
    assert GOVERNANCE_FIXTURE_PATH.read_text(encoding="utf-8") == render_snapshot(observed)
    example = json.loads(GOVERNANCE_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert example["surface"]["request_fields"] == ["memory_id"]
    assert example["responses"]["created"]["receipt"]["outcome"] == "created"
    assert example["responses"]["idempotent_replay"]["receipt"]["outcome"] == "already_promoted"
    assert example["responses"]["rejected_owner_injection"]["error"]["code"] == "invalid_request"


def test_hub_governance_example_is_checkout_path_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"
    snapshots = []
    for name in ("first-checkout", "second-checkout"):
        checkout = tmp_path / name
        fixture = checkout / "tests" / "fixtures" / "codex" / "failed_command.jsonl"
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(source.read_bytes())
        monkeypatch.setattr(hub_contract, "REPOSITORY_ROOT", checkout)
        snapshots.append(hub_contract.build_governance_snapshot())

    assert snapshots[0] == snapshots[1]
