from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from codecairn_v03_acceptance.adapters import pico
from codecairn_v03_acceptance.adapters.pico import (
    PicoAdapterError,
    PicoTurnSpec,
    collect_learn_trace,
    collect_recall_trace,
    execute_pico_turn,
    prepare_pico_configs,
)


def test_turn_spec_builds_the_installed_public_cli_command_and_isolates_state(tmp_path: Path) -> None:
    executable = tmp_path / "venv" / "bin" / "pico"
    executable.parent.mkdir(parents=True)
    executable.touch()
    pico_home = tmp_path / "pico-home"
    workspace = pico_home / "workspace"
    workspace.mkdir(parents=True)
    config = tmp_path / "private" / "recall.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    trace_dir = tmp_path / "traces" / "recall"
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()

    spec = PicoTurnSpec(
        executable=executable,
        message="上次为什么修改重试次数？",
        session_id="cli:v03-recall-001",
        workspace=workspace,
        operator_dir=operator_dir,
        config=config,
        pico_home=pico_home,
        trace_dir=trace_dir,
        timeout_seconds=120,
    )

    assert spec.command == (
        str(executable.resolve()),
        "run",
        "--message",
        "上次为什么修改重试次数？",
        "--session",
        "cli:v03-recall-001",
        "--workspace",
        str(workspace.resolve()),
        "--config",
        str(config.resolve()),
        "--no-markdown",
        "--no-logs",
    )
    assert spec.environment({"PATH": "/usr/bin", "UNRELATED_SECRET": "must-not-pass"}) == {
        "HOME": str(pico_home.resolve()),
        "LANG": "C.UTF-8",
        "NO_PROXY": "127.0.0.1,localhost",
        "PATH": f"{executable.resolve().parent}:/usr/bin",
        "PICO_HOME": str(pico_home.resolve()),
        "PICO_TRACING": "1",
        "PICO_TRACING_DIR": str(trace_dir.resolve()),
        "PYTHONPATH": "",
        "PYTHONNOUSERSITE": "1",
    }


def test_recall_trace_joins_turn_memory_and_llm_input_without_trusting_stdout(tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace"
    artifacts = trace_dir / "logs" / "audit-artifacts"
    artifacts.mkdir(parents=True)
    remembered = "mem_" + "1" * 64
    memory_artifact = artifacts / "memory.json"
    llm_artifact = artifacts / "llm.json"
    compiled_context = f"Decision label amber-cairn-2049\nMemory: {remembered}"
    _write_json(
        memory_artifact,
        [
            {
                "text": compiled_context,
                "score": 0.0,
                "metadata": {
                    "backend": "codecairn",
                    "repo_key": "local/v03-acceptance",
                    "freshness": "fresh",
                    "rendered_memory_ids": [remembered],
                    "source_uris": [f"codecairn://memory/{remembered}"],
                    "source_cursor": 12,
                    "index_cursor": 12,
                },
            }
        ],
    )
    _write_json(llm_artifact, {"messages": [{"role": "system", "content": f"# Memory\n{compiled_context}"}], "tools": []})
    spans = [
        _span("spine.turn", "trace-recall", "span-root", "cli:v03-recall-001", {"spine.outcome": "completed"}),
        _span(
            "memory.recall",
            "trace-recall",
            "span-memory",
            "cli:v03-recall-001",
            {
                "memory.hits": 1,
                "memory.recall.artifact_path": str(memory_artifact),
                "memory.recall.artifact_sha1": _sha1(memory_artifact),
                "memory.recall.artifact_bytes": memory_artifact.stat().st_size,
            },
        ),
        _span(
            "llm.call",
            "trace-recall",
            "span-llm",
            "cli:v03-recall-001",
            {
                "llm.input.artifact_path": str(llm_artifact),
                "llm.input.artifact_sha1": _sha1(llm_artifact),
                "llm.input.artifact_bytes": llm_artifact.stat().st_size,
            },
        ),
    ]
    spans_path = trace_dir / "logs" / "audit-spans.log"
    spans_path.parent.mkdir(parents=True, exist_ok=True)
    spans_path.write_text("".join(json.dumps(span) + "\n" for span in spans), encoding="utf-8")

    receipt = collect_recall_trace(
        trace_dir=trace_dir,
        session_id="cli:v03-recall-001",
        expected_memory_ids={remembered},
        expected_repo_key="local/v03-acceptance",
        decision_marker="amber-cairn-2049",
    )

    assert receipt == {
        "trace_contract": "audit.span.v1",
        "trace_id": "trace-recall",
        "session_id": "cli:v03-recall-001",
        "terminal_outcome": "completed",
        "recalled_memory_ids": [remembered],
        "llm_input_memory_ids": [remembered],
        "source_uris": [f"codecairn://memory/{remembered}"],
        "source_cursor": 12,
        "index_cursor": 12,
        "forbidden_tool_calls": 0,
    }

    _write_json(memory_artifact, [{**json.loads(memory_artifact.read_text())[0], "text": "unrelated recalled text"}])
    spans[1]["attributes"]["memory.recall.artifact_sha1"] = _sha1(memory_artifact)
    spans[1]["attributes"]["memory.recall.artifact_bytes"] = memory_artifact.stat().st_size
    spans_path.write_text("".join(json.dumps(span) + "\n" for span in spans), encoding="utf-8")
    with pytest.raises(PicoAdapterError, match="evidence_incomplete"):
        collect_recall_trace(
            trace_dir=trace_dir,
            session_id="cli:v03-recall-001",
            expected_memory_ids={remembered},
            expected_repo_key="local/v03-acceptance",
            decision_marker="amber-cairn-2049",
        )

    memory_artifact.write_text("[]", encoding="utf-8")
    with pytest.raises(PicoAdapterError, match="evidence_incomplete"):
        collect_recall_trace(
            trace_dir=trace_dir,
            session_id="cli:v03-recall-001",
            expected_memory_ids={remembered},
            expected_repo_key="local/v03-acceptance",
            decision_marker="amber-cairn-2049",
        )


def test_learn_trace_requires_one_completed_real_turn(tmp_path: Path) -> None:
    trace_dir = tmp_path / "learn-trace"
    spans_path = trace_dir / "logs" / "audit-spans.log"
    spans_path.parent.mkdir(parents=True)
    spans = [
        _span("spine.turn", "trace-learn", "span-root", "cli:v03-learn-001", {"spine.outcome": "completed"}),
        _span("llm.call", "trace-learn", "span-llm", "cli:v03-learn-001", {}),
        _span("tool.call", "trace-learn", "span-tool", "cli:v03-learn-001", {"tool.name": "edit_file"}),
    ]
    spans_path.write_text("".join(json.dumps(span) + "\n" for span in spans), encoding="utf-8")

    receipt = collect_learn_trace(trace_dir=trace_dir, session_id="cli:v03-learn-001")

    assert receipt == {
        "trace_contract": "audit.span.v1",
        "trace_id": "trace-learn",
        "session_id": "cli:v03-learn-001",
        "terminal_outcome": "completed",
        "llm_call_count": 1,
        "tool_call_count": 1,
    }


def test_trace_reader_rejects_symlinks_and_oversized_untrusted_input(monkeypatch, tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace"
    spans_path = trace_dir / "logs" / "audit-spans.log"
    spans_path.parent.mkdir(parents=True)
    outside = tmp_path / "outside.log"
    outside.write_text("{}\n", encoding="utf-8")
    spans_path.symlink_to(outside)

    with pytest.raises(PicoAdapterError, match="evidence_incomplete"):
        collect_learn_trace(trace_dir=trace_dir, session_id="cli:v03-learn-001")

    spans_path.unlink()
    spans_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(pico, "_MAX_TRACE_LOG_BYTES", 1)
    with pytest.raises(PicoAdapterError, match="evidence_incomplete"):
        collect_learn_trace(trace_dir=trace_dir, session_id="cli:v03-learn-001")


def test_recall_config_disables_side_channels_without_copying_secrets_into_receipt(tmp_path: Path) -> None:
    base_config = tmp_path / "base.json"
    _write_json(
        base_config,
        {
            "agents": {"defaults": {"model": "openai/example", "provider": "openai", "enablePersonalization": True}},
            "providers": {"openai": {"apiKey": "PRIVATE-KEY"}},
            "tools": {"mcpServers": {"filesystem": {"command": "unsafe"}}, "toolSearch": {"enabled": True}},
            "routing": {"enabled": True},
        },
    )

    pair = prepare_pico_configs(base_config=base_config, output_dir=tmp_path / "private")
    learn = json.loads(pair.learn.read_text(encoding="utf-8"))
    recall = json.loads(pair.recall.read_text(encoding="utf-8"))

    assert stat.S_IMODE(pair.learn.stat().st_mode) == 0o600
    assert stat.S_IMODE(pair.recall.stat().st_mode) == 0o600
    assert learn["memory"] == {"backend": "codecairn", "memoryTopK": 5}
    assert recall["memory"] == {"backend": "codecairn", "memoryTopK": 5}
    assert recall["agents"]["defaults"]["enablePersonalization"] is False
    assert recall["routing"]["enabled"] is False
    assert recall["skillForge"] == {"enabled": False, "router": {"enabled": False}}
    assert recall["tools"]["mcpServers"] == {}
    assert recall["tools"]["restrictToWorkspace"] is True
    assert recall["tools"]["toolSearch"]["enabled"] is False
    assert set(recall["tools"]["disabledTools"]) >= {"exec", "read_file", "web_fetch", "web_search", "tool_search", "tool_call"}
    assert set(learn["tools"]["disabledTools"]) >= {"web_fetch", "web_search", "message", "spawn", "cron", "tool_search", "tool_call"}
    assert "PRIVATE-KEY" in pair.learn.read_text(encoding="utf-8")
    assert "PRIVATE-KEY" not in json.dumps(pair.public_receipt)


def test_execute_turn_uses_a_real_process_and_exports_a_verified_session(tmp_path: Path) -> None:
    executable = tmp_path / "venv" / "bin" / "pico"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        f"""#!{sys.executable}
import hashlib
import json
import sys
from pathlib import Path

if sys.argv[1] == "run":
    print("agent response")
    raise SystemExit(0)
if sys.argv[1:3] == ["sessions", "export"]:
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    session_id = sys.argv[3]
    payload = {{
        "key": session_id,
        "created_at": "2026-07-31T00:00:00+00:00",
        "updated_at": "2026-07-31T00:00:01+00:00",
        "metadata": {{}},
        "last_consolidated": 0,
        "pending_clarification": None,
        "messages": [{{"role": "user", "content": "task"}}],
        "message_count": 1,
        "transcript_markdown": "# Session",
    }}
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    output.write_text(json.dumps({{"schema": "pico.session.export.v1", "payload": payload,
                                  "sha256": hashlib.sha256(canonical).hexdigest()}}), encoding="utf-8")
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    pico_home = tmp_path / "pico-home"
    workspace = pico_home / "workspace"
    workspace.mkdir(parents=True)
    config = tmp_path / "private" / "learn.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    spec = PicoTurnSpec(
        executable=executable,
        message="完成验收任务",
        session_id="cli:v03-learn-001",
        workspace=workspace,
        operator_dir=operator_dir,
        config=config,
        pico_home=pico_home,
        trace_dir=tmp_path / "trace",
        timeout_seconds=5,
    )

    receipt = execute_pico_turn(spec, artifact_dir=tmp_path / "artifacts", source_environment={"PATH": os.environ["PATH"]})

    assert receipt["contract"] == "codecairn.v03-acceptance.pico-process.v1"
    assert receipt["terminal_class"] == "completed"
    assert receipt["process_id"].startswith("pid:")
    assert receipt["session_id"] == "cli:v03-learn-001"
    assert receipt["exit_code"] == 0
    assert receipt["session_export"]["schema"] == "pico.session.export.v1"
    assert receipt["session_export"]["session_id"] == "cli:v03-learn-001"
    assert (tmp_path / "artifacts" / "session.pico-session.json").is_file()
    assert stat.S_IMODE((tmp_path / "artifacts" / "stdout.txt").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "artifacts" / "stderr.txt").stat().st_mode) == 0o600


def _span(name: str, trace_id: str, span_id: str, session_id: str, attributes: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": "audit.span.v1",
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": None,
        "name": name,
        "kind": "INTERNAL",
        "startTime": "2026-07-31T00:00:00+00:00",
        "endTime": "2026-07-31T00:00:01+00:00",
        "status": {"code": "OK", "message": ""},
        "events": [],
        "attributes": {"session.id": session_id, **attributes},
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()
