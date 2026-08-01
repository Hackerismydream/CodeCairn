"""Verify a materialized source checkout against its frozen Git tree."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from codecairn_v03_acceptance.bounded_process import run_bounded_process


class CheckoutIntegrityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def frozen_checkout_identity(path: Path, expected_commit: str) -> tuple[Path, str, str]:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise CheckoutIntegrityError("candidate_checkout_invalid", "candidate checkout must be an absolute regular directory")
    root = Path(_git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != path.resolve():
        raise CheckoutIntegrityError("candidate_checkout_invalid", "candidate path is not the Git checkout root")
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    if commit != expected_commit:
        raise CheckoutIntegrityError("candidate_commit_mismatch", "candidate checkout commit does not match the campaign")
    tracked = _git(root, "ls-files", "-v", "-z")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    tree = _git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    listing = _git(root, "ls-tree", "-rz", "--full-tree", "HEAD")
    if (
        status
        or any(not entry.startswith(b"H ") for entry in tracked.split(b"\0") if entry)
        or not _materialized_tree_matches(root, listing)
    ):
        raise CheckoutIntegrityError("candidate_checkout_dirty", "candidate checkout contains uncommitted changes")
    return root, commit, tree


def _git(cwd: Path, *arguments: str) -> bytes:
    if (git := shutil.which("git")) is None:
        raise CheckoutIntegrityError("candidate_checkout_invalid", "Git is required to verify the candidate checkout")
    try:
        result = run_bounded_process(
            (git, "-C", str(cwd), "-c", "core.fsmonitor=false", *arguments),
            cwd=cwd,
            environment={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.environ.get("PATH", "")},
            timeout_seconds=120,
            stdout_limit=16 * 1024 * 1024,
            stderr_limit=1_048_576,
        )
    except OSError as error:
        raise CheckoutIntegrityError("candidate_checkout_invalid", "candidate Git identity could not be verified") from error
    if result.terminal != "exited" or result.exit_code != 0:
        raise CheckoutIntegrityError("candidate_checkout_invalid", "candidate Git identity could not be verified")
    return result.stdout


def _materialized_tree_matches(root: Path, listing: bytes) -> bool:
    try:
        for record in filter(None, listing.split(b"\0")):
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, expected_oid = metadata.split()
            relative = Path(os.fsdecode(raw_path))
            if kind != b"blob" or relative.is_absolute() or ".." in relative.parts:
                return False
            path = root / relative
            if mode == b"120000":
                if not path.is_symlink():
                    return False
                content = os.fsencode(os.readlink(path))
            elif mode in {b"100644", b"100755"}:
                if path.is_symlink() or not path.is_file():
                    return False
                content = path.read_bytes()
                if (b"100755" if path.stat().st_mode & 0o111 else b"100644") != mode:
                    return False
            else:
                return False
            payload = f"blob {len(content)}\0".encode() + content
            if hashlib.sha1(payload, usedforsecurity=False).hexdigest().encode() != expected_oid:
                return False
    except (OSError, ValueError):
        return False
    return True
