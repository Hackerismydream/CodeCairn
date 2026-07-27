---
status: accepted
---

# Version 0.1 Records an Explicit Retrieval Profile

`codecairn init` records one retrieval profile instead of relying on an
implicit provider fallback. When a DashScope key is available, the recommended
profile uses `qwen3.7-text-embedding` at 1,024 dimensions; without a key,
initialization records the pinned local FastEmbed profile as its documented
default. The user may override either choice explicitly. Hashing remains
test-only.

Alibaba Cloud's current
[text embedding API reference](https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api)
lists `qwen3.7-text-embedding`, 1,024 as its default supported dimension, and an
OpenAI-compatible surface. Endpoint and model availability remain
region/workspace-specific. `init --check-provider` or `doctor --live` validates
the selected endpoint with a real one-input embedding; without that check the
profile is `configured`, not `live_verified`. A failed check is an actionable
configuration error, not a fallback.

This amends ADR 0015's default `text-embedding-v4` composition. Existing index
identity rules still force a rebuild when provider, model, dimension, revision,
or adapter changes. Semantic extraction uses a separately configured
OpenAI-compatible chat model; without it, source import and deterministic Task
Experience remain durable while semantic work stays visibly pending.
