#!/usr/bin/env python3
"""Generate and verify deterministic Hub Read and Governance snapshots."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from codecairn_hub_api.app import create_hub_app
from codecairn_hub_api.queries import HubReadModule, RecallReadiness
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from typer.testing import CliRunner

from codecairn.bootstrap import create_application, create_myna_application
from codecairn.entrypoints.cli import build_app
from codecairn.evaluation.gates import EvaluationEmbedder, EvaluationReranker
from codecairn.importers.jsonl import JsonlScan
from codecairn.importers.jsonl import read_jsonl as _read_jsonl
from codecairn.memory.context import RENDERER_ID
from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.library import promotion_from_dict
from codecairn.memory.models import RecallResult
from codecairn.service.application import MemoryDetail, MemoryPage, RememberRequest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPOSITORY_ROOT / "contracts" / "hub-read" / "v1.example.json"
GOVERNANCE_FIXTURE_PATH = REPOSITORY_ROOT / "contracts" / "hub-governance" / "v1.example.json"
REPO_KEY = "github.com/Hackerismydream/CodeCairn"
GOVERNANCE_REPO_KEY = "github.com/example/preferences"
GOVERNANCE_SESSION_TOKEN = "contract-session-token-with-32-characters"
GOVERNANCE_PERSON_ID = f"person_{'1' * 64}"
GOVERNANCE_TITLE = "Response language"
GOVERNANCE_CONTENT = "Reply in concise Chinese."
GOVERNANCE_SUBJECT = "response-language"
EXPECTED_RENDERER = "codecairn/typed-excerpt-context-v2"
PREDECESSOR_TITLE = "旧结论: 单元测试足以证明连续性"
PREDECESSOR_CONTENT = "单元测试通过即可证明跨进程连续性。"
SUCCESSOR_TITLE = "连续性必须由全新进程验证"
SUCCESSOR_CONTENT = "跨进程连续性需要持久化回执, 并在全新进程中验证同一任务只投递一次。"
EVOLUTION_REASON = "全新进程验证替代了仅依赖单元测试的旧结论。"
ADMITTED_QUERY = SUCCESSOR_TITLE
ABSTAINED_QUERY = "为前端首页选择一种暖色插画风格"
CONTRACT_RETRIEVAL = (EvaluationEmbedder(), EvaluationReranker())


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def _fixed_time(milliseconds: int) -> Iterator[None]:
    nanoseconds = milliseconds * 1_000_000
    with (
        patch("codecairn.service.application.time.time_ns", return_value=nanoseconds),
        patch("codecairn.service.runtime.time.time_ns", return_value=nanoseconds),
        patch("codecairn.service.myna.time.time_ns", return_value=nanoseconds),
        patch("codecairn.storage.library_markdown.time.time_ns", return_value=nanoseconds),
        patch("codecairn_hub_api.queries.time.time_ns", return_value=nanoseconds),
    ):
        yield


def _canonical_governance_scan(*args: Any, **kwargs: Any) -> JsonlScan:
    """Remove checkout location from the identity-bearing contract fixture."""
    return replace(_read_jsonl(*args, **kwargs), source_path=Path("/codecairn-contract/governance.jsonl"))


def _invoke(runner: CliRunner, cli: Any, arguments: list[str]) -> object:
    result = runner.invoke(cli, arguments)
    if result.exit_code != 0:
        raise RuntimeError(f"CLI command failed: codecairn {' '.join(arguments)}\n{result.output}") from result.exception
    return json.loads(result.output)


def build_snapshot() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="codecairn-hub-contract-") as temporary:
        temporary_root = Path(temporary)
        repository = temporary_root / "repository"
        runtime_root = temporary_root / "runtime"
        repository.mkdir()
        subprocess.run(("git", "init", "-q", str(repository)), check=True)

        def factory(path: Path, **kwargs: Any) -> Any:
            return create_application(path, retrieval_adapters=CONTRACT_RETRIEVAL, **kwargs)

        runner = CliRunner()
        cli = build_app(factory)
        with _working_directory(repository):
            _invoke(runner, cli, ["init", "--root", str(runtime_root), "--repo-key", REPO_KEY, "--retrieval-profile", "fastembed"])
            with _fixed_time(1_700_000_000_000):
                predecessor = _invoke(
                    runner,
                    cli,
                    [
                        "remember",
                        "repository_knowledge",
                        PREDECESSOR_CONTENT,
                        "--title",
                        PREDECESSOR_TITLE,
                        "--subject-key",
                        "restart-recovery",
                    ],
                )
            with _fixed_time(1_700_000_001_000):
                successor = _invoke(
                    runner,
                    cli,
                    [
                        "remember",
                        "repository_knowledge",
                        SUCCESSOR_CONTENT,
                        "--title",
                        SUCCESSOR_TITLE,
                        "--subject-key",
                        "restart-recovery",
                    ],
                )
            predecessor_id = _required_string(predecessor, "memory_id")
            successor_id = _required_string(successor, "memory_id")
            with _fixed_time(1_700_000_002_000):
                _invoke(runner, cli, ["memory", "supersede", predecessor_id, successor_id, "--reason", EVOLUTION_REASON])

            application = create_application(runtime_root, repo_key=REPO_KEY, retrieval_adapters=CONTRACT_RETRIEVAL)
            hub = HubReadModule(
                application=application,
                repo_key=REPO_KEY,
                recall_readiness=RecallReadiness(
                    profile="deterministic-offline-test", state="configuration_ready", live_checked=False, remediation=None
                ),
            )
            with _fixed_time(1_700_000_003_000):
                responses = {
                    "memories": hub.memories(memory_type=None, status=None, limit=20, cursor=None, selected_memory_id=successor_id),
                    "recall_admitted": hub.recall(
                        query=ADMITTED_QUERY, limit=20, include_superseded=False, workstream_key=None, token_budget=8_192
                    ),
                    "recall_abstained": hub.recall(
                        query=ABSTAINED_QUERY, limit=20, include_superseded=False, workstream_key=None, token_budget=8_192
                    ),
                    "system": hub.system(),
                }

    snapshot = {
        "schema_version": 1,
        "contract": "codecairn/hub-read-v1",
        "evidence_boundary": {
            "browser_connected_to_runtime": False,
            "code_path": "real CLI writes plus Hub Read Module over Markdown, SQLite, and LanceDB",
            "retrieval_adapter": "deterministic offline test adapter, not a production provider",
        },
        "responses": responses,
    }
    normalized = _normalize(snapshot)
    validate_snapshot(normalized)
    return normalized


def build_governance_snapshot() -> dict[str, object]:
    """Exercise the one Myna Hub write through its closed HTTP adapter."""
    with tempfile.TemporaryDirectory(prefix="codecairn-hub-governance-contract-") as temporary:
        runtime_root = Path(temporary) / "runtime"
        application = create_application(runtime_root, repo_key=GOVERNANCE_REPO_KEY, retrieval_adapters=CONTRACT_RETRIEVAL)
        with patch("codecairn.importers.jsonl.read_jsonl", side_effect=_canonical_governance_scan):
            application.import_session(
                REPOSITORY_ROOT / "tests" / "fixtures" / "codex" / "failed_command.jsonl",
                repo_key=GOVERNANCE_REPO_KEY,
                index=False,
                boundary_kind="manual_finalize",
            )
        experience = next(
            memory for memory in application.list_memories(repo_key=GOVERNANCE_REPO_KEY) if memory.memory_type == "task_experience"
        )
        source_fact = next(fact for fact in experience.facts if fact.role == "user")
        with _fixed_time(1_700_000_004_000):
            preference = application.remember_direct(
                RememberRequest(
                    repo_key=GOVERNANCE_REPO_KEY,
                    memory_type="user_preference",
                    title=GOVERNANCE_TITLE,
                    content=GOVERNANCE_CONTENT,
                    category="workflow",
                    subject_key=GOVERNANCE_SUBJECT,
                    source_fact_ids=(source_fact.fact_id,),
                )
            )
        memory_files_before = {path.relative_to(runtime_root): path.read_bytes() for path in (runtime_root / "memory").glob("**/*.md")}

        library = create_myna_application(runtime_root, repository_key=GOVERNANCE_REPO_KEY, retrieval_adapters=CONTRACT_RETRIEVAL)
        app = create_hub_app(
            application=application,
            repo_key=GOVERNANCE_REPO_KEY,
            session_token=GOVERNANCE_SESSION_TOKEN,
            recall_readiness=RecallReadiness(
                profile="deterministic-offline-test", state="configuration_ready", live_checked=False, remediation=None
            ),
            library=library,
        )
        headers = {"x-codecairn-hub-token": GOVERNANCE_SESSION_TOKEN}
        accepted_request = {"memory_id": preference.memory_id}
        rejected_request = {"memory_id": preference.memory_id, "person_id": f"person_{'0' * 64}"}
        with (
            _fixed_time(1_700_000_005_000),
            patch("codecairn.storage.library_markdown.secrets.token_hex", return_value="1" * 64),
            patch("codecairn_hub_api.app.uuid.uuid4", return_value=uuid.UUID(int=0)),
            TestClient(app) as client,
        ):
            created = client.post("/hub-governance/v1/preferences/promote", headers=headers, json=accepted_request)
            replay = client.post("/hub-governance/v1/preferences/promote", headers=headers, json=accepted_request)
            owner_injection = client.post("/hub-governance/v1/preferences/promote", headers=headers, json=rejected_request)
        if created.status_code != 200 or replay.status_code != 200 or owner_injection.status_code != 422:
            raise AssertionError("Hub Governance HTTP fixture did not produce the expected statuses")
        memory_files_after = {path.relative_to(runtime_root): path.read_bytes() for path in (runtime_root / "memory").glob("**/*.md")}
        promotion = library.library().promotions[0]

    snapshot = {
        "schema_version": 1,
        "contract": "codecairn/hub-governance-v1",
        "evidence_boundary": {
            "browser_connected_to_runtime": False,
            "code_path": "real User Preference write plus Hub Governance HTTP adapter over local Markdown and SQLite",
            "retrieval_adapter": "deterministic offline test adapter, not a production provider",
            "formal_v0_5_acceptance": False,
            "source_locator": "canonical contract path for cross-platform identity stability",
        },
        "surface": {
            "method": "POST",
            "path": "/hub-governance/v1/preferences/promote",
            "authentication": "server-issued Hub session token",
            "request_fields": ["memory_id"],
            "server_bound_fields": ["person_id", "current_repository_key", "active_scopes"],
            "query_parameters": False,
        },
        "requests": {"promote": accepted_request, "rejected_owner_injection": rejected_request},
        "responses": {
            "created": created.json(),
            "idempotent_replay": replay.json(),
            "rejected_owner_injection": owner_injection.json(),
        },
        "semantics": {
            "copy_memory": memory_files_after != memory_files_before,
            "source_memory_identity_changes": promotion.source.memory_id != preference.memory_id,
            "promotion_is_reference": promotion.source.repository_key == GOVERNANCE_REPO_KEY
            and promotion.source.memory_id == preference.memory_id,
        },
    }
    normalized = _normalize(snapshot)
    validate_governance_snapshot(normalized)
    return normalized


def _required_string(value: object, key: str) -> str:
    if not isinstance(value, dict) or not isinstance(value.get(key), str):
        raise ValueError(f"CLI output is missing string field: {key}")
    return value[key]


def _normalize(value: object, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _normalize(item_value, key=item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        return 0.0 if key == "latency_ms" else round(value, 6)
    if key == "root" and isinstance(value, str):
        return "<temporary-runtime>"
    return value


def validate_snapshot(snapshot: dict[str, object]) -> None:
    if RENDERER_ID != EXPECTED_RENDERER:
        raise AssertionError(f"Hub renderer contract changed: {RENDERER_ID}")
    responses = snapshot.get("responses")
    if not isinstance(responses, dict):
        raise AssertionError("Hub response examples are missing")

    memories_response = responses["memories"]
    if not isinstance(memories_response, dict):
        raise AssertionError("Memories response is missing")
    page = TypeAdapter(MemoryPage).validate_python(memories_response["page"])
    selected = memories_response["selected"]
    if not isinstance(selected, dict):
        raise AssertionError("Memories response must include the selected memory")
    detail = TypeAdapter(MemoryDetail).validate_python(selected["detail"])
    history = TypeAdapter(MemoryHistory).validate_python(selected["history"])
    admitted_response = responses["recall_admitted"]
    abstained_response = responses["recall_abstained"]
    if not isinstance(admitted_response, dict) or not isinstance(abstained_response, dict):
        raise AssertionError("Recall response examples are missing")
    admitted = TypeAdapter(RecallResult).validate_python(admitted_response["result"])
    abstained = TypeAdapter(RecallResult).validate_python(abstained_response["result"])

    if len(page.items) != 2 or {item.status for item in page.items} != {"active", "superseded"}:
        raise AssertionError("Memories response must expose list lifecycle status")
    if detail.memory.title != SUCCESSOR_TITLE or detail.status != "active":
        raise AssertionError("MemoryDetail must expose the selected memory and lifecycle status")
    if detail.resource_uri != f"codecairn://memory/{detail.memory.memory_id}":
        raise AssertionError("MemoryDetail must expose the canonical memory resource URI")
    statuses = dict(history.statuses)
    if set(statuses.values()) != {"active", "superseded"} or len(history.evolutions) != 1:
        raise AssertionError("Memory history must preserve one active and one superseded revision")

    admitted_trace = admitted.sidecar.admission_trace
    if admitted_trace is None or admitted_trace.outcome != "admitted" or admitted_trace.reason != "relevant_candidate":
        raise AssertionError("Related recall must be admitted by a relevant candidate")
    if admitted.sidecar.context_trace is None or admitted.sidecar.context_trace.renderer != EXPECTED_RENDERER:
        raise AssertionError("Related recall uses an unexpected renderer")

    abstained_trace = abstained.sidecar.admission_trace
    if abstained_trace is None or abstained_trace.outcome != "abstained" or abstained_trace.reason != "below_threshold":
        raise AssertionError("Unrelated recall must explicitly abstain below the relevance threshold")
    if abstained.sidecar.ranked or "No relevant memory was admitted." not in abstained.markdown:
        raise AssertionError("Abstained recall must return no ranked memory and an explicit message")
    if not any(omission.reason == "relevance" for omission in abstained.sidecar.omissions):
        raise AssertionError("Abstained recall must expose at least one relevance omission")
    if abstained.sidecar.context_trace is None or abstained.sidecar.context_trace.renderer != EXPECTED_RENDERER:
        raise AssertionError("Abstained recall uses an unexpected renderer")

    system = responses["system"]
    if not isinstance(system, dict):
        raise AssertionError("System response is missing")
    required_system_keys = {
        "schema_version",
        "observed_at_ms",
        "status",
        "repo_key",
        "runtime_schema",
        "counts",
        "semantic_jobs",
        "hook_receipts",
        "index_jobs",
        "subsystems",
        "providers",
        "recall_readiness",
        "privacy",
    }
    if not required_system_keys.issubset(system):
        raise AssertionError(f"System response is missing keys: {sorted(required_system_keys - set(system))}")
    counts = system["counts"]
    if not isinstance(counts, dict) or system["repo_key"] != REPO_KEY or counts["memories"] != 2:
        raise AssertionError("System response does not describe the fixture namespace")
    if "root" in system or "<temporary-runtime>" in json.dumps(system):
        raise AssertionError("System response must not expose the runtime root")
    readiness = system["recall_readiness"]
    if not isinstance(readiness, dict) or readiness != {
        "profile": "deterministic-offline-test",
        "state": "configuration_ready",
        "live_checked": False,
        "remediation": None,
    }:
        raise AssertionError("System response must distinguish configured recall from a live provider check")


def validate_governance_snapshot(snapshot: dict[str, object]) -> None:
    if set(snapshot) != {"schema_version", "contract", "evidence_boundary", "surface", "requests", "responses", "semantics"}:
        raise AssertionError("Hub Governance example has unexpected top-level fields")
    if snapshot["schema_version"] != 1 or snapshot["contract"] != "codecairn/hub-governance-v1":
        raise AssertionError("Hub Governance example has an unexpected identity")
    evidence_boundary = snapshot["evidence_boundary"]
    if not isinstance(evidence_boundary, dict) or evidence_boundary != {
        "browser_connected_to_runtime": False,
        "code_path": "real User Preference write plus Hub Governance HTTP adapter over local Markdown and SQLite",
        "formal_v0_5_acceptance": False,
        "retrieval_adapter": "deterministic offline test adapter, not a production provider",
        "source_locator": "canonical contract path for cross-platform identity stability",
    }:
        raise AssertionError("Hub Governance evidence boundary is invalid")

    surface = snapshot["surface"]
    if not isinstance(surface, dict) or surface != {
        "method": "POST",
        "path": "/hub-governance/v1/preferences/promote",
        "authentication": "server-issued Hub session token",
        "request_fields": ["memory_id"],
        "server_bound_fields": ["person_id", "current_repository_key", "active_scopes"],
        "query_parameters": False,
    }:
        raise AssertionError("Hub Governance must expose exactly one server-bound write surface")

    requests = snapshot["requests"]
    responses = snapshot["responses"]
    if not isinstance(requests, dict) or not isinstance(responses, dict):
        raise AssertionError("Hub Governance request and response examples are missing")
    promote_request = requests.get("promote")
    if not isinstance(promote_request, dict) or set(promote_request) != {"memory_id"}:
        raise AssertionError("Preference promotion accepts only memory_id")
    memory_id = promote_request["memory_id"]
    if not isinstance(memory_id, str) or not memory_id.startswith("mem_") or len(memory_id) != 68:
        raise AssertionError("Preference promotion requires one canonical Memory ID")
    injected_request = requests.get("rejected_owner_injection")
    if not isinstance(injected_request, dict) or set(injected_request) != {"memory_id", "person_id"}:
        raise AssertionError("Owner-injection rejection example is missing")

    created = responses.get("created")
    replay = responses.get("idempotent_replay")
    rejected = responses.get("rejected_owner_injection")
    if not isinstance(created, dict) or not isinstance(replay, dict) or not isinstance(rejected, dict):
        raise AssertionError("Hub Governance response examples are missing")
    for response, expected_outcome in ((created, "created"), (replay, "already_promoted")):
        if set(response) != {"schema_version", "library_context", "receipt"} or response["schema_version"] != 1:
            raise AssertionError("Preference promotion response fields are not closed")
        context = response["library_context"]
        receipt = response["receipt"]
        if not isinstance(context, dict) or context != {
            "person_id": GOVERNANCE_PERSON_ID,
            "current_repository_key": GOVERNANCE_REPO_KEY,
            "active_scopes": ["global", "repository"],
        }:
            raise AssertionError("Person and scopes must be derived by the bound server")
        if not isinstance(receipt, dict) or set(receipt) != {"outcome", "promotion"} or receipt["outcome"] != expected_outcome:
            raise AssertionError("Preference promotion receipt is invalid")
        promotion = promotion_from_dict(receipt["promotion"])
        if (
            promotion.person_id != GOVERNANCE_PERSON_ID
            or promotion.subject_key != GOVERNANCE_SUBJECT
            or promotion.source.repository_key != GOVERNANCE_REPO_KEY
            or promotion.source.memory_id != memory_id
        ):
            raise AssertionError("Promotion must reference the bound Person, repository, subject, and source Memory")
    created_receipt = created.get("receipt")
    replay_receipt = replay.get("receipt")
    if (
        not isinstance(created_receipt, dict)
        or not isinstance(replay_receipt, dict)
        or created_receipt.get("promotion") != replay_receipt.get("promotion")
    ):
        raise AssertionError("Repeated promotion must return the original immutable reference")

    if set(rejected) != {"schema_version", "error"} or rejected["schema_version"] != 1:
        raise AssertionError("Rejected governance response fields are not closed")
    error = rejected["error"]
    if not isinstance(error, dict) or error != {
        "code": "invalid_request",
        "message": "The Hub request does not match the version 1 interface.",
        "retryable": False,
        "remediation": None,
        "request_id": "hubreq_00000000000000000000000000000000",
    }:
        raise AssertionError("Owner injection must fail through the standard closed error envelope")

    semantics = snapshot["semantics"]
    if not isinstance(semantics, dict) or semantics != {
        "copy_memory": False,
        "source_memory_identity_changes": False,
        "promotion_is_reference": True,
    }:
        raise AssertionError("Hub Governance example changed its exercised reference semantics")


def render_snapshot(snapshot: dict[str, object]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _check_fixture(*, path: Path, observed: dict[str, object], label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing Hub contract fixture. Run with --write: {path}")
    expected_text = path.read_text(encoding="utf-8")
    observed_text = render_snapshot(observed)
    if expected_text == observed_text:
        return
    difference = "".join(
        difflib.unified_diff(
            expected_text.splitlines(keepends=True),
            observed_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"fresh {label} contract",
        )
    )
    raise AssertionError(f"Hub contract fixture is stale:\n{difference}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Replace checked-in snapshots after reviewing a contract change.")
    arguments = parser.parse_args()
    read_observed = build_snapshot()
    governance_observed = build_governance_snapshot()
    if arguments.write:
        for path, observed in ((FIXTURE_PATH, read_observed), (GOVERNANCE_FIXTURE_PATH, governance_observed)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_snapshot(observed), encoding="utf-8")
            print(f"updated {path.relative_to(REPOSITORY_ROOT)}")
        return 0
    _check_fixture(path=FIXTURE_PATH, observed=read_observed, label="Hub Read")
    _check_fixture(path=GOVERNANCE_FIXTURE_PATH, observed=governance_observed, label="Hub Governance")
    print(
        json.dumps(
            {
                "fixtures": [str(FIXTURE_PATH.relative_to(REPOSITORY_ROOT)), str(GOVERNANCE_FIXTURE_PATH.relative_to(REPOSITORY_ROOT))],
                "renderer": EXPECTED_RENDERER,
                "result": "pass",
                "surfaces": {
                    "hub_read": sorted(read_observed["responses"]),
                    "hub_governance": sorted(governance_observed["responses"]),
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
