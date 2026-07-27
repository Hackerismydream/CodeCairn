#!/usr/bin/env python3
"""Install one wheel into an isolated uv tool directory and exercise v0.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
TRACE_RECORDS = (
    {"type": "session_meta", "payload": {"id": "installed-smoke-001"}},
    {
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run the repository test suite."}]},
    },
    {
        "type": "response_item",
        "payload": {"type": "function_call", "name": "exec_command", "arguments": '{"cmd":"uv run pytest"}', "call_id": "call-001"},
    },
    {
        "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "call-001", "output": "Process exited with code 1\n1 failed"},
    },
)


def installed_smoke(*, wheel: Path, evidence: Path) -> dict[str, object]:
    started = time.perf_counter()
    stages: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="codecairn-installed-") as directory:
        temporary = Path(directory)
        tool_dir, bin_dir = temporary / "tools", temporary / "bin"
        environment = {
            **{key: value for key, value in os.environ.items() if not key.startswith(("COV_CORE_", "COVERAGE_"))},
            "UV_TOOL_DIR": str(tool_dir),
            "UV_TOOL_BIN_DIR": str(bin_dir),
        }
        _stage(
            stages,
            "uv-tool-install",
            ("uv", "tool", "install", "--force", "--python", "3.12", str(wheel.resolve())),
            environment=environment,
        )
        executable = bin_dir / "codecairn"
        mcp_executable = bin_dir / "codecairn-mcp"
        if not executable.is_file() or not mcp_executable.is_file():
            raise ValueError("installed console scripts are missing")
        _stage(stages, "cli-help", (str(executable), "--help"), environment=environment)

        repository = temporary / "repository"
        repository.mkdir()
        _stage(stages, "git-init", ("git", "init", "--quiet"), cwd=repository, environment=environment)
        runtime = temporary / "runtime"
        initialized = _stage(
            stages,
            "init",
            (str(executable), "init", "--root", str(runtime), "--repo-key", "installed/smoke", "--retrieval-profile", "fastembed"),
            cwd=repository,
            environment=environment,
        )
        if json.loads(initialized.stdout)["status"] != "initialized":
            raise ValueError("installed init did not initialize")

        trace = temporary / "session.jsonl"
        trace.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in TRACE_RECORDS))
        imported = _stage(
            stages,
            "import",
            (str(executable), "import", str(trace), "--finalize"),
            cwd=repository,
            environment=environment,
            timeout=600,
        )
        import_result = json.loads(imported.stdout)
        if import_result["created_memory_count"] != 1 or not import_result["index"]["synced"]:
            raise ValueError(f"installed import/index failed: {import_result}")
        listed = _stage(stages, "list", (str(executable), "list"), cwd=repository, environment=environment)
        memories = json.loads(listed.stdout)
        if len(memories) != 1:
            raise ValueError("installed lifecycle did not retain exactly one memory")
        recalled = _stage(
            stages, "recall", (str(executable), "recall", "repository test suite"), cwd=repository, environment=environment, timeout=600
        )
        if not json.loads(recalled.stdout)["sidecar"]["ranked"]:
            raise ValueError("installed lifecycle recall returned no memory")
        manual_path_seconds = time.perf_counter() - started

        _mcp_smoke(mcp_executable, repository, environment)
        stages.append({"name": "mcp-initialize", "status": "pass"})
        hook = temporary / "claude-settings.json"
        _stage(
            stages,
            "hook-dry-run",
            (str(executable), "hook", "install", "--claude", "--dry-run", "--claude-settings", str(hook)),
            cwd=repository,
            environment={**environment, "PATH": f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"},
        )
        doctor = _stage(stages, "doctor", (str(executable), "doctor", "--format", "json"), cwd=repository, environment=environment)
        if json.loads(doctor.stdout)["repo_key"] != "installed/smoke":
            raise ValueError("installed doctor resolved the wrong namespace")
        one_client_path_seconds = time.perf_counter() - started

        verified = _stage(
            stages,
            "evidence-verify",
            (str(executable), "evidence", "verify", str(evidence.resolve())),
            environment=environment,
            timeout=300,
        )
        if not json.loads(verified.stdout)["verified"]:
            raise ValueError("installed evidence verifier failed")
    return {
        "schema_version": 1,
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "manual_path_seconds": manual_path_seconds,
        "one_client_path_seconds": one_client_path_seconds,
        "total_seconds": time.perf_counter() - started,
        "stages": stages,
    }


def _stage(
    stages: list[dict[str, object]],
    name: str,
    command: tuple[str, ...],
    *,
    cwd: Path | None = None,
    environment: dict[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=timeout)
    stages.append(
        {"name": name, "status": "pass" if result.returncode == 0 else "failed", "duration_seconds": time.perf_counter() - started}
    )
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr[-2000:]}")
    return result


def _mcp_smoke(executable: Path, repository: Path, environment: dict[str, str]) -> None:
    async def exercise() -> None:
        parameters = StdioServerParameters(command=str(executable), cwd=repository, env=environment)
        async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            tools = await client.list_tools()
            resources = await client.list_resource_templates()
            doctor = await client.call_tool("doctor", {})
            if len(tools.tools) != 7 or len(resources.resourceTemplates) != 1 or doctor.isError:
                raise ValueError("installed MCP contract is incomplete")

    anyio.run(exercise)


def _wheel(path: Path) -> Path:
    if path.is_file():
        return path
    wheels = sorted(path.glob("codecairn-*.whl"))
    if len(wheels) != 1:
        raise ValueError("wheel path must resolve to exactly one CodeCairn wheel")
    return wheels[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=ROOT / "dist")
    parser.add_argument("--evidence", type=Path, default=ROOT / "evidence/benchmark-v3")
    arguments = parser.parse_args()
    print(json.dumps(installed_smoke(wheel=_wheel(arguments.wheel), evidence=arguments.evidence), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
