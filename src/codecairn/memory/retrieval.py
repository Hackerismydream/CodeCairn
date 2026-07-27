"""Stable fingerprints for checked-in retrieval configuration artifacts."""

from __future__ import annotations

import hashlib
from typing import Protocol

from codecairn.memory.schema import canonical_json


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

    def rerank(self, query: str, documents: tuple[tuple[str, str, float], ...]) -> tuple[tuple[str, float], ...]: ...


def retrieval_config_sha256(public_config: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(public_config).encode("utf-8")).hexdigest()
