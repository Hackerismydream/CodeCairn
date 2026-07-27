"""Stable fingerprints for checked-in retrieval configuration artifacts."""

from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise
from typing import Protocol

from codecairn.memory.schema import canonical_json

_TOKEN = re.compile(r"[\w./:#-]+", flags=re.UNICODE)


class EmbeddingProvider(Protocol):
    model_id: str
    source_id: str
    revision: str
    dimension: int
    index_identity: str

    def embed_query(self, text: str) -> tuple[float, ...]: ...

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class RerankingProvider(Protocol):
    model_id: str

    def rerank(
        self,
        query: str,
        documents: tuple[tuple[str, str, float], ...],
    ) -> tuple[tuple[str, float], ...]: ...


class HashingEmbedder:
    """Small deterministic adapter reserved for tests."""

    model_id = "test/hashing-sha256-v1"
    source_id = "builtin/hashing-sha256-v1"
    revision = "test-v1"
    dimension = 128
    index_identity = "hashing-test:builtin/hashing-sha256-v1@test-v1:128"

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed(text)

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed(text) for text in texts)

    def _embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimension
        tokens = [match.group(0).casefold() for match in _TOKEN.finditer(text)]
        for feature in (*tokens, *(f"{left}\0{right}" for left, right in pairwise(tokens))):
            digest = hashlib.sha256(feature.encode()).digest()
            vector[int.from_bytes(digest[:2], "big") % self.dimension] += (
                1.0 if digest[2] & 1 else -1.0
            )
        norm = math.sqrt(sum(value * value for value in vector))
        return tuple(vector if norm == 0 else (value / norm for value in vector))


class FusionReranker:
    """Deterministic test reranker that keeps hybrid fusion scores."""

    model_id = "test/fusion-v1"

    def rerank(
        self,
        query: str,
        documents: tuple[tuple[str, str, float], ...],
    ) -> tuple[tuple[str, float], ...]:
        del query
        return tuple((memory_id, score) for memory_id, _text, score in documents)


def retrieval_config_sha256(public_config: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(public_config).encode("utf-8")).hexdigest()
