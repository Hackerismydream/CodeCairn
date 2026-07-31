from __future__ import annotations

import json
import subprocess
from dataclasses import fields
from pathlib import Path

from codecairn_hub_api.app import OnboardingApplyRequest, OnboardingPreviewRequest, create_hub_app
from codecairn_hub_api.cli import build_live_hub
from codecairn_hub_api.queries import RecallReadiness
from fastapi.testclient import TestClient

from codecairn.bootstrap import create_application
from codecairn.configuration import initialize_repository
from codecairn.importers.history import LocalAgentHistory
from codecairn.service.onboarding import (
    CaptureActionReport,
    ImportActionReport,
    OnboardingModule,
    OnboardingPreview,
    OnboardingReport,
    OnboardingTotals,
    RetentionPreview,
    SourceCandidatePreview,
    SourceClientPreview,
)

REPO_KEY = "github.com/Hackerismydream/CodeCairn"
TOKEN = "test-session-token-with-32-characters"
READY = RecallReadiness(profile="injected", state="configuration_ready", live_checked=False, remediation=None)


def _repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    return Path(
        subprocess.run(
            ("git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()


def _source(path: Path, *, cwd: Path) -> None:
    path.parent.mkdir(parents=True)
    records = (
        {"type": "session_meta", "payload": {"id": "api-session", "cwd": str(cwd)}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Fix tests."}]},
        },
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]},
        },
    )
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records))


def test_onboarding_transport_previews_and_applies_without_changing_hub_read_routes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "private-home"
    _source(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
    onboarding = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )
    client = TestClient(
        create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY, onboarding=onboarding)
    )
    headers = {"x-codecairn-hub-token": TOKEN}

    preview = client.post("/hub-onboarding/v1/preview", headers=headers, json={})
    assert preview.status_code == 200
    assert str(tmp_path) not in preview.text
    token = preview.json()["consent_token"]
    applied = client.post("/hub-onboarding/v1/apply", headers=headers, json={"consent_token": token})
    memories = client.get("/hub-read/v1/memories", headers=headers)

    assert applied.status_code == 200
    assert applied.json()["outcome"] == "complete"
    assert applied.json()["totals"]["created_memories"] == 1
    assert memories.status_code == 200
    assert memories.json()["page"]["items"][0]["memory_type"] == "task_experience"


def test_onboarding_transport_rejects_paths_and_maps_stale_consent_to_a_typed_error(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    home = tmp_path / "private-home"
    source = home / ".codex/sessions/2026/08/01/session.jsonl"
    _source(source, cwd=repository)
    runtime = tmp_path / "runtime"
    application = create_application(runtime, repo_key=REPO_KEY)
    onboarding = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=home, identity_secret=b"opaque-source-secret"),
    )
    client = TestClient(
        create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY, onboarding=onboarding)
    )
    headers = {"x-codecairn-hub-token": TOKEN}

    arbitrary_path = client.post("/hub-onboarding/v1/preview", headers=headers, json={"source_path": "/etc/passwd"})
    query_path = client.post("/hub-onboarding/v1/preview?source_path=/etc/passwd", headers=headers, json={})
    query_token = client.post("/hub-onboarding/v1/apply?consent_token=url-secret", headers=headers, json={"consent_token": "x" * 32})
    preview = client.post("/hub-onboarding/v1/preview", headers=headers, json={})
    source.write_text(source.read_text() + "\n")
    stale = client.post("/hub-onboarding/v1/apply", headers=headers, json={"consent_token": preview.json()["consent_token"]})

    assert arbitrary_path.status_code == 422
    assert arbitrary_path.json()["error"]["code"] == "invalid_request"
    assert "/etc/passwd" not in arbitrary_path.text
    assert query_path.status_code == 400
    assert query_path.json()["error"]["code"] == "invalid_query"
    assert "/etc/passwd" not in query_path.text
    assert query_token.status_code == 400
    assert query_token.json()["error"]["code"] == "invalid_query"
    assert "url-secret" not in query_token.text
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "snapshot_stale"
    assert stale.json()["error"]["retryable"] is True
    assert not runtime.exists()


def test_onboarding_transport_rejects_unbounded_or_malformed_source_ids(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    common_dir = _repository(repository)
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY)
    onboarding = OnboardingModule(
        application=application,
        repo_key=REPO_KEY,
        repository_common_dir=common_dir,
        history=LocalAgentHistory(home=tmp_path / "empty-home", identity_secret=b"opaque-source-secret"),
    )
    client = TestClient(
        create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY, onboarding=onboarding)
    )
    headers = {"x-codecairn-hub-token": TOKEN}

    for source_id in ("src_" + "a" * 65, "../../private/session.jsonl", "src_" + "G" * 64):
        response = client.post("/hub-onboarding/v1/preview", headers=headers, json={"selected_source_ids": [source_id]})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert source_id not in response.text


def test_live_composition_imports_durably_and_restart_reads_the_same_memory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    runtime = tmp_path / "runtime"
    initialize_repository(start=repository, root=runtime, repo_key=REPO_KEY, retrieval_profile="dashscope")
    home = tmp_path / "client-home"
    _source(home / ".codex/sessions/2026/08/01/session.jsonl", cwd=repository)
    unavailable_executable = tmp_path / "missing-codecairn"
    client = TestClient(build_live_hub(repository, session_token=TOKEN, client_home=home, executable=unavailable_executable))
    headers = {"x-codecairn-hub-token": TOKEN}

    preview = client.post("/hub-onboarding/v1/preview", headers=headers, json={})
    applied = client.post("/hub-onboarding/v1/apply", headers=headers, json={"consent_token": preview.json()["consent_token"]})
    restarted = TestClient(build_live_hub(repository, session_token=TOKEN, client_home=home, executable=unavailable_executable))
    memories = restarted.get("/hub-read/v1/memories", headers=headers)
    after = restarted.post("/hub-onboarding/v1/preview", headers=headers, json={})

    assert preview.status_code == 200
    assert preview.json()["selected_import_count"] == 1
    assert str(home) not in preview.text
    assert applied.status_code == 200
    assert applied.json()["outcome"] == "complete"
    assert applied.json()["totals"]["created_memories"] == 1
    assert applied.json()["index_state"] == "failed"
    assert memories.status_code == 200
    assert memories.json()["page"]["items"][0]["memory_type"] == "task_experience"
    assert after.status_code == 200
    assert after.json()["selected_import_count"] == 0
    assert after.json()["consent_token"] is None
    codex = next(item for item in after.json()["sources"] if item["client"] == "codex")
    assert codex["candidates"][0]["import_state"] == "already_imported"
    assert codex["candidates"][0]["selected"] is False


def test_checked_in_onboarding_example_matches_the_closed_transport_contract() -> None:
    example = json.loads((Path(__file__).parents[3] / "contracts/hub-onboarding/v1.example.json").read_text())
    preview = example["responses"]["preview"]
    apply = example["responses"]["apply"]
    stale = example["responses"]["snapshot_stale"]

    OnboardingPreviewRequest.model_validate(example["requests"]["preview"])
    OnboardingApplyRequest.model_validate(example["requests"]["apply"])
    assert set(preview) == _field_names(OnboardingPreview)
    assert set(preview["retention"]) == _field_names(RetentionPreview)
    assert all(set(item) == _field_names(SourceClientPreview) for item in preview["sources"])
    assert all(
        set(candidate) == _field_names(SourceCandidatePreview) for source in preview["sources"] for candidate in source["candidates"]
    )
    assert "local source locator and import cursor" in preview["retention"]["retained"]
    assert set(apply) == _field_names(OnboardingReport)
    assert set(apply["totals"]) == _field_names(OnboardingTotals)
    assert all(set(item) == _field_names(ImportActionReport) for item in apply["imports"])
    assert all(set(item) == _field_names(CaptureActionReport) for item in apply["capture"])
    assert set(stale) == {"schema_version", "error"}
    assert set(stale["error"]) == {"code", "message", "retryable", "remediation", "request_id"}


def _field_names(value: type) -> set[str]:
    return {field.name for field in fields(value)}
