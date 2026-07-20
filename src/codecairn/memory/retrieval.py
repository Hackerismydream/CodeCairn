from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from codecairn.memory.embedding import EmbeddingProvider
from codecairn.memory.model_artifact import fastembed_version
from codecairn.memory.reranking import RerankingProvider

RetrievalProfile = Literal["fastembed", "hashing-test"]


@dataclass(frozen=True, slots=True)
class RetrievalProviders:
    """One immutable embedding and reranking configuration shared by a runtime."""

    profile: RetrievalProfile
    embedder: EmbeddingProvider
    reranker: RerankingProvider
    embedding_license: str
    reranker_license: str

    @property
    def config_sha256(self) -> str:
        return retrieval_config_sha256(self.public_config)

    @property
    def public_config(self) -> dict[str, object]:
        if self.profile == "hashing-test":
            return {
                "method": "hybrid-rrf-test-adapters",
                "embedding": {
                    "adapter": "hashing-test",
                    "model": self.embedder.model_id,
                    "source": self.embedder.source_id,
                    "revision": self.embedder.revision,
                    "dimension": self.embedder.dimension,
                    "license": self.embedding_license,
                },
                "reranker": {
                    "adapter": "fusion-score-test",
                    "model": self.reranker.model_id,
                    "source": self.reranker.source_id,
                    "revision": self.reranker.revision,
                    "license": self.reranker_license,
                },
            }
        return {
            "method": "hybrid-rrf-cross-encoder",
            "embedding": {
                "adapter": "fastembed",
                "adapter_version": fastembed_version(),
                "adapter_license": "Apache-2.0",
                "model": self.embedder.model_id,
                "source": self.embedder.source_id,
                "revision": self.embedder.revision,
                "dimension": self.embedder.dimension,
                "license": self.embedding_license,
            },
            "reranker": {
                "adapter": "fastembed-cross-encoder",
                "adapter_version": fastembed_version(),
                "adapter_license": "Apache-2.0",
                "model": self.reranker.model_id,
                "source": self.reranker.source_id,
                "revision": self.reranker.revision,
                "license": self.reranker_license,
            },
        }


def retrieval_config_sha256(config: dict[str, object]) -> str:
    canonical = json.dumps(
        config,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
