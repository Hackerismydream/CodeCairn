"""Bind a participant's live Hub to the frozen machine snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import read_json
from codecairn_v03_acceptance.adapters.hub import (
    SEMANTIC_SHA256_FIELD,
    HubReadClient,
    HubSnapshot,
    OperationReceipt,
    hub_web_bundle_identity,
)
from codecairn_v03_acceptance.campaign import PRESENTATION_SNAPSHOT_PATH, verify_campaign
from codecairn_v03_acceptance.checkout import CheckoutIntegrityError, frozen_checkout_identity


@dataclass(frozen=True, slots=True)
class FrozenHubPresentation:
    snapshot_path: Path
    codecairn_commit: str
    query: str
    selected_memory_id: str
    lifecycle_memory_id: str
    machine_observation: dict[str, object]
    view_projections: dict[str, dict[str, object]]
    view_semantic_sha256: dict[str, str]
    hub_bundle_sha256: str

    @classmethod
    def from_campaign(cls, campaign_dir: Path) -> FrozenHubPresentation:
        """Load a machine-passed, source-collected participant presentation."""
        report = verify_campaign(campaign_dir)
        if not report.machine_complete or report.outcome in {"fail", "not_evaluable"}:
            raise ValueError("participant presentation requires a passed automated machine gate")
        observation = _object(read_json(campaign_dir / "machine" / "observation.json"), field="machine observation")
        if observation.get("collector") != "source_pilot":
            raise ValueError("participant presentation requires source-pilot evidence")
        hub_observation = _object(observation.get("hub"), field="Hub observation")
        protocol = _object(read_json(campaign_dir / "protocol.json"), field="protocol")
        manifest = _object(read_json(campaign_dir / "manifest.json"), field="campaign manifest")
        candidate = _object(manifest.get("candidate"), field="campaign candidate")
        codecairn_commit = candidate.get("codecairn_commit")
        scenario = _object(protocol.get("scenario"), field="protocol scenario")
        query = scenario.get("recall_query")
        snapshot_path = campaign_dir / PRESENTATION_SNAPSHOT_PATH
        snapshot = _object(read_json(snapshot_path), field="Hub snapshot")
        collector = _object(read_json(campaign_dir / "machine" / "raw" / "collector-receipt.json"), field="collector receipt")
        hub_bundle_sha256 = collector.get("hub_bundle_sha256")
        if snapshot.get("contract") != "codecairn.v03-acceptance.hub-snapshot.v1":
            raise ValueError("participant Hub snapshot contract is invalid")
        frozen_machine = _object(snapshot.get("machine_observation"), field="Hub snapshot observation")
        if frozen_machine != hub_observation:
            raise ValueError("participant Hub snapshot does not match the machine observation")
        relations = hub_observation.get("supersessions")
        if (
            not isinstance(codecairn_commit, str)
            or not isinstance(query, str)
            or not query
            or not isinstance(relations, list)
            or len(relations) != 1
            or not isinstance(hub_bundle_sha256, str)
            or len(hub_bundle_sha256) != 64
            or any(character not in "0123456789abcdef" for character in hub_bundle_sha256)
        ):
            raise ValueError("participant Hub scenario identity is invalid")
        relation = _object(relations[0], field="Hub supersession")
        selected_memory_id = hub_observation.get("selected_memory_id")
        lifecycle_memory_id = relation.get("successor_id")
        if not isinstance(selected_memory_id, str) or not isinstance(lifecycle_memory_id, str):
            raise ValueError("participant Hub memory identity is invalid")
        views = _object(snapshot.get("views"), field="Hub snapshot views")
        if set(views) != {"system", "memories", "lifecycle_memories", "recall"}:
            raise ValueError("participant Hub snapshot views are invalid")
        projections: dict[str, dict[str, object]] = {}
        semantic_sha256: dict[str, str] = {}
        for name, value in views.items():
            receipt = _object(value, field=f"Hub {name} receipt")
            projection = _object(receipt.get("projection"), field=f"Hub {name} projection")
            projections[name] = projection
            semantic_sha256[name] = _semantic_digest(projection, field=f"Hub {name} semantic digest")
        return cls(
            snapshot_path=snapshot_path,
            codecairn_commit=codecairn_commit,
            query=query,
            selected_memory_id=selected_memory_id,
            lifecycle_memory_id=lifecycle_memory_id,
            machine_observation=frozen_machine,
            view_projections=projections,
            view_semantic_sha256=semantic_sha256,
            hub_bundle_sha256=hub_bundle_sha256,
        )

    def assert_live_matches(self, client: HubReadClient) -> HubSnapshot:
        """Reject a Hub whose current public reads differ from the frozen view."""
        current = client.snapshot(
            query=self.query, selected_memory_id=self.selected_memory_id, lifecycle_memory_id=self.lifecycle_memory_id
        )
        receipts = {
            "system": current.system,
            "memories": current.memories,
            "lifecycle_memories": current.lifecycle_memories,
            "recall": current.recall,
        }
        projections = {name: receipt.projection for name, receipt in receipts.items()}
        semantic_sha256 = {name: _receipt_semantic_digest(receipt) for name, receipt in receipts.items()}
        if (
            current.machine_observation != self.machine_observation
            or projections != self.view_projections
            or semantic_sha256 != self.view_semantic_sha256
        ):
            raise ValueError("live Hub does not match the frozen participant presentation")
        return current

    def assert_clean_source_checkout(self, checkout: Path) -> None:
        """Require the participant Hub source to be the frozen clean commit."""
        try:
            frozen_checkout_identity(checkout, self.codecairn_commit)
        except CheckoutIntegrityError as error:
            raise ValueError("participant Hub checkout does not match the frozen clean commit") from error
        if hub_web_bundle_identity(checkout)["tree_sha256"] != self.hub_bundle_sha256:
            raise ValueError("participant Hub production bundle does not match the frozen machine build")


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _receipt_semantic_digest(receipt: OperationReceipt) -> str:
    projection_digest = _semantic_digest(receipt.projection, field=f"live Hub {receipt.operation} semantic digest")
    if receipt.semantic_sha256 != projection_digest:
        raise ValueError(f"live Hub {receipt.operation} semantic digest is inconsistent")
    return projection_digest


def _semantic_digest(projection: dict[str, object], *, field: str) -> str:
    value = projection.get(SEMANTIC_SHA256_FIELD)
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} is invalid")
    return value
