"""Git discovery and strict non-secret repository binding configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from codecairn.memory.config import RetrievalConfig, RetrievalProfile, RuntimeConfig, SemanticConfig
from codecairn.memory.errors import ConfigurationError

_ROOT_KEYS = {"schema_version", "runtime_root", "repo_key", "retrieval", "semantic"}
_RETRIEVAL_KEYS = {"profile", "model", "dimension", "endpoint", "revision", "cache_dir"}
_SEMANTIC_KEYS = {"profile", "model", "endpoint"}
_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


@dataclass(frozen=True, slots=True)
class GitRepository:
    root: Path
    common_dir: Path

    @property
    def binding_path(self) -> Path:
        return self.common_dir / "codecairn.toml"


def discover_repository(start: Path) -> GitRepository:
    root = _git(start, "rev-parse", "--show-toplevel")
    if root is None:
        raise ConfigurationError("Current path is not inside a Git repository")
    repository_root = Path(root).resolve()
    common = _git(repository_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None:
        raise ConfigurationError("Git common directory cannot be resolved")
    return GitRepository(repository_root, Path(common).resolve())


def resolve_runtime_config(
    *,
    start: Path,
    config_path: Path | None = None,
    root: Path | None = None,
    repo_key: str | None = None,
    remote: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    env = os.environ if environment is None else environment
    repository = discover_repository(start)
    binding_path = (config_path or repository.binding_path).resolve()
    if not binding_path.is_file():
        raise ConfigurationError(f"Repository binding is missing: {binding_path}")
    data = _read_binding(binding_path)
    frozen_key = _string(data, "repo_key")
    resolved_key = repo_key or frozen_key
    if not resolved_key:
        resolved_key = derive_repo_key(repository, remote=remote)
    root_value = root or _environment_path(env.get("CODECAIRN_RUNTIME_ROOT"))
    if root_value is None:
        root_value = _runtime_path(_string(data, "runtime_root"))
    retrieval = _retrieval(data["retrieval"], environment=env)
    semantic = _semantic(data["semantic"], environment=env)
    return RuntimeConfig(
        runtime_root=root_value.resolve(), repo_key=resolved_key, binding_path=binding_path, retrieval=retrieval, semantic=semantic
    )


def initialize_repository(
    *,
    start: Path,
    config_path: Path | None = None,
    root: Path | None = None,
    repo_key: str | None = None,
    remote: str | None = None,
    retrieval_profile: RetrievalProfile | None = None,
    semantic_profile: str | None = None,
    force: bool = False,
    environment: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    env = os.environ if environment is None else environment
    repository = discover_repository(start)
    binding_path = (config_path or repository.binding_path).resolve()
    existing = _read_binding(binding_path) if binding_path.exists() else None
    frozen_key = _string(existing, "repo_key") if existing is not None else None
    selected_key = repo_key or frozen_key or derive_repo_key(repository, remote=remote)
    selected_root = root or _environment_path(env.get("CODECAIRN_RUNTIME_ROOT"))
    if selected_root is None and existing is not None:
        selected_root = _runtime_path(_string(existing, "runtime_root"))
    selected_root = (selected_root or Path.home() / ".codecairn").resolve()
    existing_retrieval = cast(dict[str, object], existing["retrieval"]) if existing is not None else None
    selected_profile = cast(
        RetrievalProfile,
        retrieval_profile
        or env.get("CODECAIRN_RETRIEVAL_PROFILE")
        or (_string(existing_retrieval, "profile") if existing_retrieval is not None else None)
        or ("dashscope" if env.get("DASHSCOPE_API_KEY") else "fastembed"),
    )
    retrieval = _retrieval(
        existing_retrieval
        if existing_retrieval is not None and selected_profile == existing_retrieval.get("profile")
        else _retrieval_dict(RetrievalConfig.default(selected_profile)),
        environment=env,
        explicit_profile=selected_profile,
    )
    existing_semantic = cast(dict[str, object], existing["semantic"]) if existing is not None else None
    selected_semantic = (
        semantic_profile
        or env.get("CODECAIRN_SEMANTIC_PROFILE")
        or (_string(existing_semantic, "profile") if existing_semantic is not None else "none")
    )
    semantic = _semantic(
        existing_semantic
        if existing_semantic is not None and selected_semantic == existing_semantic.get("profile")
        else {"profile": selected_semantic},
        environment=env,
        explicit_profile=selected_semantic,
    )
    config = RuntimeConfig(
        runtime_root=selected_root, repo_key=selected_key, binding_path=binding_path, retrieval=retrieval, semantic=semantic
    )
    encoded = _render_binding(config, portable_default=root is None)
    if binding_path.exists() and binding_path.read_text() != encoded and not force:
        raise ConfigurationError(f"Binding differs from requested configuration: {binding_path}; use --force")
    if not binding_path.exists() or binding_path.read_text() != encoded:
        _atomic_write(binding_path, encoded)
    selected_root.mkdir(parents=True, exist_ok=True)
    return config


def derive_repo_key(repository: GitRepository, *, remote: str | None = None) -> str:
    selected = _select_remote(repository.root, explicit=remote)
    if selected is not None:
        url = _git(repository.root, "remote", "get-url", selected)
        if url is None:
            raise ConfigurationError(f"Git remote has no URL: {selected}")
        return normalize_remote(url)
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", repository.root.name).strip("-") or "repository"
    digest = hashlib.sha256(str(repository.common_dir).encode()).hexdigest()[:16]
    return f"local/{slug}-{digest}"


def normalize_remote(value: str) -> str:
    match = _REMOTE.fullmatch(value)
    if match and "://" not in value:
        host, path = match.groups()
    else:
        parsed = urlparse(value)
        host, path = parsed.hostname, parsed.path
    if not host:
        raise ConfigurationError("Git remote must be an SSH or HTTPS repository URL")
    normalized = path.strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    if len(normalized.split("/")) < 2:
        raise ConfigurationError("Git remote must contain owner and repository")
    return f"{host.lower()}/{normalized}"


def _read_binding(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"Cannot parse repository binding: {path}") from error
    if set(data) != _ROOT_KEYS or data.get("schema_version") != 1:
        raise ConfigurationError("Binding has unknown keys or an unsupported schema")
    retrieval = _table(data, "retrieval", _RETRIEVAL_KEYS)
    semantic = _table(data, "semantic", _SEMANTIC_KEYS)
    if any(key in json.dumps(data).casefold() for key in ("api_key", "token", "secret")):
        raise ConfigurationError("Secrets are forbidden in repository bindings")
    return {**data, "retrieval": retrieval, "semantic": semantic}


def _retrieval(value: object, *, environment: Mapping[str, str], explicit_profile: str | None = None) -> RetrievalConfig:
    data = cast(dict[str, object], value)
    profile = cast(RetrievalProfile, explicit_profile or environment.get("CODECAIRN_RETRIEVAL_PROFILE") or data.get("profile"))
    default = RetrievalConfig.default(profile)
    cache = environment.get("CODECAIRN_MODEL_CACHE") or data.get("cache_dir")
    dimension = environment.get("CODECAIRN_EMBEDDING_DIMENSION") or data.get("dimension")
    endpoint = environment.get("CODECAIRN_EMBEDDING_ENDPOINT", cast(str | None, data.get("endpoint")))
    if profile == "fastembed":
        endpoint = None
    if not isinstance(dimension, str | int) or isinstance(dimension, bool):
        raise ConfigurationError("Embedding dimension must be an integer")
    return RetrievalConfig(
        profile=profile,
        model=environment.get("CODECAIRN_EMBEDDING_MODEL", cast(str, data.get("model"))) or default.model,
        dimension=int(dimension or default.dimension),
        endpoint=endpoint or default.endpoint,
        revision=environment.get("CODECAIRN_EMBEDDING_REVISION", cast(str, data.get("revision"))) or default.revision,
        cache_dir=_environment_path(cast(str | None, cache)),
    )


def _semantic(value: object, *, environment: Mapping[str, str], explicit_profile: str | None = None) -> SemanticConfig:
    data = cast(dict[str, object], value)
    profile = explicit_profile or environment.get("CODECAIRN_SEMANTIC_PROFILE") or data.get("profile")
    assert isinstance(profile, str)
    if profile == "none":
        return SemanticConfig()
    return SemanticConfig(
        profile=profile,
        model=environment.get("CODECAIRN_SEMANTIC_MODEL", cast(str | None, data.get("model"))),
        endpoint=environment.get("CODECAIRN_SEMANTIC_ENDPOINT", cast(str | None, data.get("endpoint"))),
    )


def _render_binding(config: RuntimeConfig, *, portable_default: bool) -> str:
    retrieval = config.retrieval
    root = "~/.codecairn" if portable_default and config.runtime_root == Path.home() / ".codecairn" else str(config.runtime_root)
    lines = [
        "schema_version = 1",
        f"runtime_root = {json.dumps(root)}",
        f"repo_key = {json.dumps(config.repo_key)}",
        "",
        "[retrieval]",
        f"profile = {json.dumps(retrieval.profile)}",
        f"model = {json.dumps(retrieval.model)}",
        f"dimension = {retrieval.dimension}",
        f"revision = {json.dumps(retrieval.revision)}",
    ]
    if retrieval.endpoint is not None:
        lines.append(f"endpoint = {json.dumps(retrieval.endpoint)}")
    if retrieval.cache_dir is not None:
        lines.append(f"cache_dir = {json.dumps(str(retrieval.cache_dir))}")
    lines.extend(["", "[semantic]", f"profile = {json.dumps(config.semantic.profile)}"])
    if config.semantic.model is not None:
        lines.append(f"model = {json.dumps(config.semantic.model)}")
    if config.semantic.endpoint is not None:
        lines.append(f"endpoint = {json.dumps(config.semantic.endpoint)}")
    return "\n".join(lines) + "\n"


def _retrieval_dict(config: RetrievalConfig) -> dict[str, object]:
    return {
        "profile": config.profile,
        "model": config.model,
        "dimension": config.dimension,
        "endpoint": config.endpoint,
        "revision": config.revision,
    }


def _runtime_path(value: str | None) -> Path:
    if value is None:
        raise ConfigurationError("runtime_root is required")
    if "$" in value or ("~" in value and not value.startswith("~/") and value != "~"):
        raise ConfigurationError("runtime_root contains unsupported expansion syntax")
    return Path(value).expanduser().resolve()


def _environment_path(value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError("Configured path must be a string")
    return _runtime_path(value)


def _table(data: Mapping[str, object], key: str, allowed: set[str]) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise ConfigurationError(f"{key} contains unknown keys")
    return cast(dict[str, object], value)


def _string(data: Mapping[str, object] | None, key: str) -> str | None:
    if data is None:
        return None
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ConfigurationError(f"{key} must be a string")
    return value


def _select_remote(root: Path, *, explicit: str | None) -> str | None:
    raw = _git(root, "remote") or ""
    remotes = tuple(item for item in raw.splitlines() if item)
    if explicit is not None:
        if explicit not in remotes:
            raise ConfigurationError(f"Unknown Git remote: {explicit}")
        return explicit
    branch = _git(root, "branch", "--show-current")
    upstream = _git(root, "config", "--get", f"branch.{branch}.remote") if branch else None
    if upstream in remotes:
        return upstream
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    if len(remotes) > 1:
        raise ConfigurationError("Multiple Git remotes require --remote")
    return None


def _git(root: Path, *arguments: str) -> str | None:
    result = subprocess.run(("git", "-C", str(root), *arguments), capture_output=True, check=False, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
