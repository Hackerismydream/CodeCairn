#!/usr/bin/env python3
"""Generate and verify the Hub's deterministic CodeCairn contract snapshot."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from codecairn_hub_api.queries import HubReadModule, RecallReadiness
from pydantic import TypeAdapter
from typer.testing import CliRunner

from codecairn.bootstrap import create_application
from codecairn.entrypoints.cli import build_app
from codecairn.evaluation.gates import EvaluationEmbedder, EvaluationReranker
from codecairn.memory.context import RENDERER_ID
from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import RecallResult
from codecairn.service.application import MemoryDetail, MemoryPage

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPOSITORY_ROOT / "contracts" / "hub-read" / "v1.example.json"
REPO_KEY = "github.com/Hackerismydream/CodeCairn"
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
        patch("codecairn_hub_api.queries.time.time_ns", return_value=nanoseconds),
    ):
        yield


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


def render_snapshot(snapshot: dict[str, object]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Replace the checked-in snapshot after reviewing a contract change.")
    arguments = parser.parse_args()
    observed = build_snapshot()
    observed_text = render_snapshot(observed)
    if arguments.write:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(observed_text, encoding="utf-8")
        print(f"updated {FIXTURE_PATH.relative_to(REPOSITORY_ROOT)}")
        return 0
    if not FIXTURE_PATH.is_file():
        raise FileNotFoundError(f"Missing Hub contract fixture. Run with --write: {FIXTURE_PATH}")
    expected_text = FIXTURE_PATH.read_text(encoding="utf-8")
    if expected_text != observed_text:
        difference = "".join(
            difflib.unified_diff(
                expected_text.splitlines(keepends=True),
                observed_text.splitlines(keepends=True),
                fromfile=str(FIXTURE_PATH),
                tofile="fresh Hub read contract",
            )
        )
        raise AssertionError(f"Hub contract fixture is stale:\n{difference}")
    print(
        json.dumps(
            {
                "fixture": str(FIXTURE_PATH.relative_to(REPOSITORY_ROOT)),
                "renderer": EXPECTED_RENDERER,
                "result": "pass",
                "surfaces": sorted(observed["responses"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
