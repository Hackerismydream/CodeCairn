# Production Embedding Uses DashScope

## Context

ADR 0013 selected a small English-only local embedding model because it was
cheap, reproducible, and sufficient to replace deterministic feature hashing.
CodeCairn now needs stronger code, Chinese, and multilingual retrieval. Alibaba
Cloud Model Studio exposes `text-embedding-v4` through an OpenAI-compatible
embedding endpoint. The model supports explicit dimensions, batches of up to 10
texts, and long code or text inputs.

Provider contract checked on 2026-07-21 against the
[Alibaba Cloud Model Studio embedding documentation](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/).

The provider-managed model alias does not expose an immutable artifact commit.
Using it improves retrieval capability but weakens bit-for-bit reproducibility
relative to a pinned local snapshot. The retrieval manifest must represent that
boundary honestly.

## Decision

Production composition defaults to the `dashscope` retrieval profile. It calls
the OpenAI-compatible `/embeddings` endpoint with model
`text-embedding-v4`, `encoding_format=float`, and an explicit 1,024-vector
dimension. The endpoint defaults to
`https://dashscope.aliyuncs.com/compatible-mode/v1` and can be replaced with a
workspace-specific Model Studio URL. Authentication comes from
`CODECAIRN_EMBEDDING_API_KEY` or the standard `DASHSCOPE_API_KEY`; credentials
never enter logs, index metadata, sidecars, or evaluation manifests.

The Adapter batches at most 20 inputs, restores provider results by their
response indexes, retries transport failures, HTTP 429, and HTTP 5xx responses,
and fails closed on malformed, missing, non-finite, or wrong-dimension vectors.
Index identity contains the Adapter version, endpoint, model alias, declared
provider revision, and dimension. Any change triggers the existing rebuildable
LanceDB migration path.

The manifest records revision `provider-managed` by default because Alibaba
Cloud does not publish an immutable snapshot identifier for this alias. An
operator may set `CODECAIRN_EMBEDDING_REVISION` to a provider deployment label;
that label is a declared observation, not a CodeCairn-verified model commit.

The local `fastembed` profile remains available as an explicit offline choice.
The pinned local CrossEncoder remains the production reranker. The
`hashing-test` profile remains test-only, and neither local profile is a silent
fallback when DashScope is unavailable or unconfigured.

## Consequences

- Production indexing and vector recall require network access and a DashScope
  API key and incur provider cost.
- The default vector dimension changes from 384 to 1,024, so existing LanceDB
  projections rebuild before use while Markdown truth remains unchanged.
- Query and document embeddings use the OpenAI-compatible surface, which does
  not expose DashScope-native `text_type` or `instruct` controls.
- Evaluations remain attributable to the recorded endpoint, alias, declared
  revision, dimension, and Adapter version, but they are not bit-for-bit
  reproducible across unannounced provider-side model changes.
