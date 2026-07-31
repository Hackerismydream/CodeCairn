from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from codecairn_v03_acceptance import cli
from codecairn_v03_acceptance.campaign import VerificationReport
from codecairn_v03_acceptance.cli import app
from codecairn_v03_acceptance.source_pilot import SourcePilotRequest
from typer.testing import CliRunner


def test_cli_starts_and_verifies_an_awaiting_source_campaign(tmp_path: Path) -> None:
    protocol = Path(__file__).parents[1] / "protocols" / "hub-comprehension-v1.json"
    output_root = tmp_path / "runs"
    runner = CliRunner()

    started = runner.invoke(
        app,
        [
            "start",
            "--protocol",
            str(protocol),
            "--output-root",
            str(output_root),
            "--run-id",
            "cli-pilot-001",
            "--codecairn-commit",
            "1" * 40,
            "--pico-commit",
            "2" * 40,
            "--delivery-mode",
            "source_checkout",
        ],
    )
    verified = runner.invoke(app, ["verify", str(output_root / "cli-pilot-001")])

    assert started.exit_code == 0, started.output
    assert json.loads(started.stdout) == {
        "artifact_dir": str(output_root / "cli-pilot-001"),
        "phase": "awaiting_machine",
        "run_id": "cli-pilot-001",
    }
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.stdout) == {
        "human_complete": False,
        "machine_complete": False,
        "outcome": "awaiting_evidence",
        "release_eligible": None,
        "violations": ["machine_evidence_missing"],
    }


def test_source_pilot_requires_explicit_live_authority_before_creating_a_campaign(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["source-pilot", *_source_pilot_arguments(tmp_path)])

    assert result.exit_code == 2
    assert "requires explicit --live-authorized" in result.output
    assert not (tmp_path / "runs").exists()


def test_source_pilot_cli_builds_one_source_request(monkeypatch, tmp_path: Path) -> None:
    captured: list[SourcePilotRequest] = []

    def fake_run(request: SourcePilotRequest):
        captured.append(request)
        return SimpleNamespace(
            status="completed",
            artifact_dir=tmp_path / "runs" / "cli-source-pilot",
            work_dir=tmp_path / "work" / "cli-source-pilot",
            report=VerificationReport(
                outcome="awaiting_evidence",
                machine_complete=True,
                human_complete=False,
                release_eligible=None,
                violations=("participant_evidence_missing",),
            ),
            failure_code=None,
        )

    monkeypatch.setattr(cli, "run_source_pilot", fake_run)
    result = CliRunner().invoke(app, ["source-pilot", *_source_pilot_arguments(tmp_path), "--live-authorized"])

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    request = captured[0]
    assert request.campaign.delivery_mode == "source_checkout"
    assert request.live_authorized is True
    assert request.steps is None
    assert json.loads(result.stdout)["status"] == "completed"


def test_source_pilot_cli_exits_nonzero_when_the_machine_gate_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        cli,
        "run_source_pilot",
        lambda _request: SimpleNamespace(
            status="completed",
            artifact_dir=tmp_path / "runs" / "cli-source-pilot",
            work_dir=tmp_path / "work" / "cli-source-pilot",
            report=VerificationReport(
                outcome="fail", machine_complete=True, human_complete=False, release_eligible=False, violations=("machine_gate_failed",)
            ),
            failure_code=None,
        ),
    )

    result = CliRunner().invoke(app, ["source-pilot", *_source_pilot_arguments(tmp_path), "--live-authorized"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["report"]["outcome"] == "fail"


def test_participant_source_hosts_only_the_matching_frozen_hub(monkeypatch, tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    checkout = tmp_path / "checkout"
    repository = tmp_path / "repository"
    for path in (campaign, checkout, repository):
        path.mkdir()
    calls: list[str] = []
    frozen = SimpleNamespace(
        snapshot_path=campaign / "machine" / "hub-snapshot.json",
        assert_clean_source_checkout=lambda path: calls.append(f"clean:{path}"),
        assert_live_matches=lambda client: calls.append(f"live:{client.origin}"),
    )
    monkeypatch.setattr(cli.FrozenHubPresentation, "from_campaign", lambda path: frozen)

    @contextmanager
    def fake_host(**_kwargs):
        yield SimpleNamespace(client=SimpleNamespace(origin="http://127.0.0.1:39001"))

    def fake_questionnaire(**arguments):
        return SimpleNamespace(origin="http://127.0.0.1:39002", serve=lambda: (arguments["submission_guard"](), calls.append("served")))

    monkeypatch.setattr(cli, "source_checkout_hub", fake_host)
    monkeypatch.setattr(cli, "ParticipantQuestionnaire", fake_questionnaire)
    result = CliRunner().invoke(
        app,
        [
            "participant-source",
            str(campaign),
            "--participant-id",
            "P001",
            "--codecairn-checkout",
            str(checkout),
            "--repository",
            str(repository),
            "--no-open-browser",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        f"clean:{checkout}",
        f"clean:{checkout}",
        "live:http://127.0.0.1:39001",
        f"clean:{checkout}",
        "live:http://127.0.0.1:39001",
        "served",
    ]
    assert json.loads(result.stdout)["questionnaire_origin"] == "http://127.0.0.1:39002"


def _source_pilot_arguments(tmp_path: Path) -> list[str]:
    protocol = Path(__file__).parents[1] / "protocols" / "hub-comprehension-v1.json"
    fixture = Path(__file__).parents[1] / "scenarios" / "retry-policy"
    checkout = tmp_path / "codecairn"
    pico_checkout = tmp_path / "pico"
    base_config = tmp_path / "pico.json"
    executable = tmp_path / "codecairn-command"
    pico_executable = tmp_path / "pico-command"
    python_executable = tmp_path / "python"
    for directory in (checkout, pico_checkout):
        directory.mkdir()
    base_config.write_text("{}", encoding="utf-8")
    for path in (executable, pico_executable, python_executable):
        path.write_text("#!/bin/false\n", encoding="utf-8")
    return [
        "--protocol",
        str(protocol),
        "--output-root",
        str(tmp_path / "runs"),
        "--work-root",
        str(tmp_path / "work"),
        "--run-id",
        "cli-source-pilot",
        "--codecairn-commit",
        "1" * 40,
        "--pico-commit",
        "2" * 40,
        "--codecairn-checkout",
        str(checkout),
        "--pico-checkout",
        str(pico_checkout),
        "--codecairn-executable",
        str(executable),
        "--pico-executable",
        str(pico_executable),
        "--scenario-python",
        str(python_executable),
        "--base-pico-config",
        str(base_config),
        "--fixture-dir",
        str(fixture),
        "--repo-key",
        "local/v03",
    ]
