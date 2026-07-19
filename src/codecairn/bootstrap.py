"""Composition root for the local CodeCairn runtime."""

from pathlib import Path

from codecairn.entrypoints.cli import build_app
from codecairn.importers.session import SessionImporter
from codecairn.memory.embedding import HashingEmbedder
from codecairn.memory.evidence import EvidenceGate
from codecairn.service.cascade import MemoryIndex, MiniCascade
from codecairn.service.recall import RecallEngine
from codecairn.service.runtime import MemoryRuntime
from codecairn.storage.lance import LanceMemoryIndex
from codecairn.storage.markdown import MarkdownMemoryStore
from codecairn.storage.sqlite import SQLiteState


def create_runtime(root: Path) -> MemoryRuntime:
    """Build the local Markdown plus SQLite runtime behind service ports."""
    resolved = root.resolve()
    state = SQLiteState(resolved / "state.sqlite3")
    index = LanceMemoryIndex(resolved / "index.lancedb")
    return MemoryRuntime(
        importer=SessionImporter(),
        memory_store=MarkdownMemoryStore(resolved),
        state=state,
        evidence_gate=EvidenceGate(),
        recall_engine=RecallEngine(
            index=index,
            state=state,
            embedder=HashingEmbedder(),
        ),
    )


def create_cascade(root: Path, *, index: MemoryIndex | None = None) -> MiniCascade:
    """Build the recoverable Markdown-to-LanceDB synchronization service."""
    resolved = root.resolve()
    return MiniCascade(
        truth=MarkdownMemoryStore(resolved),
        state=SQLiteState(resolved / "state.sqlite3"),
        index=index or LanceMemoryIndex(resolved / "index.lancedb"),
    )


app = build_app(create_runtime)


def main() -> None:
    """Run the dependency-injected local CLI."""
    app()
