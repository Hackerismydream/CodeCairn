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
    DiscoveredSource,
    HistoryInspection,
    ImportProgress,
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

    def inspect(self, *, repository_common_dir: Path) -> HistoryInspection:
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
    ) -> ImportOutcome:
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
    return (
        OnboardingModule(
            application=selected_application,
            repo_key=REPO_KEY,
            repository_common_dir=tmp_path / "repository/.git",
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
    module = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=tmp_path / "repository/.git",
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

        def inspect(self, *, repository_common_dir: Path) -> HistoryInspection:
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


def _codex_session(path: Path, *, cwd: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = (
        {"type": "session_meta", "payload": {"id": "session", "cwd": str(cwd)}},
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
