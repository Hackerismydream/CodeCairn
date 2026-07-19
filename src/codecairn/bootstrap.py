"""Composition root for the local CodeCairn runtime."""

from pathlib import Path

from codecairn.entrypoints.cli import build_app
from codecairn.importers.codex import CodexImporter
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def create_runtime(root: Path) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    return MemoryRuntime(
        importer=CodexImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=SQLiteState(resolved / "state.sqlite3"),
    )


app = build_app(create_runtime)


def main() -> None:
    """Run the dependency-injected local CLI."""
    app()
