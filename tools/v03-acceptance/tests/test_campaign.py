from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from codecairn_v03_acceptance.campaign import (
    PRESENTATION_SNAPSHOT_PATH,
    RAW_INVENTORY_CONTRACT,
    SOURCE_PILOT_RECEIPT_CONTRACT,
    CampaignRequest,
    _raw_inventory,
    _record_participant_response,
    _record_questionnaire_response,
    _record_questionnaire_review,
    _record_review,
    _record_source_pilot_observation,
    record_machine_observation,
    seal_campaign,
    start_campaign,
    verify_campaign,
)

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json


def _write_protocol(path: Path) -> Path:
    path.parent.mkdir(parents=True)
    frozen = Path(__file__).parents[1] / "protocols" / "hub-comprehension-v1.json"
    path.write_bytes(frozen.read_bytes())
    return path


def test_campaign_start_is_immutable_and_unverified_evidence_cannot_pass(tmp_path: Path) -> None:
    request = CampaignRequest(
        protocol_path=_write_protocol(tmp_path / "inputs" / "protocol.json"),
        output_root=tmp_path / "runs",
        run_id="v03-pilot-001",
        codecairn_commit="1" * 40,
        pico_commit="2" * 40,
        delivery_mode="source_checkout",
    )

    handle = start_campaign(request)
    report = verify_campaign(handle.artifact_dir)

    assert handle.phase == "awaiting_machine"
    assert report.outcome == "awaiting_evidence"
    assert report.machine_complete is False
    assert report.human_complete is False
    assert report.release_eligible is None
    assert report.violations == ("machine_evidence_missing",)
    with pytest.raises(FileExistsError):
        start_campaign(request)


def test_campaign_rejects_a_weakened_lookalike_protocol(tmp_path: Path) -> None:
    protocol = _write_protocol(tmp_path / "inputs" / "protocol.json")
    value = cast(dict[str, object], read_json(protocol))
    cast(dict[str, object], value["promotion"]).update(
        {"planned_participants": 1, "minimum_valid_participants": 1, "minimum_participant_passes": 1, "minimum_passes_per_question": 1}
    )
    protocol.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=r"frozen version 0\.3 protocol"):
        start_campaign(
            CampaignRequest(
                protocol_path=protocol,
                output_root=tmp_path / "runs",
                run_id="weakened-protocol",
                codecairn_commit="1" * 40,
                pico_commit="2" * 40,
                delivery_mode="source_checkout",
            )
        )


def test_source_checkout_campaign_can_measure_comprehension_but_cannot_release(tmp_path: Path) -> None:
    request = CampaignRequest(
        protocol_path=_write_protocol(tmp_path / "inputs" / "protocol.json"),
        output_root=tmp_path / "runs",
        run_id="v03-pilot-002",
        codecairn_commit="1" * 40,
        pico_commit="2" * 40,
        delivery_mode="source_checkout",
    )
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir)

    report = verify_campaign(handle.artifact_dir)

    assert report.outcome == "pass"
    assert report.machine_complete is True
    assert report.human_complete is True
    assert report.release_eligible is False
    assert report.violations == ("delivery_mode_source_checkout", "campaign_unsealed")


def test_release_campaign_requires_all_immutable_distribution_digests(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="artifact digests"):
        CampaignRequest(
            protocol_path=_write_protocol(tmp_path / "inputs" / "protocol.json"),
            output_root=tmp_path / "runs",
            run_id="v03-release-001",
            codecairn_commit="1" * 40,
            pico_commit="2" * 40,
            delivery_mode="release_artifact",
        )


def test_sealed_release_candidate_stays_ineligible_until_the_installed_collector_exists(tmp_path: Path) -> None:
    request = CampaignRequest(
        protocol_path=_write_protocol(tmp_path / "inputs" / "protocol.json"),
        output_root=tmp_path / "runs",
        run_id="v03-release-002",
        codecairn_commit="1" * 40,
        pico_commit="2" * 40,
        delivery_mode="release_artifact",
        codecairn_artifact_sha256="a" * 64,
        pico_artifact_sha256="b" * 64,
        hub_artifact_sha256="c" * 64,
    )
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir)

    unsealed = verify_campaign(handle.artifact_dir)
    sealed = seal_campaign(handle.artifact_dir)
    verified = verify_campaign(handle.artifact_dir)

    assert unsealed.outcome == "pass"
    assert unsealed.release_eligible is False
    assert unsealed.violations == ("formal_release_collector_unavailable", "campaign_unsealed")
    assert sealed == verified
    assert verified.outcome == "pass"
    assert verified.release_eligible is False
    assert verified.violations == ("formal_release_collector_unavailable",)

    observation_path = handle.artifact_dir / "machine" / "observation.json"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    observation["hub"]["recall_memory_ids"] = []
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory"):
        verify_campaign(handle.artifact_dir)


def test_machine_failure_is_derived_from_observation_instead_of_claimed_by_runner(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-003")
    handle = start_campaign(request)
    observation = _machine_observation(request)
    assert isinstance(observation["hub"], dict)
    observation["hub"]["recall_memory_ids"] = []

    _record_automated_machine(handle.artifact_dir, observation)
    report = verify_campaign(handle.artifact_dir)
    machine = json.loads((handle.artifact_dir / "machine" / "result.json").read_text(encoding="utf-8"))

    assert machine["terminal_class"] == "failed"
    assert machine["checks"]["hub_recall_read"] is False
    assert report.outcome == "fail"
    assert report.release_eligible is False
    assert report.violations == ("machine_gate_failed",)


def test_lifecycle_gate_rejects_a_superseded_memory_in_default_recall(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-lifecycle-recall")
    handle = start_campaign(request)
    observation = _machine_observation(request)
    hub = cast(dict[str, object], observation["hub"])
    predecessor = cast(list[dict[str, str]], hub["supersessions"])[0]["predecessor_id"]
    cast(list[str], hub["recall_ranked_memory_ids"]).append(predecessor)

    _record_automated_machine(handle.artifact_dir, observation)
    machine = cast(dict[str, object], read_json(handle.artifact_dir / "machine" / "result.json"))

    assert cast(dict[str, bool], machine["checks"])["supersession_visible"] is False
    assert verify_campaign(handle.artifact_dir).outcome == "fail"


def test_handwritten_machine_observation_cannot_satisfy_the_automated_gate(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-manual")
    handle = start_campaign(request)

    record_machine_observation(handle.artifact_dir, _machine_observation(request))
    report = verify_campaign(handle.artifact_dir)

    assert report.outcome == "not_evaluable"
    assert report.machine_complete is True
    assert report.human_complete is False
    assert report.release_eligible is None
    assert report.violations == ("machine_evidence_not_automated",)


def test_installed_artifact_identity_must_match_the_frozen_candidate(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-identity")
    handle = start_campaign(request)
    observation = _machine_observation(request)
    assert isinstance(observation["installed"], dict)
    observation["installed"]["pico_artifact_sha256"] = "f" * 64

    _record_automated_machine(handle.artifact_dir, observation)
    machine = json.loads((handle.artifact_dir / "machine" / "result.json").read_text(encoding="utf-8"))

    assert machine["checks"]["exact_candidate_identity"] is False
    assert verify_campaign(handle.artifact_dir).outcome == "fail"


def test_infrastructure_failure_is_not_counted_as_product_failure(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-004")
    handle = start_campaign(request)
    observation = _machine_observation(request)
    observation["terminal_class"] = "infrastructure_failure"
    observation["failure_code"] = "provider_failure"

    _record_automated_machine(handle.artifact_dir, observation)
    report = verify_campaign(handle.artifact_dir)

    assert report.outcome == "not_evaluable"
    assert report.release_eligible is None
    assert report.violations == ("machine_infrastructure_failed",)


def test_scripted_participants_and_llm_reviewers_cannot_stand_in_for_humans(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-005")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir, participant_kind="scripted", reviewer_kind="llm")

    report = verify_campaign(handle.artifact_dir)

    assert report.outcome == "not_evaluable"
    assert report.human_complete is True
    assert report.release_eligible is None
    assert report.violations == ("human_evidence_not_formal",)


def test_human_threshold_failure_is_a_complete_failed_campaign(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-006")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir, failing_participants={"P001", "P002"})

    report = verify_campaign(handle.artifact_dir)

    assert report.outcome == "fail"
    assert report.human_complete is True
    assert report.release_eligible is False
    assert report.violations == ("delivery_mode_source_checkout", "campaign_unsealed")


def test_sealed_campaign_rejects_new_evidence(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-007")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir)
    seal_campaign(handle.artifact_dir)

    with pytest.raises(ValueError, match="sealed campaign"):
        _record_questionnaire_response(handle.artifact_dir, _participant_response(handle.artifact_dir, "P001"))


def test_nested_file_named_inventory_is_covered_by_the_root_seal(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-nested-inventory")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir)
    nested = handle.artifact_dir / "raw" / "inventory.json"
    _write(nested, {"raw": "evidence"})
    seal_campaign(handle.artifact_dir)

    nested.write_text('{"raw":"tampered"}', encoding="utf-8")

    with pytest.raises(ValueError, match="inventory"):
        verify_campaign(handle.artifact_dir)


def test_source_pilot_raw_inventory_is_required_offline(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-raw-required")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))

    (handle.artifact_dir / "machine" / "raw" / "stage.json").unlink()

    with pytest.raises(ValueError, match="raw inventory"):
        verify_campaign(handle.artifact_dir)


def test_manual_human_json_cannot_satisfy_the_formal_gate(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-manual-humans")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    for index in range(1, 6):
        participant_id = f"P{index:03d}"
        _record_participant_response(
            handle.artifact_dir, _participant_response(handle.artifact_dir, participant_id), collector="manual"
        )
        _record_review(handle.artifact_dir, _participant_review(handle.artifact_dir, participant_id), collector="manual")

    assert verify_campaign(handle.artifact_dir).violations == ("human_evidence_not_formal",)


def test_review_is_bound_to_the_exact_response(tmp_path: Path) -> None:
    request = _source_request(tmp_path, "v03-pilot-review-binding")
    handle = start_campaign(request)
    _record_automated_machine(handle.artifact_dir, _machine_observation(request))
    _record_complete_human_evidence(handle.artifact_dir)
    response_path = handle.artifact_dir / "participants" / "P001" / "response.json"
    response = cast(dict[str, object], read_json(response_path))
    cast(dict[str, dict[str, str]], response["answers"])["remembered"]["answer"] = "tampered after review"
    response_path.write_text(json.dumps(response), encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed response"):
        verify_campaign(handle.artifact_dir)


def _question_ids() -> tuple[str, ...]:
    return ("remembered", "provenance", "recall_reason", "lifecycle")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _machine_observation(request: CampaignRequest) -> dict[str, object]:
    remembered = "mem_" + "1" * 64
    predecessor = "mem_" + "2" * 64
    successor = "mem_" + "3" * 64
    return {
        "schema_version": 1,
        "contract": "codecairn.v03-acceptance.machine-observation.v1",
        "terminal_class": "completed",
        "failure_code": None,
        "candidate": {
            "codecairn_commit": request.codecairn_commit,
            "pico_commit": request.pico_commit,
            "delivery_mode": request.delivery_mode,
            "codecairn_artifact_sha256": request.codecairn_artifact_sha256,
            "pico_artifact_sha256": request.pico_artifact_sha256,
            "hub_artifact_sha256": request.hub_artifact_sha256,
        },
        "installed": {
            "codecairn_artifact_sha256": request.codecairn_artifact_sha256,
            "pico_artifact_sha256": request.pico_artifact_sha256,
            "hub_artifact_sha256": request.hub_artifact_sha256,
            "source_checkouts_absent": request.delivery_mode == "release_artifact",
            "plugin_entry_point": "codecairn.integrations.pico",
        },
        "pico": {
            "adapter": "subprocess",
            "install_kind": "installed_distribution",
            "provider_mode": "configured",
            "plugin_id": "codecairn-memory",
            "backend": "codecairn",
            "task_a": {
                "process_id": "process-a",
                "session_id": "cli:v03-learn",
                "trace_contract": "audit.span.v1",
                "task_verified": True,
                "captured_memory_ids": [remembered],
            },
            "task_b": {
                "process_id": "process-b",
                "session_id": "cli:v03-recall",
                "trace_contract": "audit.span.v1",
                "recalled_memory_ids": [remembered],
                "llm_input_memory_ids": [remembered],
                "forbidden_tool_calls": 0,
            },
        },
        "codecairn": {
            "source_journal_memory_ids": [remembered],
            "public_recall_memory_ids": [remembered],
            "evidence_reference_memory_ids": [remembered],
        },
        "hub": {
            "adapter": "http",
            "repository_key": "local/v03-acceptance",
            "system_repository_key": "local/v03-acceptance",
            "recall_repository_key": "local/v03-acceptance",
            "lifecycle_repository_key": "local/v03-acceptance",
            "memories_memory_ids": [remembered, predecessor, successor],
            "selected_memory_id": remembered,
            "selected_evidence_fact_ids": ["fact_" + "a" * 64],
            "selected_evidence_references": [{"fact_id": "fact_" + "a" * 64, "provider": "pico", "session_id": "cli:v03-learn"}],
            "recall_memory_ids": [remembered],
            "recall_ranked_memory_ids": [remembered],
            "recall_admission": {"outcome": "admitted", "reason": "relevant_candidate"},
            "recall_omissions": [{"memory_id": predecessor, "reason": "lifecycle"}],
            "recall_context_sha256": "f" * 64,
            "system_status": "ok",
            "recall_readiness": {"state": "configuration_ready", "live_checked": False},
            "statuses": {remembered: "active", predecessor: "superseded", successor: "active"},
            "supersessions": [{"predecessor_id": predecessor, "successor_id": successor}],
        },
    }


def _source_request(tmp_path: Path, run_id: str) -> CampaignRequest:
    return CampaignRequest(
        protocol_path=_write_protocol(tmp_path / "inputs" / f"{run_id}.protocol.json"),
        output_root=tmp_path / "runs",
        run_id=run_id,
        codecairn_commit="1" * 40,
        pico_commit="2" * 40,
        delivery_mode="source_checkout",
    )


def _record_automated_machine(artifact_dir: Path, observation: dict[str, object]) -> None:
    raw_dir = artifact_dir / "machine" / "raw"
    candidate = {
        "schema_version": 1,
        "codecairn": {"commit": cast(dict[str, object], observation["candidate"])["codecairn_commit"], "clean": True},
        "pico": {"commit": cast(dict[str, object], observation["candidate"])["pico_commit"], "clean": True},
    }
    bundle_sha256 = "d" * 64
    required = (
        "stage.json",
        "pico-config.json",
        "list-before-receipt.json",
        "learn-evidence.json",
        "list-after-receipt.json",
        "public-recall-receipt.json",
        "recall-evidence.json",
        "lifecycle-seed.json",
    )
    _write(raw_dir / "candidate-identity.json", candidate)
    _write(raw_dir / "candidate-identity-final.json", candidate)
    _write(raw_dir / "workspace-identity.json", {"hub_build": {"bundle": {"tree_sha256": bundle_sha256}}})
    for name in required:
        _write(raw_dir / name, {"fixture": name})
    task_verification = {"task_verified": True}
    _write(raw_dir / "task-verification.json", task_verification)
    _write(
        artifact_dir / PRESENTATION_SNAPSHOT_PATH,
        {"contract": "codecairn.v03-acceptance.hub-snapshot.v1", "machine_observation": observation["hub"], "views": {}},
    )
    inventory = {"schema_version": 1, "contract": RAW_INVENTORY_CONTRACT, "files": _raw_inventory(raw_dir)}
    _write(raw_dir / "raw-inventory.json", inventory)
    pico = cast(dict[str, object], observation["pico"])
    _write(
        raw_dir / "collector-receipt.json",
        {
            "schema_version": 1,
            "contract": SOURCE_PILOT_RECEIPT_CONTRACT,
            "mode": "live",
            "candidate_identity_sha256": canonical_sha256(candidate),
            "stage_sha256": canonical_sha256(read_json(raw_dir / "stage.json")),
            "task_verification_sha256": canonical_sha256(task_verification),
            "hub_snapshot_sha256": file_sha256(artifact_dir / PRESENTATION_SNAPSHOT_PATH),
            "raw_inventory_sha256": file_sha256(raw_dir / "raw-inventory.json"),
            "observation_sha256": canonical_sha256({**observation, "collector": "source_pilot"}),
            "hub_bundle_sha256": bundle_sha256,
            "captured_memory_ids": cast(dict[str, object], pico["task_a"])["captured_memory_ids"],
            "recalled_memory_ids": cast(dict[str, object], pico["task_b"])["recalled_memory_ids"],
        },
    )
    _record_source_pilot_observation(artifact_dir, observation)


def _record_complete_human_evidence(
    artifact_dir: Path, *, participant_kind: str = "human", reviewer_kind: str = "human", failing_participants: set[str] | None = None
) -> None:
    failing_participants = failing_participants or set()
    snapshot_path = artifact_dir / PRESENTATION_SNAPSHOT_PATH
    if not snapshot_path.exists():
        _write(snapshot_path, {"contract": "codecairn.v03-acceptance.hub-snapshot.v1", "views": ["memories", "recall", "system"]})
    for index in range(1, 6):
        participant_id = f"P{index:03d}"
        _record_questionnaire_response(
            artifact_dir, _participant_response(artifact_dir, participant_id, participant_kind=participant_kind)
        )
        _record_questionnaire_review(
            artifact_dir,
            _participant_review(
                artifact_dir, participant_id, reviewer_kind=reviewer_kind, passed=participant_id not in failing_participants
            ),
        )


def _participant_response(artifact_dir: Path, participant_id: str, *, participant_kind: str = "human") -> dict[str, object]:
    snapshot_path = artifact_dir / PRESENTATION_SNAPSHOT_PATH
    return {
        "schema_version": 1,
        "contract": "codecairn.v03-acceptance.participant-response.v1",
        "participant_id": participant_id,
        "participant_kind": participant_kind,
        "moderator_content_hint_count": 0,
        "eligibility": {"prior_codecairn_exposure": False, "codecairn_contributor": False, "target_learner": True},
        "consent_to_local_evidence": True,
        "presentation": {
            "candidate_sha256": canonical_sha256(read_json(artifact_dir / "manifest.json")),
            "hub_snapshot_path": PRESENTATION_SNAPSHOT_PATH,
            "hub_snapshot_sha256": file_sha256(snapshot_path),
        },
        "answers": {question_id: {"answer": f"{question_id} answer"} for question_id in _question_ids()},
    }


def _participant_review(
    artifact_dir: Path, participant_id: str, *, reviewer_kind: str = "human", passed: bool = True
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": "codecairn.v03-acceptance.review.v1",
        "participant_id": participant_id,
        "reviewer_id": "R001",
        "reviewer_kind": reviewer_kind,
        "reviewer_attestation": {"independent_from_participant": True, "used_frozen_rubric_only": True},
        "response_sha256": file_sha256(artifact_dir / "participants" / participant_id / "response.json"),
        "rubric_id": "v03-comprehension-rubric-v1",
        "ratings": {
            question_id: {"verdict": "pass" if passed else "fail", "reason_code": "accurate" if passed else "inaccurate"}
            for question_id in _question_ids()
        },
    }
