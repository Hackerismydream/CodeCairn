from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from codecairn_v03_acceptance import presentation
from codecairn_v03_acceptance.adapters.hub import HubReadClient, HubSnapshot, OperationReceipt
from codecairn_v03_acceptance.presentation import FrozenHubPresentation

_SELECTED = "mem_" + "1" * 64
_PREDECESSOR = "mem_" + "2" * 64
_SUCCESSOR = "mem_" + "3" * 64


def test_frozen_presentation_requires_the_live_hub_to_match_every_public_projection(monkeypatch, tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    machine = {"selected_memory_id": _SELECTED, "supersessions": [{"predecessor_id": _PREDECESSOR, "successor_id": _SUCCESSOR}]}
    projections = {name: {"view": name} for name in ("system", "memories", "lifecycle_memories", "recall")}
    semantic_digests = {name: str(index) * 64 for index, name in enumerate(projections, start=1)}
    _write_json(campaign / "machine" / "observation.json", {"collector": "source_pilot", "hub": machine})
    _write_json(campaign / "manifest.json", {"candidate": {"codecairn_commit": "1" * 40}})
    _write_json(campaign / "protocol.json", {"scenario": {"recall_query": "为什么修改重试次数？"}})
    _write_json(campaign / "machine" / "raw" / "collector-receipt.json", {"hub_bundle_sha256": "d" * 64})
    _write_json(
        campaign / "machine" / "hub-snapshot.json",
        {
            "contract": "codecairn.v03-acceptance.hub-snapshot.v1",
            "machine_observation": machine,
            "views": {
                name: {"projection": {**value, "semantic_sha256": semantic_digests[name]}} for name, value in projections.items()
            },
        },
    )
    monkeypatch.setattr(
        presentation, "verify_campaign", lambda _campaign: SimpleNamespace(machine_complete=True, outcome="awaiting_evidence")
    )
    frozen = FrozenHubPresentation.from_campaign(campaign)
    snapshot = _snapshot(machine, projections, semantic_digests)

    assert frozen.assert_live_matches(cast(HubReadClient, _Client(snapshot))) == snapshot

    drifted = _snapshot({**machine, "selected_memory_id": _SUCCESSOR}, projections, semantic_digests)
    with pytest.raises(ValueError, match="does not match"):
        frozen.assert_live_matches(cast(HubReadClient, _Client(drifted)))

    content_drifted_digests = {**semantic_digests, "memories": "f" * 64}
    content_drifted = _snapshot(machine, projections, content_drifted_digests)
    with pytest.raises(ValueError, match="does not match"):
        frozen.assert_live_matches(cast(HubReadClient, _Client(content_drifted)))


def test_frozen_presentation_rejects_dirty_or_mismatched_source(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    frozen = FrozenHubPresentation(
        snapshot_path=tmp_path / "snapshot.json",
        codecairn_commit="1" * 40,
        query="query",
        selected_memory_id=_SELECTED,
        lifecycle_memory_id=_SUCCESSOR,
        machine_observation={},
        view_projections={},
        view_semantic_sha256={},
        hub_bundle_sha256="d" * 64,
    )

    def reject_source(_checkout: Path, _commit: str) -> tuple[Path, str, str]:
        raise presentation.CheckoutIntegrityError("candidate_checkout_dirty", "dirty")

    monkeypatch.setattr(presentation, "frozen_checkout_identity", reject_source)

    with pytest.raises(ValueError, match="frozen clean commit"):
        frozen.assert_clean_source_checkout(checkout)


@pytest.mark.parametrize("concealment", ["--assume-unchanged", "--skip-worktree"])
def test_frozen_presentation_rejects_index_concealed_hub_changes(tmp_path: Path, concealment: str) -> None:
    checkout = tmp_path / "checkout"
    page = checkout / "apps" / "hub-web" / "app" / "page.tsx"
    page.parent.mkdir(parents=True)
    page.write_text("export default function Page() { return null; }\n", encoding="utf-8")
    _git(checkout, "init")
    _git(checkout, "add", "apps/hub-web/app/page.tsx")
    _git(checkout, "-c", "user.name=test", "-c", "user.email=test@invalid", "commit", "-m", "initial")
    commit = _git(checkout, "rev-parse", "HEAD")
    _git(checkout, "update-index", concealment, "apps/hub-web/app/page.tsx")
    page.write_text("export default function Page() { return 'changed'; }\n", encoding="utf-8")
    assert _git(checkout, "status", "--porcelain=v1", "--untracked-files=all") == ""
    frozen = FrozenHubPresentation(
        snapshot_path=tmp_path / "snapshot.json",
        codecairn_commit=commit,
        query="query",
        selected_memory_id=_SELECTED,
        lifecycle_memory_id=_SUCCESSOR,
        machine_observation={},
        view_projections={},
        view_semantic_sha256={},
        hub_bundle_sha256="d" * 64,
    )

    with pytest.raises(ValueError, match="frozen clean commit"):
        frozen.assert_clean_source_checkout(checkout.resolve())


def test_frozen_presentation_overrides_weak_repository_stat_checks(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    page = checkout / "apps" / "hub-web" / "app" / "page.tsx"
    page.parent.mkdir(parents=True)
    page.write_text("export const value = 1;\n", encoding="utf-8")
    timestamp = 1_600_000_000_000_000_000
    os.utime(page, ns=(timestamp, timestamp))
    _git(checkout, "init")
    _git(checkout, "add", "apps/hub-web/app/page.tsx")
    _git(checkout, "-c", "user.name=test", "-c", "user.email=test@invalid", "commit", "-m", "initial")
    commit = _git(checkout, "rev-parse", "HEAD")
    _git(checkout, "config", "core.trustctime", "false")
    _git(checkout, "config", "core.checkStat", "minimal")
    page.write_text("export const value = 2;\n", encoding="utf-8")
    os.utime(page, ns=(timestamp, timestamp))
    assert _git(checkout, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert _git(checkout, "ls-files", "-v") == "H apps/hub-web/app/page.tsx"
    frozen = FrozenHubPresentation(
        snapshot_path=tmp_path / "snapshot.json",
        codecairn_commit=commit,
        query="query",
        selected_memory_id=_SELECTED,
        lifecycle_memory_id=_SUCCESSOR,
        machine_observation={},
        view_projections={},
        view_semantic_sha256={},
        hub_bundle_sha256="d" * 64,
    )

    with pytest.raises(ValueError, match="frozen clean commit"):
        frozen.assert_clean_source_checkout(checkout.resolve())


def test_frozen_presentation_rejects_a_changed_production_bundle(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    frozen = FrozenHubPresentation(
        snapshot_path=tmp_path / "snapshot.json",
        codecairn_commit="1" * 40,
        query="query",
        selected_memory_id=_SELECTED,
        lifecycle_memory_id=_SUCCESSOR,
        machine_observation={},
        view_projections={},
        view_semantic_sha256={},
        hub_bundle_sha256="d" * 64,
    )
    monkeypatch.setattr(presentation, "frozen_checkout_identity", lambda path, commit: (path, commit, "tree"))
    monkeypatch.setattr(presentation, "hub_web_bundle_identity", lambda _checkout: {"tree_sha256": "e" * 64})

    with pytest.raises(ValueError, match="production bundle"):
        frozen.assert_clean_source_checkout(checkout)


def test_frozen_presentation_rejects_a_failed_machine_gate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(presentation, "verify_campaign", lambda _campaign: SimpleNamespace(machine_complete=True, outcome="fail"))

    with pytest.raises(ValueError, match="passed automated machine gate"):
        FrozenHubPresentation.from_campaign(tmp_path)


class _Client:
    def __init__(self, snapshot: HubSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self, *, query: str, selected_memory_id: str, lifecycle_memory_id: str) -> HubSnapshot:
        assert query == "为什么修改重试次数？"
        assert selected_memory_id == _SELECTED
        assert lifecycle_memory_id == _SUCCESSOR
        return self._snapshot


def _snapshot(machine: dict[str, object], projections: dict[str, dict[str, object]], semantic_digests: dict[str, str]) -> HubSnapshot:
    def receipt(name: str) -> OperationReceipt:
        return OperationReceipt(
            operation=name,
            http_status=200,
            request_id=f"hubreq_{name}",
            body_sha256="a" * 64,
            projection={**projections[name], "semantic_sha256": semantic_digests[name]},
            semantic_sha256=semantic_digests[name],
        )

    return HubSnapshot(
        system=receipt("system"),
        memories=receipt("memories"),
        lifecycle_memories=receipt("lifecycle_memories"),
        recall=receipt("recall"),
        machine_observation=machine,
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _git(checkout: Path, *arguments: str) -> str:
    return subprocess.run(("git", "-C", str(checkout), *arguments), check=True, capture_output=True, text=True).stdout.strip()
