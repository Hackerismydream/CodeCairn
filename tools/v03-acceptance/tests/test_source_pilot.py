from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import cast

import pytest
from codecairn_v03_acceptance.adapters.codecairn import ListSnapshot, PublicJSONArtifact, RecallReceipt
from codecairn_v03_acceptance.adapters.hub import HubSnapshot, OperationReceipt
from codecairn_v03_acceptance.adapters.pico import PicoAdapterError
from codecairn_v03_acceptance.campaign import CampaignRequest, seal_campaign, verify_campaign
from codecairn_v03_acceptance.source_pilot import (
    LifecycleSeed,
    PicoTurnEvidence,
    PreparedPilot,
    SourcePilotError,
    SourcePilotRequest,
    SourcePilotSteps,
    _clean_checkout,
    _console_python,
    _frozen_scenario,
    _git,
    _git_output,
    _module_belongs_to,
    run_source_pilot,
)

from codecairn.evaluation.artifacts import read_json, write_bytes_exclusive

_PROTOCOL = Path(__file__).parents[1] / "protocols" / "hub-comprehension-v1.json"
_CODECAIRN_COMMIT = "a" * 40
_PICO_COMMIT = "b" * 40
_REPO_KEY = "local/v03-acceptance"
_CAPTURED = "mem_" + "1" * 64
_UNRELATED = "mem_" + "2" * 64
_PREDECESSOR = "mem_" + "3" * 64
_SUCCESSOR = "mem_" + "4" * 64
_FACT = "fact_" + "5" * 64
_LEARN_SESSION = "cli:v03-learn-scripted"
_RECALL_SESSION = "cli:v03-recall-scripted"


def test_source_pilot_derives_observation_and_preserves_ordered_receipts(tmp_path: Path) -> None:
    steps = ScriptedSteps()
    request = _request(tmp_path, steps=steps)

    result = run_source_pilot(request)

    assert result.status == "completed"
    assert result.failure_code is None
    assert result.report is not None
    assert result.report.outcome == "fail"
    assert result.report.violations == ("machine_gate_failed",)
    assert steps.calls == [
        "validate_candidates",
        "prepare",
        "list_before",
        "run_learn",
        "verify_learn",
        "list_after",
        "recall_public",
        "run_recall",
        "seed_lifecycle",
        f"snapshot_hub:{_CAPTURED}:{_SUCCESSOR}",
    ]

    observation = cast(dict[str, object], read_json(result.artifact_dir / "machine" / "observation.json"))
    assert observation["collector"] == "source_pilot"
    assert observation["candidate"] == {
        "codecairn_commit": _CODECAIRN_COMMIT,
        "pico_commit": _PICO_COMMIT,
        "delivery_mode": "source_checkout",
        "codecairn_artifact_sha256": None,
        "pico_artifact_sha256": None,
        "hub_artifact_sha256": None,
    }
    pico = cast(dict[str, object], observation["pico"])
    assert pico["adapter"] == "scripted"
    assert pico["install_kind"] == "scripted"
    assert pico["provider_mode"] == "scripted"
    task_a = cast(dict[str, object], pico["task_a"])
    task_b = cast(dict[str, object], pico["task_b"])
    assert task_a["captured_memory_ids"] == [_CAPTURED]
    assert task_a["task_verified"] is True
    assert task_b["recalled_memory_ids"] == [_CAPTURED]
    codecairn = cast(dict[str, object], observation["codecairn"])
    assert codecairn == {
        "source_journal_memory_ids": [_CAPTURED],
        "public_recall_memory_ids": [_CAPTURED],
        "evidence_reference_memory_ids": [_CAPTURED],
    }
    hub = cast(dict[str, object], observation["hub"])
    assert hub["adapter"] == "in_process"
    assert hub["selected_memory_id"] == _CAPTURED
    assert hub["selected_evidence_fact_ids"] == [_FACT]

    snapshot = cast(dict[str, object], read_json(result.artifact_dir / "machine" / "hub-snapshot.json"))
    assert snapshot["contract"] == "codecairn.v03-acceptance.hub-snapshot.v1"
    assert cast(dict[str, object], snapshot["machine_observation"]) == hub
    collector = cast(dict[str, object], read_json(result.artifact_dir / "machine" / "raw" / "collector-receipt.json"))
    assert collector["mode"] == "scripted"
    assert collector["captured_memory_ids"] == [_CAPTURED]
    assert verify_campaign(result.artifact_dir) == result.report

    with pytest.raises(FileExistsError):
        run_source_pilot(request)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (PicoAdapterError("provider_failure", "provider unavailable"), "infrastructure_failure"),
        (PicoAdapterError("evidence_incomplete", "trace missing"), "evidence_failure"),
    ],
)
def test_source_pilot_failures_are_classified_and_preserved(tmp_path: Path, error: Exception, expected_status: str) -> None:
    steps = FailingScriptedSteps(error)
    result = run_source_pilot(_request(tmp_path, steps=steps))

    assert result.status == expected_status
    assert result.failure_code == cast(PicoAdapterError, error).code
    assert result.report is not None
    failure = cast(dict[str, object], read_json(result.artifact_dir / "machine" / "collector-failure.json"))
    assert failure["terminal_class"] == expected_status
    assert failure["step"] == "run_learn"
    assert failure["failure_code"] == cast(PicoAdapterError, error).code
    assert not (result.artifact_dir / "machine" / "observation.json").exists()
    report = verify_campaign(result.artifact_dir)
    assert report == result.report
    assert report.violations == (
        ("machine_infrastructure_failed",) if expected_status == "infrastructure_failure" else ("machine_evidence_failed",)
    )
    assert seal_campaign(result.artifact_dir) == report


def test_source_pilot_rejects_release_artifact_mode_even_with_scripted_steps(tmp_path: Path) -> None:
    digest = "c" * 64
    campaign = CampaignRequest(
        protocol_path=_PROTOCOL,
        output_root=tmp_path / "results",
        run_id="release-scripted",
        codecairn_commit=_CODECAIRN_COMMIT,
        pico_commit=_PICO_COMMIT,
        delivery_mode="release_artifact",
        codecairn_artifact_sha256=digest,
        pico_artifact_sha256=digest,
        hub_artifact_sha256=digest,
    )
    with pytest.raises(ValueError, match="source_checkout"):
        SourcePilotRequest(
            campaign=campaign,
            work_root=tmp_path / "work",
            codecairn_checkout=tmp_path / "codecairn",
            pico_checkout=tmp_path / "pico",
            codecairn_executable=tmp_path / "codecairn-bin",
            pico_executable=tmp_path / "pico-bin",
            scenario_python_executable=tmp_path / "python",
            base_pico_config=tmp_path / "pico.json",
            fixture_dir=tmp_path / "fixture",
            repo_key=_REPO_KEY,
            steps=ScriptedSteps(),
        )


def test_live_source_pilot_requires_explicit_spend_authorization_before_creating_campaign(tmp_path: Path) -> None:
    request = _request(tmp_path, steps=ScriptedSteps())
    live_request = SourcePilotRequest(
        campaign=request.campaign,
        work_root=request.work_root,
        codecairn_checkout=request.codecairn_checkout,
        pico_checkout=request.pico_checkout,
        codecairn_executable=request.codecairn_executable,
        pico_executable=request.pico_executable,
        scenario_python_executable=request.scenario_python_executable,
        base_pico_config=request.base_pico_config,
        fixture_dir=request.fixture_dir,
        repo_key=request.repo_key,
    )

    with pytest.raises(ValueError, match="live_authorized"):
        run_source_pilot(live_request)

    assert not (request.campaign.output_root / request.campaign.run_id).exists()


def test_injected_steps_cannot_self_declare_live_evidence(tmp_path: Path) -> None:
    class ImpostorSteps(ScriptedSteps):
        mode = "live"

    result = run_source_pilot(_request(tmp_path, steps=ImpostorSteps()))

    assert result.status == "completed"
    assert result.report is not None
    assert result.report.outcome == "fail"
    observation = cast(dict[str, object], read_json(result.artifact_dir / "machine" / "observation.json"))
    assert cast(dict[str, object], observation["pico"])["adapter"] == "scripted"


def test_live_steps_read_the_campaigns_frozen_protocol_copy(tmp_path: Path) -> None:
    request = _request(tmp_path, steps=ScriptedSteps())
    result = run_source_pilot(request)

    assert _frozen_scenario(result.artifact_dir / "machine" / "raw")["recall_evidence_marker"] == "CI 偶发网络抖动"


def test_console_wrapper_and_module_must_match_tracked_candidate(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    package = checkout / "pkg"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    _git(checkout, "init")
    _git(checkout, "add", "pkg/__init__.py")
    _git(checkout, "-c", "user.name=test", "-c", "user.email=test@invalid", "commit", "-m", "initial")
    commit = _git_output(checkout, "rev-parse", "HEAD")
    identity = {"modules": {"pkg": str(module)}}

    tracked = _module_belongs_to(identity, "pkg", checkout, commit)

    assert tracked["path"] == "pkg/__init__.py"
    ignored = checkout / "ignored" / "pkg"
    ignored.mkdir(parents=True)
    (checkout / ".git" / "info" / "exclude").write_text("ignored/\n", encoding="utf-8")
    ignored_module = ignored / "__init__.py"
    ignored_module.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(SourcePilotError, match="not tracked"):
        _module_belongs_to({"modules": {"pkg": str(ignored_module)}}, "pkg", checkout, commit)

    wrapper = tmp_path / "bin" / "codecairn"
    wrapper.parent.mkdir()
    python = wrapper.parent / "python"
    python.write_bytes(b"python")
    python.chmod(0o700)
    wrapper.write_text(f"#!{python}\nmalicious()\n", encoding="utf-8")
    wrapper.chmod(0o700)
    with pytest.raises(SourcePilotError, match="wrapper"):
        _console_python(wrapper, module="codecairn.bootstrap", function="main")


@pytest.mark.parametrize("concealment", ["--assume-unchanged", "--skip-worktree"])
def test_source_pilot_rejects_index_concealed_runtime_changes(tmp_path: Path, concealment: str) -> None:
    checkout = tmp_path / "checkout"
    runtime = checkout / "src" / "runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    _git(checkout, "init")
    _git(checkout, "add", "src/runtime.py")
    _git(checkout, "-c", "user.name=test", "-c", "user.email=test@invalid", "commit", "-m", "initial")
    commit = _git_output(checkout, "rev-parse", "HEAD")
    _git(checkout, "update-index", concealment, "src/runtime.py")
    runtime.write_text("VALUE = 2\n", encoding="utf-8")
    assert _git_output(checkout, "status", "--porcelain=v1", "--untracked-files=all") == ""

    with pytest.raises(SourcePilotError, match="uncommitted changes"):
        _clean_checkout(checkout.resolve(), commit)


def test_source_pilot_overrides_weak_repository_stat_checks(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    runtime = checkout / "src" / "runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    timestamp = 1_600_000_000_000_000_000
    os.utime(runtime, ns=(timestamp, timestamp))
    _git(checkout, "init")
    _git(checkout, "add", "src/runtime.py")
    _git(checkout, "-c", "user.name=test", "-c", "user.email=test@invalid", "commit", "-m", "initial")
    commit = _git_output(checkout, "rev-parse", "HEAD")
    _git(checkout, "config", "core.trustctime", "false")
    _git(checkout, "config", "core.checkStat", "minimal")
    runtime.write_text("VALUE = 2\n", encoding="utf-8")
    os.utime(runtime, ns=(timestamp, timestamp))
    assert _git_output(checkout, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert _git_output(checkout, "ls-files", "-v") == "H src/runtime.py"

    with pytest.raises(SourcePilotError, match="uncommitted changes"):
        _clean_checkout(checkout.resolve(), commit)


def _request(tmp_path: Path, *, steps: SourcePilotSteps) -> SourcePilotRequest:
    campaign = CampaignRequest(
        protocol_path=_PROTOCOL,
        output_root=tmp_path / "results",
        run_id="source-pilot",
        codecairn_commit=_CODECAIRN_COMMIT,
        pico_commit=_PICO_COMMIT,
        delivery_mode="source_checkout",
    )
    return SourcePilotRequest(
        campaign=campaign,
        work_root=tmp_path / "work",
        codecairn_checkout=tmp_path / "codecairn",
        pico_checkout=tmp_path / "pico",
        codecairn_executable=tmp_path / "codecairn-bin",
        pico_executable=tmp_path / "pico-bin",
        scenario_python_executable=tmp_path / "python",
        base_pico_config=tmp_path / "pico.json",
        fixture_dir=tmp_path / "fixture",
        repo_key=_REPO_KEY,
        steps=steps,
    )


class ScriptedSteps(SourcePilotSteps):
    mode = "scripted"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_candidates(self, request: SourcePilotRequest) -> dict[str, object]:
        self.calls.append("validate_candidates")
        return {
            "schema_version": 1,
            "codecairn": {"commit": request.campaign.codecairn_commit, "clean": True},
            "pico": {"commit": request.campaign.pico_commit, "clean": True},
            "scripted": True,
        }

    def prepare(self, request: SourcePilotRequest, *, work_dir: Path, raw_dir: Path) -> PreparedPilot:
        del raw_dir
        self.calls.append("prepare")
        return PreparedPilot(
            state={"work_dir": str(work_dir), "request": request.campaign.run_id},
            repo_key=request.repo_key,
            stage_receipt={"contract": "scripted-stage", "scenario_id": "retry-policy-v1"},
            identity_receipt={"linked_worktrees": True, "scripted": True, "hub_build": {"bundle": {"tree_sha256": "d" * 64}}},
            config_receipt={"memory_backend": "codecairn", "scripted": True},
        )

    def list_before(self, prepared: PreparedPilot, *, raw_dir: Path) -> ListSnapshot:
        del prepared
        self.calls.append("list_before")
        return _list_snapshot(raw_dir / "scripted-list-before.json", [])

    def run_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> PicoTurnEvidence:
        del prepared, request, raw_dir
        self.calls.append("run_learn")
        return PicoTurnEvidence(
            process={"terminal_class": "completed", "exit_code": 0, "process_id": "scripted:learn"},
            trace={
                "trace_contract": "audit.span.v1",
                "trace_id": "trace-scripted-learn",
                "session_id": _LEARN_SESSION,
                "terminal_outcome": "completed",
            },
        )

    def verify_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> dict[str, object]:
        del prepared, request, raw_dir
        self.calls.append("verify_learn")
        return {"contract": "codecairn.v03-acceptance.retry-policy-verification.v1", "task_verified": True, "violations": []}

    def list_after(self, prepared: PreparedPilot, *, raw_dir: Path) -> ListSnapshot:
        del prepared
        self.calls.append("list_after")
        captured = {
            "memory_id": _CAPTURED,
            "memory_type": "task_experience",
            "origin": "capture",
            "repo_key": _REPO_KEY,
            "evidence": [{"provider": "pico", "session_id": _LEARN_SESSION}],
        }
        unrelated = {
            "memory_id": _UNRELATED,
            "memory_type": "repository_knowledge",
            "origin": "agent_asserted",
            "repo_key": _REPO_KEY,
            "evidence": [],
        }
        return _list_snapshot(raw_dir / "scripted-list-after.json", [unrelated, captured])

    def recall_public(self, prepared: PreparedPilot, *, query: str, expected_memory_ids: set[str], raw_dir: Path) -> RecallReceipt:
        del prepared
        self.calls.append("recall_public")
        assert expected_memory_ids == {_CAPTURED}
        raw = json.dumps({"sidecar": "scripted"}, sort_keys=True).encode()
        path = raw_dir / "scripted-public-recall.json"
        write_bytes_exclusive(path, raw)
        return RecallReceipt(
            artifact=PublicJSONArtifact(path=path, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)),
            repo_key=_REPO_KEY,
            query=query,
            source_cursor=3,
            index_cursor=3,
            ranked_memory_ids=(_CAPTURED,),
            rendered_memory_ids=(_CAPTURED,),
            recalled_memory_ids=(_CAPTURED,),
            source_uris=(f"codecairn://memory/{_CAPTURED}",),
        )

    def run_recall(
        self, prepared: PreparedPilot, *, request: SourcePilotRequest, expected_memory_ids: set[str], raw_dir: Path
    ) -> PicoTurnEvidence:
        del prepared, request, raw_dir
        self.calls.append("run_recall")
        assert expected_memory_ids == {_CAPTURED}
        return PicoTurnEvidence(
            process={"terminal_class": "completed", "exit_code": 0, "process_id": "scripted:recall"},
            trace={
                "trace_contract": "audit.span.v1",
                "trace_id": "trace-scripted-recall",
                "session_id": _RECALL_SESSION,
                "terminal_outcome": "completed",
                "recalled_memory_ids": [_CAPTURED],
                "llm_input_memory_ids": [_CAPTURED],
                "forbidden_tool_calls": 0,
            },
        )

    def seed_lifecycle(self, prepared: PreparedPilot, *, raw_dir: Path) -> LifecycleSeed:
        del prepared, raw_dir
        self.calls.append("seed_lifecycle")
        return LifecycleSeed(
            predecessor_id=_PREDECESSOR,
            successor_id=_SUCCESSOR,
            receipt={"memory_type": "repository_knowledge", "source": "scenario_seed"},
        )

    def snapshot_hub(
        self, prepared: PreparedPilot, *, request: SourcePilotRequest, query: str, selected_memory_id: str, lifecycle_memory_id: str
    ) -> HubSnapshot:
        del prepared, request, query
        self.calls.append(f"snapshot_hub:{selected_memory_id}:{lifecycle_memory_id}")
        machine = {
            "adapter": "http",
            "repository_key": _REPO_KEY,
            "system_repository_key": _REPO_KEY,
            "recall_repository_key": _REPO_KEY,
            "lifecycle_repository_key": _REPO_KEY,
            "memories_memory_ids": [_CAPTURED, _PREDECESSOR, _SUCCESSOR],
            "selected_memory_id": selected_memory_id,
            "selected_evidence_fact_ids": [_FACT],
            "selected_evidence_references": [{"fact_id": _FACT, "provider": "pico", "session_id": _LEARN_SESSION}],
            "recall_memory_ids": [_CAPTURED],
            "recall_ranked_memory_ids": [_CAPTURED],
            "recall_admission": {"outcome": "admitted", "reason": "relevant_candidate"},
            "recall_omissions": [{"memory_id": _PREDECESSOR, "reason": "lifecycle"}],
            "recall_context_sha256": "9" * 64,
            "system_status": "ok",
            "recall_readiness": {"state": "configuration_ready", "live_checked": False},
            "statuses": {_CAPTURED: "active", _PREDECESSOR: "superseded", _SUCCESSOR: "active"},
            "supersessions": [{"predecessor_id": _PREDECESSOR, "successor_id": lifecycle_memory_id}],
        }
        return HubSnapshot(
            system=_operation("system"),
            memories=_operation("memories"),
            lifecycle_memories=_operation("memories"),
            recall=_operation("recall"),
            machine_observation=machine,
        )


class FailingScriptedSteps(ScriptedSteps):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def run_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> PicoTurnEvidence:
        del prepared, request, raw_dir
        self.calls.append("run_learn")
        raise self._error


def _list_snapshot(path: Path, memories: list[dict[str, object]]) -> ListSnapshot:
    raw = json.dumps(memories, sort_keys=True).encode()
    write_bytes_exclusive(path, raw)
    return ListSnapshot(
        artifact=PublicJSONArtifact(path=path, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)),
        repo_key=_REPO_KEY,
        memories=tuple(memories),
    )


def _operation(name: str) -> OperationReceipt:
    return OperationReceipt(
        operation=name, http_status=200, request_id=f"hubreq_{name}", body_sha256="8" * 64, projection={"operation": name}
    )
