from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise
from typing import Protocol

VECTOR_DIMENSION = 256
_TOKEN_PATTERN = re.compile(r"[a-z0-9_./-]+|[^\W\s]", re.IGNORECASE)


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> tuple[float, ...]: ...


class HashingEmbedder:
    """Deterministic local feature-hashing embedder for dependency-free recall."""

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * VECTOR_DIMENSION
        tokens = [match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(text)]
        features = tokens + [f"{left}\x00{right}" for left, right in pairwise(tokens)]
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            position = int.from_bytes(digest[:2], "big") % VECTOR_DIMENSION
            vector[position] += 1.0 if digest[2] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)
