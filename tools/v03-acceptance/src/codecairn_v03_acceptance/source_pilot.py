"""CodeCairn-owned orchestration for the source-checkout v0.3 pilot.

The collector deliberately has no input for a normalized machine observation.
It derives that observation from installed public commands, Pico trace
artifacts, an external task verifier, and the Hub's same-origin read contract.
The injected step seam exists only so the orchestration can be tested without
making provider calls; scripted steps are always marked as scripted and can
never satisfy the machine gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json, write_bytes_exclusive, write_json_exclusive
from codecairn_v03_acceptance.adapters.codecairn import (
    CodeCairnPublicCLI,
    ListSnapshot,
    RecallReceipt,
    derive_new_pico_task_experience_ids,
)
from codecairn_v03_acceptance.adapters.hub import HubSnapshot, OperationReceipt, hub_web_bundle_identity, source_checkout_hub
from codecairn_v03_acceptance.adapters.pico import (
    PicoConfigPair,
    PicoTurnSpec,
    collect_learn_trace,
    collect_recall_trace,
    execute_pico_turn,
    prepare_pico_configs,
)
from codecairn_v03_acceptance.bounded_process import run_bounded_process
from codecairn_v03_acceptance.campaign import (
    RAW_INVENTORY_CONTRACT,
    CampaignRequest,
    VerificationReport,
    _raw_inventory,
    _record_source_pilot_observation,
    start_campaign,
    verify_campaign,
)
from codecairn_v03_acceptance.checkout import CheckoutIntegrityError, frozen_checkout_identity
from codecairn_v03_acceptance.scenario import stage_retry_policy_scenario, verify_retry_policy_scenario

SOURCE_PILOT_RECEIPT_CONTRACT = "codecairn.v03-acceptance.source-pilot.v1"
SOURCE_PILOT_FAILURE_CONTRACT = "codecairn.v03-acceptance.source-pilot-failure.v1"
HUB_SNAPSHOT_CONTRACT = "codecairn.v03-acceptance.hub-snapshot.v1"

_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}\Z")
_SAFE_REPO_KEY = re.compile(r"[^\x00-\x1f]{1,512}\Z")
_PUBLIC_ENVIRONMENT_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "AZURE_API_KEY",
        "AZURE_API_BASE",
        "CODECAIRN_EMBEDDING_API_KEY",
        "DASHSCOPE_API_KEY",
        "GEMINI_API_KEY",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)
_INFRASTRUCTURE_CODES = frozenset(
    {
        "codecairn_process_failed",
        "codecairn_timeout",
        "hub_build_failed",
        "hub_build_preflight_failed",
        "hub_build_output_too_large",
        "hub_preflight_failed",
        "hub_process_exited",
        "hub_start_timeout",
        "pico_output_too_large",
        "provider_failure",
        "session_export_failed",
    }
)

StepMode = Literal["live", "scripted"]
PilotStatus = Literal["completed", "evidence_failure", "infrastructure_failure"]


class SourcePilotError(RuntimeError):
    """A stable orchestration or public-process failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class SourcePilotRequest:
    """All explicit authority and identities needed for one source pilot."""

    campaign: CampaignRequest
    work_root: Path
    codecairn_checkout: Path
    pico_checkout: Path
    codecairn_executable: Path
    pico_executable: Path
    scenario_python_executable: Path
    base_pico_config: Path
    fixture_dir: Path
    repo_key: str
    timeout_seconds: int = 900
    live_authorized: bool = False
    hub_python_executable: Path | None = None
    retrieval_profile: Literal["dashscope", "fastembed"] | None = None
    environment: Mapping[str, str] | None = field(default=None, repr=False)
    steps: SourcePilotSteps | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.campaign.delivery_mode != "source_checkout":
            raise ValueError("the source pilot only accepts source_checkout campaigns")
        if not _SAFE_REPO_KEY.fullmatch(self.repo_key):
            raise ValueError("source pilot repository key is invalid")
        if not 1 <= self.timeout_seconds <= 7_200:
            raise ValueError("source pilot timeout must be between 1 and 7200 seconds")
        artifact_dir = (self.campaign.output_root / self.campaign.run_id).resolve()
        work_dir = (self.work_root / self.campaign.run_id).resolve()
        if work_dir == artifact_dir or work_dir.is_relative_to(artifact_dir) or artifact_dir.is_relative_to(work_dir):
            raise ValueError("source pilot work state and campaign evidence must not overlap")


@dataclass(frozen=True, slots=True)
class PreparedPilot:
    """A step-owned runtime plus public preparation receipts."""

    state: object
    repo_key: str
    stage_receipt: dict[str, object]
    identity_receipt: dict[str, object]
    config_receipt: dict[str, object]


@dataclass(frozen=True, slots=True)
class PicoTurnEvidence:
    process: dict[str, object]
    trace: dict[str, object]


@dataclass(frozen=True, slots=True)
class LifecycleSeed:
    predecessor_id: str
    successor_id: str
    receipt: dict[str, object]


@dataclass(frozen=True, slots=True)
class SourcePilotResult:
    status: PilotStatus
    artifact_dir: Path
    work_dir: Path
    report: VerificationReport | None
    failure_code: str | None


class SourcePilotSteps(Protocol):
    """Typed test seam; implementations return evidence, never observations."""

    mode: StepMode

    def validate_candidates(self, request: SourcePilotRequest) -> dict[str, object]: ...

    def prepare(self, request: SourcePilotRequest, *, work_dir: Path, raw_dir: Path) -> PreparedPilot: ...

    def list_before(self, prepared: PreparedPilot, *, raw_dir: Path) -> ListSnapshot: ...

    def run_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> PicoTurnEvidence: ...

    def verify_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> dict[str, object]: ...

    def list_after(self, prepared: PreparedPilot, *, raw_dir: Path) -> ListSnapshot: ...

    def recall_public(self, prepared: PreparedPilot, *, query: str, expected_memory_ids: set[str], raw_dir: Path) -> RecallReceipt: ...

    def run_recall(
        self, prepared: PreparedPilot, *, request: SourcePilotRequest, expected_memory_ids: set[str], raw_dir: Path
    ) -> PicoTurnEvidence: ...

    def seed_lifecycle(self, prepared: PreparedPilot, *, raw_dir: Path) -> LifecycleSeed: ...

    def snapshot_hub(
        self, prepared: PreparedPilot, *, request: SourcePilotRequest, query: str, selected_memory_id: str, lifecycle_memory_id: str
    ) -> HubSnapshot: ...


def run_source_pilot(request: SourcePilotRequest) -> SourcePilotResult:
    """Run the full source pilot and fill its campaign from derived evidence."""
    steps = request.steps or LiveSourcePilotSteps()
    mode: StepMode = "live" if request.steps is None and type(steps) is LiveSourcePilotSteps else "scripted"
    if mode == "live" and request.live_authorized is not True:
        raise ValueError("live source pilot requires explicit live_authorized=True")
    handle = start_campaign(request.campaign)
    artifact_dir = handle.artifact_dir.resolve()
    work_dir = (request.work_root / request.campaign.run_id).resolve()
    raw_dir = artifact_dir / "machine" / "raw"
    current_step = "create_work_root"
    try:
        work_dir.mkdir(parents=True, exist_ok=False)
        os.chmod(work_dir, 0o700)
        current_step = "validate_candidates"
        candidate_receipt = steps.validate_candidates(request)
        write_json_exclusive(raw_dir / "candidate-identity.json", candidate_receipt)

        current_step = "prepare"
        prepared = steps.prepare(request, work_dir=work_dir, raw_dir=raw_dir)
        if prepared.repo_key != request.repo_key:
            raise SourcePilotError("repository_identity_mismatch", "prepared repository namespace changed")
        write_json_exclusive(raw_dir / "stage.json", prepared.stage_receipt)
        write_json_exclusive(raw_dir / "workspace-identity.json", prepared.identity_receipt)
        write_json_exclusive(raw_dir / "pico-config.json", prepared.config_receipt)

        current_step = "list_before"
        before = steps.list_before(prepared, raw_dir=raw_dir)
        write_json_exclusive(raw_dir / "list-before-receipt.json", _list_receipt(before))

        current_step = "run_learn"
        learn = steps.run_learn(prepared, request=request, raw_dir=raw_dir)
        _require_completed_process(learn.process, phase="learn")
        write_json_exclusive(raw_dir / "learn-evidence.json", asdict(learn))

        current_step = "verify_learn"
        task_verification = steps.verify_learn(prepared, request=request, raw_dir=raw_dir)
        write_json_exclusive(raw_dir / "task-verification.json", task_verification)

        current_step = "list_after"
        after = steps.list_after(prepared, raw_dir=raw_dir)
        write_json_exclusive(raw_dir / "list-after-receipt.json", _list_receipt(after))
        captured_ids = derive_new_pico_task_experience_ids(
            before, after, repo_key=request.repo_key, learn_session_id=cast(str, learn.trace["session_id"])
        )

        current_step = "public_recall"
        protocol = cast(dict[str, object], read_json(artifact_dir / "protocol.json"))
        scenario = cast(dict[str, object], protocol["scenario"])
        query = cast(str, scenario["recall_query"])
        public_recall = steps.recall_public(prepared, query=query, expected_memory_ids=set(captured_ids), raw_dir=raw_dir)
        write_json_exclusive(raw_dir / "public-recall-receipt.json", _recall_receipt(public_recall))

        current_step = "run_recall"
        recall = steps.run_recall(prepared, request=request, expected_memory_ids=set(captured_ids), raw_dir=raw_dir)
        _require_completed_process(recall.process, phase="recall")
        write_json_exclusive(raw_dir / "recall-evidence.json", asdict(recall))

        current_step = "seed_lifecycle"
        lifecycle = steps.seed_lifecycle(prepared, raw_dir=raw_dir)
        _memory_id(lifecycle.predecessor_id)
        _memory_id(lifecycle.successor_id)
        write_json_exclusive(raw_dir / "lifecycle-seed.json", asdict(lifecycle))

        current_step = "snapshot_hub"
        recalled_ids = cast(list[str], recall.trace["recalled_memory_ids"])
        continuity = sorted(set(captured_ids) & set(recalled_ids))
        if not continuity:
            raise SourcePilotError("fresh_process_continuity_missing", "fresh Pico recall used no captured Task Experience")
        hub = steps.snapshot_hub(
            prepared, request=request, query=query, selected_memory_id=continuity[0], lifecycle_memory_id=lifecycle.successor_id
        )
        hub_observation = dict(hub.machine_observation)
        hub_observation["adapter"] = "http" if mode == "live" else "in_process"
        hub_snapshot = _hub_snapshot(hub, machine_observation=hub_observation)
        write_json_exclusive(artifact_dir / "machine" / "hub-snapshot.json", hub_snapshot)

        if mode == "live":
            current_step = "revalidate_candidates"
            final_candidate_receipt = steps.validate_candidates(request)
            write_json_exclusive(raw_dir / "candidate-identity-final.json", final_candidate_receipt)
            if final_candidate_receipt != candidate_receipt:
                raise SourcePilotError("candidate_identity_drifted", "candidate identity changed during the source pilot")
            expected_bundle = cast(dict[str, object], cast(dict[str, object], prepared.identity_receipt["hub_build"])["bundle"])
            if hub_web_bundle_identity(request.codecairn_checkout) != expected_bundle:
                raise SourcePilotError("hub_bundle_drifted", "Hub production bundle changed during the source pilot")

        current_step = "derive_observation"
        observation = _machine_observation(
            request=request,
            mode=mode,
            learn=learn,
            recall=recall,
            task_verification=task_verification,
            captured_ids=captured_ids,
            after=after,
            public_recall=public_recall,
            hub_observation=hub_observation,
        )
        collector_receipt = {
            "schema_version": 1,
            "contract": SOURCE_PILOT_RECEIPT_CONTRACT,
            "mode": mode,
            "candidate_identity_sha256": canonical_sha256(candidate_receipt),
            "stage_sha256": canonical_sha256(prepared.stage_receipt),
            "task_verification_sha256": canonical_sha256(task_verification),
            "hub_snapshot_sha256": file_sha256(artifact_dir / "machine" / "hub-snapshot.json"),
            "observation_sha256": canonical_sha256({**observation, "collector": "source_pilot"}),
            "hub_bundle_sha256": cast(
                str, cast(dict[str, object], cast(dict[str, object], prepared.identity_receipt["hub_build"])["bundle"])["tree_sha256"]
            ),
            "captured_memory_ids": list(captured_ids),
            "recalled_memory_ids": recalled_ids,
        }
        raw_inventory = {"schema_version": 1, "contract": RAW_INVENTORY_CONTRACT, "files": _raw_inventory(raw_dir)}
        write_json_exclusive(raw_dir / "raw-inventory.json", raw_inventory)
        collector_receipt["raw_inventory_sha256"] = file_sha256(raw_dir / "raw-inventory.json")
        write_json_exclusive(raw_dir / "collector-receipt.json", collector_receipt)
        _record_source_pilot_observation(artifact_dir, observation)
        return SourcePilotResult(
            status="completed", artifact_dir=artifact_dir, work_dir=work_dir, report=verify_campaign(artifact_dir), failure_code=None
        )
    except Exception as error:
        status, code = _classify_failure(error)
        write_json_exclusive(
            artifact_dir / "machine" / "collector-failure.json",
            {
                "schema_version": 1,
                "contract": SOURCE_PILOT_FAILURE_CONTRACT,
                "terminal_class": status,
                "step": current_step,
                "failure_code": code,
                "exception_type": type(error).__name__,
                "message": str(error),
            },
        )
        return SourcePilotResult(
            status=status, artifact_dir=artifact_dir, work_dir=work_dir, report=verify_campaign(artifact_dir), failure_code=code
        )


@dataclass(slots=True)
class _LiveState:
    cli: CodeCairnPublicCLI
    learn_workspace: Path
    recall_workspace: Path
    learn_home: Path
    recall_home: Path
    learn_config: Path
    recall_config: Path
    operator_dir: Path
    stage_receipt: dict[str, object]
    public_environment: Mapping[str, str]


class LiveSourcePilotSteps(SourcePilotSteps):
    """Default implementation over installed CLI, Git, traces, and loopback HTTP."""

    mode: StepMode = "live"

    def validate_candidates(self, request: SourcePilotRequest) -> dict[str, object]:
        return {
            "schema_version": 1,
            "codecairn": _clean_checkout(request.codecairn_checkout, request.campaign.codecairn_commit),
            "pico": _clean_checkout(request.pico_checkout, request.campaign.pico_commit),
            "installed_environment": _installed_environment_identity(request),
        }

    def prepare(self, request: SourcePilotRequest, *, work_dir: Path, raw_dir: Path) -> PreparedPilot:
        hub_build = _build_hub(request, raw_dir=raw_dir)
        stage_dir = work_dir / "stage"
        stage_dir.mkdir()
        stage_receipt = stage_retry_policy_scenario(fixture_dir=request.fixture_dir, workspace=stage_dir)
        seed = work_dir / "task-repository"
        seed.mkdir()
        for source in sorted(stage_dir.iterdir()):
            shutil.copyfile(source, seed / source.name)
        _git(seed, "init", "--initial-branch=main")
        _git(seed, "add", "--", "retry_policy.py", "test_retry_policy.py")
        _git(
            seed,
            "-c",
            "user.name=CodeCairn v0.3",
            "-c",
            "user.email=codecairn-v03@invalid",
            "commit",
            "-m",
            "Stage pinned retry-policy scenario",
            environment={"GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"},
        )
        learn_home = work_dir / "pico-learn"
        recall_home = work_dir / "pico-recall"
        learn_home.mkdir()
        recall_home.mkdir()
        learn_workspace = learn_home / "workspace"
        recall_workspace = recall_home / "workspace"
        _git(seed, "worktree", "add", "--detach", str(learn_workspace), "HEAD")
        _git(seed, "worktree", "add", "--detach", str(recall_workspace), "HEAD")
        common = _git_output(learn_workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
        recall_common = _git_output(recall_workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if Path(common).resolve() != Path(recall_common).resolve():
            raise SourcePilotError("worktree_identity_mismatch", "learn and recall do not share one Git common directory")

        runtime_root = work_dir / "codecairn-runtime"
        operator_dir = work_dir / "operator"
        operator_dir.mkdir()
        init_arguments = [
            str(request.codecairn_executable.resolve()),
            "init",
            "--root",
            str(runtime_root),
            "--repo-key",
            request.repo_key,
            "--semantic-profile",
            "none",
        ]
        if request.retrieval_profile is not None:
            init_arguments.extend(["--retrieval-profile", request.retrieval_profile])
        initialized = _run_public_json(
            tuple(init_arguments),
            cwd=learn_workspace,
            artifact_path=raw_dir / "codecairn-init.json",
            environment=_public_environment(request.environment),
            timeout_seconds=min(request.timeout_seconds, 300),
        )
        if (
            initialized.get("status") != "initialized"
            or initialized.get("repo_key") != request.repo_key
            or Path(cast(str, initialized.get("root"))).resolve() != runtime_root.resolve()
        ):
            raise SourcePilotError("codecairn_init_invalid", "installed CodeCairn init returned a mismatched binding")
        config = Path(common).resolve() / "codecairn.toml"
        cli = CodeCairnPublicCLI(
            executable=request.codecairn_executable,
            operator_dir=operator_dir,
            config=config,
            runtime_root=runtime_root,
            repo_key=request.repo_key,
            timeout_seconds=min(request.timeout_seconds, 300),
        )
        configs: PicoConfigPair = prepare_pico_configs(base_config=request.base_pico_config, output_dir=work_dir / "pico-config")
        identity = {
            "schema_version": 1,
            "seed_commit": _git_output(seed, "rev-parse", "HEAD"),
            "git_common_dir": str(Path(common).resolve()),
            "learn_workspace": str(learn_workspace.resolve()),
            "recall_workspace": str(recall_workspace.resolve()),
            "separate_pico_homes": learn_home.resolve() != recall_home.resolve(),
            "hub_build": hub_build,
        }
        state = _LiveState(
            cli=cli,
            learn_workspace=learn_workspace,
            recall_workspace=recall_workspace,
            learn_home=learn_home,
            recall_home=recall_home,
            learn_config=configs.learn,
            recall_config=configs.recall,
            operator_dir=operator_dir,
            stage_receipt=stage_receipt,
            public_environment=cli.environment(os.environ if request.environment is None else request.environment),
        )
        return PreparedPilot(
            state=state,
            repo_key=request.repo_key,
            stage_receipt=stage_receipt,
            identity_receipt=identity,
            config_receipt=configs.public_receipt,
        )

    def list_before(self, prepared: PreparedPilot, *, raw_dir: Path) -> ListSnapshot:
        state = _live_state(prepared)
        return state.cli.list_memories(artifact_path=raw_dir / "list-before.json", source_environment=state.public_environment)

    def run_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> PicoTurnEvidence:
        state = _live_state(prepared)
        session_id = _session_id("learn", request.campaign.run_id)
        trace_dir = raw_dir / "learn-trace"
        prompt = cast(str, _frozen_scenario(raw_dir)["task_a_prompt"])
        process = execute_pico_turn(
            PicoTurnSpec(
                executable=request.pico_executable,
                message=prompt,
                session_id=session_id,
                workspace=state.learn_workspace,
                operator_dir=state.operator_dir,
                config=state.learn_config,
                pico_home=state.learn_home,
                trace_dir=trace_dir,
                timeout_seconds=request.timeout_seconds,
            ),
            artifact_dir=raw_dir / "learn-process",
            source_environment=request.environment,
        )
        trace = collect_learn_trace(trace_dir=trace_dir, session_id=session_id)
        return PicoTurnEvidence(process=process, trace=trace)

    def verify_learn(self, prepared: PreparedPilot, *, request: SourcePilotRequest, raw_dir: Path) -> dict[str, object]:
        state = _live_state(prepared)
        status = _git_output(state.learn_workspace, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
        write_json_exclusive(raw_dir / "scenario-git-status.json", {"porcelain_v1": status})
        if status != [" M retry_policy.py"]:
            raise SourcePilotError("scenario_workspace_drifted", "Agent workspace changed outside retry_policy.py")
        mirror = state.learn_workspace.parent / "verification-mirror"
        mirror.mkdir()
        for name in ("retry_policy.py", "test_retry_policy.py"):
            shutil.copyfile(state.learn_workspace / name, mirror / name)
        return verify_retry_policy_scenario(
            workspace=mirror, stage_receipt=prepared.stage_receipt, python_executable=request.scenario_python_executable
        )

    def list_after(self, prepared: PreparedPilot, *, raw_dir: Path) -> ListSnapshot:
        state = _live_state(prepared)
        return state.cli.list_memories(artifact_path=raw_dir / "list-after.json", source_environment=state.public_environment)

    def recall_public(self, prepared: PreparedPilot, *, query: str, expected_memory_ids: set[str], raw_dir: Path) -> RecallReceipt:
        state = _live_state(prepared)
        return state.cli.recall(
            query,
            expected_memory_ids=expected_memory_ids,
            artifact_path=raw_dir / "public-recall.json",
            source_environment=state.public_environment,
        )

    def run_recall(
        self, prepared: PreparedPilot, *, request: SourcePilotRequest, expected_memory_ids: set[str], raw_dir: Path
    ) -> PicoTurnEvidence:
        state = _live_state(prepared)
        session_id = _session_id("recall", request.campaign.run_id)
        trace_dir = raw_dir / "recall-trace"
        scenario = _frozen_scenario(raw_dir)
        prompt = cast(str, scenario["task_b_prompt"])
        process = execute_pico_turn(
            PicoTurnSpec(
                executable=request.pico_executable,
                message=prompt,
                session_id=session_id,
                workspace=state.recall_workspace,
                operator_dir=state.operator_dir,
                config=state.recall_config,
                pico_home=state.recall_home,
                trace_dir=trace_dir,
                timeout_seconds=request.timeout_seconds,
            ),
            artifact_dir=raw_dir / "recall-process",
            source_environment=request.environment,
        )
        trace = collect_recall_trace(
            trace_dir=trace_dir,
            session_id=session_id,
            expected_memory_ids=expected_memory_ids,
            expected_repo_key=request.repo_key,
            decision_marker=cast(str, scenario["recall_evidence_marker"]),
        )
        return PicoTurnEvidence(process=process, trace=trace)

    def seed_lifecycle(self, prepared: PreparedPilot, *, raw_dir: Path) -> LifecycleSeed:
        state = _live_state(prepared)
        common = (
            "--repo-key",
            prepared.repo_key,
            "--root",
            str(state.cli.runtime_root.resolve()),
            "--config",
            str(state.cli.config.resolve()),
        )
        old = _run_public_json(
            (
                str(state.cli.executable.resolve()),
                "remember",
                "repository_knowledge",
                "Scenario-only lifecycle seed revision alpha.",
                "--title",
                "v0.3 scenario lifecycle seed alpha",
                "--subject-key",
                "codecairn-v03-lifecycle-seed",
                "--tag",
                "v03-scenario-seed",
                *common,
            ),
            cwd=state.operator_dir,
            artifact_path=raw_dir / "lifecycle-old.json",
            environment=state.public_environment,
            timeout_seconds=state.cli.timeout_seconds,
        )
        new = _run_public_json(
            (
                str(state.cli.executable.resolve()),
                "remember",
                "repository_knowledge",
                "Scenario-only lifecycle seed revision beta.",
                "--title",
                "v0.3 scenario lifecycle seed beta",
                "--subject-key",
                "codecairn-v03-lifecycle-seed",
                "--tag",
                "v03-scenario-seed",
                *common,
            ),
            cwd=state.operator_dir,
            artifact_path=raw_dir / "lifecycle-new.json",
            environment=state.public_environment,
            timeout_seconds=state.cli.timeout_seconds,
        )
        predecessor_id = _repository_knowledge_id(old, repo_key=prepared.repo_key)
        successor_id = _repository_knowledge_id(new, repo_key=prepared.repo_key)
        evolution = _run_public_json(
            (
                str(state.cli.executable.resolve()),
                "memory",
                "supersede",
                predecessor_id,
                successor_id,
                "--reason",
                "v0.3 scenario-only lifecycle visibility seed",
                *common,
            ),
            cwd=state.operator_dir,
            artifact_path=raw_dir / "lifecycle-supersede.json",
            environment=state.public_environment,
            timeout_seconds=state.cli.timeout_seconds,
        )
        if evolution.get("predecessor_id") != predecessor_id or evolution.get("successor_id") != successor_id:
            raise SourcePilotError("lifecycle_seed_invalid", "public supersession receipt changed identities")
        return LifecycleSeed(
            predecessor_id=predecessor_id,
            successor_id=successor_id,
            receipt={
                "contract": "codecairn.v03-acceptance.scenario-lifecycle-seed.v1",
                "memory_type": "repository_knowledge",
                "source": "scenario_seed",
                "evolution": evolution,
            },
        )

    def snapshot_hub(
        self, prepared: PreparedPilot, *, request: SourcePilotRequest, query: str, selected_memory_id: str, lifecycle_memory_id: str
    ) -> HubSnapshot:
        state = _live_state(prepared)
        with source_checkout_hub(
            checkout=request.codecairn_checkout,
            repository=state.learn_workspace,
            python_executable=request.hub_python_executable,
            environment=request.environment,
        ) as session:
            return session.client.snapshot(query=query, selected_memory_id=selected_memory_id, lifecycle_memory_id=lifecycle_memory_id)


def _machine_observation(
    *,
    request: SourcePilotRequest,
    mode: StepMode,
    learn: PicoTurnEvidence,
    recall: PicoTurnEvidence,
    task_verification: dict[str, object],
    captured_ids: tuple[str, ...],
    after: ListSnapshot,
    public_recall: RecallReceipt,
    hub_observation: dict[str, object],
) -> dict[str, object]:
    evidence_ids = {
        cast(str, memory["memory_id"])
        for memory in after.memories
        if memory.get("memory_id") in captured_ids and isinstance(memory.get("evidence"), list) and bool(memory["evidence"])
    }
    candidate = {
        "codecairn_commit": request.campaign.codecairn_commit,
        "pico_commit": request.campaign.pico_commit,
        "delivery_mode": request.campaign.delivery_mode,
        "codecairn_artifact_sha256": request.campaign.codecairn_artifact_sha256,
        "pico_artifact_sha256": request.campaign.pico_artifact_sha256,
        "hub_artifact_sha256": request.campaign.hub_artifact_sha256,
    }
    return {
        "schema_version": 1,
        "contract": "codecairn.v03-acceptance.machine-observation.v1",
        "terminal_class": "completed",
        "failure_code": None,
        "candidate": candidate,
        "installed": {
            "codecairn_artifact_sha256": None,
            "pico_artifact_sha256": None,
            "hub_artifact_sha256": None,
            "source_checkouts_absent": False,
            "plugin_entry_point": "codecairn.integrations.pico",
        },
        "pico": {
            "adapter": "subprocess" if mode == "live" else "scripted",
            "install_kind": "installed_distribution" if mode == "live" else "scripted",
            "provider_mode": "configured" if mode == "live" else "scripted",
            "plugin_id": "codecairn-memory",
            "backend": "codecairn",
            "task_a": {
                "process_id": learn.process["process_id"],
                "session_id": learn.trace["session_id"],
                "trace_contract": learn.trace["trace_contract"],
                "task_verified": task_verification.get("task_verified") is True,
                "captured_memory_ids": list(captured_ids),
            },
            "task_b": {
                "process_id": recall.process["process_id"],
                "session_id": recall.trace["session_id"],
                "trace_contract": recall.trace["trace_contract"],
                "recalled_memory_ids": recall.trace["recalled_memory_ids"],
                "llm_input_memory_ids": recall.trace["llm_input_memory_ids"],
                "forbidden_tool_calls": recall.trace["forbidden_tool_calls"],
            },
        },
        "codecairn": {
            "source_journal_memory_ids": list(captured_ids),
            "public_recall_memory_ids": list(public_recall.recalled_memory_ids),
            "evidence_reference_memory_ids": sorted(evidence_ids),
        },
        "hub": hub_observation,
    }


def _hub_snapshot(hub: HubSnapshot, *, machine_observation: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": HUB_SNAPSHOT_CONTRACT,
        "views": {
            "system": _operation_receipt(hub.system),
            "memories": _operation_receipt(hub.memories),
            "lifecycle_memories": _operation_receipt(hub.lifecycle_memories),
            "recall": _operation_receipt(hub.recall),
        },
        "machine_observation": machine_observation,
    }


def _operation_receipt(receipt: OperationReceipt) -> dict[str, object]:
    return {
        "operation": receipt.operation,
        "http_status": receipt.http_status,
        "request_id": receipt.request_id,
        "body_sha256": receipt.body_sha256,
        "projection": receipt.projection,
    }


def _list_receipt(snapshot: ListSnapshot) -> dict[str, object]:
    return {
        "repo_key": snapshot.repo_key,
        "memory_ids": list(snapshot.memory_ids),
        "artifact": {"path": str(snapshot.artifact.path), "sha256": snapshot.artifact.sha256, "bytes": snapshot.artifact.bytes},
    }


def _recall_receipt(receipt: RecallReceipt) -> dict[str, object]:
    return {
        "artifact": {"path": str(receipt.artifact.path), "sha256": receipt.artifact.sha256, "bytes": receipt.artifact.bytes},
        "repo_key": receipt.repo_key,
        "query": receipt.query,
        "source_cursor": receipt.source_cursor,
        "index_cursor": receipt.index_cursor,
        "ranked_memory_ids": list(receipt.ranked_memory_ids),
        "rendered_memory_ids": list(receipt.rendered_memory_ids),
        "recalled_memory_ids": list(receipt.recalled_memory_ids),
        "source_uris": list(receipt.source_uris),
    }


def _require_completed_process(receipt: dict[str, object], *, phase: str) -> None:
    if receipt.get("terminal_class") != "completed" or receipt.get("exit_code") != 0:
        raise SourcePilotError(f"pico_{phase}_process_failed", f"Pico {phase} process did not complete")


def _classify_failure(error: Exception) -> tuple[Literal["evidence_failure", "infrastructure_failure"], str]:
    code = getattr(error, "code", None)
    stable_code = code if isinstance(code, str) and code else _exception_code(error)
    infrastructure = stable_code in _INFRASTRUCTURE_CODES or stable_code.endswith(("_timeout", "_process_failed"))
    return ("infrastructure_failure" if infrastructure else "evidence_failure", stable_code)


def _exception_code(error: Exception) -> str:
    if isinstance(error, FileExistsError):
        return "exclusive_artifact_exists"
    return re.sub(r"[^a-z0-9]+", "_", type(error).__name__.casefold()).strip("_") or "source_pilot_failed"


def _installed_environment_identity(request: SourcePilotRequest) -> dict[str, object]:
    codecairn_python = _console_python(request.codecairn_executable, module="codecairn.bootstrap", function="main")
    pico_python = _console_python(request.pico_executable, module="pico.cli.commands", function="run")
    hub_python = (
        request.hub_python_executable.absolute()
        if request.hub_python_executable is not None
        else (request.codecairn_checkout / ".venv" / "bin" / "python").absolute()
    )
    codecairn = _python_identity(codecairn_python, modules=("codecairn",))
    pico = _python_identity(pico_python, modules=("pico", "codecairn"))
    hub = _python_identity(hub_python, modules=("codecairn", "codecairn_hub_api"))
    tracked_modules = {
        "codecairn_console": _module_belongs_to(codecairn, "codecairn", request.codecairn_checkout, request.campaign.codecairn_commit),
        "pico_codecairn": _module_belongs_to(pico, "codecairn", request.codecairn_checkout, request.campaign.codecairn_commit),
        "pico": _module_belongs_to(pico, "pico", request.pico_checkout, request.campaign.pico_commit),
        "hub_codecairn": _module_belongs_to(hub, "codecairn", request.codecairn_checkout, request.campaign.codecairn_commit),
        "hub_api": _module_belongs_to(hub, "codecairn_hub_api", request.codecairn_checkout, request.campaign.codecairn_commit),
    }
    _require_entry_point(codecairn, group="console_scripts", name="codecairn", value="codecairn.bootstrap:main")
    _require_entry_point(pico, group="console_scripts", name="pico", value="pico.cli.commands:run")
    _require_entry_point(pico, group="pico.plugins", name="codecairn", value="codecairn.integrations.pico")
    return {
        "codecairn_console": str(request.codecairn_executable.resolve()),
        "pico_console": str(request.pico_executable.resolve()),
        "hub_python": str(hub_python),
        "codecairn": codecairn,
        "pico": pico,
        "hub": hub,
        "tracked_modules": tracked_modules,
        "identity_boundary": "fixed_console_wrappers_and_imported_modules_at_frozen_git_blobs",
    }


def _console_python(executable: Path, *, module: str, function: str) -> Path:
    if not executable.is_absolute() or executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise SourcePilotError("installed_console_invalid", "installed console script must be an absolute executable regular file")
    try:
        lines = executable.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise SourcePilotError("installed_console_invalid", "installed console script is unreadable") from error
    first_line = lines[0] if lines else ""
    if not first_line.startswith("#!/") or " " in first_line[2:]:
        raise SourcePilotError("installed_console_invalid", "installed console script must name one absolute Python interpreter")
    python = Path(first_line[2:])
    expected_body = [
        "# -*- coding: utf-8 -*-",
        "import sys",
        f"from {module} import {function}",
        'if __name__ == "__main__":',
        '    if sys.argv[0].endswith("-script.pyw"):',
        "        sys.argv[0] = sys.argv[0][:-11]",
        '    elif sys.argv[0].endswith(".exe"):',
        "        sys.argv[0] = sys.argv[0][:-4]",
        f"    sys.exit({function}())",
    ]
    if lines[1:] != expected_body:
        raise SourcePilotError("installed_console_invalid", "installed console wrapper does not match its frozen entry point")
    if not python.is_absolute() or python.parent != executable.parent or not python.is_file() or not os.access(python, os.X_OK):
        raise SourcePilotError("installed_console_invalid", "installed console script Python interpreter is unavailable")
    return python


def _python_identity(python: Path, *, modules: tuple[str, ...]) -> dict[str, object]:
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise SourcePilotError("installed_python_invalid", "installed Python interpreter is unavailable")
    module_literal = json.dumps(list(modules))
    probe = (
        "import importlib,importlib.metadata as m,json;"
        f"names={module_literal};"
        "mods={n:str(__import__(n).__file__) for n in names};"
        "eps=[{'group':e.group,'name':e.name,'value':e.value} for e in m.entry_points() "
        "if (e.group,e.name) in {('console_scripts','codecairn'),('console_scripts','pico'),"
        "('pico.plugins','codecairn')}];"
        "dists={};"
        "\nfor n in ('codecairn','pico-harness'):\n"
        " try:dists[n]=m.version(n)\n"
        " except m.PackageNotFoundError:pass\n"
        "print(json.dumps({'modules':mods,'entry_points':eps,'distributions':dists},sort_keys=True))"
    )
    try:
        completed = run_bounded_process(
            (str(python), "-I", "-c", probe),
            cwd=python.parent,
            environment={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            timeout_seconds=60,
            stdout_limit=1_048_576,
            stderr_limit=1_048_576,
        )
    except OSError as error:
        raise SourcePilotError("installed_identity_probe_failed", "installed environment probe could not complete") from error
    if completed.terminal != "exited" or completed.exit_code != 0 or not completed.stdout:
        raise SourcePilotError("installed_identity_probe_failed", "installed environment probe failed")
    try:
        value = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourcePilotError("installed_identity_probe_failed", "installed environment probe returned invalid JSON") from error
    if not isinstance(value, dict):
        raise SourcePilotError("installed_identity_probe_failed", "installed environment identity is not an object")
    return cast(dict[str, object], value)


def _module_belongs_to(identity: dict[str, object], module: str, checkout: Path, commit: str) -> dict[str, str]:
    modules = identity.get("modules")
    path = cast(dict[str, object], modules).get(module) if isinstance(modules, dict) else None
    root = checkout.resolve()
    resolved = Path(path).resolve() if isinstance(path, str) else Path()
    if not isinstance(path, str) or not resolved.is_relative_to(root):
        raise SourcePilotError(
            "installed_candidate_mismatch", f"installed {module} module does not belong to its frozen candidate checkout"
        )
    relative = resolved.relative_to(root).as_posix()
    try:
        tracked = _git_output(root, "ls-files", "--error-unmatch", "--", relative)
        frozen_blob = _git_output(root, "rev-parse", f"{commit}:{relative}")
        current_blob = _git_output(root, "hash-object", "--", relative)
    except SourcePilotError as error:
        raise SourcePilotError("installed_candidate_mismatch", f"installed {module} module is not tracked by the candidate") from error
    if tracked != relative or current_blob != frozen_blob:
        raise SourcePilotError("installed_candidate_mismatch", f"installed {module} module differs from its frozen Git blob")
    return {"path": relative, "blob": frozen_blob}


def _require_entry_point(identity: dict[str, object], *, group: str, name: str, value: str) -> None:
    entry_points = identity.get("entry_points")
    if not isinstance(entry_points, list) or not any(
        isinstance(item, dict) and item == {"group": group, "name": name, "value": value} for item in entry_points
    ):
        raise SourcePilotError("installed_entry_point_mismatch", f"installed {group}:{name} entry point is not {value}")


def _build_hub(request: SourcePilotRequest, *, raw_dir: Path) -> dict[str, object]:
    environment = _public_environment(request.environment)
    npm = shutil.which("npm", path=environment.get("PATH"))
    if npm is None:
        raise SourcePilotError("hub_build_preflight_failed", "npm is unavailable for the Hub production build")
    environment["CI"] = "1"
    command = (npm, "run", "hub:build")
    limit = 5 * 1024 * 1024
    try:
        result = run_bounded_process(
            command,
            cwd=request.codecairn_checkout.resolve(),
            environment=environment,
            timeout_seconds=min(request.timeout_seconds, 900),
            stdout_limit=limit,
            stderr_limit=limit,
        )
    except OSError as error:
        raise SourcePilotError("hub_build_failed", "Hub production build could not complete") from error
    write_bytes_exclusive(raw_dir / "hub-build.stdout", result.stdout)
    write_bytes_exclusive(raw_dir / "hub-build.stderr", result.stderr)
    if result.terminal in {"stdout_limit", "stderr_limit"}:
        raise SourcePilotError("hub_build_output_too_large", "Hub production build exceeded the evidence output limit")
    if result.terminal != "exited" or result.exit_code != 0:
        raise SourcePilotError("hub_build_failed", f"Hub production build ended as {result.terminal} ({result.exit_code})")
    return {
        "command_sha256": canonical_sha256(list(command)),
        "exit_code": result.exit_code,
        "stdout": {"bytes": len(result.stdout), "sha256": hashlib.sha256(result.stdout).hexdigest()},
        "stderr": {"bytes": len(result.stderr), "sha256": hashlib.sha256(result.stderr).hexdigest()},
        "bundle": hub_web_bundle_identity(request.codecairn_checkout),
    }


def _clean_checkout(path: Path, expected_commit: str) -> dict[str, object]:
    try:
        root, commit, tree = frozen_checkout_identity(path, expected_commit)
    except CheckoutIntegrityError as error:
        raise SourcePilotError(error.code, str(error)) from error
    return {"checkout": str(root), "commit": commit, "tree": tree, "clean": True}


def _git(cwd: Path, *arguments: str, environment: Mapping[str, str] | None = None) -> None:
    _git_output(cwd, *arguments, environment=environment)


def _git_output(cwd: Path, *arguments: str, environment: Mapping[str, str] | None = None) -> str:
    git = shutil.which("git")
    if git is None:
        raise SourcePilotError("git_unavailable", "Git is required for source-pilot isolation")
    child_environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        **({} if environment is None else dict(environment)),
    }
    try:
        completed = run_bounded_process(
            (git, "-C", str(cwd.resolve()), *arguments),
            cwd=cwd,
            environment=child_environment,
            timeout_seconds=120,
            stdout_limit=5 * 1024 * 1024,
            stderr_limit=5 * 1024 * 1024,
        )
    except OSError as error:
        raise SourcePilotError("git_process_failed", "Git operation could not complete") from error
    if completed.terminal != "exited" or completed.exit_code != 0:
        raise SourcePilotError("git_process_failed", f"Git operation ended as {completed.terminal}")
    try:
        return completed.stdout.decode().strip()
    except UnicodeDecodeError as error:
        raise SourcePilotError("git_output_invalid", "Git returned non-UTF-8 output") from error


def _run_public_json(
    command: tuple[str, ...], *, cwd: Path, artifact_path: Path, environment: Mapping[str, str], timeout_seconds: int
) -> dict[str, object]:
    try:
        result = run_bounded_process(
            command,
            cwd=cwd.resolve(),
            environment=environment,
            timeout_seconds=timeout_seconds,
            stdout_limit=10 * 1024 * 1024,
            stderr_limit=10 * 1024 * 1024,
        )
    except OSError as error:
        raise SourcePilotError("public_command_process_failed", "installed public command could not start") from error
    write_bytes_exclusive(artifact_path, result.stdout)
    write_bytes_exclusive(artifact_path.with_suffix(".stderr"), result.stderr)
    if result.terminal == "timeout":
        raise SourcePilotError("public_command_timeout", "installed public command timed out")
    if result.terminal in {"stdout_limit", "stderr_limit"}:
        raise SourcePilotError("public_command_output_too_large", "installed public command output exceeded the evidence limit")
    if result.terminal != "exited" or result.exit_code != 0:
        raise SourcePilotError("public_command_process_failed", f"installed public command exited with {result.exit_code}")
    try:
        value = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourcePilotError("public_command_output_invalid", "installed public command returned non-JSON output") from error
    if not isinstance(value, dict):
        raise SourcePilotError("public_command_output_invalid", "installed public command returned a non-object")
    return cast(dict[str, object], value)


def _public_environment(source: Mapping[str, str] | None) -> dict[str, str]:
    selected = os.environ if source is None else source
    environment = {key: value for key, value in selected.items() if key in _PUBLIC_ENVIRONMENT_KEYS}
    environment.setdefault("LANG", "C.UTF-8")
    environment.update({"PYTHONPATH": "", "PYTHONNOUSERSITE": "1"})
    return environment


def _repository_knowledge_id(value: dict[str, object], *, repo_key: str) -> str:
    memory_id = _memory_id(value.get("memory_id"))
    if value.get("repo_key") != repo_key or value.get("memory_type") != "repository_knowledge":
        raise SourcePilotError("lifecycle_seed_invalid", "scenario seed was not Repository Knowledge in the target namespace")
    return memory_id


def _memory_id(value: object) -> str:
    if not isinstance(value, str) or _MEMORY_ID.fullmatch(value) is None:
        raise SourcePilotError("memory_identity_invalid", "public Memory identity is invalid")
    return value


def _session_id(kind: Literal["learn", "recall"], run_id: str) -> str:
    return f"cli:v03-{kind}-{canonical_sha256(run_id)[:12]}"


def _frozen_scenario(raw_dir: Path) -> dict[str, object]:
    protocol = cast(dict[str, object], read_json(raw_dir.parents[1] / "protocol.json"))
    return cast(dict[str, object], protocol["scenario"])


def _live_state(prepared: PreparedPilot) -> _LiveState:
    if not isinstance(prepared.state, _LiveState):
        raise SourcePilotError("source_pilot_state_invalid", "live steps received scripted state")
    return prepared.state
