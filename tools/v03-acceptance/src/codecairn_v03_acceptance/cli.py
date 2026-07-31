"""Operator CLI for the private v0.3 Hub acceptance campaign."""

from __future__ import annotations

import json
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from codecairn.evaluation.artifacts import read_json
from codecairn_v03_acceptance.adapters.hub import HubAdapterError, source_checkout_hub
from codecairn_v03_acceptance.campaign import (
    CampaignRequest,
    DeliveryMode,
    record_machine_observation,
    seal_campaign,
    start_campaign,
    verify_campaign,
)
from codecairn_v03_acceptance.presentation import FrozenHubPresentation
from codecairn_v03_acceptance.questionnaire import ParticipantQuestionnaire, ReviewerQuestionnaire
from codecairn_v03_acceptance.source_pilot import SourcePilotRequest, run_source_pilot

app = typer.Typer(
    name="codecairn-v03-acceptance",
    help="Collect v0.3 Hub acceptance evidence and recompute its sealed verdict offline.",
    no_args_is_help=True,
)


@app.command("start")
def start(
    protocol: Annotated[Path, typer.Option("--protocol", exists=True, dir_okay=False, readable=True)],
    output_root: Annotated[Path, typer.Option("--output-root")],
    run_id: Annotated[str, typer.Option("--run-id")],
    codecairn_commit: Annotated[str, typer.Option("--codecairn-commit")],
    pico_commit: Annotated[str, typer.Option("--pico-commit")],
    delivery_mode: Annotated[str, typer.Option("--delivery-mode")],
    codecairn_artifact_sha256: Annotated[str | None, typer.Option("--codecairn-artifact-sha256")] = None,
    pico_artifact_sha256: Annotated[str | None, typer.Option("--pico-artifact-sha256")] = None,
    hub_artifact_sha256: Annotated[str | None, typer.Option("--hub-artifact-sha256")] = None,
) -> None:
    """Freeze one candidate and protocol into a new immutable campaign."""
    try:
        request = CampaignRequest(
            protocol_path=protocol,
            output_root=output_root,
            run_id=run_id,
            codecairn_commit=codecairn_commit,
            pico_commit=pico_commit,
            delivery_mode=cast(DeliveryMode, delivery_mode),
            codecairn_artifact_sha256=codecairn_artifact_sha256,
            pico_artifact_sha256=pico_artifact_sha256,
            hub_artifact_sha256=hub_artifact_sha256,
        )
        handle = start_campaign(request)
    except (FileExistsError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"artifact_dir": str(handle.artifact_dir), "phase": handle.phase, "run_id": handle.run_id})


@app.command("record-machine")
def record_machine(
    campaign: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    observation: Annotated[Path, typer.Option("--observation", exists=True, dir_okay=False, readable=True)],
) -> None:
    """Record manual diagnostic evidence; it cannot satisfy the automated gate."""
    try:
        record_machine_observation(campaign, read_json(observation))
    except (FileExistsError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"recorded": "machine", "collector": "manual", "campaign": str(campaign)})


@app.command("source-pilot")
def source_pilot(
    protocol: Annotated[Path, typer.Option("--protocol", exists=True, dir_okay=False, readable=True)],
    output_root: Annotated[Path, typer.Option("--output-root")],
    work_root: Annotated[Path, typer.Option("--work-root")],
    run_id: Annotated[str, typer.Option("--run-id")],
    codecairn_commit: Annotated[str, typer.Option("--codecairn-commit")],
    pico_commit: Annotated[str, typer.Option("--pico-commit")],
    codecairn_checkout: Annotated[Path, typer.Option("--codecairn-checkout", exists=True, file_okay=False)],
    pico_checkout: Annotated[Path, typer.Option("--pico-checkout", exists=True, file_okay=False)],
    codecairn_executable: Annotated[Path, typer.Option("--codecairn-executable", exists=True, dir_okay=False)],
    pico_executable: Annotated[Path, typer.Option("--pico-executable", exists=True, dir_okay=False)],
    scenario_python_executable: Annotated[Path, typer.Option("--scenario-python", exists=True, dir_okay=False)],
    base_pico_config: Annotated[Path, typer.Option("--base-pico-config", exists=True, dir_okay=False, readable=True)],
    fixture_dir: Annotated[Path, typer.Option("--fixture-dir", exists=True, file_okay=False, readable=True)],
    repo_key: Annotated[str, typer.Option("--repo-key")],
    hub_python_executable: Annotated[Path | None, typer.Option("--hub-python", exists=True, dir_okay=False)] = None,
    retrieval_profile: Annotated[str | None, typer.Option("--retrieval-profile")] = None,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds", min=1, max=7_200)] = 900,
    live_authorized: Annotated[
        bool, typer.Option("--live-authorized", help="Authorize Pico and configured-LLM execution for this source-only pilot.")
    ] = False,
) -> None:
    """Run the source-checkout machine pilot from raw public evidence."""
    if not live_authorized:
        raise typer.BadParameter("source-pilot requires explicit --live-authorized")
    if retrieval_profile not in {None, "dashscope", "fastembed"}:
        raise typer.BadParameter("retrieval profile must be dashscope or fastembed")
    try:
        campaign = CampaignRequest(
            protocol_path=protocol,
            output_root=output_root,
            run_id=run_id,
            codecairn_commit=codecairn_commit,
            pico_commit=pico_commit,
            delivery_mode="source_checkout",
        )
        result = run_source_pilot(
            SourcePilotRequest(
                campaign=campaign,
                work_root=work_root,
                codecairn_checkout=codecairn_checkout,
                pico_checkout=pico_checkout,
                codecairn_executable=codecairn_executable,
                pico_executable=pico_executable,
                scenario_python_executable=scenario_python_executable,
                base_pico_config=base_pico_config,
                fixture_dir=fixture_dir.resolve(),
                repo_key=repo_key,
                timeout_seconds=timeout_seconds,
                live_authorized=True,
                hub_python_executable=hub_python_executable,
                retrieval_profile=cast(Literal["dashscope", "fastembed"] | None, retrieval_profile),
            )
        )
    except (FileExistsError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "artifact_dir": str(result.artifact_dir),
            "failure_code": result.failure_code,
            "report": None if result.report is None else asdict(result.report),
            "status": result.status,
            "work_dir": str(result.work_dir),
        }
    )
    if (
        result.status != "completed"
        or result.report is None
        or not result.report.machine_complete
        or result.report.outcome != "awaiting_evidence"
    ):
        raise typer.Exit(1)


@app.command("reviewer")
def reviewer(
    campaign: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    participant_id: Annotated[str, typer.Option("--participant-id")],
    reviewer_id: Annotated[str, typer.Option("--reviewer-id")],
    open_browser: Annotated[bool, typer.Option("--open-browser/--no-open-browser")] = True,
) -> None:
    """Collect one human blind review separately from the original answer."""
    try:
        questionnaire = ReviewerQuestionnaire(campaign_dir=campaign, participant_id=participant_id, reviewer_id=reviewer_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(questionnaire.origin)
    if open_browser:
        webbrowser.open(questionnaire.origin)
    questionnaire.serve()


@app.command("participant-source")
def participant_source(
    campaign: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    participant_id: Annotated[str, typer.Option("--participant-id")],
    codecairn_checkout: Annotated[Path, typer.Option("--codecairn-checkout", exists=True, file_okay=False)],
    repository: Annotated[Path, typer.Option("--repository", exists=True, file_okay=False)],
    hub_python_executable: Annotated[Path | None, typer.Option("--hub-python", exists=True, dir_okay=False)] = None,
    moderator_content_hint_count: Annotated[int, typer.Option("--moderator-content-hint-count", min=0)] = 0,
    open_browser: Annotated[bool, typer.Option("--open-browser/--no-open-browser")] = True,
) -> None:
    """Launch the frozen source Hub and collect one bound participant response."""
    try:
        presentation = FrozenHubPresentation.from_campaign(campaign)
        presentation.assert_clean_source_checkout(codecairn_checkout)
        with source_checkout_hub(
            checkout=codecairn_checkout, repository=repository, python_executable=hub_python_executable
        ) as session:

            def submission_guard() -> None:
                presentation.assert_clean_source_checkout(codecairn_checkout)
                try:
                    presentation.assert_live_matches(session.client)
                except HubAdapterError as error:
                    raise ValueError(str(error)) from error

            submission_guard()
            questionnaire = ParticipantQuestionnaire(
                campaign_dir=campaign,
                participant_id=participant_id,
                hub_origin=session.client.origin,
                hub_snapshot=presentation.snapshot_path,
                moderator_content_hint_count=moderator_content_hint_count,
                submission_guard=submission_guard,
            )
            _emit({"hub_origin": session.client.origin, "questionnaire_origin": questionnaire.origin})
            if open_browser:
                webbrowser.open(questionnaire.origin)
            questionnaire.serve()
    except (HubAdapterError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@app.command("seal")
def seal(campaign: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)]) -> None:
    """Freeze all evidence and emit the recomputed campaign verdict."""
    try:
        report = seal_campaign(campaign)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(asdict(report))


@app.command("verify")
def verify(campaign: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)]) -> None:
    """Recompute a campaign verdict offline from its frozen artifacts."""
    try:
        report = verify_campaign(campaign)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(asdict(report))


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
