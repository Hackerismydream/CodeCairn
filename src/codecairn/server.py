from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from codecairn.bootstrap import create_application
from codecairn.entrypoints.api import create_app


def create_configured_app() -> FastAPI:
    runtime_root = Path(os.environ.get("CODECAIRN_RUNTIME_ROOT", ".codecairn"))
    artifact_root = Path(os.environ.get("CODECAIRN_ARTIFACT_ROOT", "artifacts"))
    source_value = os.environ.get("CODECAIRN_SOURCE_ROOTS", str(Path.cwd()))
    source_roots = tuple(Path(value) for value in source_value.split(os.pathsep) if value)
    bind_host = os.environ.get("CODECAIRN_BIND_HOST", "127.0.0.1")
    return create_app(
        create_application(runtime_root),
        source_roots=source_roots,
        artifact_root=artifact_root,
        bind_host=bind_host,
    )


def main() -> None:
    host = os.environ.get("CODECAIRN_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("CODECAIRN_PORT", "8000"))
    uvicorn.run(create_configured_app(), host=host, port=port)


if __name__ == "__main__":
    main()
