from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import cast

from codecairn.memory.semantic import ClauseDraft

_CACHE_SCHEMA = "codecairn/projection-draft-cache-v1"
_CACHE_KEY = re.compile(r"[0-9a-f]{64}")
_MAX_CACHE_ENTRY_BYTES = 16 * 1024 * 1024


class ProjectionCacheCorrupt(ValueError):
    """A recoverable projection-cache entry failed integrity validation."""


class JsonProjectionCache:
    """Content-addressed operational cache for untrusted clause drafts."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def get(self, cache_key: str) -> tuple[ClauseDraft, ...] | None:
        path = self._path(cache_key)
        try:
            stat = path.lstat()
        except FileNotFoundError:
            return None
        if path.is_symlink() or not path.is_file() or stat.st_size > _MAX_CACHE_ENTRY_BYTES:
            raise ProjectionCacheCorrupt("Projection cache entry is not a bounded regular file")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProjectionCacheCorrupt("Projection cache entry is unreadable") from error
        return _decode_entry(raw, expected_key=cache_key)

    def put(self, cache_key: str, drafts: tuple[ClauseDraft, ...]) -> None:
        path = self._path(cache_key)
        encoded = _encode_entry(cache_key, drafts)
        if len(encoded) > _MAX_CACHE_ENTRY_BYTES:
            raise ValueError("Projection cache entry exceeds its byte limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if self.get(cache_key) != drafts:
                raise ProjectionCacheCorrupt("Projection cache key has conflicting contents")
            return
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{cache_key}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self.get(cache_key) != drafts:
                    raise ProjectionCacheCorrupt(
                        "Projection cache key was concurrently written with different contents"
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, cache_key: str) -> Path:
        if _CACHE_KEY.fullmatch(cache_key) is None:
            raise ValueError("Projection cache key must be a lowercase SHA-256 digest")
        path = (self._root / cache_key[:2] / f"{cache_key}.json").resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Projection cache path escapes its root")
        return path


def _encode_entry(cache_key: str, drafts: tuple[ClauseDraft, ...]) -> bytes:
    content: dict[str, object] = {
        "schema": _CACHE_SCHEMA,
        "cache_key": cache_key,
        "drafts": [
            {
                "text": draft.text,
                "source_fact_ids": list(draft.source_fact_ids),
            }
            for draft in drafts
        ],
    }
    content["payload_sha256"] = _content_digest(content)
    return _canonical_json(content).encode("utf-8")


def _decode_entry(raw: object, *, expected_key: str) -> tuple[ClauseDraft, ...]:
    if not isinstance(raw, dict) or set(raw) != {
        "schema",
        "cache_key",
        "drafts",
        "payload_sha256",
    }:
        raise ProjectionCacheCorrupt("Projection cache entry has an invalid schema")
    if raw.get("schema") != _CACHE_SCHEMA or raw.get("cache_key") != expected_key:
        raise ProjectionCacheCorrupt("Projection cache entry identity does not match its path")
    digest = raw.get("payload_sha256")
    content = {key: value for key, value in raw.items() if key != "payload_sha256"}
    if not isinstance(digest, str) or digest != _content_digest(content):
        raise ProjectionCacheCorrupt("Projection cache entry digest does not match its payload")
    values = raw.get("drafts")
    if not isinstance(values, list):
        raise ProjectionCacheCorrupt("Projection cache drafts must be a list")
    drafts: list[ClauseDraft] = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {"text", "source_fact_ids"}:
            raise ProjectionCacheCorrupt("Projection cache draft has an invalid schema")
        text = value.get("text")
        source_ids = value.get("source_fact_ids")
        if (
            not isinstance(text, str)
            or not isinstance(source_ids, list)
            or not all(isinstance(item, str) for item in source_ids)
        ):
            raise ProjectionCacheCorrupt("Projection cache draft fields are invalid")
        drafts.append(
            ClauseDraft(
                text=text,
                source_fact_ids=tuple(cast(list[str], source_ids)),
            )
        )
    return tuple(drafts)


def _content_digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
