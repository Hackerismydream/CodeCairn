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
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic import TypeAdapter
from typer.testing import CliRunner

from codecairn.bootstrap import create_application
from codecairn.entrypoints.cli import build_app
from codecairn.memory.context import RENDERER_ID
from codecairn.memory.evolution import MemoryHistory
from codecairn.memory.models import RecallResult
from codecairn.memory.schema import CodingMemory
from codecairn.service.application import MemoryDetail, MemoryPage
from tests.retrieval_fakes import TEST_RETRIEVAL

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPOSITORY_ROOT / "hub" / "fixtures" / "codecairn-contract.json"
REPO_KEY = "github.com/Hackerismydream/CodeCairn"
EXPECTED_RENDERER = "codecairn/typed-excerpt-context-v2"
PREDECESSOR_TITLE = "旧结论: 单元测试足以证明连续性"
PREDECESSOR_CONTENT = "单元测试通过即可证明跨进程连续性。"
SUCCESSOR_TITLE = "连续性必须由全新进程验证"
SUCCESSOR_CONTENT = "跨进程连续性需要持久化回执, 并在全新进程中验证同一任务只投递一次。"
EVOLUTION_REASON = "全新进程验证替代了仅依赖单元测试的旧结论。"
ADMITTED_QUERY = SUCCESSOR_TITLE
ABSTAINED_QUERY = "为前端首页选择一种暖色插画风格"


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
            return create_application(path, retrieval_adapters=TEST_RETRIEVAL, **kwargs)

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

            outputs = {
                "list": _invoke(runner, cli, ["list"]),
                "memory_show": _invoke(runner, cli, ["memory", "show", successor_id]),
                "memory_history": _invoke(runner, cli, ["memory", "history", successor_id]),
                "recall_admitted": _invoke(runner, cli, ["recall", ADMITTED_QUERY]),
                "recall_abstained": _invoke(runner, cli, ["recall", ABSTAINED_QUERY]),
                "doctor": _invoke(runner, cli, ["doctor", "--format", "json"]),
            }
            application = create_application(runtime_root, repo_key=REPO_KEY, retrieval_adapters=TEST_RETRIEVAL)
            outputs["memory_page"] = asdict(application.list_memory_page(repo_key=REPO_KEY))
            outputs["memory_detail"] = asdict(application.get_memory(repo_key=REPO_KEY, memory_id=successor_id))

    snapshot = {
        "schema_version": 1,
        "fixture_kind": "deterministic_read_contract",
        "evidence_boundary": {
            "browser_connected_to_runtime": False,
            "code_path": "real CLI, service, Markdown, SQLite, and LanceDB",
            "retrieval_adapter": "deterministic offline test adapter, not a production provider",
        },
        "outputs": outputs,
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
    outputs = snapshot.get("outputs")
    if not isinstance(outputs, dict):
        raise AssertionError("Snapshot outputs are missing")

    memories = TypeAdapter(tuple[CodingMemory, ...]).validate_python(outputs["list"])
    shown = TypeAdapter(CodingMemory).validate_python(outputs["memory_show"])
    history = TypeAdapter(MemoryHistory).validate_python(outputs["memory_history"])
    page = TypeAdapter(MemoryPage).validate_python(outputs["memory_page"])
    detail = TypeAdapter(MemoryDetail).validate_python(outputs["memory_detail"])
    admitted = TypeAdapter(RecallResult).validate_python(outputs["recall_admitted"])
    abstained = TypeAdapter(RecallResult).validate_python(outputs["recall_abstained"])

    if len(memories) != 2 or shown.memory_id not in {memory.memory_id for memory in memories}:
        raise AssertionError("List and memory-show outputs do not describe the same fixture")
    if len(page.items) != 2 or {item.status for item in page.items} != {"active", "superseded"}:
        raise AssertionError("MemoryPage must expose list lifecycle status")
    if detail.memory.memory_id != shown.memory_id or detail.status != "active":
        raise AssertionError("MemoryDetail must expose the selected memory and lifecycle status")
    if detail.resource_uri != f"codecairn://memory/{shown.memory_id}":
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

    doctor = outputs["doctor"]
    if not isinstance(doctor, dict):
        raise AssertionError("Doctor JSON is missing")
    required_doctor_keys = {
        "status",
        "schema",
        "repo_key",
        "memories",
        "semantic_jobs",
        "hook_receipts",
        "index_jobs",
        "subsystems",
        "providers",
        "privacy",
    }
    if not required_doctor_keys.issubset(doctor):
        raise AssertionError(f"Doctor JSON is missing keys: {sorted(required_doctor_keys - set(doctor))}")
    if doctor["repo_key"] != REPO_KEY or doctor["memories"] != 2:
        raise AssertionError("Doctor JSON does not describe the fixture namespace")


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
                tofile="fresh CLI contract",
            )
        )
        raise AssertionError(f"Hub contract fixture is stale:\n{difference}")
    print(
        json.dumps(
            {
                "fixture": str(FIXTURE_PATH.relative_to(REPOSITORY_ROOT)),
                "renderer": EXPECTED_RENDERER,
                "result": "pass",
                "surfaces": sorted(observed["outputs"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
