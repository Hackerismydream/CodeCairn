from __future__ import annotations

from pathlib import Path

from codecairn_hub_api.app import create_hub_app
from codecairn_hub_api.queries import RecallReadiness
from fastapi.testclient import TestClient

from codecairn.bootstrap import create_application, create_myna_application
from codecairn.service.application import RememberRequest
from tests.retrieval_fakes import TEST_RETRIEVAL

TOKEN = "test-session-token-with-32-characters"
REPO_A = "github.com/acme/alpha"
REPO_B = "github.com/acme/beta"
FIXTURE = Path(__file__).parents[3] / "tests/fixtures/codex/failed_command.jsonl"
READY = RecallReadiness(profile="injected", state="configuration_ready", live_checked=False, remediation=None)


def _preference(root: Path, *, repo_key: str, subject: str, content: str):
    application = create_application(root, repo_key=repo_key, retrieval_adapters=TEST_RETRIEVAL)
    application.import_session(FIXTURE, repo_key=repo_key, index=False, boundary_kind="manual_finalize")
    experience = next(memory for memory in application.list_memories(repo_key=repo_key) if memory.memory_type == "task_experience")
    source_fact = next(fact for fact in experience.facts if fact.role == "user")
    preference = application.remember_direct(
        RememberRequest(
            repo_key=repo_key,
            memory_type="user_preference",
            title="Response language",
            content=content,
            category="workflow",
            subject_key=subject,
            source_fact_ids=(source_fact.fact_id,),
        )
    )
    application.sync_index(worker_id=f"hub-{repo_key}")
    return preference


def _client(root: Path, *, repo_key: str) -> TestClient:
    application = create_application(root, repo_key=repo_key, retrieval_adapters=TEST_RETRIEVAL)
    library = create_myna_application(root, repository_key=repo_key, retrieval_adapters=TEST_RETRIEVAL)
    return TestClient(
        create_hub_app(application=application, repo_key=repo_key, session_token=TOKEN, recall_readiness=READY, library=library)
    )


def test_hub_projects_server_bound_person_scope_and_promotion_eligibility(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    preference = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")

    response = _client(root, repo_key=REPO_A).get(
        "/hub-read/v1/memories", headers={"x-codecairn-hub-token": TOKEN}, params={"selected_memory_id": preference.memory_id}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["library_context"]["current_repository_key"] == REPO_A
    assert payload["library_context"]["active_scopes"] == ["global", "repository"]
    assert payload["library_context"]["person_id"].startswith("person_")
    assert payload["selected"]["governance"] == {"state": "eligible", "eligible": True, "promotion_id": None, "error_code": None}


def test_promotion_route_accepts_only_memory_id_and_replays_idempotently(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    preference = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    client = _client(root, repo_key=REPO_A)
    headers = {"x-codecairn-hub-token": TOKEN}

    created = client.post("/hub-governance/v1/preferences/promote", headers=headers, json={"memory_id": preference.memory_id})
    repeated = client.post("/hub-governance/v1/preferences/promote", headers=headers, json={"memory_id": preference.memory_id})
    injected = client.post(
        "/hub-governance/v1/preferences/promote",
        headers=headers,
        json={"memory_id": preference.memory_id, "person_id": f"person_{'0' * 64}"},
    )

    assert created.status_code == repeated.status_code == 200
    assert created.json()["receipt"]["outcome"] == "created"
    assert repeated.json()["receipt"]["outcome"] == "already_promoted"
    assert created.json()["receipt"]["promotion"] == repeated.json()["receipt"]["promotion"]
    assert injected.status_code == 422
    assert injected.json()["error"]["code"] == "invalid_request"


def test_hub_recall_reports_global_source_client_and_repository_shadowing(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    global_preference = _preference(
        root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese for every repository."
    )
    _client(root, repo_key=REPO_A).post(
        "/hub-governance/v1/preferences/promote",
        headers={"x-codecairn-hub-token": TOKEN},
        json={"memory_id": global_preference.memory_id},
    )
    client = _client(root, repo_key=REPO_B)

    global_result = client.post(
        "/hub-read/v1/recall", headers={"x-codecairn-hub-token": TOKEN}, json={"query": "Which language should the response use?"}
    ).json()["result"]
    local = _preference(
        root, repo_key=REPO_B, subject="response-language", content="For this repository, write release notes in English."
    )
    shadowed_result = client.post(
        "/hub-read/v1/recall", headers={"x-codecairn-hub-token": TOKEN}, json={"query": "Which language should release notes use?"}
    ).json()["result"]

    selected = next(item for item in global_result["sidecar"]["ranked"] if item["memory_id"] == global_preference.memory_id)
    assert selected["effective_scope"] == "global"
    assert selected["source"]["repository_key"] == REPO_A
    assert global_result["sidecar"]["requesting_client"] == "hub"
    assert global_result["sidecar"]["active_scopes"] == ["global", "repository"]
    assert global_preference.memory_id not in {item["memory_id"] for item in shadowed_result["sidecar"]["ranked"]}
    assert shadowed_result["sidecar"]["shadowed"][0]["shadowed_by_memory_ids"] == [local.memory_id]


def test_hub_memories_can_browse_and_select_global_preferences(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    promoted = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    _client(root, repo_key=REPO_A).post(
        "/hub-governance/v1/preferences/promote", headers={"x-codecairn-hub-token": TOKEN}, json={"memory_id": promoted.memory_id}
    )

    response = _client(root, repo_key=REPO_B).get(
        "/hub-read/v1/memories",
        headers={"x-codecairn-hub-token": TOKEN},
        params={"scope": "global", "selected_memory_id": promoted.memory_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["memory_id"] for item in payload["page"]["items"]] == [promoted.memory_id]
    assert payload["page"]["items"][0]["effective_scope"] == "global"
    assert payload["page"]["items"][0]["source_repository_key"] == REPO_A
    assert payload["selected"]["detail"]["memory"]["memory_id"] == promoted.memory_id
    assert payload["selected"]["effective_scope"] == "global"
    assert payload["selected"]["source_repository_key"] == REPO_A
    assert "governance" not in payload["selected"]


def test_source_repository_projects_its_promoted_preference_as_global(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    promoted = _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    client = _client(root, repo_key=REPO_A)
    headers = {"x-codecairn-hub-token": TOKEN}
    assert (
        client.post("/hub-governance/v1/preferences/promote", headers=headers, json={"memory_id": promoted.memory_id}).status_code
        == 200
    )

    payload = client.get("/hub-read/v1/memories", headers=headers, params={"scope": "global"}).json()

    assert [item["memory_id"] for item in payload["page"]["items"]] == [promoted.memory_id]
    assert payload["page"]["items"][0]["shadowed_by_memory_ids"] == []
    assert payload["selected"]["effective_scope"] == "global"


def test_myna_composition_preserves_historical_hub_recall(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _preference(root, repo_key=REPO_A, subject="response-language", content="Reply in concise Chinese.")
    response = _client(root, repo_key=REPO_A).post(
        "/hub-read/v1/recall", headers={"x-codecairn-hub-token": TOKEN}, json={"query": "response language", "include_superseded": True}
    )

    assert response.status_code == 200
    assert "person_id" not in response.json()["result"]["sidecar"]
