from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "real_client_smoke.py"
SPEC = importlib.util.spec_from_file_location("codecairn_real_client_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_claude_provider_requires_deepseek_v4_flash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_KEY", "secret")
    monkeypatch.setenv("PROVIDER_BASE", "https://api.deepseek.com/v1")
    for model in ("deepseek-chat", "vendor/deepseek-chat", "deepseek-ai/DeepSeek-V3"):
        monkeypatch.setenv("PROVIDER_MODEL", model)
        with pytest.raises(ValueError, match="DeepSeek V4 Flash"):
            MODULE._claude_provider("PROVIDER_KEY", "PROVIDER_BASE", "PROVIDER_MODEL")

    monkeypatch.setenv("PROVIDER_MODEL", "deepseek-v4-flash")
    provider = MODULE._claude_provider("PROVIDER_KEY", "PROVIDER_BASE", "PROVIDER_MODEL")
    assert provider["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert provider["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com"
