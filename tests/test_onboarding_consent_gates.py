from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

import codecairn.entrypoints.hooks as hook_module
import codecairn.service.onboarding as onboarding_module
from codecairn.bootstrap import create_application
from codecairn.entrypoints.hooks import LocalHookCaptureAdapter
from codecairn.importers.history import LocalAgentHistory
from codecairn.memory.episode import BoundaryKind
from codecairn.memory.models import ImportResult, IndexHealth
from codecairn.service.application import ImportOutcome, IndexSyncReport
from codecairn.service.onboarding import (
    CaptureClient,
    CapturePlan,
    DiscoveredSource,
    HistoryInspection,
    HistorySource,
    ImportProgress,
    OnboardingApplication,
    OnboardingError,
    OnboardingModule,
    PreviewRequest,
)
from codecairn.storage.sqlite import SQLiteImportProgress

REPO_KEY = "github.com/Hackerismydream/CodeCairn"


class MutableHistory:
    def __init__(self, sources: tuple[DiscoveredSource, ...], *, revision: str = "history-v1") -> None:
        self.sources = sources
        self.revision = revision

    def inspect(self, *, repository_common_dir: Path, import_progress: ImportProgress | None = None) -> HistoryInspection:
        return HistoryInspection(self.sources, {"codex": 0, "claude": 0}, {"codex": 0, "claude": 0}, False, self.revision)


class RecordingApplication:
    def __init__(self, outcomes: tuple[ImportOutcome, ...] = ()) -> None:
        self.calls = 0
        self.outcomes = outcomes

    def import_session(
        self,
        source_path: Path,
        *,
        repo_key: str,
        source_root: Path | None = None,
        index: bool = True,
        boundary_kind: BoundaryKind | None = None,
        expected_source_sha256: str | None = None,
        before_write: Callable[[], object] | None = None,
    ) -> ImportOutcome:
        if before_write is not None:
            before_write()
        outcome = self.outcomes[self.calls]
        self.calls += 1
        return outcome


def _source(tmp_path: Path, suffix: str = "1") -> DiscoveredSource:
    return DiscoveredSource(
        f"src_{suffix * 64}", "codex", tmp_path / f"{suffix}.jsonl", tmp_path, suffix * 64, f"Codex session {suffix * 8}", 3, 128, 1
    )


def _module(
    tmp_path: Path,
    *,
    history: MutableHistory | None = None,
    application: RecordingApplication | None = None,
    now_ms: Callable[[], int] | None = None,
    import_progress: ImportProgress | None = None,
    source_content_egress: Literal["none", "memory_text_to_embedding"] = "none",
) -> tuple[OnboardingModule, MutableHistory, RecordingApplication]:
    selected_history = history or MutableHistory((_source(tmp_path),))
    selected_application = application or RecordingApplication()
    common_dir = tmp_path / "repository/.git"
    common_dir.mkdir(parents=True, exist_ok=True)
    return (
        OnboardingModule(
            application=selected_application,
            repo_key=REPO_KEY,
            repository_common_dir=common_dir,
            history=selected_history,
            import_progress=import_progress,
            now_ms=now_ms,
            source_content_egress=source_content_egress,
            consent_ttl_ms=10,
        ),
        selected_history,
        selected_application,
    )


def _preview_token(module: OnboardingModule) -> str:
    token = module.preview(PreviewRequest()).consent_token
    assert token is not None
    return token


def test_missing_git_common_directory_is_rejected_before_discovery(tmp_path: Path) -> None:
    with pytest.raises(OnboardingError) as raised:
        OnboardingModule(
            application=RecordingApplication(),
            repo_key=REPO_KEY,
            repository_common_dir=tmp_path / "missing/.git",
            history=MutableHistory((_source(tmp_path),)),
        )

    assert raised.value.code == "repository_unavailable"


def test_expired_or_substituted_consent_never_reaches_the_application(tmp_path: Path) -> None:
    clock = [1_000]
    module, _history, application = _module(tmp_path, now_ms=lambda: clock[0])
    token = _preview_token(module)

    with pytest.raises(OnboardingError, match="invalid") as substituted:
        module.apply(token[:-1] + ("A" if token[-1] != "A" else "B"))
    clock[0] = 1_011
    with pytest.raises(OnboardingError, match="expired") as expired:
        module.apply(token)

    assert substituted.value.code == "consent_invalid"
    assert expired.value.code == "consent_expired"
    assert expired.value.retryable is True
    assert application.calls == 0


@pytest.mark.parametrize("selected", (("src_" + "f" * 64,), ("src_" + "1" * 64,) * 2))
def test_invalid_or_duplicate_source_selection_is_rejected_before_consent(tmp_path: Path, selected: tuple[str, ...]) -> None:
    module, _history, application = _module(tmp_path)

    with pytest.raises(OnboardingError) as raised:
        module.preview(PreviewRequest(selected_source_ids=selected))

    assert raised.value.code == "invalid_selection"
    assert application.calls == 0


def test_duplicate_capture_selection_is_rejected_before_consent(tmp_path: Path) -> None:
    module, _history, application = _module(tmp_path)

    with pytest.raises(OnboardingError) as raised:
        module.preview(PreviewRequest(install_capture_for=("codex", "codex")))

    assert raised.value.code == "invalid_selection"
    assert application.calls == 0


def test_import_ledger_change_invalidates_the_complete_plan(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
        import_progress=SQLiteImportProgress(path=runtime / "state.sqlite3", repo_key=REPO_KEY),
    )
    token = _preview_token(module)
    application.import_session(
        source, repo_key=REPO_KEY, source_root=home / ".codex/sessions", index=False, boundary_kind="manual_finalize"
    )
    before = _tree_snapshot(runtime)

    with pytest.raises(OnboardingError) as raised:
        module.apply(token)

    assert raised.value.code == "snapshot_stale"
    assert len(application.list_memories(repo_key=REPO_KEY)) == 1
    assert _tree_snapshot(runtime) == before


def test_replacing_the_git_repository_after_preview_invalidates_consent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )
    token = _preview_token(module)
    common_dir.rename(repository / ".git-before-preview")
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    replacement_common_dir = Path(
        subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "--path-format=absolute", "--git-common-dir"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()
    assert replacement_common_dir == common_dir

    with pytest.raises(OnboardingError) as raised:
        module.apply(token)

    assert raised.value.code == "snapshot_stale"
    assert application.list_memories(repo_key=REPO_KEY) == ()


def test_normal_head_movement_does_not_invalidate_repository_consent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )
    token = _preview_token(module)
    subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=CodeCairn Test",
            "-c",
            "user.email=codecairn@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "move HEAD",
        ),
        check=True,
        capture_output=True,
    )

    report = module.apply(token)

    assert report.outcome == "complete"
    assert application.list_memories(repo_key=REPO_KEY)


def test_linked_worktree_sharing_the_git_common_directory_is_accepted(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=CodeCairn Test",
            "-c",
            "user.email=codecairn@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "base",
        ),
        check=True,
        capture_output=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(("git", "-C", str(repository), "worktree", "add", "-b", "linked", str(linked)), check=True, capture_output=True)
    home = tmp_path / "home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=linked)
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    report = module.apply(_preview_token(module))

    assert report.outcome == "complete"
    assert application.list_memories(repo_key=REPO_KEY)


def test_repository_replacement_at_the_final_prewrite_seam_fails_closed(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    delegate = LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret")

    class ReplaceAfterApplyRediscovery:
        calls = 0

        def inspect(self, *, repository_common_dir: Path, import_progress: ImportProgress | None = None) -> HistoryInspection:
            result = delegate.inspect(repository_common_dir=repository_common_dir)
            self.calls += 1
            if self.calls == 2:
                common_dir.rename(repository / ".git-before-apply")
                subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
            return result

    module = OnboardingModule(
        application=application, repo_key=REPO_KEY, repository_common_dir=common_dir, history=ReplaceAfterApplyRediscovery()
    )
    token = _preview_token(module)

    with pytest.raises(OnboardingError) as raised:
        module.apply(token)

    assert raised.value.code == "snapshot_stale"
    assert application.list_memories(repo_key=REPO_KEY) == ()


def test_repository_replacement_at_application_entry_fails_before_runtime_creation(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    runtime = tmp_path / "runtime"
    delegate = create_application(runtime, repo_key=REPO_KEY)

    class ReplaceAtApplicationEntry:
        def import_session(
            self,
            source_path: Path,
            *,
            repo_key: str,
            source_root: Path | None = None,
            index: bool = True,
            boundary_kind: BoundaryKind | None = None,
            expected_source_sha256: str | None = None,
            before_write: Callable[[], object] | None = None,
        ) -> ImportOutcome:
            common_dir.rename(repository / ".git-before-write")
            subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
            return delegate.import_session(
                source_path,
                repo_key=repo_key,
                source_root=source_root,
                index=index,
                boundary_kind=boundary_kind,
                expected_source_sha256=expected_source_sha256,
                before_write=before_write,
            )

    module = OnboardingModule(
        application=ReplaceAtApplicationEntry(),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    with pytest.raises(OnboardingError) as raised:
        module.apply(_preview_token(module))

    assert raised.value.code == "snapshot_stale"
    assert not runtime.exists()


def test_repository_replacement_after_one_import_returns_a_partial_receipt(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    root = home / ".codex/sessions/2026/08/01"
    _codex_session(root / "one.jsonl", cwd=repository, session_id="one")
    _codex_session(root / "two.jsonl", cwd=repository, session_id="two")
    runtime = tmp_path / "runtime"
    delegate = create_application(runtime, repo_key=REPO_KEY)

    class ReplaceAtSecondImport:
        calls = 0

        def import_session(
            self,
            source_path: Path,
            *,
            repo_key: str,
            source_root: Path | None = None,
            index: bool = True,
            boundary_kind: BoundaryKind | None = None,
            expected_source_sha256: str | None = None,
            before_write: Callable[[], object] | None = None,
        ) -> ImportOutcome:
            self.calls += 1
            if self.calls == 2:
                common_dir.rename(repository / ".git-after-first-import")
                subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
            return delegate.import_session(
                source_path,
                repo_key=repo_key,
                source_root=source_root,
                index=index,
                boundary_kind=boundary_kind,
                expected_source_sha256=expected_source_sha256,
                before_write=before_write,
            )

    module = OnboardingModule(
        application=ReplaceAtSecondImport(),
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )

    report = module.apply(_preview_token(module))

    assert report.outcome == "partial"
    assert tuple(item.outcome for item in report.imports) == ("imported", "failed")
    assert report.imports[1].error_code == "snapshot_stale"
    assert report.totals.created_memories == 1
    assert report.requires_new_preview is True
    assert len(delegate.list_memories(repo_key=REPO_KEY)) == 1


@pytest.mark.parametrize("prior_import", (False, True))
def test_repository_replacement_at_capture_entry_preserves_receipt_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior_import: bool
) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    target = tmp_path / "home/.codex/hooks.json"
    target.parent.mkdir(parents=True)
    executable = tmp_path / "codecairn"
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: "0.144.6")
    delegate = LocalHookCaptureAdapter(client="codex", target=target, executable=executable)
    application: OnboardingApplication
    history: HistorySource
    if prior_import:
        home = tmp_path / "history-home"
        _codex_session(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
        application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
        history = LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret")
    else:
        application = RecordingApplication()
        history = MutableHistory(())

    class ReplaceAtCaptureEntry:
        client: CaptureClient = "codex"

        def inspect(self) -> CapturePlan:
            return delegate.inspect()

        def apply(self, plan: CapturePlan, *, before_write: Callable[[], object] | None = None) -> bool:
            common_dir.rename(repository / ".git-before-capture")
            subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
            return delegate.apply(plan, before_write=before_write)

    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=history,
        captures=(ReplaceAtCaptureEntry(),),
    )
    preview = module.preview(PreviewRequest(install_capture_for=("codex",)))
    assert preview.consent_token is not None

    if prior_import:
        report = module.apply(preview.consent_token)
        assert report.outcome == "partial"
        assert report.imports[0].outcome == "imported"
        assert report.capture[0].error_code == "snapshot_stale"
    else:
        with pytest.raises(OnboardingError) as raised:
            module.apply(preview.consent_token)
        assert raised.value.code == "snapshot_stale"
    assert not target.exists()


@pytest.mark.parametrize("drift", ("adapter", "retention", "egress"))
def test_adapter_retention_or_egress_drift_invalidates_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str) -> None:
    module, history, application = _module(tmp_path)
    token = _preview_token(module)
    if drift == "adapter":
        history.revision = "history-v2"
    elif drift == "retention":
        monkeypatch.setattr(onboarding_module, "RETENTION_REVISION", "retention-v2")
    else:
        module._source_content_egress = "memory_text_to_embedding"

    with pytest.raises(OnboardingError) as raised:
        module.apply(token)

    assert raised.value.code == "snapshot_stale"
    assert application.calls == 0


def test_client_version_drift_invalidates_consent_before_import_or_settings_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = ["0.144.6"]
    monkeypatch.setattr(hook_module, "detect_client_version", lambda _client: version[0])
    target = tmp_path / "home/.codex/hooks.json"
    target.parent.mkdir(parents=True)
    executable = tmp_path / "codecairn"
    executable.write_text("#!/bin/sh\n")
    history = MutableHistory((_source(tmp_path),))
    application = RecordingApplication()
    common_dir = tmp_path / "repository/.git"
    common_dir.mkdir(parents=True)
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=history,
        captures=(LocalHookCaptureAdapter(client="codex", target=target, executable=executable),),
    )
    preview = module.preview(PreviewRequest(install_capture_for=("codex",)))
    assert preview.consent_token is not None
    version[0] = "0.145.0"

    with pytest.raises(OnboardingError) as raised:
        module.apply(preview.consent_token)

    assert raised.value.code == "snapshot_stale"
    assert application.calls == 0
    assert not target.exists()


def test_source_mutation_at_the_import_seam_cannot_change_durable_state(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _codex_session(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    assert application.list_memories(repo_key=REPO_KEY) == ()
    before = _tree_snapshot(runtime)
    delegate = LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret")

    class MutateAfterApplyPreflight:
        calls = 0

        def inspect(self, *, repository_common_dir: Path, import_progress: ImportProgress | None = None) -> HistoryInspection:
            result = delegate.inspect(repository_common_dir=repository_common_dir)
            self.calls += 1
            if self.calls == 2:
                source.write_text(source.read_text() + "\n")
            return result

    module = OnboardingModule(
        application=application, repo_key=REPO_KEY, repository_common_dir=common_dir, history=MutateAfterApplyPreflight()
    )

    report = module.apply(_preview_token(module))

    assert report.outcome == "failed"
    assert report.imports[0].error_code == "source_rewritten"
    assert application.list_memories(repo_key=REPO_KEY) == ()
    assert _tree_snapshot(runtime) == before


@pytest.mark.parametrize("failed,stale", ((1, 0), (0, 1)))
def test_failed_or_stale_index_health_dominates_the_apply_aggregate(tmp_path: Path, failed: int, stale: int) -> None:
    ready = _outcome(IndexHealth(pending=0, leased=0, indexed=1, failed=0, stale=0))
    unhealthy = _outcome(IndexHealth(pending=0, leased=0, indexed=1, failed=failed, stale=stale))
    history = MutableHistory((_source(tmp_path, "1"), _source(tmp_path, "2")))
    module, _history, _application = _module(tmp_path, history=history, application=RecordingApplication((ready, unhealthy)))

    report = module.apply(_preview_token(module))

    assert report.outcome == "complete"
    assert report.index_state == "failed"


def _outcome(health: IndexHealth) -> ImportOutcome:
    result = ImportResult("codex", "session", "a" * 64, 3, 2, 0, 3, 1, 0, 0)
    synced = not (health.pending or health.leased or health.failed or health.stale)
    return ImportOutcome(result, IndexSyncReport(True, synced, health))


def _repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    common = subprocess.run(
        ("git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"), check=True, capture_output=True, text=True
    ).stdout.strip()
    return Path(common).resolve()


def _codex_session(path: Path, *, cwd: Path, session_id: str = "session") -> None:
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


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()
    }
