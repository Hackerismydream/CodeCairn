from __future__ import annotations

import json
from pathlib import Path

import pytest

from codecairn.memory.semantic import ClauseDraft
from codecairn.storage.semantic_cache import JsonProjectionCache, ProjectionCacheCorrupt


def test_projection_cache_round_trips_across_instances(tmp_path: Path) -> None:
    root = tmp_path / "projection-cache"
    drafts = (
        ClauseDraft(
            text="Caroline adopted Poppy.",
            source_fact_ids=("fact-1",),
        ),
    )

    JsonProjectionCache(root).put("a" * 64, drafts)

    assert JsonProjectionCache(root).get("a" * 64) == drafts


def test_projection_cache_rejects_a_corrupted_entry(tmp_path: Path) -> None:
    root = tmp_path / "projection-cache"
    cache_key = "b" * 64
    cache = JsonProjectionCache(root)
    cache.put(cache_key, (ClauseDraft(text="Grounded clause.", source_fact_ids=("fact-1",)),))
    cache_file = next(root.rglob("*.json"))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["drafts"][0]["text"] = "Tampered clause."
    cache_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProjectionCacheCorrupt):
        cache.get(cache_key)


def test_projection_cache_rejects_an_unsafe_key(tmp_path: Path) -> None:
    cache = JsonProjectionCache(tmp_path / "projection-cache")

    with pytest.raises(ValueError, match="SHA-256"):
        cache.get("../outside")
