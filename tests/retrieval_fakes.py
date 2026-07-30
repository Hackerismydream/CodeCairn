from __future__ import annotations

import hashlib
import math


class HashingEmbedder:
    model_id = "test-hashing-v1"
    source_id = "tests"
    revision = "1"
    dimension = 128
    index_identity = "test-hashing-v1:128"
    relevance_threshold = 0.25

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed(text)

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed(text) for text in texts)

    def _embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimension
        for token in text.casefold().split():
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:2], "big") % self.dimension] += 1.0
        magnitude = math.sqrt(sum(value * value for value in vector))
        return tuple(value / magnitude for value in vector) if magnitude else tuple(vector)


class FusionReranker:
    model_id = "test-fusion-v1"

    def rerank(self, query: str, documents: tuple[tuple[str, str, float], ...]) -> tuple[tuple[str, float], ...]:
        terms = set(query.casefold().split())
        ranked = ((memory_id, score + len(terms & set(text.casefold().split()))) for memory_id, text, score in documents)
        return tuple(sorted(ranked, key=lambda item: (-item[1], item[0])))


TEST_RETRIEVAL = (HashingEmbedder(), FusionReranker())
