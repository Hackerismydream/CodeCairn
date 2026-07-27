"""Stable fingerprints for checked-in retrieval configuration artifacts."""

from __future__ import annotations

import hashlib

from codecairn.memory.schema import canonical_json


def retrieval_config_sha256(public_config: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(public_config).encode("utf-8")).hexdigest()
