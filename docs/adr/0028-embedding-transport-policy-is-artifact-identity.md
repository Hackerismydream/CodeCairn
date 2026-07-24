# Embedding Transport Policy Is Artifact Identity

## Status

Accepted.

## Context

The second formal v19 corpus attempt completed two conversation checkpoints and
then stopped while rebuilding the `conv-41` document index. DashScope accepted
the 82nd embedding request but did not finish the response within the configured
30-second read timeout.

The failure receipt recorded 81 fully observed embedding calls and one
unobserved provider attempt. Automatic retry and exact-build resume correctly
failed closed because that final request may have been billed.

Increasing the timeout to 120 seconds initially produced the same corpus build
contract as the failed 30-second attempt. The DashScope public configuration
recorded model, source, revision, dimension, pricing, and license, but omitted
timeout, maximum attempts, and retry backoff. These values determine whether a
request is still allowed to wait or retry, so treating different policies as
the same artifact identity made safe recovery impossible.

## Decision

The DashScope embedding Adapter exposes a credential-free transport policy:

```json
{
  "timeout_seconds": 120.0,
  "max_attempts": 3,
  "retry_backoff_seconds": 1.0
}
```

`RetrievalProviders.public_config` includes this policy for DashScope. Corpus
build contracts and retrieval configuration digests therefore distinguish
transport policies.

Query-vector build contracts also persist the policy. The frozen query-vector
Adapter validates and restores it, so a scored run retains the exact public
embedding contract without loading credentials or enabling provider fallback.
Legacy test or offline embedders that do not expose a transport policy remain
valid and do not invent one.

The formal v19 build uses a 120-second timeout. Retry semantics do not change:
connection failures known to precede provider acceptance may retry within the
configured maximum; read, write, and remote-protocol failures remain ambiguous
and stop after the first accepted attempt.

## Consequences

- A 30-second failed artifact and a 120-second rebuild have different,
  auditable build contracts.
- API keys remain absent from public configuration and artifacts.
- Longer waiting does not widen retrieval, alter vectors, or add automatic
  ambiguous retries.
- Existing incomplete artifacts cannot be silently resumed under a different
  transport policy.
- Formal corpus and query-vector artifacts must use the same published
  transport policy.
