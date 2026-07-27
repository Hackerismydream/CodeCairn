from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from codecairn.configuration import (
    ConfigurationError,
    discover_repository,
    initialize_repository,
    normalize_remote,
    resolve_runtime_config,
)


def _repository(path: Path, *, remote: str | None = None) -> Path:
    path.mkdir()
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    if remote is not None:
        subprocess.run(
            ("git", "-C", str(path), "remote", "add", "origin", remote),
            check=True,
        )
    return path


def test_init_is_idempotent_and_resolves_from_subdirectory(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path / "repo",
        remote="git@GitHub.com:Acme/Widgets.git",
    )
    nested = repository / "src" / "package"
    nested.mkdir(parents=True)
    root = tmp_path / "runtime"

    first = initialize_repository(start=nested, root=root, environment={})
    before = first.binding_path.read_bytes()
    second = initialize_repository(start=repository, root=root, environment={})

    assert first.repo_key == "github.com/Acme/Widgets"
    assert first == second
    assert second.binding_path.read_bytes() == before
    assert discover_repository(nested).common_dir == repository / ".git"


def test_frozen_repo_key_wins_and_environment_overrides_provider(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repo")
    initialized = initialize_repository(
        start=repository,
        root=tmp_path / "runtime",
        repo_key="acme/widgets",
        retrieval_profile="fastembed",
        environment={},
    )
    resolved = resolve_runtime_config(
        start=repository,
        environment={
            "CODECAIRN_REPO_KEY": "ignored/namespace",
            "CODECAIRN_RETRIEVAL_PROFILE": "dashscope",
            "CODECAIRN_EMBEDDING_MODEL": "qwen3.7-text-embedding",
            "CODECAIRN_EMBEDDING_DIMENSION": "1024",
            "CODECAIRN_EMBEDDING_ENDPOINT": ("https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "CODECAIRN_EMBEDDING_REVISION": "provider-managed",
        },
    )

    assert resolved.repo_key == initialized.repo_key
    assert resolved.retrieval.profile == "dashscope"


def test_remote_normalization_and_ambiguity(tmp_path: Path) -> None:
    assert normalize_remote("git@github.com:Acme/Widgets.git") == (
        normalize_remote("https://github.com/Acme/Widgets.git")
    )
    repository = _repository(tmp_path / "repo")
    for name in ("upstream", "fork"):
        subprocess.run(
            (
                "git",
                "-C",
                str(repository),
                "remote",
                "add",
                name,
                f"https://github.com/Acme/{name}.git",
            ),
            check=True,
        )
    with pytest.raises(ConfigurationError, match="Multiple Git remotes"):
        initialize_repository(start=repository, environment={})


def test_binding_rejects_unknown_keys_and_never_serializes_secret(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repo")
    config = initialize_repository(
        start=repository,
        root=tmp_path / "runtime",
        repo_key="acme/widgets",
        environment={"DASHSCOPE_API_KEY": "top-secret"},
    )
    assert b"top-secret" not in config.binding_path.read_bytes()
    with config.binding_path.open("a") as target:
        target.write("unknown = true\n")
    with pytest.raises(ConfigurationError, match="unknown keys"):
        resolve_runtime_config(start=repository, environment={})


def test_linked_worktree_uses_common_binding(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repo")
    subprocess.run(
        ("git", "-C", str(repository), "commit", "--allow-empty", "-m", "initial"),
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "-b",
            "linked",
            str(worktree),
        ),
        check=True,
        capture_output=True,
    )

    initialized = initialize_repository(
        start=worktree,
        root=tmp_path / "runtime",
        repo_key="acme/widgets",
        environment={},
    )

    assert initialized.binding_path == repository / ".git" / "codecairn.toml"
    assert resolve_runtime_config(start=repository, environment={}).repo_key == ("acme/widgets")
