"""Safe fixed-root discovery for owned Codex and Claude Code history."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from pathlib import Path
from typing import Literal

from codecairn.configuration import discover_repository
from codecairn.importers.jsonl import MAX_SESSION_BYTES, SourceByteLimitExceeded, open_directory_no_symlinks, read_import_scan
from codecairn.importers.session import SessionImporter
from codecairn.memory.errors import ConfigurationError, TraceImportError, TraceParseError
from codecairn.service.onboarding import DiscoveredSource, HistoryInspection, ImportProgress

_MAX_CANDIDATES = 256
_MAX_DIRECTORIES = 1_024
_MAX_DIRECTORY_ENTRIES = 4_096
_MAX_FILES_PER_DIRECTORY = 16
_MAX_NATIVE_CWDS = 64
_MAX_OBSERVED_BYTES = 256 * 1024 * 1024
_ADAPTER_REVISION = "codecairn.local-agent-history.v1"
HistoryClient = Literal["codex", "claude"]
_HISTORY_ROOTS: tuple[tuple[HistoryClient, str], ...] = (("codex", ".codex/sessions"), ("claude", ".claude/projects"))


class LocalAgentHistory:
    def __init__(self, *, home: Path, identity_secret: bytes) -> None:
        if len(identity_secret) < 16:
            raise ValueError("History identity secret must contain at least 16 bytes")
        self._home = home.resolve()
        self._secret = identity_secret

    def inspect(self, *, repository_common_dir: Path, import_progress: ImportProgress | None = None) -> HistoryInspection:
        admitted: list[DiscoveredSource] = []
        unresolved: dict[Literal["codex", "claude"], int] = {"codex": 0, "claude": 0}
        invalid: dict[Literal["codex", "claude"], int] = {"codex": 0, "claude": 0}
        observed_bytes = 0
        truncated = False
        client_candidate_limit = max(1, _MAX_CANDIDATES // len(_HISTORY_ROOTS))
        client_directory_limit = max(1, _MAX_DIRECTORIES // len(_HISTORY_ROOTS))
        client_byte_limit = max(1, _MAX_OBSERVED_BYTES // len(_HISTORY_ROOTS))
        for client, relative_root in _HISTORY_ROOTS:
            root = self._home / relative_root
            files, walk_truncated = _jsonl_files(
                root,
                max_directories=client_directory_limit,
                max_files=client_candidate_limit,
                preferred_directory=(root / str(repository_common_dir.parent).replace(os.sep, "-") if client == "claude" else None),
            )
            truncated = truncated or walk_truncated
            client_observed_bytes = 0

            def account_source_bytes(count: int) -> None:
                nonlocal observed_bytes, client_observed_bytes
                observed_bytes += count
                client_observed_bytes += count

            for source in files:
                try:
                    metadata = source.lstat()
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError("source_not_regular")
                    remaining = min(_MAX_OBSERVED_BYTES - observed_bytes, client_byte_limit - client_observed_bytes)
                    if remaining <= 1:
                        truncated = True
                        break
                    scan = read_import_scan(
                        source,
                        source_root=root,
                        checkpoint=None,
                        max_session_bytes=min(MAX_SESSION_BYTES, remaining - 1),
                        on_source_read=account_source_bytes,
                    )
                    trace = SessionImporter().from_scan(scan)
                    if trace.provider != client:
                        raise ValueError("provider_mismatch")
                    if not source_matches_repository(client, scan.records, expected_common_dir=repository_common_dir):
                        unresolved[client] += 1
                        continue
                except SourceByteLimitExceeded:
                    invalid[client] += 1
                    truncated = True
                    continue
                except (OSError, ValueError, TraceImportError, ConfigurationError):
                    invalid[client] += 1
                    continue
                source_id = _source_id(self._secret, client, source)
                import_state = (
                    import_progress(
                        source_path=source,
                        raw_event_count=trace.raw_event_count,
                        source_fingerprint=trace.source_sha256,
                        raw_event_sha256s=tuple(digest for _record, digest in scan.records),
                    )
                    if import_progress is not None
                    else "new"
                )
                if import_state == "invalid":
                    invalid[client] += 1
                    continue
                admitted.append(
                    DiscoveredSource(
                        source_id=source_id,
                        client=client,
                        path=source,
                        source_root=root,
                        fingerprint=trace.source_sha256,
                        session_label=f"{client.title()} session {source_id[-8:]}",
                        raw_event_count=trace.raw_event_count,
                        estimated_bytes=scan.source_byte_count,
                        latest_activity_ms=metadata.st_mtime_ns // 1_000_000,
                        import_state=import_state,
                    )
                )
        sources = tuple(sorted(admitted, key=lambda item: (item.client, item.source_id)))
        return HistoryInspection(sources, unresolved, invalid, truncated, _ADAPTER_REVISION)


def _jsonl_files(
    root: Path, *, max_directories: int, max_files: int, preferred_directory: Path | None = None
) -> tuple[tuple[Path, ...], bool]:
    if max_directories <= 0 or max_files <= 0:
        return (), True
    try:
        root_descriptor = open_directory_no_symlinks(root)
    except (OSError, TraceParseError):
        return (), False
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    buckets: list[list[tuple[int, Path]]] = []
    directories = 0
    truncated = False
    pending = [(root_descriptor, root)]
    while pending:
        if directories >= max_directories:
            truncated = True
            for descriptor, _path in pending:
                os.close(descriptor)
            break
        directory_descriptor, directory = pending.pop()
        directories += 1
        children: list[tuple[int, Path]] = []
        candidates: list[tuple[int, Path]] = []
        try:
            with os.scandir(directory_descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= _MAX_DIRECTORY_ENTRIES:
                        truncated = True
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if directories + len(pending) + len(children) >= max_directories:
                                truncated = True
                                continue
                            child = os.open(entry.name, directory_flags, dir_fd=directory_descriptor)
                            children.append((child, directory / entry.name))
                        elif entry.name.endswith(".jsonl") and entry.is_file(follow_symlinks=False):
                            candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, directory / entry.name))
                    except OSError:
                        continue
        except OSError:
            pass
        finally:
            os.close(directory_descriptor)
        pending.extend(sorted(children, key=lambda item: str(item[1])))
        candidates.sort(reverse=True)
        per_directory_limit = min(max_files, _MAX_FILES_PER_DIRECTORY)
        truncated = truncated or len(candidates) > per_directory_limit
        if candidates:
            buckets.append(candidates[:per_directory_limit])
    found: list[Path] = []
    for rank in range(min(max_files, _MAX_FILES_PER_DIRECTORY)):
        layer = sorted(
            (bucket[rank] for bucket in buckets if rank < len(bucket)),
            key=lambda item: (preferred_directory is None or item[1].parent != preferred_directory, -item[0], str(item[1])),
        )
        found.extend(path for _, path in layer[: max_files - len(found)])
        if len(found) == max_files:
            truncated = truncated or any(rank + 1 < len(bucket) for bucket in buckets) or len(layer) > max_files
            break
    return tuple(found), truncated


def source_matches_repository(
    client: Literal["codex", "claude"], records: tuple[tuple[dict[str, object], str], ...], *, expected_common_dir: Path
) -> bool:
    paths: list[Path] = []
    for record, _digest in records:
        if client == "claude":
            if "cwd" not in record:
                continue
            value: object = record["cwd"]
        elif record.get("type") in {"session_meta", "turn_context"}:
            payload = record.get("payload")
            value = payload.get("cwd") if isinstance(payload, dict) else None
        else:
            continue
        path = Path(value).expanduser() if isinstance(value, str) and value else None
        if path is None or not path.is_absolute():
            return False
        paths.append(path)
    unique = tuple(dict.fromkeys(paths))
    return bool(unique) and len(unique) <= _MAX_NATIVE_CWDS and all(_same_repository(path, expected_common_dir) for path in unique)


def history_source_root(home: Path, client: HistoryClient) -> Path:
    return Path(os.path.abspath(home / dict(_HISTORY_ROOTS)[client]))


def _same_repository(cwd: Path, expected_common_dir: Path) -> bool:
    try:
        observed = discover_repository(cwd).common_dir
        return os.path.samefile(observed, expected_common_dir)
    except (OSError, ConfigurationError):
        return False


def _source_id(secret: bytes, client: str, source: Path) -> str:
    digest = hmac.new(secret, f"{client}\0{source.resolve()}".encode(), hashlib.sha256).hexdigest()
    return f"src_{digest}"
