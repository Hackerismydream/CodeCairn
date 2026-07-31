from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json, write_json_exclusive

PROTOCOL_CONTRACT = "codecairn.v03-acceptance.protocol.v1"
FROZEN_PROTOCOL_SHA256 = "9764be26964bebce402f4f76c2ece17b2a9b2fab30f5b89fccf1f0ee8780d408"
CAMPAIGN_CONTRACT = "codecairn.v03-acceptance.campaign.v1"
QUESTION_IDS = ("remembered", "provenance", "recall_reason", "lifecycle")
MACHINE_CHECKS = (
    "exact_candidate_identity",
    "installed_pico",
    "codecairn_backend_selected",
    "real_pico_trace",
    "fresh_process_continuity",
    "evidence_chain_valid",
    "hub_memories_read",
    "hub_recall_read",
    "hub_system_read",
    "supersession_visible",
)
MACHINE_RESULT_CONTRACT = "codecairn.v03-acceptance.machine-result.v1"
MACHINE_OBSERVATION_CONTRACT = "codecairn.v03-acceptance.machine-observation.v1"
SOURCE_PILOT_RECEIPT_CONTRACT = "codecairn.v03-acceptance.source-pilot.v1"
SOURCE_PILOT_FAILURE_CONTRACT = "codecairn.v03-acceptance.source-pilot-failure.v1"
RAW_INVENTORY_CONTRACT = "codecairn.v03-acceptance.raw-inventory.v1"
PARTICIPANT_RESPONSE_CONTRACT = "codecairn.v03-acceptance.participant-response.v1"
REVIEW_CONTRACT = "codecairn.v03-acceptance.review.v1"
SUMMARY_CONTRACT = "codecairn.v03-acceptance.summary.v1"
INVENTORY_CONTRACT = "codecairn.v03-acceptance.inventory.v1"
_SAFE_RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")
_HEX_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ARTIFACT_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}\Z")
_FACT_ID = re.compile(r"fact_[0-9a-f]{64}\Z")
_PARTICIPANT_ID = re.compile(r"P[0-9]{3}\Z")
PRESENTATION_SNAPSHOT_PATH = "machine/hub-snapshot.json"

DeliveryMode = Literal["source_checkout", "release_artifact"]
CampaignPhase = Literal["awaiting_machine", "awaiting_humans", "awaiting_review", "complete"]
VerificationOutcome = Literal["awaiting_evidence", "not_evaluable", "fail", "pass"]


@dataclass(frozen=True, slots=True)
class CampaignRequest:
    protocol_path: Path
    output_root: Path
    run_id: str
    codecairn_commit: str
    pico_commit: str
    delivery_mode: DeliveryMode
    codecairn_artifact_sha256: str | None = None
    pico_artifact_sha256: str | None = None
    hub_artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if not _SAFE_RUN_ID.fullmatch(self.run_id):
            raise ValueError("run_id must be a safe lowercase artifact identifier")
        for field, value in (("codecairn_commit", self.codecairn_commit), ("pico_commit", self.pico_commit)):
            if not _HEX_SHA.fullmatch(value):
                raise ValueError(f"{field} must be a lowercase 40-character Git commit")
        if self.delivery_mode not in {"source_checkout", "release_artifact"}:
            raise ValueError("delivery_mode is unsupported")
        artifact_digests = (self.codecairn_artifact_sha256, self.pico_artifact_sha256, self.hub_artifact_sha256)
        if self.delivery_mode == "release_artifact" and any(value is None for value in artifact_digests):
            raise ValueError("release_artifact delivery requires all artifact digests")
        if any(value is not None and not _ARTIFACT_SHA.fullmatch(value) for value in artifact_digests):
            raise ValueError("artifact digests must be lowercase 64-character SHA-256 values")


@dataclass(frozen=True, slots=True)
class CampaignHandle:
    run_id: str
    artifact_dir: Path
    phase: CampaignPhase


@dataclass(frozen=True, slots=True)
class VerificationReport:
    outcome: VerificationOutcome
    machine_complete: bool
    human_complete: bool
    release_eligible: bool | None
    violations: tuple[str, ...]


def start_campaign(request: CampaignRequest) -> CampaignHandle:
    protocol = _protocol(request.protocol_path)
    artifact_dir = request.output_root / request.run_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    write_json_exclusive(artifact_dir / "protocol.json", protocol)
    write_json_exclusive(
        artifact_dir / "manifest.json",
        {
            "schema_version": 1,
            "contract": CAMPAIGN_CONTRACT,
            "run_id": request.run_id,
            "protocol_id": protocol["protocol_id"],
            "protocol_sha256": canonical_sha256(protocol),
            "candidate": {
                "codecairn_commit": request.codecairn_commit,
                "pico_commit": request.pico_commit,
                "delivery_mode": request.delivery_mode,
                "codecairn_artifact_sha256": request.codecairn_artifact_sha256,
                "pico_artifact_sha256": request.pico_artifact_sha256,
                "hub_artifact_sha256": request.hub_artifact_sha256,
            },
        },
    )
    return CampaignHandle(run_id=request.run_id, artifact_dir=artifact_dir, phase="awaiting_machine")


def record_machine_observation(artifact_dir: Path, observation: object) -> None:
    """Record diagnostic normalized evidence that is never considered automated."""
    _record_machine_observation(artifact_dir, observation, collector="manual")


def _record_source_pilot_observation(artifact_dir: Path, observation: object) -> None:
    """Record evidence only after the source orchestrator has frozen raw receipts."""
    _record_machine_observation(artifact_dir, observation, collector="source_pilot")


def _record_machine_observation(artifact_dir: Path, observation: object, *, collector: Literal["manual", "source_pilot"]) -> None:
    _assert_unsealed(artifact_dir)
    manifest = _manifest(artifact_dir / "manifest.json")
    candidate_observation = _object(observation, field="machine observation")
    if "collector" in candidate_observation:
        raise ValueError("machine evidence collector is assigned by the trusted entrypoint")
    normalized = _machine_observation({**candidate_observation, "collector": collector})
    result = _derive_machine_result(manifest, normalized)
    write_json_exclusive(artifact_dir / "machine" / "observation.json", normalized)
    write_json_exclusive(artifact_dir / "machine" / "result.json", result)


def _record_questionnaire_response(artifact_dir: Path, response: object) -> None:
    _record_participant_response(artifact_dir, response, collector="questionnaire")


def _record_participant_response(artifact_dir: Path, response: object, *, collector: Literal["manual", "questionnaire"]) -> None:
    _assert_unsealed(artifact_dir)
    protocol = _protocol(artifact_dir / "protocol.json")
    value = _object(response, field="participant response")
    participant_id = value.get("participant_id")
    if not isinstance(participant_id, str) or participant_id not in _participant_ids(protocol):
        raise ValueError("participant response identity is outside the frozen roster")
    if "collector" in value:
        raise ValueError("participant collector is assigned by the trusted entrypoint")
    validated = _participant_response_value({**value, "collector": collector}, participant_id=participant_id)
    write_json_exclusive(artifact_dir / "participants" / participant_id / "response.json", validated)


def _record_questionnaire_review(artifact_dir: Path, review: object) -> None:
    _record_review(artifact_dir, review, collector="questionnaire")


def _record_review(artifact_dir: Path, review: object, *, collector: Literal["manual", "questionnaire"]) -> None:
    _assert_unsealed(artifact_dir)
    protocol = _protocol(artifact_dir / "protocol.json")
    value = _object(review, field="participant review")
    participant_id = value.get("participant_id")
    if not isinstance(participant_id, str) or participant_id not in _participant_ids(protocol):
        raise ValueError("participant review identity is outside the frozen roster")
    if "collector" in value:
        raise ValueError("review collector is assigned by the trusted entrypoint")
    rubric = _object(protocol["rubric"], field="acceptance rubric")
    validated = _review_value({**value, "collector": collector}, participant_id=participant_id, rubric_id=cast(str, rubric["id"]))
    write_json_exclusive(artifact_dir / "reviews" / f"{participant_id}.json", validated)


def seal_campaign(artifact_dir: Path) -> VerificationReport:
    if (artifact_dir / "inventory.json").exists():
        return verify_campaign(artifact_dir)
    report = _derive_report(artifact_dir, sealed=True)
    if report.outcome == "awaiting_evidence":
        raise ValueError("campaign cannot be sealed while evidence is incomplete")
    summary = _summary(report)
    summary_path = artifact_dir / "summary.json"
    if summary_path.exists():
        if read_json(summary_path) != summary:
            raise ValueError("campaign summary does not match recomputed evidence")
    else:
        write_json_exclusive(summary_path, summary)
    write_json_exclusive(artifact_dir / "inventory.json", _inventory(artifact_dir))
    return verify_campaign(artifact_dir)


def verify_campaign(artifact_dir: Path) -> VerificationReport:
    sealed = (artifact_dir / "inventory.json").is_file()
    if sealed:
        _verify_inventory(artifact_dir)
    report = _derive_report(artifact_dir, sealed=sealed)
    if sealed and read_json(artifact_dir / "summary.json") != _summary(report):
        raise ValueError("campaign summary does not match recomputed evidence")
    return report


def _derive_report(artifact_dir: Path, *, sealed: bool) -> VerificationReport:
    manifest = _object(read_json(artifact_dir / "manifest.json"), field="campaign manifest")
    protocol = _protocol(artifact_dir / "protocol.json")
    _validate_manifest(manifest)
    if manifest.get("protocol_sha256") != canonical_sha256(protocol):
        raise ValueError("campaign protocol digest does not match its manifest")
    observation_path = artifact_dir / "machine" / "observation.json"
    result_path = artifact_dir / "machine" / "result.json"
    failure_path = artifact_dir / "machine" / "collector-failure.json"
    if failure_path.is_file():
        if observation_path.is_file() or result_path.is_file():
            raise ValueError("source-pilot success and failure evidence conflict")
        failure = _source_pilot_failure(read_json(failure_path))
        if failure["terminal_class"] == "infrastructure_failure":
            return VerificationReport(
                outcome="not_evaluable",
                machine_complete=True,
                human_complete=False,
                release_eligible=None,
                violations=("machine_infrastructure_failed",),
            )
        return VerificationReport(
            outcome="fail", machine_complete=True, human_complete=False, release_eligible=False, violations=("machine_evidence_failed",)
        )
    if not observation_path.is_file() and not result_path.is_file():
        return VerificationReport(
            outcome="awaiting_evidence",
            machine_complete=False,
            human_complete=False,
            release_eligible=None,
            violations=("machine_evidence_missing",),
        )
    if not observation_path.is_file() or not result_path.is_file():
        return VerificationReport(
            outcome="awaiting_evidence",
            machine_complete=False,
            human_complete=False,
            release_eligible=None,
            violations=("machine_evidence_incomplete",),
        )
    observation = _machine_observation(read_json(observation_path))
    if observation["collector"] == "source_pilot":
        _verify_source_pilot_evidence(artifact_dir, observation)
    machine = _machine_result(result_path)
    if machine != _derive_machine_result(manifest, observation):
        raise ValueError("machine result does not match recomputed raw observation")
    terminal_class = machine["terminal_class"]
    if terminal_class == "infrastructure_failure":
        return VerificationReport(
            outcome="not_evaluable",
            machine_complete=True,
            human_complete=False,
            release_eligible=None,
            violations=("machine_infrastructure_failed",),
        )
    if terminal_class == "failed":
        return VerificationReport(
            outcome="fail", machine_complete=True, human_complete=False, release_eligible=False, violations=("machine_gate_failed",)
        )
    if observation["collector"] != "source_pilot":
        return VerificationReport(
            outcome="not_evaluable",
            machine_complete=True,
            human_complete=False,
            release_eligible=None,
            violations=("machine_evidence_not_automated",),
        )
    promotion = _object(protocol["promotion"], field="acceptance promotion")
    participant_ids = _participant_ids(protocol)
    missing = tuple(
        participant_id
        for participant_id in participant_ids
        if not (artifact_dir / "participants" / participant_id / "response.json").is_file()
        or not (artifact_dir / "reviews" / f"{participant_id}.json").is_file()
    )
    if missing:
        return VerificationReport(
            outcome="awaiting_evidence",
            machine_complete=True,
            human_complete=False,
            release_eligible=None,
            violations=("participant_evidence_missing",),
        )
    participant_passes = 0
    valid_participants = 0
    question_passes = dict.fromkeys(QUESTION_IDS, 0)
    formal_humans = True
    rubric = _object(protocol["rubric"], field="acceptance rubric")
    for participant_id in participant_ids:
        response = _participant_response(
            artifact_dir / "participants" / participant_id / "response.json", participant_id=participant_id
        )
        response_sha256 = file_sha256(artifact_dir / "participants" / participant_id / "response.json")
        review = _review(
            artifact_dir / "reviews" / f"{participant_id}.json",
            participant_id=participant_id,
            rubric_id=cast(str, rubric["id"]),
            response_sha256=response_sha256,
        )
        eligibility = cast(dict[str, bool], response["eligibility"])
        presentation = cast(dict[str, str], response["presentation"])
        snapshot_path = artifact_dir / PRESENTATION_SNAPSHOT_PATH
        presentation_matches = (
            presentation["candidate_sha256"] == canonical_sha256(manifest)
            and presentation["hub_snapshot_path"] == PRESENTATION_SNAPSHOT_PATH
            and snapshot_path.is_file()
            and presentation["hub_snapshot_sha256"] == file_sha256(snapshot_path)
        )
        participant_is_valid = (
            response["collector"] == "questionnaire"
            and response["participant_kind"] == "human"
            and review["collector"] == "questionnaire"
            and review["reviewer_kind"] == "human"
            and cast(int, response["moderator_content_hint_count"]) <= cast(int, promotion["moderator_content_hints_max"])
            and response["consent_to_local_evidence"] is True
            and eligibility["prior_codecairn_exposure"] is False
            and eligibility["codecairn_contributor"] is False
            and eligibility["target_learner"] is True
            and presentation_matches
        )
        formal_humans = formal_humans and participant_is_valid
        valid_participants += int(participant_is_valid)
        ratings = cast(dict[str, dict[str, str]], review["ratings"])
        for question_id in QUESTION_IDS:
            question_passes[question_id] += int(participant_is_valid and ratings[question_id]["verdict"] == "pass")
        participant_passes += int(
            participant_is_valid and all(ratings[question_id]["verdict"] == "pass" for question_id in QUESTION_IDS)
        )
    if valid_participants < cast(int, promotion["minimum_valid_participants"]):
        return VerificationReport(
            outcome="not_evaluable",
            machine_complete=True,
            human_complete=True,
            release_eligible=None,
            violations=("human_evidence_not_formal",),
        )
    threshold_passed = participant_passes >= cast(int, promotion["minimum_participant_passes"]) and all(
        question_passes[question_id] >= cast(int, promotion["minimum_passes_per_question"]) for question_id in QUESTION_IDS
    )
    outcome: VerificationOutcome = "pass" if threshold_passed else "fail"
    candidate = _object(manifest.get("candidate"), field="campaign candidate")
    violations: list[str] = []
    if candidate.get("delivery_mode") != "release_artifact":
        violations.append("delivery_mode_source_checkout")
    else:
        # The current collector can run a truthful source pilot, but the Hub
        # has no immutable installed distribution yet. Keep release promotion
        # fail-closed until raw installed-artifact receipts replace the
        # operator-supplied normalized observation.
        violations.append("formal_release_collector_unavailable")
    if machine["pico_adapter"] != "subprocess":
        violations.append("pico_adapter_not_live")
    if machine["hub_adapter"] != "http":
        violations.append("hub_adapter_not_live")
    if not formal_humans:
        violations.append("human_evidence_not_formal")
    if not sealed:
        violations.append("campaign_unsealed")
    return VerificationReport(
        outcome=outcome,
        machine_complete=True,
        human_complete=True,
        release_eligible=outcome == "pass" and not violations,
        violations=tuple(violations),
    )


def _source_pilot_failure(value: object) -> dict[str, object]:
    failure = _object(value, field="source-pilot failure")
    if (
        set(failure) != {"schema_version", "contract", "terminal_class", "step", "failure_code", "exception_type", "message"}
        or failure["schema_version"] != 1
        or failure["contract"] != SOURCE_PILOT_FAILURE_CONTRACT
        or failure["terminal_class"] not in {"evidence_failure", "infrastructure_failure"}
        or any(
            not isinstance(failure[name], str) or not failure[name] for name in ("step", "failure_code", "exception_type", "message")
        )
    ):
        raise ValueError("source-pilot failure artifact is invalid")
    return failure


def _verify_source_pilot_evidence(artifact_dir: Path, observation: dict[str, object]) -> None:
    raw_dir = artifact_dir / "machine" / "raw"
    receipt = _object(read_json(raw_dir / "collector-receipt.json"), field="source-pilot receipt")
    required = {
        "schema_version",
        "contract",
        "mode",
        "candidate_identity_sha256",
        "stage_sha256",
        "task_verification_sha256",
        "hub_snapshot_sha256",
        "raw_inventory_sha256",
        "observation_sha256",
        "hub_bundle_sha256",
        "captured_memory_ids",
        "recalled_memory_ids",
    }
    if set(receipt) != required or receipt["schema_version"] != 1 or receipt["contract"] != SOURCE_PILOT_RECEIPT_CONTRACT:
        raise ValueError("source-pilot receipt fields are invalid")
    for name in required & {
        "candidate_identity_sha256",
        "stage_sha256",
        "task_verification_sha256",
        "hub_snapshot_sha256",
        "raw_inventory_sha256",
        "observation_sha256",
        "hub_bundle_sha256",
    }:
        if not isinstance(receipt[name], str) or not _ARTIFACT_SHA.fullmatch(cast(str, receipt[name])):
            raise ValueError("source-pilot receipt digest is invalid")
    mode = receipt["mode"]
    pico = _object(observation["pico"], field="Pico observation")
    hub = _object(observation["hub"], field="Hub observation")
    if mode not in {"live", "scripted"} or (mode == "live") != (pico["adapter"] == "subprocess" and hub["adapter"] == "http"):
        raise ValueError("source-pilot receipt mode does not match its observation")
    inventory_path = raw_dir / "raw-inventory.json"
    inventory = _object(read_json(inventory_path), field="source-pilot raw inventory")
    if (
        set(inventory) != {"schema_version", "contract", "files"}
        or inventory["schema_version"] != 1
        or inventory["contract"] != RAW_INVENTORY_CONTRACT
        or receipt["raw_inventory_sha256"] != file_sha256(inventory_path)
        or inventory["files"] != _raw_inventory(raw_dir)
    ):
        raise ValueError("source-pilot raw inventory is invalid")
    paths = {item["path"] for item in cast(list[dict[str, object]], inventory["files"])}
    required_paths = {
        "candidate-identity.json",
        "stage.json",
        "workspace-identity.json",
        "pico-config.json",
        "list-before-receipt.json",
        "learn-evidence.json",
        "task-verification.json",
        "list-after-receipt.json",
        "public-recall-receipt.json",
        "recall-evidence.json",
        "lifecycle-seed.json",
    }
    if not required_paths <= paths or (mode == "live" and "candidate-identity-final.json" not in paths):
        raise ValueError("source-pilot raw evidence is incomplete")
    candidate = _object(read_json(raw_dir / "candidate-identity.json"), field="source candidate identity")
    manifest = _object(read_json(artifact_dir / "manifest.json"), field="campaign manifest")
    manifest_candidate = _object(manifest.get("candidate"), field="campaign candidate")
    for name, commit_field in (("codecairn", "codecairn_commit"), ("pico", "pico_commit")):
        identity = _object(candidate.get(name), field=f"{name} candidate identity")
        if identity.get("commit") != manifest_candidate[commit_field] or identity.get("clean") is not True:
            raise ValueError("source-pilot candidate identity is invalid")
    if mode == "live" and read_json(raw_dir / "candidate-identity-final.json") != candidate:
        raise ValueError("source-pilot candidate identity drifted")
    snapshot = _object(read_json(artifact_dir / PRESENTATION_SNAPSHOT_PATH), field="Hub snapshot")
    task_a = _object(pico["task_a"], field="Pico task A")
    task_b = _object(pico["task_b"], field="Pico task B")
    workspace = _object(read_json(raw_dir / "workspace-identity.json"), field="source workspace identity")
    hub_build = _object(workspace.get("hub_build"), field="Hub build identity")
    bundle = _object(hub_build.get("bundle"), field="Hub bundle identity")
    if (
        receipt["candidate_identity_sha256"] != canonical_sha256(candidate)
        or receipt["stage_sha256"] != canonical_sha256(read_json(raw_dir / "stage.json"))
        or receipt["task_verification_sha256"] != canonical_sha256(read_json(raw_dir / "task-verification.json"))
        or receipt["hub_snapshot_sha256"] != file_sha256(artifact_dir / PRESENTATION_SNAPSHOT_PATH)
        or receipt["observation_sha256"] != canonical_sha256(observation)
        or receipt["hub_bundle_sha256"] != bundle.get("tree_sha256")
        or snapshot.get("machine_observation") != hub
        or receipt["captured_memory_ids"] != task_a["captured_memory_ids"]
        or receipt["recalled_memory_ids"] != task_b["recalled_memory_ids"]
    ):
        raise ValueError("source-pilot receipt does not bind its derived evidence")


def _raw_inventory(raw_dir: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(raw_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("source-pilot raw evidence cannot contain symlinks")
        if not path.is_file() or path.name in {"collector-receipt.json", "raw-inventory.json"}:
            continue
        files.append({"path": path.relative_to(raw_dir).as_posix(), "sha256": file_sha256(path), "bytes": path.stat().st_size})
    return files


def _manifest(path: Path) -> dict[str, object]:
    manifest = _object(read_json(path), field="campaign manifest")
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: dict[str, object]) -> None:
    if set(manifest) != {"schema_version", "contract", "run_id", "protocol_id", "protocol_sha256", "candidate"}:
        raise ValueError("campaign manifest fields are invalid")
    if manifest["schema_version"] != 1 or manifest["contract"] != CAMPAIGN_CONTRACT:
        raise ValueError("campaign manifest contract is unsupported")
    if not isinstance(manifest["run_id"], str) or not _SAFE_RUN_ID.fullmatch(manifest["run_id"]):
        raise ValueError("campaign run id is invalid")
    if not isinstance(manifest["protocol_id"], str) or not _SAFE_RUN_ID.fullmatch(manifest["protocol_id"]):
        raise ValueError("campaign protocol id is invalid")
    if not isinstance(manifest["protocol_sha256"], str) or not _ARTIFACT_SHA.fullmatch(manifest["protocol_sha256"]):
        raise ValueError("campaign protocol digest is invalid")
    _candidate(manifest["candidate"])


def _machine_observation(value: object) -> dict[str, object]:
    observation = _object(value, field="machine observation")
    if set(observation) != {
        "schema_version",
        "contract",
        "terminal_class",
        "failure_code",
        "collector",
        "candidate",
        "installed",
        "pico",
        "codecairn",
        "hub",
    }:
        raise ValueError("machine observation fields are invalid")
    if observation["schema_version"] != 1 or observation["contract"] != MACHINE_OBSERVATION_CONTRACT:
        raise ValueError("machine observation contract is unsupported")
    if observation["collector"] not in {"manual", "source_pilot"}:
        raise ValueError("machine evidence collector is invalid")
    terminal_class = observation["terminal_class"]
    failure_code = observation["failure_code"]
    if terminal_class not in {"completed", "infrastructure_failure"}:
        raise ValueError("machine observation terminal class is invalid")
    if (terminal_class == "completed" and failure_code is not None) or (
        terminal_class == "infrastructure_failure" and (not isinstance(failure_code, str) or not failure_code)
    ):
        raise ValueError("machine observation failure code is invalid")
    _candidate(observation["candidate"])
    _installed_identity(observation["installed"])
    _pico_observation(observation["pico"])
    _codecairn_observation(observation["codecairn"])
    _hub_observation(observation["hub"])
    return observation


def _derive_machine_result(manifest: dict[str, object], observation: dict[str, object]) -> dict[str, object]:
    candidate = _candidate(observation["candidate"])
    installed = _installed_identity(observation["installed"])
    pico = _object(observation["pico"], field="Pico observation")
    task_a = _object(pico["task_a"], field="Pico task A")
    task_b = _object(pico["task_b"], field="Pico task B")
    codecairn = _object(observation["codecairn"], field="CodeCairn observation")
    hub = _object(observation["hub"], field="Hub observation")
    captured = set(cast(list[str], task_a["captured_memory_ids"]))
    recalled = set(cast(list[str], task_b["recalled_memory_ids"]))
    injected = set(cast(list[str], task_b["llm_input_memory_ids"]))
    continuity = captured & recalled & injected
    source_journal = set(cast(list[str], codecairn["source_journal_memory_ids"]))
    public_recall = set(cast(list[str], codecairn["public_recall_memory_ids"]))
    evidence_references = set(cast(list[str], codecairn["evidence_reference_memory_ids"]))
    hub_memories = set(cast(list[str], hub["memories_memory_ids"]))
    hub_recall = set(cast(list[str], hub["recall_memory_ids"]))
    hub_ranked = set(cast(list[str], hub["recall_ranked_memory_ids"]))
    selected_memory_id = cast(str, hub["selected_memory_id"])
    selected_evidence = cast(list[str], hub["selected_evidence_fact_ids"])
    selected_references = cast(list[dict[str, str]], hub["selected_evidence_references"])
    admission = cast(dict[str, str], hub["recall_admission"])
    readiness = cast(dict[str, object], hub["recall_readiness"])
    statuses = cast(dict[str, str], hub["statuses"])
    supersessions = cast(list[dict[str, str]], hub["supersessions"])
    artifact_names = ("codecairn_artifact_sha256", "pico_artifact_sha256", "hub_artifact_sha256")
    installed_artifacts_match = all(installed[name] == candidate[name] for name in artifact_names)
    source_identity_isolated = candidate["delivery_mode"] != "release_artifact" or installed["source_checkouts_absent"] is True
    checks = {
        "exact_candidate_identity": (candidate == manifest["candidate"] and installed_artifacts_match and source_identity_isolated),
        "installed_pico": (
            pico["install_kind"] == "installed_distribution" and installed["plugin_entry_point"] == "codecairn.integrations.pico"
        ),
        "codecairn_backend_selected": pico["plugin_id"] == "codecairn-memory" and pico["backend"] == "codecairn",
        "real_pico_trace": (
            pico["adapter"] == "subprocess"
            and pico["provider_mode"] == "configured"
            and task_a["trace_contract"] == "audit.span.v1"
            and task_b["trace_contract"] == "audit.span.v1"
            and task_a["task_verified"] is True
            and bool(captured)
        ),
        "fresh_process_continuity": (
            task_a["process_id"] != task_b["process_id"]
            and task_a["session_id"] != task_b["session_id"]
            and cast(int, task_b["forbidden_tool_calls"]) == 0
            and bool(continuity)
        ),
        "evidence_chain_valid": bool(
            continuity and continuity <= source_journal and continuity <= public_recall and continuity <= evidence_references
        ),
        "hub_memories_read": bool(
            continuity
            and continuity <= hub_memories
            and selected_memory_id in continuity
            and selected_evidence
            and any(
                reference["provider"] == "pico"
                and reference["session_id"] == task_a["session_id"]
                and reference["fact_id"] in selected_evidence
                for reference in selected_references
            )
        ),
        "hub_recall_read": bool(
            continuity
            and continuity <= hub_recall
            and continuity <= hub_ranked
            and admission["outcome"] == "admitted"
            and admission["reason"]
        ),
        "hub_system_read": (
            isinstance(hub["repository_key"], str)
            and bool(hub["repository_key"])
            and hub["repository_key"] == hub["system_repository_key"]
            and hub["repository_key"] == hub["recall_repository_key"]
            and hub["repository_key"] == hub["lifecycle_repository_key"]
            and hub["system_status"] in {"ok", "degraded"}
            and isinstance(readiness["state"], str)
            and bool(readiness["state"])
        ),
        "supersession_visible": any(
            relation["predecessor_id"] in hub_memories
            and relation["successor_id"] in hub_memories
            and relation["predecessor_id"] not in hub_recall
            and relation["predecessor_id"] not in hub_ranked
            and statuses.get(relation["predecessor_id"]) == "superseded"
            and statuses.get(relation["successor_id"]) == "active"
            for relation in supersessions
        ),
    }
    terminal_class = (
        "infrastructure_failure"
        if observation["terminal_class"] == "infrastructure_failure"
        else ("passed" if all(checks.values()) else "failed")
    )
    return {
        "schema_version": 1,
        "contract": MACHINE_RESULT_CONTRACT,
        "terminal_class": terminal_class,
        "pico_adapter": pico["adapter"],
        "hub_adapter": hub["adapter"],
        "checks": checks,
    }


def _machine_result(path: Path) -> dict[str, object]:
    result = _object(read_json(path), field="machine result")
    if set(result) != {"schema_version", "contract", "terminal_class", "pico_adapter", "hub_adapter", "checks"}:
        raise ValueError("machine result fields are invalid")
    if result["schema_version"] != 1 or result["contract"] != MACHINE_RESULT_CONTRACT:
        raise ValueError("machine result contract is unsupported")
    if result["terminal_class"] not in {"passed", "failed", "infrastructure_failure"}:
        raise ValueError("machine result terminal class is invalid")
    if result["pico_adapter"] not in {"subprocess", "scripted"} or result["hub_adapter"] not in {"http", "in_process"}:
        raise ValueError("machine result adapter identity is invalid")
    checks = _object(result["checks"], field="machine checks")
    if set(checks) != set(MACHINE_CHECKS) or any(not isinstance(checks[name], bool) for name in MACHINE_CHECKS):
        raise ValueError("machine result checks are invalid")
    if result["terminal_class"] == "passed" and not all(cast(bool, checks[name]) for name in MACHINE_CHECKS):
        raise ValueError("passed machine result contains a failed check")
    return result


def _candidate(value: object) -> dict[str, object]:
    candidate = _object(value, field="campaign candidate")
    if set(candidate) != {
        "codecairn_commit",
        "pico_commit",
        "delivery_mode",
        "codecairn_artifact_sha256",
        "pico_artifact_sha256",
        "hub_artifact_sha256",
    }:
        raise ValueError("campaign candidate fields are invalid")
    for name in ("codecairn_commit", "pico_commit"):
        commit = candidate[name]
        if not isinstance(commit, str) or not _HEX_SHA.fullmatch(commit):
            raise ValueError("campaign candidate commit is invalid")
    if candidate["delivery_mode"] not in {"source_checkout", "release_artifact"}:
        raise ValueError("campaign candidate delivery mode is invalid")
    digests = (candidate["codecairn_artifact_sha256"], candidate["pico_artifact_sha256"], candidate["hub_artifact_sha256"])
    if candidate["delivery_mode"] == "release_artifact" and any(value is None for value in digests):
        raise ValueError("release artifact candidate is missing artifact digests")
    if any(value is not None and (not isinstance(value, str) or not _ARTIFACT_SHA.fullmatch(value)) for value in digests):
        raise ValueError("campaign candidate artifact digest is invalid")
    return candidate


def _installed_identity(value: object) -> dict[str, object]:
    identity = _object(value, field="installed candidate identity")
    if set(identity) != {
        "codecairn_artifact_sha256",
        "pico_artifact_sha256",
        "hub_artifact_sha256",
        "source_checkouts_absent",
        "plugin_entry_point",
    }:
        raise ValueError("installed candidate identity fields are invalid")
    for name in ("codecairn_artifact_sha256", "pico_artifact_sha256", "hub_artifact_sha256"):
        digest = identity[name]
        if digest is not None and (not isinstance(digest, str) or not _ARTIFACT_SHA.fullmatch(digest)):
            raise ValueError("installed candidate artifact digest is invalid")
    if not isinstance(identity["source_checkouts_absent"], bool):
        raise ValueError("installed candidate checkout isolation is invalid")
    if not isinstance(identity["plugin_entry_point"], str) or not identity["plugin_entry_point"]:
        raise ValueError("installed candidate plugin entry point is invalid")
    return identity


def _pico_observation(value: object) -> dict[str, object]:
    pico = _object(value, field="Pico observation")
    if set(pico) != {"adapter", "install_kind", "provider_mode", "plugin_id", "backend", "task_a", "task_b"}:
        raise ValueError("Pico observation fields are invalid")
    if pico["adapter"] not in {"subprocess", "scripted"}:
        raise ValueError("Pico observation adapter is invalid")
    if pico["install_kind"] not in {"installed_distribution", "source_checkout", "scripted"}:
        raise ValueError("Pico observation install kind is invalid")
    if pico["provider_mode"] not in {"configured", "local_deterministic", "scripted"}:
        raise ValueError("Pico observation provider mode is invalid")
    if not isinstance(pico["plugin_id"], str) or not isinstance(pico["backend"], str):
        raise ValueError("Pico plugin identity is invalid")
    task_a = _object(pico["task_a"], field="Pico task A")
    if set(task_a) != {"process_id", "session_id", "trace_contract", "task_verified", "captured_memory_ids"}:
        raise ValueError("Pico task A fields are invalid")
    task_b = _object(pico["task_b"], field="Pico task B")
    if set(task_b) != {
        "process_id",
        "session_id",
        "trace_contract",
        "recalled_memory_ids",
        "llm_input_memory_ids",
        "forbidden_tool_calls",
    }:
        raise ValueError("Pico task B fields are invalid")
    for task in (task_a, task_b):
        for field in ("process_id", "session_id", "trace_contract"):
            if not isinstance(task[field], str) or not task[field]:
                raise ValueError("Pico task identity is invalid")
    if not isinstance(task_a["task_verified"], bool):
        raise ValueError("Pico task verifier result is invalid")
    _memory_ids(task_a["captured_memory_ids"], field="Pico captured memories")
    _memory_ids(task_b["recalled_memory_ids"], field="Pico recalled memories")
    _memory_ids(task_b["llm_input_memory_ids"], field="Pico injected memories")
    forbidden = task_b["forbidden_tool_calls"]
    if not isinstance(forbidden, int) or isinstance(forbidden, bool) or forbidden < 0:
        raise ValueError("Pico forbidden tool call count is invalid")
    return pico


def _codecairn_observation(value: object) -> dict[str, object]:
    observation = _object(value, field="CodeCairn observation")
    if set(observation) != {"source_journal_memory_ids", "public_recall_memory_ids", "evidence_reference_memory_ids"}:
        raise ValueError("CodeCairn observation fields are invalid")
    for name in observation:
        _memory_ids(observation[name], field=f"CodeCairn {name}")
    return observation


def _hub_observation(value: object) -> dict[str, object]:
    hub = _object(value, field="Hub observation")
    if set(hub) != {
        "adapter",
        "repository_key",
        "system_repository_key",
        "recall_repository_key",
        "lifecycle_repository_key",
        "memories_memory_ids",
        "selected_memory_id",
        "selected_evidence_fact_ids",
        "selected_evidence_references",
        "recall_memory_ids",
        "recall_ranked_memory_ids",
        "recall_admission",
        "recall_omissions",
        "recall_context_sha256",
        "system_status",
        "recall_readiness",
        "statuses",
        "supersessions",
    }:
        raise ValueError("Hub observation fields are invalid")
    if hub["adapter"] not in {"http", "in_process"}:
        raise ValueError("Hub observation adapter is invalid")
    for name in ("repository_key", "system_repository_key", "recall_repository_key", "lifecycle_repository_key"):
        if not isinstance(hub[name], str):
            raise ValueError("Hub repository identity is invalid")
    memories = _memory_ids(hub["memories_memory_ids"], field="Hub memories")
    selected_memory_id = hub["selected_memory_id"]
    if not isinstance(selected_memory_id, str) or not _MEMORY_ID.fullmatch(selected_memory_id) or selected_memory_id not in memories:
        raise ValueError("Hub selected memory identity is invalid")
    evidence_fact_ids = hub["selected_evidence_fact_ids"]
    if (
        not isinstance(evidence_fact_ids, list)
        or any(not isinstance(fact_id, str) or not _FACT_ID.fullmatch(fact_id) for fact_id in evidence_fact_ids)
        or len(evidence_fact_ids) != len(set(evidence_fact_ids))
    ):
        raise ValueError("Hub selected evidence identities are invalid")
    evidence_references = hub["selected_evidence_references"]
    if not isinstance(evidence_references, list):
        raise ValueError("Hub selected Evidence References must be a list")
    reference_identities: list[tuple[str, str, str]] = []
    for item in evidence_references:
        reference = _object(item, field="Hub selected Evidence Reference")
        if (
            set(reference) != {"fact_id", "provider", "session_id"}
            or not isinstance(reference["fact_id"], str)
            or not _FACT_ID.fullmatch(reference["fact_id"])
            or reference["fact_id"] not in evidence_fact_ids
            or not isinstance(reference["provider"], str)
            or not reference["provider"]
            or not isinstance(reference["session_id"], str)
            or not reference["session_id"]
        ):
            raise ValueError("Hub selected Evidence Reference is invalid")
        reference_identities.append((reference["fact_id"], reference["provider"], reference["session_id"]))
    if len(reference_identities) != len(set(reference_identities)):
        raise ValueError("Hub selected Evidence References are duplicated")
    _memory_ids(hub["recall_memory_ids"], field="Hub recalled memories")
    _memory_ids(hub["recall_ranked_memory_ids"], field="Hub ranked memories")
    admission = _object(hub["recall_admission"], field="Hub Recall admission")
    if (
        set(admission) != {"outcome", "reason"}
        or admission["outcome"] not in {"admitted", "abstained"}
        or not isinstance(admission["reason"], str)
        or not admission["reason"]
    ):
        raise ValueError("Hub Recall admission is invalid")
    omissions = hub["recall_omissions"]
    if not isinstance(omissions, list):
        raise ValueError("Hub Recall omissions must be a list")
    for item in omissions:
        omission = _object(item, field="Hub Recall omission")
        if (
            set(omission) != {"memory_id", "reason"}
            or not isinstance(omission["memory_id"], str)
            or not _MEMORY_ID.fullmatch(omission["memory_id"])
            or not isinstance(omission["reason"], str)
            or not omission["reason"]
        ):
            raise ValueError("Hub Recall omission is invalid")
    context_sha256 = hub["recall_context_sha256"]
    if not isinstance(context_sha256, str) or not _ARTIFACT_SHA.fullmatch(context_sha256):
        raise ValueError("Hub Recall context digest is invalid")
    if hub["system_status"] not in {"ok", "degraded"}:
        raise ValueError("Hub system status is invalid")
    readiness = _object(hub["recall_readiness"], field="Hub Recall readiness")
    if (
        set(readiness) != {"state", "live_checked"}
        or not isinstance(readiness["state"], str)
        or not readiness["state"]
        or not isinstance(readiness["live_checked"], bool)
    ):
        raise ValueError("Hub Recall readiness is invalid")
    statuses = _object(hub["statuses"], field="Hub memory statuses")
    if set(statuses) != set(memories) or any(
        not _MEMORY_ID.fullmatch(memory_id) or status not in {"active", "superseded"} for memory_id, status in statuses.items()
    ):
        raise ValueError("Hub memory statuses are invalid")
    supersessions = hub["supersessions"]
    if not isinstance(supersessions, list):
        raise ValueError("Hub supersessions must be a list")
    for item in supersessions:
        relation = _object(item, field="Hub supersession")
        ids = (relation.get("predecessor_id"), relation.get("successor_id"))
        if set(relation) != {"predecessor_id", "successor_id"} or any(
            not isinstance(memory_id, str) or not _MEMORY_ID.fullmatch(memory_id) for memory_id in ids
        ):
            raise ValueError("Hub supersession is invalid")
    return hub


def _memory_ids(value: object, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not _MEMORY_ID.fullmatch(item) for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{field} must contain unique Memory IDs")
    return cast(list[str], value)


def _participant_response(path: Path, *, participant_id: str) -> dict[str, object]:
    return _participant_response_value(read_json(path), participant_id=participant_id)


def _participant_response_value(value: object, *, participant_id: str) -> dict[str, object]:
    response = _object(value, field="participant response")
    if set(response) != {
        "schema_version",
        "contract",
        "participant_id",
        "collector",
        "participant_kind",
        "moderator_content_hint_count",
        "eligibility",
        "consent_to_local_evidence",
        "presentation",
        "answers",
    }:
        raise ValueError("participant response fields are invalid")
    if (
        response["schema_version"] != 1
        or response["contract"] != PARTICIPANT_RESPONSE_CONTRACT
        or response["participant_id"] != participant_id
        or response["collector"] not in {"manual", "questionnaire"}
        or response["participant_kind"] not in {"human", "scripted"}
    ):
        raise ValueError("participant response identity is invalid")
    hints = response["moderator_content_hint_count"]
    if not isinstance(hints, int) or isinstance(hints, bool) or hints < 0:
        raise ValueError("participant response hint count is invalid")
    eligibility = _object(response["eligibility"], field="participant eligibility")
    if set(eligibility) != {"prior_codecairn_exposure", "codecairn_contributor", "target_learner"} or any(
        not isinstance(value, bool) for value in eligibility.values()
    ):
        raise ValueError("participant eligibility is invalid")
    if not isinstance(response["consent_to_local_evidence"], bool):
        raise ValueError("participant evidence consent is invalid")
    presentation = _object(response["presentation"], field="participant presentation binding")
    if (
        set(presentation) != {"candidate_sha256", "hub_snapshot_path", "hub_snapshot_sha256"}
        or not isinstance(presentation["candidate_sha256"], str)
        or not _ARTIFACT_SHA.fullmatch(presentation["candidate_sha256"])
        or presentation["hub_snapshot_path"] != PRESENTATION_SNAPSHOT_PATH
        or not isinstance(presentation["hub_snapshot_sha256"], str)
        or not _ARTIFACT_SHA.fullmatch(presentation["hub_snapshot_sha256"])
    ):
        raise ValueError("participant presentation binding is invalid")
    answers = _object(response["answers"], field="participant answers")
    if set(answers) != set(QUESTION_IDS):
        raise ValueError("participant response does not answer the four questions")
    for question_id in QUESTION_IDS:
        answer = _object(answers[question_id], field=f"{question_id} answer")
        if set(answer) != {"answer"} or not isinstance(answer["answer"], str) or not answer["answer"].strip():
            raise ValueError("participant answer is empty")
    return response


def _review(path: Path, *, participant_id: str, rubric_id: str, response_sha256: str) -> dict[str, object]:
    review = _review_value(read_json(path), participant_id=participant_id, rubric_id=rubric_id)
    if review["response_sha256"] != response_sha256:
        raise ValueError("participant review does not match the reviewed response")
    return review


def _review_value(value: object, *, participant_id: str, rubric_id: str) -> dict[str, object]:
    review = _object(value, field="participant review")
    if set(review) != {
        "schema_version",
        "contract",
        "participant_id",
        "collector",
        "reviewer_id",
        "reviewer_kind",
        "reviewer_attestation",
        "response_sha256",
        "rubric_id",
        "ratings",
    }:
        raise ValueError("participant review fields are invalid")
    attestation = _object(review.get("reviewer_attestation"), field="reviewer attestation")
    if (
        review["schema_version"] != 1
        or review["contract"] != REVIEW_CONTRACT
        or review["participant_id"] != participant_id
        or review["collector"] not in {"manual", "questionnaire"}
        or not isinstance(review["reviewer_id"], str)
        or not review["reviewer_id"]
        or review["reviewer_id"] == participant_id
        or review["reviewer_kind"] not in {"human", "llm"}
        or attestation != {"independent_from_participant": True, "used_frozen_rubric_only": True}
        or not isinstance(review["response_sha256"], str)
        or not _ARTIFACT_SHA.fullmatch(review["response_sha256"])
        or review["rubric_id"] != rubric_id
    ):
        raise ValueError("participant review identity is invalid")
    ratings = _object(review["ratings"], field="participant ratings")
    if set(ratings) != set(QUESTION_IDS):
        raise ValueError("participant review ratings are invalid")
    for question_id in QUESTION_IDS:
        rating = _object(ratings[question_id], field=f"{question_id} rating")
        if (
            set(rating) != {"verdict", "reason_code"}
            or rating["verdict"] not in {"pass", "fail"}
            or rating["reason_code"] not in {"accurate", "inaccurate", "unsupported"}
            or (rating["verdict"] == "pass" and rating["reason_code"] != "accurate")
            or (rating["verdict"] == "fail" and rating["reason_code"] == "accurate")
        ):
            raise ValueError("participant review rating is invalid")
    return review


def _summary(report: VerificationReport) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": SUMMARY_CONTRACT,
        "outcome": report.outcome,
        "machine_complete": report.machine_complete,
        "human_complete": report.human_complete,
        "release_eligible": report.release_eligible,
        "violations": list(report.violations),
    }


def _inventory(artifact_dir: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("campaign inventory cannot contain symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(artifact_dir).as_posix()
        if relative == "inventory.json":
            continue
        files.append({"path": relative, "sha256": file_sha256(path), "bytes": path.stat().st_size})
    return {"schema_version": 1, "contract": INVENTORY_CONTRACT, "files": files}


def _verify_inventory(artifact_dir: Path) -> None:
    inventory = _object(read_json(artifact_dir / "inventory.json"), field="campaign inventory")
    if set(inventory) != {"schema_version", "contract", "files"}:
        raise ValueError("campaign inventory fields are invalid")
    if inventory["schema_version"] != 1 or inventory["contract"] != INVENTORY_CONTRACT:
        raise ValueError("campaign inventory contract is unsupported")
    expected = inventory["files"]
    if not isinstance(expected, list):
        raise ValueError("campaign inventory files must be a list")
    current = _inventory(artifact_dir)["files"]
    if expected != current:
        raise ValueError("campaign inventory does not match the sealed filesystem")


def _assert_unsealed(artifact_dir: Path) -> None:
    if (artifact_dir / "inventory.json").exists():
        raise ValueError("sealed campaign cannot accept new evidence")


def _participant_ids(protocol: dict[str, object]) -> tuple[str, ...]:
    promotion = _object(protocol["promotion"], field="acceptance promotion")
    planned = cast(int, promotion["planned_participants"])
    participant_ids = tuple(f"P{index:03d}" for index in range(1, planned + 1))
    if any(not _PARTICIPANT_ID.fullmatch(participant_id) for participant_id in participant_ids):
        raise ValueError("acceptance participant roster exceeds the version 1 identifier space")
    return participant_ids


def _protocol(path: Path) -> dict[str, object]:
    protocol = _object(read_json(path), field="acceptance protocol")
    required = {"schema_version", "contract", "protocol_id", "locale", "scenario", "questions", "rubric", "promotion"}
    if set(protocol) != required or protocol["schema_version"] != 1 or protocol["contract"] != PROTOCOL_CONTRACT:
        raise ValueError("acceptance protocol contract is unsupported")
    if not isinstance(protocol["protocol_id"], str) or not _SAFE_RUN_ID.fullmatch(protocol["protocol_id"]):
        raise ValueError("acceptance protocol id is invalid")
    if protocol["locale"] != "zh-CN":
        raise ValueError("version 1 acceptance protocol must use zh-CN")
    scenario = _object(protocol["scenario"], field="acceptance scenario")
    if set(scenario) != {
        "id",
        "task_a_prompt",
        "task_b_prompt",
        "recall_query",
        "recall_evidence_marker",
        "required_provider_mode",
        "supersession_source",
    }:
        raise ValueError("acceptance scenario fields are invalid")
    for name in ("id", "task_a_prompt", "task_b_prompt", "recall_query", "recall_evidence_marker"):
        text = scenario[name]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("acceptance scenario text is empty")
    if scenario["required_provider_mode"] != "configured" or scenario["supersession_source"] != "scenario_seed":
        raise ValueError("acceptance scenario evidence boundary is invalid")
    questions = protocol["questions"]
    if not isinstance(questions, list):
        raise ValueError("acceptance questions must be a list")
    ids: list[str] = []
    for item in questions:
        question = _object(item, field="acceptance question")
        if set(question) != {"id", "prompt", "pass_criterion"} or not isinstance(question["id"], str):
            raise ValueError("acceptance question fields are invalid")
        if (
            not isinstance(question["prompt"], str)
            or not question["prompt"].strip()
            or not isinstance(question["pass_criterion"], str)
            or not question["pass_criterion"].strip()
        ):
            raise ValueError("acceptance question prompt is empty")
        ids.append(question["id"])
    if tuple(ids) != QUESTION_IDS:
        raise ValueError("acceptance protocol must define the four ordered comprehension questions")
    rubric = _object(protocol["rubric"], field="acceptance rubric")
    if set(rubric) != {"id", "review_mode", "allowed_reason_codes"}:
        raise ValueError("acceptance rubric fields are invalid")
    if (
        not isinstance(rubric["id"], str)
        or not _SAFE_RUN_ID.fullmatch(rubric["id"])
        or rubric["review_mode"] != "human_blind"
        or rubric["allowed_reason_codes"] != ["accurate", "inaccurate", "unsupported"]
    ):
        raise ValueError("acceptance rubric is invalid")
    promotion = _object(protocol["promotion"], field="acceptance promotion")
    if set(promotion) != {
        "planned_participants",
        "minimum_valid_participants",
        "minimum_participant_passes",
        "minimum_passes_per_question",
        "moderator_content_hints_max",
    }:
        raise ValueError("acceptance promotion fields are invalid")
    planned = promotion["planned_participants"]
    valid_participants = promotion["minimum_valid_participants"]
    participant_passes = promotion["minimum_participant_passes"]
    question_passes = promotion["minimum_passes_per_question"]
    hints = promotion["moderator_content_hints_max"]
    if (
        not isinstance(planned, int)
        or isinstance(planned, bool)
        or planned < 1
        or not isinstance(valid_participants, int)
        or isinstance(valid_participants, bool)
        or valid_participants < 1
        or valid_participants > planned
        or not isinstance(participant_passes, int)
        or isinstance(participant_passes, bool)
        or participant_passes < 1
        or participant_passes > planned
        or not isinstance(question_passes, int)
        or isinstance(question_passes, bool)
        or question_passes < 1
        or question_passes > planned
        or hints != 0
    ):
        raise ValueError("acceptance promotion thresholds are invalid")
    if canonical_sha256(protocol) != FROZEN_PROTOCOL_SHA256:
        raise ValueError("acceptance protocol does not match the frozen version 0.3 protocol")
    return protocol


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)
