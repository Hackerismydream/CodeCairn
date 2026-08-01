"""Client hook envelope and settings adapters."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codecairn.service.onboarding import CapturePlan

HookClient = Literal["claude", "codex"]
_MINIMUM = {"claude": (2, 1, 220), "codex": (0, 144, 6)}
_EVENT = {"claude": ("SessionEnd", "session_end"), "codex": ("Stop", "stop")}


class LocalHookCaptureAdapter:
    """Preview and install one fixed client settings target without exposing it."""

    def __init__(self, *, client: HookClient, target: Path, executable: Path) -> None:
        self.client = client
        self._target = Path(os.path.abspath(target))
        self._executable = executable.resolve()

    def inspect(self) -> CapturePlan:
        parent_identity = _parent_identity(self._target.parent)
        result = install_hook(
            client=self.client, target=self._target, executable=self._executable, dry_run=True, expected_parent_identity=parent_identity
        )
        intended_sha256 = hashlib.sha256(json.dumps(result["merged"], separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        identity = json.dumps(
            {
                "client": self.client,
                "client_version": result["client_version"],
                "current_sha256": result["current_state_sha256"],
                "executable_path_sha256": hashlib.sha256(str(self._executable).encode()).hexdigest(),
                "executable_sha256": _safe_file_sha256(self._executable, maximum=64 * 1024 * 1024),
                "intended_sha256": intended_sha256,
                "parent_identity": parent_identity,
                "target_sha256": hashlib.sha256(str(self._target).encode()).hexdigest(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return CapturePlan(
            client=self.client,
            event="stop" if self.client == "codex" else "session_end",
            state="available" if result["changed"] else "installed",
            fingerprint=hashlib.sha256(identity.encode()).hexdigest(),
            expected_state_sha256=str(result["current_state_sha256"]),
            adapter_revision="codecairn.local-hook-capture.v1",
        )

    def apply(self, plan: CapturePlan, *, before_write: Callable[[], object] | None = None) -> bool:
        parent_identity = _parent_identity(self._target.parent)
        if plan.client != self.client or self.inspect() != plan:
            raise ValueError("hook_preview_stale")
        result = install_hook(
            client=self.client,
            target=self._target,
            executable=self._executable,
            dry_run=False,
            expected_state_sha256=plan.expected_state_sha256,
            expected_parent_identity=parent_identity,
            before_write=before_write,
        )
        return bool(result["changed"])


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


def install_hook(
    *,
    client: HookClient,
    target: Path,
    executable: Path,
    dry_run: bool,
    expected_state_sha256: str | None = None,
    expected_parent_identity: str | None = None,
    before_write: Callable[[], object] | None = None,
) -> dict[str, object]:
    version = detect_client_version(client)
    directory_fd, parent_identity = _open_parent(target.parent)
    try:
        if expected_parent_identity is not None and parent_identity != expected_parent_identity:
            raise ValueError("hook_preview_stale")
        data, mode, current_state_sha256 = _read_settings(target, directory_fd=directory_fd)
        if expected_state_sha256 is not None and current_state_sha256 != expected_state_sha256:
            raise ValueError("hook_preview_stale")
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("hook_config_invalid")
        event = _EVENT[client][0]
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError("hook_config_invalid")
        command = f"{shlex.quote(str(executable.resolve()))} hook run --client {client}"
        handler = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
        changed = handler not in entries
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
            "current_state_sha256": current_state_sha256,
            "uninstall": f"Remove the handler whose command is: {command}",
        }
        if not dry_run and changed:
            if before_write is not None:
                before_write()
            _guarded_replace(target.name, encoded.encode(), mode, current_state_sha256, directory_fd=directory_fd)
            if _parent_identity(target.parent) != parent_identity:
                raise ValueError("hook_preview_stale")
        return result
    finally:
        os.close(directory_fd)


def _read_settings(path: Path, *, directory_fd: int | None = None, allow_shared: bool = False) -> tuple[dict[str, object], int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path.name if directory_fd is not None else path, flags, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or (metadata.st_nlink != 1 and not allow_shared) or metadata.st_size > 1024 * 1024:
                raise ValueError("hook_config_invalid")
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("hook_config_invalid")
        value = json.loads(raw)
    except FileNotFoundError:
        return {}, 0o600, "absent"
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("hook_config_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("hook_config_invalid")
    return value, metadata.st_mode & 0o777, hashlib.sha256(raw).hexdigest()


def _open_parent(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("not a directory")
        return descriptor, f"{metadata.st_dev:x}:{metadata.st_ino:x}"
    except OSError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise ValueError("hook_config_invalid") from error


def _parent_identity(path: Path) -> str:
    descriptor, identity = _open_parent(path)
    os.close(descriptor)
    return identity


def _safe_file_sha256(path: Path, *, maximum: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
                raise ValueError("unsupported_client")
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        raise ValueError("unsupported_client") from error


def _guarded_replace(target: str, content: bytes, mode: int, expected_sha256: str, *, directory_fd: int) -> None:
    temporary = f".{target}.{secrets.token_hex(16)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
    preserve_temporary = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        intended_sha256 = hashlib.sha256(content).hexdigest()
        if expected_sha256 == "absent":
            try:
                os.link(temporary, target, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
            except FileExistsError as error:
                raise ValueError("hook_preview_stale") from error
            if not _state_matches(target, intended_sha256, directory_fd):
                raise ValueError("hook_preview_stale")
            os.fsync(directory_fd)
            return
        _rename_swap(temporary, target, directory_fd=directory_fd)
        if not _state_matches(temporary, expected_sha256, directory_fd) or not _state_matches(target, intended_sha256, directory_fd):
            try:
                if _state_matches(target, intended_sha256, directory_fd):
                    _rename_swap(temporary, target, directory_fd=directory_fd)
                    if not _state_matches(temporary, intended_sha256, directory_fd):
                        _rename_swap(temporary, target, directory_fd=directory_fd)
            except OSError:
                preserve_temporary = True
                raise
            raise ValueError("hook_preview_stale")
        os.fsync(directory_fd)
    finally:
        if not preserve_temporary:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)


def _rename_swap(source: str, target: str, *, directory_fd: int) -> None:
    symbol = "renameatx_np" if sys.platform == "darwin" else "renameat2" if sys.platform.startswith("linux") else None
    if symbol is None:
        raise ValueError("hook_cas_unavailable")
    try:
        rename = getattr(ctypes.CDLL(None, use_errno=True), symbol)
    except AttributeError as error:
        raise ValueError("hook_cas_unavailable") from error
    rename.restype = ctypes.c_int
    if rename(directory_fd, os.fsencode(source), directory_fd, os.fsencode(target), 2):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _state_matches(path: str, expected_sha256: str, directory_fd: int) -> bool:
    try:
        return _read_settings(Path(path), directory_fd=directory_fd, allow_shared=True)[2] == expected_sha256
    except ValueError:
        return False


def _required(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value.encode()) > maximum:
        raise ValueError("hook_input_invalid")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
