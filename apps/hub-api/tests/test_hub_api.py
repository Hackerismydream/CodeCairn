from __future__ import annotations

import pytest
from codecairn_hub_api.app import create_hub_app
from codecairn_hub_api.queries import RecallReadiness
from fastapi.testclient import TestClient

from codecairn.bootstrap import create_application
from codecairn.memory.config import RetrievalConfig
from codecairn.service.application import RememberRequest
from tests.retrieval_fakes import TEST_RETRIEVAL

REPO_KEY = "github.com/Hackerismydream/CodeCairn"
TOKEN = "test-session-token-with-32-characters"
READY = RecallReadiness(profile="injected", state="configuration_ready", live_checked=False, remediation=None)


def test_memories_view_combines_page_detail_and_history(tmp_path) -> None:
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY, retrieval_adapters=TEST_RETRIEVAL)
    memory = application.remember_direct(
        RememberRequest(
            repo_key=REPO_KEY,
            memory_type="repository_knowledge",
            title="重启恢复必须跨进程验证",
            content="连续性需要在全新进程中验证。",
            subject_key="restart-recovery",
        )
    )
    client = TestClient(create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY))

    response = client.get("/hub-read/v1/memories", headers={"x-codecairn-hub-token": TOKEN})

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["repo_key"] == REPO_KEY
    assert payload["page"]["items"] == [
        {
            "memory_id": memory.memory_id,
            "memory_type": "repository_knowledge",
            "title": "重启恢复必须跨进程验证",
            "status": "active",
            "created_at_ms": memory.created_at_ms,
        }
    ]
    assert payload["selected"]["detail"]["memory"]["memory_id"] == memory.memory_id
    assert payload["selected"]["detail"]["resource_uri"] == f"codecairn://memory/{memory.memory_id}"
    assert payload["selected"]["history"]["statuses"] == [[memory.memory_id, "active"]]


def test_recall_view_runs_the_real_admission_pipeline(tmp_path) -> None:
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY, retrieval_adapters=TEST_RETRIEVAL)
    memory = application.remember_direct(
        RememberRequest(
            repo_key=REPO_KEY,
            memory_type="repository_knowledge",
            title="重启恢复必须跨进程验证",
            content="连续性需要在全新进程中验证。",
            subject_key="restart-recovery",
        )
    )
    application.sync_index(worker_id="hub-test")
    client = TestClient(create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY))

    response = client.post(
        "/hub-read/v1/recall", headers={"x-codecairn-hub-token": TOKEN}, json={"query": memory.title, "limit": 10, "token_budget": 2048}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["result"]["sidecar"]["admission_trace"]["outcome"] == "admitted"
    assert payload["result"]["sidecar"]["ranked"][0]["memory_id"] == memory.memory_id
    assert memory.content in payload["result"]["markdown"]


def test_system_view_is_a_sanitized_point_in_time_doctor_snapshot(tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    application = create_application(runtime_root, repo_key=REPO_KEY, retrieval_adapters=TEST_RETRIEVAL)
    application.remember_direct(
        RememberRequest(
            repo_key=REPO_KEY,
            memory_type="repository_knowledge",
            title="本地状态",
            content="系统页只展示当前可观察状态。",
            subject_key="system-snapshot",
        )
    )
    client = TestClient(create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY))

    response = client.get("/hub-read/v1/system", headers={"x-codecairn-hub-token": TOKEN})

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["repo_key"] == REPO_KEY
    assert payload["counts"]["memories"] == 1
    assert payload["status"] in {"ok", "degraded"}
    assert "observed_at_ms" in payload
    assert payload["recall_readiness"] == {
        "profile": "injected",
        "state": "configuration_ready",
        "live_checked": False,
        "remediation": None,
    }
    assert "root" not in payload
    assert str(runtime_root) not in response.text


def test_system_view_separates_missing_recall_key_from_unchecked_doctor_status(tmp_path) -> None:
    application = create_application(
        tmp_path / "runtime", repo_key=REPO_KEY, retrieval=RetrievalConfig.default("dashscope"), environment={}
    )
    missing_key = RecallReadiness(
        profile="dashscope",
        state="missing_key",
        live_checked=False,
        remediation="Set CODECAIRN_EMBEDDING_API_KEY or DASHSCOPE_API_KEY and restart the Hub.",
    )
    client = TestClient(create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=missing_key))

    response = client.get("/hub-read/v1/system", headers={"x-codecairn-hub-token": TOKEN})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["subsystems"]["config"]["status"] == "ok"
    assert payload["providers"]["retrieval"] == "dashscope"
    assert payload["providers"]["retrieval_state"] == "configured"
    assert payload["recall_readiness"]["state"] == "missing_key"
    assert "CODECAIRN_EMBEDDING_API_KEY" in payload["recall_readiness"]["remediation"]


def test_hub_requires_its_ephemeral_session_token_and_never_caches_reads(tmp_path) -> None:
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY, retrieval_adapters=TEST_RETRIEVAL)
    with pytest.raises(ValueError, match="at least 32 characters"):
        create_hub_app(application=application, repo_key=REPO_KEY, session_token="short", recall_readiness=READY)
    client = TestClient(create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY))

    rejected = client.get("/hub-read/v1/memories")
    accepted = client.get("/hub-read/v1/memories", headers={"x-codecairn-hub-token": TOKEN})

    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "unauthorized"
    assert rejected.json()["error"]["request_id"]
    assert accepted.status_code == 200
    assert accepted.headers["cache-control"] == "no-store"

    public_schema = client.get("/openapi.json")
    assert public_schema.status_code == 404


def test_unknown_memory_uses_a_stable_error_envelope(tmp_path) -> None:
    application = create_application(tmp_path / "runtime", repo_key=REPO_KEY, retrieval_adapters=TEST_RETRIEVAL)
    client = TestClient(
        create_hub_app(application=application, repo_key=REPO_KEY, session_token=TOKEN, recall_readiness=READY),
        raise_server_exceptions=False,
    )

    response = client.get(
        "/hub-read/v1/memories", headers={"x-codecairn-hub-token": TOKEN}, params={"selected_memory_id": f"mem_{'0' * 64}"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "memory_not_found"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["error"]["request_id"] == response.headers["x-codecairn-request-id"]
