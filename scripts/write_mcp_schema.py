"""Regenerate the checked-in version 0.1 MCP schema snapshot."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from codecairn.bootstrap import create_application
from codecairn.entrypoints.mcp import build_server, schema_snapshot

ROOT = Path(__file__).parents[1]


def _factory(root: Path, **kwargs: Any) -> Any:
    return create_application(root, test_retrieval=True, **kwargs)


async def _write() -> None:
    server = build_server(_factory, working_directory=ROOT)
    payload = await schema_snapshot(server)
    output = ROOT / "docs" / "schemas" / "mcp-v01.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    asyncio.run(_write())
