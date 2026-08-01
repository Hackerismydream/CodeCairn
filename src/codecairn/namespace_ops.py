"""Backup-first namespace export and reset operations."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from codecairn.storage.library_markdown import MarkdownLibraryStore
from codecairn.storage.sqlite import SQLiteState


class NamespaceIndex(Protocol):
    def delete_namespace(self, *, repo_key: str) -> None: ...


def export_namespace(*, root: Path, repo_key: str, output: Path) -> dict[str, object]:
    target = output.resolve()
    if target.exists():
        raise ValueError(f"Export output already exists: {target}")
    target.mkdir(parents=True)
    slug = _slug(repo_key)
    for kind in ("memory", "evolution"):
        source = root / kind / slug
        if source.exists():
            shutil.copytree(source, target / kind / slug)
    database = root / "state.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as source_db, sqlite3.connect(target / "state.sqlite3") as destination_db:
            source_db.backup(destination_db)
    files = tuple(
        sorted(
            {
                path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in target.rglob("*")
                if path.is_file()
            }.items()
        )
    )
    manifest = {"schema_version": 1, "repo_key": repo_key, "file_count": len(files), "files": dict(files)}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"output": str(target), **manifest}


def reset_namespace(
    *, root: Path, repo_key: str, confirm: str | None, dry_run: bool, state: SQLiteState, index: NamespaceIndex | None
) -> dict[str, object]:
    library = MarkdownLibraryStore(root)
    with library.lock():
        scan = library.scan_promotions()
        if scan.issues:
            raise ValueError("global_preference_invalid")
        references = tuple(item.promotion.promotion_id for item in scan.promotions if item.promotion.source.repository_key == repo_key)
        slug = _slug(repo_key)
        paths = tuple(str(path) for path in (root / "memory" / slug, root / "evolution" / slug) if path.exists())
        memory_count = len(state.list_memories(repo_key=repo_key))
        preview: dict[str, object] = {
            "repo_key": repo_key,
            "memory_count": memory_count,
            "global_reference_count": len(references),
            "paths": paths,
            "dry_run": dry_run,
        }
        if dry_run:
            return preview
        if references:
            raise ValueError("memory_referenced_by_global_scope")
        if confirm != repo_key:
            raise ValueError(f"Reset requires --confirm {repo_key}")
        backup = root / "backups" / f"{time.time_ns()}-{slug}"
        export_namespace(root=root, repo_key=repo_key, output=backup)
        for source in (root / "memory" / slug, root / "evolution" / slug):
            if source.exists():
                destination = backup / "removed" / source.parent.name / slug
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(source, destination)
        state.delete_namespace(repo_key=repo_key)
        if index is not None:
            index.delete_namespace(repo_key=repo_key)
        return {**preview, "dry_run": False, "backup": str(backup)}


def _slug(repo_key: str) -> str:
    return hashlib.sha256(repo_key.encode()).hexdigest()[:16]
