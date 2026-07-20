from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _use_test_retrieval_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODECAIRN_RETRIEVAL_PROFILE", "hashing-test")
