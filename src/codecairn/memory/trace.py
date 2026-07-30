from __future__ import annotations

import hashlib

EMPTY_RAW_PREFIX_SHA256 = hashlib.sha256(b"codecairn:raw-prefix:v1").hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:20]}"


def extend_raw_prefix_sha256(prefix_sha256: str, raw_event_sha256: str) -> str:
    """Extend the stable digest for one ordered raw-event prefix."""
    encoded = bytes.fromhex(prefix_sha256) + bytes.fromhex(raw_event_sha256)
    return hashlib.sha256(encoded).hexdigest()
