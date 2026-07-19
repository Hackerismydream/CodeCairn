from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from codecairn.evaluation.coding import (
    AgentExecution,
    AgentRunRequest,
    CodingRunConfig,
    TraceEvent,
    load_coding_suite,
    parse_codex_trace,
    report_coding_runs,
    run_coding_evaluation,
)

BENCHMARK_ROOT = Path(__file__).parent.parent / "benchmarks" / "coding"


@dataclass
class RecordingAgent:
    fail_repeats: set[int] = field(default_factory=set)
    requests: list[AgentRunRequest] = field(default_factory=list)

    @property
    def public_config(self) -> dict[str, object]:
        return {"adapter": "recording", "model": "test-agent"}

    def run(self, request: AgentRunRequest) -> AgentExecution:
        self.requests.append(request)
        if request.repeat in self.fail_repeats:
            raise RuntimeError("provider unavailable")
        target = request.workspace / "answer.txt"
        target.write_text("memory\n" if request.recall_context else "plain\n", encoding="utf-8")
        return AgentExecution(
            exit_code=0,
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            cost_usd=0.01,
            events=(
                TraceEvent(step=1, kind="file_read", path="answer.txt"),
                TraceEvent(step=2, kind="file_read", path="answer.txt"),
                TraceEvent(step=3, kind="command", command="false", exit_code=1),
                TraceEvent(step=4, kind="command", command="false", exit_code=1),
                TraceEvent(step=5, kind="file_change", path="answer.txt"),
            ),
            raw_trace="fixture trace\n",
        )


def _write_suite(root: Path) -> Path:
    starter = root / "starter"
    starter.mkdir(parents=True)
    (starter / "answer.txt").write_text("broken\n", encoding="utf-8")
    (starter / "verify.py").write_text(
        "from pathlib import Path\n"
        "value = Path('answer.txt').read_text().strip()\n"
        "raise SystemExit(0 if value in {'plain', 'memory'} else 1)\n",
        encoding="utf-8",
    )
    memory = root / "memory.md"
    memory.write_text("Use the repository convention.\n", encoding="utf-8")
    suite = root / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite_id": "fixture",
                "tasks": [
                    {
                        "task_id": "task-01",
                        "prompt": "Repair answer.txt.",
                        "starter_path": "starter",
                        "recall_context_path": "memory.md",
                        "verifier_argv": ["{python}", "verify.py"],
                        "verifier_timeout_seconds": 10,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return suite


def test_coding_runner_creates_isolated_immutable_runs_and_executes_verifier(
    tmp_path: Path,
) -> None:
    suite_path = _write_suite(tmp_path / "inputs")
    agent = RecordingAgent()
    config = CodingRunConfig(
        suite_path=suite_path,
        output_root=tmp_path / "runs",
        experiment_id="fixture-exp",
        repository_commit="abc123",
        repeats=3,
        seed=17,
    )

    artifact = run_coding_evaluation(config, agent=agent)

    assert artifact.summary["planned_run_count"] == 6
    assert artifact.summary["completed_run_count"] == 6
    assert artifact.summary["infrastructure_failure_count"] == 0
    assert {request.arm for request in agent.requests} == {"memory-on", "memory-off"}
    assert [request.arm for request in agent.requests] == [
        "memory-off",
        "memory-off",
        "memory-off",
        "memory-on",
        "memory-on",
        "memory-on",
    ]
    off_requests = [request for request in agent.requests if request.arm == "memory-off"]
    assert all(request.recall_context is None for request in off_requests)
    assert all(not (request.workspace / ".codecairn").exists() for request in off_requests)
    assert len({request.workspace for request in agent.requests}) == 6

    run_dirs = sorted(path for path in artifact.run_dir.iterdir() if path.is_dir())
    assert len(run_dirs) == 6
    for run_dir in run_dirs:
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        verifier = json.loads((run_dir / "verifier.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert result["outcome"] == "passed"
        assert verifier["exit_code"] == 0
        assert verifier["executed_in_workspace"] is True
        assert manifest["workspace_snapshot_before_sha256"]
        assert manifest["memory_snapshot_sha256"]
        assert (run_dir / "raw-agent-trace.log").read_text(encoding="utf-8")

    on = artifact.summary["arms"]["memory-on"]
    assert on["pass_rate"] == 1.0
    assert on["mean_repeated_file_reads"] == 1.0
    assert on["mean_repeated_failed_commands"] == 1.0
    assert on["mean_steps_to_first_useful_action"] == 5.0
    assert on["total_tokens"] == 360
    assert on["total_input_tokens"] == 300
    assert on["total_cached_input_tokens"] == 120
    assert on["total_output_tokens"] == 60
    assert on["total_cost_usd"] == pytest.approx(0.03)

    before = {path: path.stat().st_mtime_ns for path in artifact.run_dir.rglob("*")}
    assert report_coding_runs(artifact.run_dir) == artifact.summary
    after = {path: path.stat().st_mtime_ns for path in artifact.run_dir.rglob("*")}
    assert after == before
    with pytest.raises(FileExistsError):
        run_coding_evaluation(config, agent=agent)


def test_provider_failure_is_infrastructure_failure_not_task_failure(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path / "inputs")
    artifact = run_coding_evaluation(
        CodingRunConfig(
            suite_path=suite_path,
            output_root=tmp_path / "runs",
            experiment_id="infra-exp",
            repository_commit="abc123",
            repeats=1,
        ),
        agent=RecordingAgent(fail_repeats={1}),
    )

    assert artifact.summary["planned_run_count"] == 2
    assert artifact.summary["completed_run_count"] == 0
    assert artifact.summary["infrastructure_failure_count"] == 2
    assert artifact.summary["arms"]["memory-on"]["task_failure_count"] == 0
    assert artifact.summary["arms"]["memory-off"]["task_failure_count"] == 0


def test_verifier_execution_error_is_infrastructure_failure(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path / "inputs")
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    payload["tasks"][0]["verifier_argv"] = ["missing-verifier-executable"]
    suite_path.write_text(json.dumps(payload), encoding="utf-8")

    artifact = run_coding_evaluation(
        CodingRunConfig(
            suite_path=suite_path,
            output_root=tmp_path / "runs",
            experiment_id="verifier-infra-exp",
            repository_commit="abc123",
            repeats=1,
        ),
        agent=RecordingAgent(),
    )

    assert artifact.summary["completed_run_count"] == 0
    assert artifact.summary["infrastructure_failure_count"] == 2
    verifier_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in artifact.run_dir.glob("*/verifier.json")
    ]
    assert len(verifier_records) == 2
    assert all(record["status"] == "infrastructure_failed" for record in verifier_records)


def test_checked_in_suite_defines_20_tasks_and_120_default_runs() -> None:
    suite = load_coding_suite(BENCHMARK_ROOT / "suite.json")

    assert len(suite.tasks) == 20
    assert len({task.task_id for task in suite.tasks}) == 20
    assert len(suite.tasks) * 2 * 3 == 120
    assert all(task.verifier_argv for task in suite.tasks)
    assert all(task.recall_context_path is not None for task in suite.tasks)


def test_codex_trace_parser_extracts_usage_changes_and_shell_wrapped_reads() -> None:
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "/bin/zsh -lc \"sed -n '1,40p' kata.py\"",
                        "exit_code": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "file_change",
                        "changes": [{"path": "kata.py"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 60,
                        "output_tokens": 20,
                    },
                }
            ),
        ]
    )

    events, input_tokens, cached_input_tokens, output_tokens = parse_codex_trace(raw)

    assert [event.kind for event in events] == ["command", "file_read", "file_change"]
    assert events[1].path == "kata.py"
    assert (input_tokens, cached_input_tokens, output_tokens) == (100, 60, 20)
