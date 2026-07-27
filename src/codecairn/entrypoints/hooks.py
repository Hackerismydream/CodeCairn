"""Client hook envelope and settings adapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

HookClient = Literal["claude", "codex"]
_MINIMUM = {"claude": (2, 1, 220), "codex": (0, 144, 6)}
_EVENT = {"claude": ("SessionEnd", "session_end"), "codex": ("Stop", "stop")}


@dataclass(frozen=True, slots=True)
class OwnedHookSource:
    client: HookClient
    event: Literal["stop", "session_end"]
    client_version: str
    session_id: str
    session_identity_sha256: str
    source_path: Path
    source_identity_sha256: str
    cwd: Path


def parse_hook_event(client: HookClient, raw: bytes, *, client_version: str, home: Path) -> OwnedHookSource:
    if not raw or len(raw) > 64 * 1024:
        raise ValueError("hook_input_invalid")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("hook_input_invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("hook_input_invalid")
    expected_event, event = _EVENT[client]
    if payload.get("hook_event_name") != expected_event:
        raise ValueError("unsupported_client")
    session_id = _required(payload, "session_id", 256)
    cwd = Path(_required(payload, "cwd", 4_096)).expanduser().resolve()
    source_value = payload.get("transcript_path")
    if source_value is None and client == "codex":
        matches = tuple(home.glob(f".codex/sessions/*/*/*/*{session_id}*.jsonl"))
        if len(matches) != 1:
            raise ValueError("source_unavailable")
        source = matches[0].resolve()
    elif isinstance(source_value, str) and 0 < len(source_value.encode()) <= 4_096:
        source = Path(source_value).expanduser().resolve()
    else:
        raise ValueError("source_unavailable")
    return OwnedHookSource(
        client=client,
        event=cast(Literal["stop", "session_end"], event),
        client_version=client_version,
        session_id=session_id,
        session_identity_sha256=_digest(f"{client}:{session_id}"),
        source_path=source,
        source_identity_sha256=_digest(str(source)),
        cwd=cwd,
    )


def detect_client_version(client: HookClient) -> str:
    executable = "claude" if client == "claude" else "codex"
    try:
        output = subprocess.run((executable, "--version"), check=True, capture_output=True, text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("unsupported_client") from error
    digits = tuple(int(value) for value in re.findall(r"\d+", output)[:3])
    if len(digits) != 3 or digits < _MINIMUM[client]:
        raise ValueError("unsupported_client")
    return output[:256]


def install_hook(*, client: HookClient, target: Path, executable: Path, dry_run: bool) -> dict[str, object]:
    version = detect_client_version(client)
    data, mode = _read_settings(target)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hook_config_invalid")
    event = _EVENT[client][0]
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise ValueError("hook_config_invalid")
    command = f"{shlex.quote(str(executable.resolve()))} hook run --client {client}"
    handler = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
    changed = not any(_handler_command(item) == command for item in entries)
    if changed:
        entries.append(handler)
    encoded = json.dumps(data, indent=2, sort_keys=True) + "\n"
    result = {
        "client": client,
        "client_version": version,
        "target": str(target),
        "changed": changed,
        "dry_run": dry_run,
        "merged": data,
        "uninstall": f"Remove the handler whose command is: {command}",
    }
    if dry_run or not changed:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_bytes() if target.exists() else None
    try:
        _atomic_replace(target, encoded.encode(), mode)
        if target.read_text() != encoded:
            raise OSError("hook_config_readback_failed")
    except Exception:
        if original is None:
            target.unlink(missing_ok=True)
        else:
            _atomic_replace(target, original, mode)
        raise
    return result


def _read_settings(path: Path) -> tuple[dict[str, object], int]:
    if not path.exists():
        return {}, 0o600
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 1024 * 1024:
        raise ValueError("hook_config_invalid")
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("hook_config_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("hook_config_invalid")
    return value, metadata.st_mode & 0o777


def _atomic_replace(target: Path, content: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _handler_command(value: object) -> str | None:
    if not isinstance(value, dict) or not isinstance(value.get("hooks"), list):
        return None
    hooks = value["hooks"]
    if len(hooks) != 1 or not isinstance(hooks[0], dict):
        return None
    command = hooks[0].get("command")
    return command if isinstance(command, str) else None


def _required(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value.encode()) > maximum:
        raise ValueError("hook_input_invalid")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
