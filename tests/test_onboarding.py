from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

import codecairn.entrypoints.hooks as hook_module
import codecairn.importers.history as history_module
import codecairn.storage.sqlite as sqlite_module
from codecairn.bootstrap import create_application
from codecairn.entrypoints.hooks import LocalHookCaptureAdapter
from codecairn.importers.history import LocalAgentHistory
from codecairn.memory.schema import LegacyRootUnsupported
from codecairn.service.onboarding import OnboardingError, OnboardingModule, PreviewRequest
from codecairn.storage.sqlite import SQLiteImportProgress

REPO_KEY = "github.com/Hackerismydream/CodeCairn"


def _repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    common_dir = subprocess.run(
        ("git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"), check=True, capture_output=True, text=True
    ).stdout.strip()
    return Path(common_dir).resolve()


def _codex_session(path: Path, *, cwd: Path, session_id: str = "codex-session-1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = (
        {"type": "session_meta", "payload": {"id": session_id, "cwd": str(cwd)}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run tests."}]},
        },
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Tests pass."}]},
        },
    )
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records))


def _claude_session(path: Path, *, cwd: Path, session_id: str = "claude-session-1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = (
        {"type": "user", "sessionId": session_id, "cwd": str(cwd), "message": {"role": "user", "content": "Run tests."}},
        {"type": "assistant", "sessionId": session_id, "cwd": str(cwd), "message": {"role": "assistant", "content": "Tests pass."}},
    )
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records))


def test_preview_discovers_current_repository_without_product_writes_or_path_disclosure(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "private-home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
        now_ms=lambda: 1_000,
    )

    preview = module.preview(PreviewRequest())

    assert preview.repo_key == REPO_KEY
    assert preview.selected_import_count == 1
    assert preview.consent_token
    codex = next(item for item in preview.sources if item.client == "codex")
    assert codex.historical_state == "available"
    assert codex.candidates[0].selected is True
    assert codex.candidates[0].raw_event_count == 3
    assert codex.candidates[0].source_id != str(source)
    assert not runtime.exists()
    serialized = json.dumps(asdict(preview), sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "session.jsonl" not in serialized


def test_apply_imports_once_and_same_token_retry_returns_the_same_report(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        now_ms=lambda: 1_000,
    )
    preview = module.preview(PreviewRequest())
    assert preview.consent_token is not None

    first = module.apply(preview.consent_token)
    retry = module.apply(preview.consent_token)
    after = module.preview(PreviewRequest())
    restarted_module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"new-process-source-secret"),
        import_progress=SQLiteImportProgress(path=tmp_path / "runtime/state.sqlite3", repo_key=REPO_KEY),
    )
    restarted = restarted_module.preview(PreviewRequest())

    assert first == retry
    assert first.outcome == "complete"
    assert first.imports[0].outcome == "imported"
    assert first.imports[0].created_memory_count == 1
    assert len(application.list_memories(repo_key=REPO_KEY)) == 1
    codex = next(item for item in after.sources if item.client == "codex")
    assert codex.candidates[0].import_state == "already_imported"
    restarted_codex = next(item for item in restarted.sources if item.client == "codex")
    assert restarted_codex.candidates[0].import_state == "already_imported"
    assert restarted_codex.candidates[0].selected is False
    assert restarted.selected_import_count == 0
    assert restarted.planned_writes == ()
    assert restarted.consent_token is None
    explicit = restarted_module.preview(PreviewRequest(selected_source_ids=(restarted_codex.candidates[0].source_id,)))
    assert explicit.consent_token is not None
    explicit_report = restarted_module.apply(explicit.consent_token)
    assert explicit_report.imports[0].outcome == "noop"
    assert explicit_report.totals.skipped_sessions == 1


def test_preview_reads_existing_import_progress_without_mutating_runtime_files(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    (runtime / "index.lance").mkdir()
    (runtime / "index.lance/sentinel").write_bytes(b"index-state")

    def snapshot() -> dict[str, tuple[bytes, int]]:
        return {
            path.relative_to(runtime).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in runtime.rglob("*")
            if path.is_file()
        }

    before = snapshot()
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    preview = module.preview(PreviewRequest())

    assert next(item for item in preview.sources if item.client == "codex").candidates[0].import_state == "already_imported"
    assert snapshot() == before


@pytest.mark.parametrize("suffix", ("-journal", "-wal", "-shm"))
def test_preview_rejects_sqlite_sidecars_without_mutating_them(tmp_path: Path, suffix: str) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    sidecar = Path(f"{runtime / 'state.sqlite3'}{suffix}")
    sidecar.write_bytes(b"untrusted-sidecar")
    before = (sidecar.read_bytes(), sidecar.stat().st_mtime_ns)
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    with pytest.raises(OnboardingError) as raised:
        module.preview(PreviewRequest())

    assert raised.value.code == "progress_unavailable"
    assert (sidecar.read_bytes(), sidecar.stat().st_mtime_ns) == before


def test_preview_rejects_import_progress_that_changes_during_its_read(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    generation = sqlite_module._sqlite_generation
    calls = 0

    def changing_generation(path: Path):
        nonlocal calls
        calls += 1
        observed = generation(path)
        if calls == 2:
            main = observed[0]
            assert main is not None
            return ((main[0], main[1], main[2], main[3] + 1), *observed[1:])
        return observed

    monkeypatch.setattr(sqlite_module, "_sqlite_generation", changing_generation)
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    with pytest.raises(OnboardingError) as raised:
        module.preview(PreviewRequest())

    assert raised.value.code == "progress_unavailable"
    assert raised.value.retryable is True


def test_preview_rejects_wal_state_instead_of_reading_a_stale_main_database(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    with sqlite3.connect(runtime / "state.sqlite3") as connection:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    with pytest.raises(OnboardingError) as raised:
        module.preview(PreviewRequest())

    assert raised.value.code == "progress_unavailable"
    assert raised.value.retryable is True


def test_preview_rejects_a_same_length_rewrite_behind_the_import_cursor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    records = tuple(json.loads(line) for line in source.read_text().splitlines())
    rewritten = ({**records[0], "rewritten": True}, *records[1:])
    source.write_text("".join(f"{json.dumps(record)}\n" for record in rewritten))
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    assert codex.candidates == ()
    assert codex.invalid_count == 1
    assert preview.consent_token is None


def test_preview_rejects_a_same_length_rewrite_after_the_resume_cursor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[-1]["payload"]["content"][0]["text"] = "Rewritten assistant output."
    source.write_text("".join(f"{json.dumps(record)}\n" for record in records))
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    assert codex.candidates == ()
    assert codex.invalid_count == 1
    assert preview.consent_token is None


def test_preview_rejects_a_truncated_imported_source(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    source.write_text("\n".join(source.read_text().splitlines()[:-1]) + "\n")
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    assert codex.candidates == ()
    assert codex.invalid_count == 1
    assert preview.consent_token is None


def test_preview_rejects_a_committed_prefix_rewrite_even_when_events_were_appended(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[0]["rewritten"] = True
    records.append({"type": "turn_context", "payload": {"cwd": str(repository)}})
    source.write_text("".join(f"{json.dumps(record)}\n" for record in records))
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    assert codex.candidates == ()
    assert codex.invalid_count == 1
    assert preview.consent_token is None


def test_clean_append_is_incremental_and_becomes_already_imported_after_apply(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    application.import_session(source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False)
    with source.open("a") as output:
        output.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run lint."}]},
                }
            )
            + "\n"
        )
        output.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Lint passes."}]},
                }
            )
            + "\n"
        )
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )

    preview = module.preview(PreviewRequest())
    candidate = next(item for item in preview.sources if item.client == "codex").candidates[0]
    assert candidate.import_state == "incremental"
    assert candidate.selected is True
    assert preview.consent_token is not None

    report = module.apply(preview.consent_token)
    after = module.preview(PreviewRequest())

    assert report.imports[0].outcome == "imported"
    assert next(item for item in after.sources if item.client == "codex").candidates[0].import_state == "already_imported"


@pytest.mark.parametrize("schema_state", ("legacy_imports", "missing_revision", "mismatched_revision"))
def test_import_progress_rejects_untrusted_schema_without_mutating_any_runtime_file(tmp_path: Path, schema_state: str) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = runtime / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE imports (repo_key TEXT, source_path TEXT, committed_raw_event_index INTEGER)")
        if schema_state != "legacy_imports":
            connection.execute("CREATE TABLE codecairn_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if schema_state == "mismatched_revision":
            connection.execute("INSERT INTO codecairn_meta VALUES ('schema_revision', 'codecairn-v00')")
    (runtime / "memories").mkdir()
    (runtime / "memories/sentinel.md").write_bytes(b"durable-memory")
    (runtime / "index.lance").mkdir()
    (runtime / "index.lance/sentinel").write_bytes(b"index-state")

    def snapshot() -> dict[str, tuple[bytes, int]]:
        return {
            path.relative_to(runtime).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in runtime.rglob("*")
            if path.is_file()
        }

    before = snapshot()
    progress = SQLiteImportProgress(path=database, repo_key=REPO_KEY)

    with pytest.raises(LegacyRootUnsupported):
        progress(
            source_path=tmp_path / "source.jsonl", raw_event_count=3, source_fingerprint="a" * 64, raw_event_sha256s=("b" * 64,) * 3
        )

    assert snapshot() == before


@pytest.mark.parametrize("database_kind", ("symlink", "directory", "fifo", "hardlink"))
def test_import_progress_rejects_non_regular_or_shared_database_without_writes(tmp_path: Path, database_kind: str) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = runtime / "state.sqlite3"
    external = tmp_path / "external-state"
    if database_kind == "symlink":
        external.write_bytes(b"external")
        database.symlink_to(external)
    elif database_kind == "directory":
        database.mkdir()
    elif database_kind == "fifo":
        os.mkfifo(database)
    else:
        external.write_bytes(b"external")
        os.link(external, database)
    metadata = database.lstat()
    before = (metadata.st_mode, metadata.st_nlink, metadata.st_size, metadata.st_mtime_ns)

    with pytest.raises(LegacyRootUnsupported):
        SQLiteImportProgress(path=database, repo_key=REPO_KEY)(
            source_path=tmp_path / "source.jsonl", raw_event_count=3, source_fingerprint="a" * 64, raw_event_sha256s=("b" * 64,) * 3
        )

    metadata = database.lstat()
    assert (metadata.st_mode, metadata.st_nlink, metadata.st_size, metadata.st_mtime_ns) == before
    assert stat.S_ISFIFO(metadata.st_mode) is (database_kind == "fifo")
    if external.exists():
        assert external.read_bytes() == b"external"


def test_apply_rejects_a_changed_source_before_the_first_product_write(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        now_ms=lambda: 1_000,
    )
    preview = module.preview(PreviewRequest())
    assert preview.consent_token is not None
    source.write_text(source.read_text() + "\n")

    with pytest.raises(OnboardingError) as raised:
        module.apply(preview.consent_token)

    assert raised.value.code == "snapshot_stale"
    assert not runtime.exists()


def test_preview_excludes_foreign_unresolved_and_invalid_history(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    foreign = tmp_path / "foreign"
    _repository(foreign)
    home = tmp_path / "home"
    root = home / ".codex/sessions/2026/08/01"
    _codex_session(root / "current.jsonl", cwd=repository, session_id="current")
    _codex_session(root / "foreign.jsonl", cwd=foreign, session_id="foreign")
    _codex_session(root / "unresolved.jsonl", cwd=tmp_path / "missing", session_id="unresolved")
    (root / "invalid.jsonl").write_text("not-json\n")
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    assert len(codex.candidates) == 1
    assert codex.unresolved_count == 2
    assert codex.invalid_count == 1
    assert preview.selected_import_count == 1


@pytest.mark.parametrize("client", ("codex", "claude"))
def test_discovery_rejects_a_session_with_mixed_repository_cwds(tmp_path: Path, client: str) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    foreign = tmp_path / "foreign"
    _repository(foreign)
    home = tmp_path / "home"
    if client == "codex":
        source = home / ".codex/sessions/2026/08/01/session.jsonl"
        _codex_session(source, cwd=repository)
        with source.open("a") as output:
            output.write(json.dumps({"type": "turn_context", "payload": {"cwd": str(foreign)}}) + "\n")
    else:
        source = home / ".claude/projects/current/session.jsonl"
        _claude_session(source, cwd=repository)
        with source.open("a") as output:
            output.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "claude-session-1",
                        "cwd": str(foreign),
                        "message": {"role": "assistant", "content": "foreign"},
                    }
                )
                + "\n"
            )
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    selected = next(item for item in preview.sources if item.client == client)
    assert selected.candidates == ()
    assert selected.unresolved_count == 1


@pytest.mark.parametrize("client", ("codex", "claude"))
def test_discovery_does_not_traverse_a_symlinked_client_directory(tmp_path: Path, client: str) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    if client == "codex":
        _codex_session(outside / "sessions/2026/08/01/session.jsonl", cwd=repository)
        (home / ".codex").symlink_to(outside, target_is_directory=True)
    else:
        _claude_session(outside / "projects/current/session.jsonl", cwd=repository)
        (home / ".claude").symlink_to(outside, target_is_directory=True)
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    assert preview.selected_import_count == 0
    assert all(not item.candidates for item in preview.sources)


def test_explicit_capture_selection_is_sanitized_and_installed_after_import(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "secret-home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    settings = home / ".codex/hooks.json"
    executable = tmp_path / "bin/codecairn"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "0.144.6")
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        captures=(LocalHookCaptureAdapter(client="codex", target=settings, executable=executable),),
    )

    preview = module.preview(PreviewRequest(install_capture_for=("codex",)))
    serialized = json.dumps(asdict(preview), sort_keys=True)
    assert str(tmp_path) not in serialized
    codex = next(item for item in preview.sources if item.client == "codex")
    assert codex.continuous_state == "available"
    assert codex.capture_selected is True
    assert preview.consent_token is not None

    report = module.apply(preview.consent_token)
    after = module.preview(PreviewRequest(selected_source_ids=()))

    assert report.capture[0].outcome == "installed"
    assert report.capture[0].event == "stop"
    assert settings.is_file()
    assert next(item for item in after.sources if item.client == "codex").continuous_state == "installed"


def test_changed_hook_settings_reject_the_whole_snapshot_before_import(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    settings = home / ".codex/hooks.json"
    executable = tmp_path / "codecairn"
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "0.144.6")
    runtime = tmp_path / "runtime"
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        captures=(LocalHookCaptureAdapter(client="codex", target=settings, executable=executable),),
    )
    preview = module.preview(PreviewRequest(install_capture_for=("codex",)))
    assert preview.consent_token is not None
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"changed_after_preview":true}\n')

    with pytest.raises(OnboardingError) as raised:
        module.apply(preview.consent_token)

    assert raised.value.code == "snapshot_stale"
    assert not runtime.exists()
    assert json.loads(settings.read_text()) == {"changed_after_preview": True}


def test_discovery_never_traverses_client_root_or_child_directory_symlinks(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    _codex_session(outside / "root.jsonl", cwd=repository)
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/projects").symlink_to(outside, target_is_directory=True)
    codex_root = home / ".codex/sessions"
    codex_root.mkdir(parents=True)
    (codex_root / "linked").symlink_to(outside, target_is_directory=True)
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    assert preview.selected_import_count == 0
    assert all(not source.candidates for source in preview.sources)


def test_discovery_stops_at_the_global_observed_byte_budget(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    monkeypatch.setattr(history_module, "_MAX_OBSERVED_BYTES", 8)
    runtime = tmp_path / "runtime"
    module = OnboardingModule(
        application=create_application(runtime, repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    assert preview.truncated is True
    assert preview.selected_import_count == 0
    assert not runtime.exists()


def test_discovery_rechecks_the_byte_limit_after_lstat_before_read(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    original_size = source.stat().st_size
    monkeypatch.setattr(history_module, "_MAX_OBSERVED_BYTES", 2 * (original_size + 8))
    read_scan = history_module.read_import_scan
    observed_limit = 0

    def grow_after_lstat(*args, **kwargs):
        nonlocal observed_limit
        observed_limit = kwargs["max_session_bytes"]
        with source.open("ab") as output:
            output.write(b" " * 16)
        return read_scan(*args, **kwargs)

    monkeypatch.setattr(history_module, "read_import_scan", grow_after_lstat)
    inspection = LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret").inspect(repository_common_dir=common_dir)

    assert source.stat().st_size > observed_limit
    assert inspection.sources == ()
    assert inspection.invalid["codex"] == 1
    assert inspection.truncated is True


def test_failure_after_one_durable_import_is_reported_as_partial(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    settings = home / ".codex/hooks.json"
    executable = tmp_path / "codecairn"
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "0.144.6")
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        captures=(LocalHookCaptureAdapter(client="codex", target=settings, executable=executable),),
    )
    preview = module.preview(PreviewRequest(install_capture_for=("codex",)))
    assert preview.consent_token is not None
    monkeypatch.setattr(hook_module, "_guarded_replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("full")))

    report = module.apply(preview.consent_token)

    assert report.outcome == "partial"
    assert report.imports[0].outcome == "imported"
    assert report.capture[0].outcome == "failed"
    assert report.totals.created_memories == 1
    assert report.totals.failed_actions == 1
    assert report.requires_new_preview is True
    assert len(application.list_memories(repo_key=REPO_KEY)) == 1


def test_bounded_discovery_keeps_recent_codex_and_claude_history_visible(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    for index in range(4):
        _codex_session(home / f".codex/sessions/2025/01/01/old-{index}.jsonl", cwd=repository, session_id=f"old-{index}")
    latest = home / ".codex/sessions/2026/08/01/latest.jsonl"
    _codex_session(latest, cwd=repository, session_id="latest")
    _claude_session(home / ".claude/projects/current/session.jsonl", cwd=repository)
    latest_ns = 2_000_000_000_000_000_000
    latest.touch()
    latest.chmod(0o600)
    os.utime(latest, ns=(latest_ns, latest_ns))
    monkeypatch.setattr(history_module, "_MAX_CANDIDATES", 4)
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    claude = next(item for item in preview.sources if item.client == "claude")
    assert preview.truncated is True
    assert latest_ns // 1_000_000 in {item.latest_activity_ms for item in codex.candidates}
    assert len(claude.candidates) == 1


def test_discovery_hard_bounds_entries_materialized_from_one_directory(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    leaf = home / ".codex/sessions/2026/08/01"
    for index in range(8):
        _codex_session(leaf / f"session-{index}.jsonl", cwd=repository, session_id=f"session-{index}")
    monkeypatch.setattr(history_module, "_MAX_DIRECTORY_ENTRIES", 3)
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    codex = next(item for item in preview.sources if item.client == "codex")
    assert preview.truncated is True
    assert len(codex.candidates) == 3


def test_directory_walk_never_opens_more_descriptors_than_its_directory_budget(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "history"
    for index in range(20):
        (root / f"project-{index}").mkdir(parents=True)
    root_descriptor = history_module.open_directory_no_symlinks(root)
    open_file = os.open
    close_file = os.close
    active = 1
    maximum = 1

    def tracked_open(*args, **kwargs):
        nonlocal active, maximum
        descriptor = open_file(*args, **kwargs)
        active += 1
        maximum = max(maximum, active)
        return descriptor

    def tracked_close(descriptor):
        nonlocal active
        close_file(descriptor)
        active -= 1

    monkeypatch.setattr(history_module, "open_directory_no_symlinks", lambda _root: root_descriptor)
    monkeypatch.setattr(history_module.os, "open", tracked_open)
    monkeypatch.setattr(history_module.os, "close", tracked_close)

    files, truncated = history_module._jsonl_files(root, max_directories=3, max_files=3)

    assert files == ()
    assert truncated is True
    assert maximum <= 3
    assert active == 0


def test_discovery_fairly_samples_claude_project_directories(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    foreign = tmp_path / "foreign"
    _repository(foreign)
    home = tmp_path / "home"
    current_dir = str(repository.resolve()).replace(os.sep, "-")
    current = home / f".claude/projects/{current_dir}/session.jsonl"
    _claude_session(current, cwd=repository, session_id="current")
    os.utime(current, ns=(1_000_000_000, 1_000_000_000))
    for index in range(4):
        source = home / f".claude/projects/foreign-{index}/session.jsonl"
        _claude_session(source, cwd=foreign, session_id=f"foreign-{index}")
        os.utime(source, ns=(2_000_000_000 + index, 2_000_000_000 + index))
    monkeypatch.setattr(history_module, "_MAX_CANDIDATES", 4)
    module = OnboardingModule(
        application=create_application(tmp_path / "runtime", repo_key=REPO_KEY),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    preview = module.preview(PreviewRequest())

    claude = next(item for item in preview.sources if item.client == "claude")
    assert len(claude.candidates) == 1
    assert claude.unresolved_count == 1
    assert preview.truncated is True


def test_discovery_never_parses_more_than_the_global_candidate_budget(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    for client_root in (home / ".codex/sessions", home / ".claude/projects"):
        for index in range(140):
            source = client_root / f"project-{index}/session.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text("{}\n")
    read_scan = history_module.read_import_scan
    calls = 0

    def counted_read_scan(*args, **kwargs):
        nonlocal calls
        calls += 1
        return read_scan(*args, **kwargs)

    monkeypatch.setattr(history_module, "read_import_scan", counted_read_scan)
    inspection = LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret").inspect(
        repository_common_dir=tmp_path / "repository/.git"
    )

    assert calls == history_module._MAX_CANDIDATES
    assert inspection.truncated is True
