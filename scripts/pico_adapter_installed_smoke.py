#!/usr/bin/env python3
"""Verify the CodeCairn Pico adapter from installed wheels in fresh processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_PROBE = r"""
import asyncio
import json
import sys
from importlib import metadata
from pathlib import Path

import codecairn.integrations.pico

assert not any(name == "pico" or name.startswith("pico.") for name in sys.modules)

from pico.memory_engine import Memory, MemoryBackend
from pico.plugin import PluginContext, PluginDiscovery, PluginRegistry, ServiceLocator

repository = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
entry_points = [ep for ep in metadata.entry_points(group="pico.plugins") if ep.name == "codecairn"]
assert len(entry_points) == 1
assert entry_points[0].value == "codecairn.integrations.pico"
found = [item for item in PluginDiscovery(entry_points_group="pico.plugins").discover() if item.manifest.id == "codecairn-memory"]
assert len(found) == 1
manifest = found[0].manifest
assert manifest.enabled_by_default is True
assert manifest.contributes.tools == []
assert [(item.name, item.factory) for item in manifest.contributes.memory_backends] == [
    ("codecairn", "codecairn.integrations.pico.backend:make_backend")
]
registry = PluginRegistry()
registry.activate(found)
assert registry.memory_backend_names() == ["codecairn"]
backend = registry.build_memory_backend(
    "codecairn",
    config={},
    services=ServiceLocator(workspace=repository),
)
assert isinstance(backend, MemoryBackend)

async def run():
    await backend.start()
    if mode == "store":
        await backend.store(
            "installed-session",
            [
                {"role": "user", "content": "Remember that installed releases require make check."},
                {"role": "assistant", "content": "Recorded."},
            ],
        )
    hits = await backend.recall("How are installed releases checked?", user_id="default", top_k=5)
    assert len(hits) == 1
    assert isinstance(hits[0], Memory)
    assert hits[0].score == 0.0
    assert hits[0].metadata["score_semantics"] == "compiled_context_not_ranked"
    assert hits[0].metadata["source_cursor"] == hits[0].metadata["index_cursor"]
    assert await backend.recall("skills", agent_id="default", top_k=5) == []
    await backend.feedback({"kind": "contract-smoke"})
    await backend.stop()
    return {
        "entry_point": {"name": entry_points[0].name, "value": entry_points[0].value},
        "manifest": {
            "id": manifest.id,
            "memory_backends": [item.name for item in manifest.contributes.memory_backends],
            "tools": [],
            "version": manifest.version,
        },
        "memory": {
            "count": len(hits),
            "freshness": hits[0].metadata["freshness"],
            "rendered_memory_ids": hits[0].metadata["rendered_memory_ids"],
            "retrieval_profile": hits[0].metadata["retrieval_profile"],
            "source_cursor": hits[0].metadata["source_cursor"],
            "source_uris": hits[0].metadata["source_uris"],
        },
    }

print(json.dumps(asyncio.run(run()), sort_keys=True))
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codecairn-wheel", type=Path, required=True)
    parser.add_argument("--pico-wheel", type=Path, required=True)
    parser.add_argument("--pico-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    codecairn_wheel = args.codecairn_wheel.resolve()
    pico_wheel = args.pico_wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="codecairn-pico-installed-") as temporary:
        root = Path(temporary)
        environment = root / "environment"
        repository = root / "repository"
        runtime = root / "runtime"
        subprocess.run(("uv", "venv", "--python", "3.12", str(environment)), check=True)
        python = environment / "bin" / "python"
        subprocess.run(("uv", "pip", "install", "--python", str(python), str(codecairn_wheel), str(pico_wheel)), check=True)
        repository.mkdir()
        _run(("git", "init", "-q"), cwd=repository)
        _run(
            (
                str(environment / "bin" / "codecairn"),
                "init",
                "--root",
                str(runtime),
                "--repo-key",
                "installed/smoke",
                "--retrieval-profile",
                "fastembed",
                "--semantic-profile",
                "none",
                "--prefetch",
            ),
            cwd=repository,
        )
        before = _skill_inventory(repository)
        first = _probe(python, repository, "store")
        second = _probe(python, repository, "recall")
        after = _skill_inventory(repository)
        if before != after:
            raise RuntimeError("Pico Local Skills changed during the CodeCairn adapter smoke")
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "kind": "codecairn.pico.installed-smoke",
            "codecairn_wheel": _wheel(codecairn_wheel),
            "pico": {"commit": args.pico_commit, **_wheel(pico_wheel)},
            "python": platform.python_version(),
            "platform": platform.platform(),
            "source_checkouts_absent": True,
            "local_skills_unchanged": True,
            "first_process": first,
            "fresh_process": second,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "sha256": _sha256(args.output)}, sort_keys=True))


def _probe(python: Path, repository: Path, mode: str) -> dict[str, Any]:
    result = _run((str(python), "-I", "-c", _PROBE, str(repository), mode), cwd=repository)
    return json.loads(result.stdout.splitlines()[-1])


def _run(command: tuple[str, ...], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env={**os.environ, "PYTHONPATH": ""}, check=True, capture_output=True, text=True)


def _wheel(path: Path) -> dict[str, str]:
    return {"filename": path.name, "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skill_inventory(repository: Path) -> list[str]:
    root = repository / ".pico" / "skills"
    return sorted(str(path.relative_to(repository)) for path in root.rglob("*")) if root.exists() else []


if __name__ == "__main__":
    main()
