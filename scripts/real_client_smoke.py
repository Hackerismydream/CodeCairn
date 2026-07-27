#!/usr/bin/env python3
"""Exercise installed CodeCairn hooks through real Codex and Claude clients."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]


def run(
    *, wheel: Path, implementation_sha: str, output: Path, claude_budget_usd: float, claude_provider: dict[str, str]
) -> dict[str, object]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="codecairn-real-clients-") as directory:
        temporary = Path(directory)
        home = temporary / "home"
        home.mkdir()
        repository = temporary / "repository"
        repository.mkdir()
        _command(("git", "init", "--quiet"), cwd=repository)
        tool_dir, bin_dir = temporary / "tools", temporary / "bin"
        environment = {
            **{key: value for key, value in os.environ.items() if not key.startswith(("COV_CORE_", "COVERAGE_"))},
            "HOME": str(home),
            "CODECAIRN_HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "UV_TOOL_DIR": str(tool_dir),
            "UV_TOOL_BIN_DIR": str(bin_dir),
        }
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        _copy_client_identity(home)
        _command(("uv", "tool", "install", "--force", "--python", "3.12", str(wheel.resolve())), environment=environment, timeout=600)
        codecairn = bin_dir / "codecairn"
        if not codecairn.is_file():
            raise ValueError("installed codecairn executable is missing")
        _command(
            (
                str(codecairn),
                "init",
                "--root",
                str(temporary / "runtime"),
                "--repo-key",
                "release/real-clients",
                "--retrieval-profile",
                "fastembed",
            ),
            cwd=repository,
            environment=environment,
            timeout=600,
        )
        codex_hooks = home / ".codex/hooks.json"
        claude_settings = temporary / "claude-settings.json"
        codex_original = b'{"hooks":{}}\n'
        claude_original = b'{"env":{"CODECAIRN_REAL_CLIENT_SMOKE":"1"}}\n'
        codex_hooks.write_bytes(codex_original)
        claude_settings.write_bytes(claude_original)
        installed = _install_hooks(codecairn, codex_hooks, claude_settings, repository, environment)
        before_count = _memory_count(codecairn, repository, environment)
        codex = _run_codex(repository, environment, implementation_sha)
        codex_result = _verify_client(
            "codex",
            codecairn=codecairn,
            repository=repository,
            environment=environment,
            session_id=codex["session_id"],
            transcript=codex["transcript"],
            query=codex["query"],
            expected_before=before_count,
            expected_receipts=3,
        )
        claude = _run_claude(repository, claude_settings, environment, implementation_sha, claude_budget_usd, claude_provider)
        try:
            claude_result = _verify_client(
                "claude",
                codecairn=codecairn,
                repository=repository,
                environment=environment,
                session_id=claude["session_id"],
                transcript=claude["transcript"],
                query=claude["query"],
                expected_before=cast(int, codex_result["memory_count_after"]),
                expected_receipts=6,
            )
        finally:
            _remove_claude_transcript(cast(Path, claude["transcript"]), cast(Path, claude["transcript_root"]))
        codex_hooks.write_bytes(codex_original)
        claude_settings.write_bytes(claude_original)
        report = {
            "schema_version": 1,
            "implementation_sha": implementation_sha,
            "wheel": wheel.name,
            "wheel_sha256": _digest(wheel.read_bytes()),
            "installed_hook_commands": installed,
            "clients": {
                "codex": {
                    **codex_result,
                    "client_version": _version(("codex", "--version"), environment),
                    "trust_mode": "isolated-home-vetted-hook-automation-bypass",
                    "hook_removed": True,
                    "config_readback_verified": codex_hooks.read_bytes() == codex_original,
                },
                "claude": {
                    **claude_result,
                    "client_version": _version(("claude", "--version"), environment),
                    "trust_mode": claude["trust_mode"],
                    "auth_mode": claude["auth_mode"],
                    "transcript_removed": True,
                    "hook_removed": True,
                    "config_readback_verified": claude_settings.read_bytes() == claude_original,
                },
            },
            "configuration": {
                "isolated": True,
                "codex_before_sha256": _digest(codex_original),
                "codex_after_sha256": _digest(codex_hooks.read_bytes()),
                "claude_before_sha256": _digest(claude_original),
                "claude_after_sha256": _digest(claude_settings.read_bytes()),
            },
            "duration_seconds": time.perf_counter() - started,
            "limitations": [
                "Codex non-interactive automation used its explicit hook-trust bypass against an isolated, vetted hook file.",
                "Claude print mode skips the interactive workspace trust dialog and used an isolated settings file.",
            ],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _copy_client_identity(home: Path) -> None:
    codex = home / ".codex"
    codex.mkdir()
    for name in ("auth.json", "config.toml"):
        source = Path.home() / ".codex" / name
        if not source.is_file():
            raise ValueError(f"Codex identity input is missing: {name}")
        shutil.copyfile(source, codex / name)
        os.chmod(codex / name, 0o600)
    claude_state = Path.home() / ".claude.json"
    if claude_state.is_file():
        shutil.copyfile(claude_state, home / ".claude.json")
        os.chmod(home / ".claude.json", 0o600)


def _install_hooks(
    codecairn: Path, codex_hooks: Path, claude_settings: Path, repository: Path, environment: dict[str, str]
) -> dict[str, object]:
    result = _command(
        (
            str(codecairn),
            "hook",
            "install",
            "--codex",
            "--claude",
            "--codex-hooks",
            str(codex_hooks),
            "--claude-settings",
            str(claude_settings),
        ),
        cwd=repository,
        environment=environment,
    )
    payload = _dict(json.loads(result.stdout), "hook install")
    hooks = payload.get("hooks")
    if (
        not isinstance(hooks, list)
        or len(hooks) != 2
        or any(not isinstance(item, dict) or item.get("changed") is not True for item in hooks)
    ):
        raise ValueError("real-client hook installation did not change both isolated settings")
    return {
        cast(str, item["client"]): {
            "changed": item["changed"],
            "command_sha256": _digest(json.dumps(_dict(item.get("merged"), "merged")["hooks"], sort_keys=True)),
        }
        for item in cast(list[dict[str, Any]], hooks)
    }


def _run_codex(repository: Path, environment: dict[str, str], implementation_sha: str) -> dict[str, Any]:
    query = f"Remember real Codex hook smoke {implementation_sha[:12]}. Reply with exactly CODEX_HOOK_SMOKE_OK."
    result = _command(
        (
            "codex",
            "exec",
            "--dangerously-bypass-hook-trust",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "-C",
            str(repository),
            query,
        ),
        cwd=repository,
        environment=environment,
        timeout=600,
    )
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    session_id = next(
        (
            record.get("thread_id")
            for record in records
            if record.get("type") in {"thread.started", "thread_started"} and isinstance(record.get("thread_id"), str)
        ),
        None,
    )
    if not session_id:
        raise ValueError("Codex real session did not expose a thread ID")
    transcript = _one_transcript(Path(environment["CODEX_HOME"]), session_id)
    return {"session_id": session_id, "transcript": transcript, "query": query}


def _run_claude(
    repository: Path, settings: Path, environment: dict[str, str], implementation_sha: str, budget_usd: float, provider: dict[str, str]
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    query = f"Remember real Claude hook smoke {implementation_sha[:12]}. Reply with exactly CLAUDE_HOOK_SMOKE_OK."
    claude_environment = {**environment, **provider}
    auth_mode = "api-key-env" if provider else "existing-oauth"
    if not provider:
        claude_environment["HOME"] = str(Path.home())
    root = Path(claude_environment["HOME"]) / ".claude"
    model = ("--model", provider["ANTHROPIC_MODEL"]) if provider else ()
    try:
        _command(
            (
                "claude",
                "-p",
                "--output-format",
                "json",
                "--setting-sources",
                "project,local",
                "--settings",
                str(settings),
                "--session-id",
                session_id,
                "--max-budget-usd",
                str(budget_usd),
                *model,
                "--tools",
                "",
            ),
            cwd=repository,
            environment=claude_environment,
            input_text=query,
            timeout=600,
        )
    except Exception:
        for path in root.rglob(f"*{session_id}*.jsonl"):
            _remove_claude_transcript(path, root)
        raise
    transcript = _one_transcript(root, session_id)
    return {
        "session_id": session_id,
        "transcript": transcript,
        "transcript_root": root,
        "query": query,
        "auth_mode": auth_mode,
        "trust_mode": "isolated-settings-print-mode-" + auth_mode,
    }


def _verify_client(
    client: str,
    *,
    codecairn: Path,
    repository: Path,
    environment: dict[str, str],
    session_id: str,
    transcript: Path,
    query: str,
    expected_before: int,
    expected_receipts: int,
) -> dict[str, object]:
    after_native = _memory_count(codecairn, repository, environment)
    if after_native <= expected_before:
        doctor = _dict(
            json.loads(_command((str(codecairn), "doctor", "--format", "json"), cwd=repository, environment=environment).stdout),
            "doctor",
        )
        raise ValueError(
            f"{client} native hook did not create memory: "
            f"before={expected_before}, after={after_native}, receipts={doctor.get('hook_receipts')}"
        )
    event = {
        "hook_event_name": "Stop" if client == "codex" else "SessionEnd",
        "session_id": session_id,
        "cwd": str(repository),
        "transcript_path": str(transcript),
    }
    for _repeat in range(2):
        _command(
            (str(codecairn), "hook", "run", "--client", client), cwd=repository, environment=environment, input_text=json.dumps(event)
        )
    after_repeat = _memory_count(codecairn, repository, environment)
    recalled = _dict(
        json.loads(_command((str(codecairn), "recall", query), cwd=repository, environment=environment, timeout=600).stdout), "recall"
    )
    doctor = _dict(
        json.loads(_command((str(codecairn), "doctor", "--format", "json"), cwd=repository, environment=environment).stdout), "doctor"
    )
    sidecar = _dict(recalled.get("sidecar"), "recall sidecar")
    receipts = _dict(doctor.get("hook_receipts"), "hook receipts")
    receipt_verified = receipts.get("failed") == 0 and cast(int, receipts.get("total", 0)) >= expected_receipts
    recall_verified = bool(sidecar.get("ranked"))
    if after_repeat != after_native or not receipt_verified or not recall_verified:
        raise ValueError(f"{client} hook receipt, idempotency, or recall verification failed")
    return {
        "hook_installed": True,
        "receipt_verified": receipt_verified,
        "recall_verified": recall_verified,
        "native_created_memory_count": after_native - expected_before,
        "memory_count_after": after_native,
        "repeat_created_memory_count": after_repeat - after_native,
        "transcript_sha256": _digest(transcript.read_bytes()),
    }


def _memory_count(codecairn: Path, repository: Path, environment: dict[str, str]) -> int:
    result = _command((str(codecairn), "list"), cwd=repository, environment=environment)
    value = json.loads(result.stdout)
    if not isinstance(value, list):
        raise ValueError("installed memory list is not an array")
    return len(value)


def _one_transcript(root: Path, session_id: str) -> Path:
    matches = tuple(path for path in root.rglob(f"*{session_id}*.jsonl") if path.is_file())
    if len(matches) != 1:
        raise ValueError(f"real-client transcript inventory is ambiguous: {len(matches)}")
    return matches[0]


def _remove_claude_transcript(path: Path, root: Path) -> None:
    root = root.resolve()
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root) or not path.is_file():
        raise ValueError("refusing to remove a non-owned Claude smoke transcript")
    path.unlink()


def _claude_provider(key_env: str | None, base_env: str | None, model_env: str | None) -> dict[str, str]:
    names = (key_env, base_env, model_env)
    if not any(names):
        return {}
    if not all(names):
        raise ValueError("all three Claude provider environment names are required")
    key = os.environ.get(cast(str, key_env))
    base = os.environ.get(cast(str, base_env))
    model = os.environ.get(cast(str, model_env))
    if not key or not base or not model:
        raise ValueError("one or more Claude provider environment values are missing")
    return {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": base.removesuffix("/v1").rstrip("/"), "ANTHROPIC_MODEL": model}


def _version(command: tuple[str, ...], environment: dict[str, str]) -> str:
    return _command(command, environment=environment).stdout.strip()[:256]


def _command(
    command: tuple[str, ...],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=environment, input=input_text, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout)[-2_000:]
        raise RuntimeError(f"{Path(command[0]).name} failed with exit code {result.returncode}: {detail}")
    return result


def _dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _digest(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _wheel(path: Path) -> Path:
    if path.is_file():
        return path
    wheels = sorted(path.glob("codecairn-*.whl"))
    if len(wheels) != 1:
        raise ValueError("wheel path must resolve to one CodeCairn wheel")
    return wheels[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=ROOT / "dist")
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spend-ack", choices=("YES",), required=True)
    parser.add_argument("--claude-max-budget-usd", type=float, required=True)
    parser.add_argument("--claude-api-key-env")
    parser.add_argument("--claude-base-url-env")
    parser.add_argument("--claude-model-env")
    arguments = parser.parse_args()
    if not math.isfinite(arguments.claude_max_budget_usd) or arguments.claude_max_budget_usd <= 0:
        parser.error("--claude-max-budget-usd must be a positive finite number")
    print(
        json.dumps(
            run(
                wheel=_wheel(arguments.wheel),
                implementation_sha=arguments.implementation_sha,
                output=arguments.output,
                claude_budget_usd=arguments.claude_max_budget_usd,
                claude_provider=_claude_provider(
                    arguments.claude_api_key_env, arguments.claude_base_url_env, arguments.claude_model_env
                ),
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
