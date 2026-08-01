"""Repository-bound discovery, consent, and import orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from codecairn.memory.episode import BoundaryKind
from codecairn.memory.errors import ImportProgressUnavailable, TraceImportError
from codecairn.service.application import ImportOutcome

Client = Literal["codex", "claude", "pico"]
CaptureClient = Literal["codex", "claude"]
ImportProgressState = Literal["new", "incremental", "already_imported", "invalid"]
_CLIENTS: tuple[Client, ...] = ("codex", "claude", "pico")
ONBOARDING_CONTRACT_REVISION = "codecairn.hub-onboarding.v1"
RETENTION_REVISION = "codecairn.onboarding.retention.v1"


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    selected_source_ids: tuple[str, ...] | None = None
    install_capture_for: tuple[CaptureClient, ...] = ()


@dataclass(frozen=True, slots=True)
class RetentionPreview:
    revision: str
    retained: tuple[str, ...]
    omitted: tuple[str, ...]
    source_content_egress: Literal["none", "memory_text_to_embedding"]


@dataclass(frozen=True, slots=True)
class SourceCandidatePreview:
    source_id: str
    session_label: str
    raw_event_count: int
    estimated_bytes: int
    latest_activity_ms: int | None
    import_state: Literal["new", "incremental", "already_imported"]
    selected: bool


@dataclass(frozen=True, slots=True)
class SourceClientPreview:
    client: Client
    historical_state: Literal["available", "none_found", "unsupported", "unresolved"]
    continuous_state: Literal["available", "installed", "not_detected", "manual_setup_required", "unsupported"]
    capture_selected: bool
    candidates: tuple[SourceCandidatePreview, ...]
    unresolved_count: int
    invalid_count: int
    remediation: str | None


@dataclass(frozen=True, slots=True)
class OnboardingPreview:
    schema_version: int
    repo_key: str
    snapshot_id: str
    expires_at_ms: int
    consent_token: str | None
    selected_import_count: int
    truncated: bool
    retention: RetentionPreview
    sources: tuple[SourceClientPreview, ...]
    planned_writes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportActionReport:
    source_id: str
    client: Literal["codex", "claude"]
    outcome: Literal["imported", "noop", "failed"]
    created_memory_count: int
    skipped_memory_count: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class CaptureActionReport:
    client: CaptureClient
    outcome: Literal["installed", "already_installed", "failed"]
    event: Literal["stop", "session_end"]
    error_code: str | None


@dataclass(frozen=True, slots=True)
class OnboardingTotals:
    imported_sessions: int
    created_memories: int
    skipped_sessions: int
    failed_actions: int


@dataclass(frozen=True, slots=True)
class OnboardingReport:
    schema_version: int
    snapshot_id: str
    repo_key: str
    outcome: Literal["complete", "noop", "partial", "failed"]
    imports: tuple[ImportActionReport, ...]
    capture: tuple[CaptureActionReport, ...]
    totals: OnboardingTotals
    index_state: Literal["ready", "pending", "failed", "not_requested"]
    requires_new_preview: bool


@dataclass(frozen=True, slots=True)
class DiscoveredSource:
    """Trusted source returned by a local Adapter; never serialize this type."""

    source_id: str
    client: Literal["codex", "claude"]
    path: Path
    source_root: Path
    fingerprint: str
    session_label: str
    raw_event_count: int
    estimated_bytes: int
    latest_activity_ms: int | None
    import_state: Literal["new", "incremental", "already_imported"] = "new"


@dataclass(frozen=True, slots=True)
class HistoryInspection:
    sources: tuple[DiscoveredSource, ...]
    unresolved: dict[Literal["codex", "claude"], int]
    invalid: dict[Literal["codex", "claude"], int]
    truncated: bool = False
    adapter_revision: str = "unknown"


class HistorySource(Protocol):
    def inspect(self, *, repository_common_dir: Path, import_progress: ImportProgress | None = None) -> HistoryInspection: ...


@dataclass(frozen=True, slots=True)
class CapturePlan:
    """Opaque exact client-settings plan returned by a Capture Adapter."""

    client: CaptureClient
    event: Literal["stop", "session_end"]
    state: Literal["available", "installed", "not_detected"]
    fingerprint: str
    expected_state_sha256: str
    adapter_revision: str


class ContinuousCapture(Protocol):
    client: CaptureClient

    def inspect(self) -> CapturePlan: ...

    def apply(self, plan: CapturePlan, *, before_write: Callable[[], object] | None = None) -> bool: ...


class OnboardingApplication(Protocol):
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
    ) -> ImportOutcome: ...


class ImportProgress(Protocol):
    def __call__(
        self, *, source_path: Path, raw_event_count: int, source_fingerprint: str, raw_event_sha256s: tuple[str, ...]
    ) -> ImportProgressState: ...


@dataclass(frozen=True, slots=True)
class _ConsentSnapshot:
    snapshot_id: str
    expires_at_ms: int
    sources: tuple[DiscoveredSource, ...]
    capture_plans: tuple[CapturePlan, ...]
    contract_revision: str
    retention_revision: str
    source_content_egress: Literal["none", "memory_text_to_embedding"]
    history_adapter_revision: str
    repository_binding_digest: str


class OnboardingModule:
    """Hide local discovery, exact consent, import, and capture behind two operations."""

    def __init__(
        self,
        *,
        application: OnboardingApplication,
        repo_key: str,
        repository_common_dir: Path,
        history: HistorySource,
        captures: tuple[ContinuousCapture, ...] = (),
        import_progress: ImportProgress | None = None,
        now_ms: Callable[[], int] | None = None,
        source_content_egress: Literal["none", "memory_text_to_embedding"] = "none",
        consent_ttl_ms: int = 10 * 60 * 1_000,
    ) -> None:
        self._application = application
        self._repo_key = repo_key
        self._repository_common_dir = repository_common_dir.resolve()
        self._repository_descriptor, self._repository_binding_digest = _open_repository_binding(
            self._repository_common_dir, repo_key=repo_key
        )
        self._history = history
        self._captures = {capture.client: capture for capture in captures}
        self._import_progress = import_progress
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._source_content_egress = source_content_egress
        self._consent_ttl_ms = consent_ttl_ms
        self._consents: dict[str, _ConsentSnapshot] = {}
        self._reports: dict[str, OnboardingReport] = {}
        self._imported_fingerprints: dict[str, str] = {}
        self._operation_lock = threading.RLock()

    def preview(self, request: PreviewRequest) -> OnboardingPreview:
        with self._operation_lock:
            return self._preview(request)

    def _preview(self, request: PreviewRequest) -> OnboardingPreview:
        self._prune_consents()
        repository_binding_digest = self._require_repository_binding()
        inspection = self._with_import_states(self._inspect_history())
        by_id = {source.source_id: source for source in inspection.sources}
        selected_ids = (
            {source.source_id for source in inspection.sources if source.import_state != "already_imported"}
            if request.selected_source_ids is None
            else set(request.selected_source_ids)
        )
        if len(selected_ids) != len(request.selected_source_ids or selected_ids) or not selected_ids <= set(by_id):
            raise OnboardingError("invalid_selection", "The selected history source is not in this repository preview.")
        capture_clients = tuple(dict.fromkeys(request.install_capture_for))
        if len(capture_clients) != len(request.install_capture_for) or any(
            client not in {"codex", "claude"} for client in capture_clients
        ):
            raise OnboardingError("invalid_selection", "The selected continuous capture client is unsupported.")
        capture_plans = self._capture_plans()
        if any(client not in capture_plans or capture_plans[client].state == "not_detected" for client in capture_clients):
            raise OnboardingError("invalid_selection", "The selected continuous capture client is not available.")
        selected = tuple(source for source in inspection.sources if source.source_id in selected_ids)
        now = int(self._now_ms())
        expires = now + self._consent_ttl_ms
        selected_capture = tuple(capture_plans[client] for client in capture_clients)
        snapshot_id = _snapshot_id(
            self._repo_key,
            selected,
            selected_capture,
            expires,
            history_adapter_revision=inspection.adapter_revision,
            source_content_egress=self._source_content_egress,
            repository_binding_digest=repository_binding_digest,
        )
        token = secrets.token_urlsafe(32) if selected or capture_clients else None
        if token is not None:
            self._consents[_token_digest(token)] = _ConsentSnapshot(
                snapshot_id,
                expires,
                selected,
                selected_capture,
                ONBOARDING_CONTRACT_REVISION,
                RETENTION_REVISION,
                self._source_content_egress,
                inspection.adapter_revision,
                repository_binding_digest,
            )
        groups = tuple(
            self._client_preview(
                client, inspection=inspection, selected_ids=selected_ids, capture_clients=capture_clients, capture_plans=capture_plans
            )
            for client in _CLIENTS
        )
        planned = tuple(
            [f"Import {len(selected)} owned historical session{'s' if len(selected) != 1 else ''}"] if selected else []
        ) + tuple(f"Install explicit {client} continuous capture" for client in capture_clients)
        retention = RetentionPreview(
            RETENTION_REVISION,
            (
                "local source locator and import cursor",
                "normalized Agent Trace facts",
                "bounded Evidence Facts",
                "derived Coding Memory",
            ),
            ("provider credentials", "full provider-native transcript copy"),
            self._source_content_egress,
        )
        return OnboardingPreview(
            1, self._repo_key, snapshot_id, expires, token, len(selected), inspection.truncated, retention, groups, planned
        )

    def apply(self, consent_token: str) -> OnboardingReport:
        with self._operation_lock:
            return self._apply(consent_token)

    def _apply(self, consent_token: str) -> OnboardingReport:
        token_key = _token_digest(consent_token)
        previous = self._reports.get(token_key)
        if previous is not None:
            return previous
        snapshot = self._consents.get(token_key)
        if snapshot is None:
            raise OnboardingError("consent_invalid", "The onboarding consent token is invalid.")
        if int(self._now_ms()) > snapshot.expires_at_ms:
            raise OnboardingError("consent_expired", "The onboarding preview expired; scan again.", retryable=True)
        repository_binding_digest = self._require_repository_binding()
        current = self._with_import_states(self._inspect_history())
        if (
            snapshot.contract_revision != ONBOARDING_CONTRACT_REVISION
            or snapshot.retention_revision != RETENTION_REVISION
            or snapshot.source_content_egress != self._source_content_egress
            or snapshot.history_adapter_revision != current.adapter_revision
            or snapshot.repository_binding_digest != repository_binding_digest
        ):
            raise OnboardingError("snapshot_stale", "The onboarding contract changed after preview.", retryable=True)
        current_by_id = {source.source_id: source for source in current.sources}
        if any(
            (found := current_by_id.get(expected.source_id)) is None
            or found.fingerprint != expected.fingerprint
            or found.import_state != expected.import_state
            for expected in snapshot.sources
        ):
            raise OnboardingError("snapshot_stale", "A selected history source changed after preview.", retryable=True)
        current_capture = self._capture_plans()
        if any(current_capture.get(plan.client) != plan for plan in snapshot.capture_plans):
            raise OnboardingError("snapshot_stale", "A selected hook configuration changed after preview.", retryable=True)
        imports: list[ImportActionReport] = []
        index_states: list[Literal["ready", "pending", "failed", "not_requested"]] = []
        for source in snapshot.sources:
            try:
                outcome = self._application.import_session(
                    source.path,
                    repo_key=self._repo_key,
                    source_root=source.source_root,
                    boundary_kind="manual_finalize",
                    expected_source_sha256=source.fingerprint,
                    before_write=lambda: self._require_repository_binding(expected_digest=snapshot.repository_binding_digest),
                )
                result = outcome.result
                imports.append(
                    ImportActionReport(
                        source.source_id,
                        source.client,
                        "imported" if result.created_memory_count > 0 else "noop",
                        result.created_memory_count,
                        result.skipped_memory_count,
                        None,
                    )
                )
                self._imported_fingerprints[source.source_id] = source.fingerprint
                index_states.append(_index_state(outcome))
            except OnboardingError as error:
                if not imports:
                    raise
                imports.append(ImportActionReport(source.source_id, source.client, "failed", 0, 0, error.code))
            except Exception as error:
                imports.append(ImportActionReport(source.source_id, source.client, "failed", 0, 0, _import_error_code(error)))

        capture_items: list[CaptureActionReport] = []
        for plan in snapshot.capture_plans:
            try:
                changed = self._captures[plan.client].apply(
                    plan, before_write=lambda: self._require_repository_binding(expected_digest=snapshot.repository_binding_digest)
                )
                capture_items.append(
                    CaptureActionReport(plan.client, "installed" if changed else "already_installed", plan.event, None)
                )
            except OnboardingError as error:
                if not imports and not capture_items:
                    raise
                capture_items.append(CaptureActionReport(plan.client, "failed", plan.event, error.code))
            except Exception as error:
                capture_items.append(CaptureActionReport(plan.client, "failed", plan.event, _capture_error_code(error)))
        capture = tuple(capture_items)
        failed_actions = sum(item.outcome == "failed" for item in imports) + sum(item.outcome == "failed" for item in capture)
        completed_actions = len(imports) + len(capture) - failed_actions
        changed_actions = sum(item.outcome == "imported" for item in imports) + sum(item.outcome == "installed" for item in capture)
        report_outcome: Literal["complete", "noop", "partial", "failed"] = (
            "partial"
            if failed_actions and completed_actions
            else "failed"
            if failed_actions
            else "complete"
            if changed_actions
            else "noop"
        )
        totals = OnboardingTotals(
            sum(item.outcome == "imported" for item in imports),
            sum(item.created_memory_count for item in imports),
            sum(item.outcome == "noop" for item in imports),
            failed_actions,
        )
        report = OnboardingReport(
            1,
            snapshot.snapshot_id,
            self._repo_key,
            report_outcome,
            tuple(imports),
            capture,
            totals,
            _aggregate_index_states(index_states),
            bool(failed_actions),
        )
        self._reports[token_key] = report
        return report

    def _with_import_states(self, inspection: HistoryInspection) -> HistoryInspection:
        def state(source: DiscoveredSource) -> ImportProgressState:
            if source.import_state != "new":
                return source.import_state
            return (
                "already_imported"
                if self._imported_fingerprints.get(source.source_id) == source.fingerprint
                else "incremental"
                if source.source_id in self._imported_fingerprints
                else "new"
            )

        sources: list[DiscoveredSource] = []
        invalid = dict(inspection.invalid)
        for source in inspection.sources:
            import_state = state(source)
            if import_state == "invalid":
                invalid[source.client] = invalid.get(source.client, 0) + 1
                continue
            sources.append(replace(source, import_state=import_state))
        return replace(inspection, sources=tuple(sources), invalid=invalid)

    def _inspect_history(self) -> HistoryInspection:
        try:
            return self._history.inspect(repository_common_dir=self._repository_common_dir, import_progress=self._import_progress)
        except ImportProgressUnavailable as error:
            raise OnboardingError(
                "progress_unavailable", "Import progress changed during preview; scan again.", retryable=True
            ) from error

    def _client_preview(
        self,
        client: Client,
        *,
        inspection: HistoryInspection,
        selected_ids: set[str],
        capture_clients: tuple[CaptureClient, ...],
        capture_plans: dict[CaptureClient, CapturePlan],
    ) -> SourceClientPreview:
        if client == "pico":
            return SourceClientPreview(
                "pico",
                "unsupported",
                "manual_setup_required",
                False,
                (),
                0,
                0,
                "Configure Pico Memory Backend `codecairn`; pre-integration Pico Session history is not imported.",
            )
        sources = tuple(source for source in inspection.sources if source.client == client)
        unresolved = inspection.unresolved.get(client, 0)
        historical_state: Literal["available", "none_found", "unsupported", "unresolved"]
        historical_state = "available" if sources else "unresolved" if unresolved else "none_found"
        plan = capture_plans.get(client)
        return SourceClientPreview(
            client,
            historical_state,
            plan.state if plan is not None else "not_detected",
            client in capture_clients,
            tuple(
                SourceCandidatePreview(
                    source.source_id,
                    source.session_label,
                    source.raw_event_count,
                    source.estimated_bytes,
                    source.latest_activity_ms,
                    source.import_state,
                    source.source_id in selected_ids,
                )
                for source in sources
            ),
            unresolved,
            inspection.invalid.get(client, 0),
            None,
        )

    def _capture_plans(self) -> dict[CaptureClient, CapturePlan]:
        plans: dict[CaptureClient, CapturePlan] = {}
        for client, adapter in self._captures.items():
            try:
                plans[client] = adapter.inspect()
            except Exception:
                continue
        return plans

    def _prune_consents(self) -> None:
        now = int(self._now_ms())
        expired = tuple(key for key, value in self._consents.items() if value.expires_at_ms < now)
        for key in expired:
            self._consents.pop(key, None)
            self._reports.pop(key, None)
        while len(self._consents) >= 64:
            oldest = next(iter(self._consents))
            self._consents.pop(oldest, None)
            self._reports.pop(oldest, None)

    def _require_repository_binding(self, *, expected_digest: str | None = None) -> str:
        descriptor = self._repository_descriptor
        try:
            retained = os.fstat(descriptor)
            current_descriptor = os.open(self._repository_common_dir, _repository_directory_flags())
            try:
                current = os.fstat(current_descriptor)
            finally:
                os.close(current_descriptor)
        except OSError as error:
            raise OnboardingError("snapshot_stale", "The repository changed after preview.", retryable=True) from error
        if (
            not stat.S_ISDIR(retained.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (retained.st_dev, retained.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise OnboardingError("snapshot_stale", "The repository changed after preview.", retryable=True)
        observed = _repository_binding_digest(
            self._repository_common_dir, repo_key=self._repo_key, device=retained.st_dev, inode=retained.st_ino
        )
        if observed != self._repository_binding_digest or (expected_digest is not None and observed != expected_digest):
            raise OnboardingError("snapshot_stale", "The repository changed after preview.", retryable=True)
        return observed

    def close(self) -> None:
        with self._operation_lock:
            descriptor = self._repository_descriptor
            if descriptor >= 0:
                self._repository_descriptor = -1
                with suppress(OSError):
                    os.close(descriptor)

    def open(self) -> None:
        with self._operation_lock:
            if self._repository_descriptor < 0:
                self._repository_descriptor, self._repository_binding_digest = _open_repository_binding(
                    self._repository_common_dir, repo_key=self._repo_key
                )
                self._consents.clear()
                self._reports.clear()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()


class OnboardingError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _snapshot_id(
    repo_key: str,
    sources: tuple[DiscoveredSource, ...],
    capture_plans: tuple[CapturePlan, ...],
    expires_at_ms: int,
    *,
    history_adapter_revision: str,
    source_content_egress: str,
    repository_binding_digest: str,
) -> str:
    value = {
        "contract_revision": ONBOARDING_CONTRACT_REVISION,
        "capture_plans": [
            (item.client, item.event, item.state, item.fingerprint, item.expected_state_sha256, item.adapter_revision)
            for item in capture_plans
        ],
        "expires_at_ms": expires_at_ms,
        "history_adapter_revision": history_adapter_revision,
        "repo_key": repo_key,
        "repository_binding_digest": repository_binding_digest,
        "retention_revision": RETENTION_REVISION,
        "source_content_egress": source_content_egress,
        "sources": [(item.source_id, item.fingerprint, item.import_state) for item in sources],
    }
    return "onb_" + hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _repository_directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_repository_binding(path: Path, *, repo_key: str) -> tuple[int, str]:
    try:
        descriptor = os.open(path, _repository_directory_flags())
    except OSError as error:
        raise OnboardingError("repository_unavailable", "The Git repository cannot be bound safely.") from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise OnboardingError("repository_unavailable", "The Git common directory is not a directory.")
    return descriptor, _repository_binding_digest(path, repo_key=repo_key, device=metadata.st_dev, inode=metadata.st_ino)


def _repository_binding_digest(path: Path, *, repo_key: str, device: int, inode: int) -> str:
    encoded = json.dumps(
        {"common_dir": str(path), "device": device, "inode": inode, "repo_key": repo_key}, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _index_state(outcome: ImportOutcome) -> Literal["ready", "pending", "failed", "not_requested"]:
    if not outcome.index.requested:
        return "not_requested"
    if outcome.index.error_type is not None or (
        outcome.index.health is not None and (outcome.index.health.failed or outcome.index.health.stale)
    ):
        return "failed"
    return "ready" if outcome.index.synced else "pending"


def _aggregate_index_states(
    states: list[Literal["ready", "pending", "failed", "not_requested"]],
) -> Literal["ready", "pending", "failed", "not_requested"]:
    if not states:
        return "not_requested"
    if "failed" in states:
        return "failed"
    if "pending" in states:
        return "pending"
    return "ready" if "ready" in states else "not_requested"


def _import_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(error, TraceImportError):
        return "trace_invalid"
    return "import_failed"


def _capture_error_code(error: Exception) -> str:
    value = str(error)
    if value in {"unsupported_client", "hook_config_invalid", "hook_preview_stale", "hook_cas_unavailable"}:
        return value
    return "hook_write_failed"
