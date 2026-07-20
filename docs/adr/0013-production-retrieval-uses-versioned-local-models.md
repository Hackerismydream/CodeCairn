# Production Retrieval Uses Versioned Local Models

## Context

The first recall implementation used deterministic feature hashing for both
indexing and queries. It was useful for contract tests, but it was not a semantic
embedding model and reciprocal-rank fusion alone was not a learned reranker.
Publishing either as production retrieval would make benchmark attribution
misleading.

## Decision

Production composition uses local ONNX models through FastEmbed. The default
embedding model is `BAAI/bge-small-en-v1.5` with 384 dimensions, loaded from a
pinned `qdrant/bge-small-en-v1.5-onnx-q` artifact commit. The default CrossEncoder
is `Xenova/ms-marco-MiniLM-L-6-v2`, also pinned to an immutable artifact commit.
CodeCairn resolves the exact Hugging Face snapshot before giving its local path
to FastEmbed. Both adapters load weights lazily at the CodeCairn boundary and
fail closed when a model returns the wrong number of vectors, an unexpected
dimension, an incomplete score set, or non-finite values.

The logical embedding model, artifact source, immutable commit, dimension, and
Adapter-sensitive index identity are stored with every LanceDB row. Opening an
index under a different identity re-embeds its rebuildable documents in bounded
batches under an inter-process operation lock. Query artifacts record the
embedding and reranker artifact identities, and evaluation manifests record the
complete public retrieval configuration and licenses.

Feature hashing and fusion-score reranking remain available only through the
explicit `hashing-test` profile used by deterministic tests. Production has no
silent fallback from a learned model to those adapters.

## Consequences

- A first production recall or index operation downloads model weights into the
  configured local cache; model weights never enter the repository or evidence
  bundle.
- Switching embedding models is an index migration, not a compatible in-place
  query change.
- Markdown remains authoritative because every learned vector can be regenerated
  from the projected documents.
- DeepSeek remains an answer and judge provider for LoCoMo; it is not represented
  as an embedding or reranking provider.
