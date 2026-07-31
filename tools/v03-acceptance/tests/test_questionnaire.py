from __future__ import annotations

import html
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from codecairn_v03_acceptance.campaign import PRESENTATION_SNAPSHOT_PATH, CampaignRequest, start_campaign
from codecairn_v03_acceptance.questionnaire import ParticipantQuestionnaire, ReviewerQuestionnaire


def test_questionnaire_collects_original_human_answers_without_exposing_rubric(tmp_path: Path) -> None:
    protocol = Path(__file__).parents[1] / "protocols" / "hub-comprehension-v1.json"
    handle = start_campaign(
        CampaignRequest(
            protocol_path=protocol,
            output_root=tmp_path / "runs",
            run_id="questionnaire-pilot-001",
            codecairn_commit="1" * 40,
            pico_commit="2" * 40,
            delivery_mode="source_checkout",
        )
    )
    hub_snapshot = handle.artifact_dir / PRESENTATION_SNAPSHOT_PATH
    hub_snapshot.parent.mkdir(parents=True, exist_ok=True)
    hub_snapshot.write_text(
        json.dumps({"contract": "codecairn.v03-acceptance.hub-snapshot.v1", "views": ["memories", "recall", "system"]}),
        encoding="utf-8",
    )
    machine_observation = {
        "pico": {"task_a": {"session_id": "cli:v03-learn", "captured_memory_ids": ["mem_" + "1" * 64]}},
        "codecairn": {"evidence_reference_memory_ids": ["mem_" + "1" * 64]},
        "hub": {
            "recall_memory_ids": ["mem_" + "1" * 64],
            "supersessions": [{"predecessor_id": "mem_" + "2" * 64, "successor_id": "mem_" + "3" * 64}],
        },
    }
    machine_path = handle.artifact_dir / "machine" / "observation.json"
    machine_path.parent.mkdir(parents=True, exist_ok=True)
    machine_path.write_text(json.dumps(machine_observation), encoding="utf-8")
    submission_enabled = False

    def guard_submission() -> None:
        if not submission_enabled:
            raise ValueError("Hub 展示已变化")

    questionnaire = ParticipantQuestionnaire(
        campaign_dir=handle.artifact_dir,
        participant_id="P001",
        hub_origin="http://127.0.0.1:39001",
        hub_snapshot=hub_snapshot,
        submission_guard=guard_submission,
    )
    thread = threading.Thread(target=questionnaire.serve, daemon=True)
    thread.start()

    with urllib.request.urlopen(questionnaire.origin, timeout=5) as response:
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
        page = response.read().decode()

    assert "Agent 记住了什么" in page
    assert "打开 CodeCairn 记忆中心" in page
    assert "默认重试次数为什么发生变化，当时怎样验证？" in page
    assert "准确概括目标和结果" not in page
    csrf = html.unescape(re.search(r'name="csrf" value="([^"]+)"', page).group(1))  # type: ignore[union-attr]
    encoded = urllib.parse.urlencode(
        {
            "csrf": csrf,
            "remembered": "记住了重试次数从 2 改成 4，并且测试通过。",
            "provenance": "来自 Pico 会话及其 Evidence Reference。",
            "recall_reason": "当前问题与重试决策相关，因此被接纳。",
            "lifecycle": "旧记忆是 superseded，新记忆 active；不是删除。",
            "no_prior_exposure": "yes",
            "not_contributor": "yes",
            "target_learner": "yes",
            "consent": "yes",
        }
    ).encode()
    request = urllib.request.Request(
        f"{questionnaire.origin}/submit",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": questionnaire.origin},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as blocked:
        urllib.request.urlopen(request, timeout=5)
    assert blocked.value.code == 400
    assert not (handle.artifact_dir / "participants" / "P001" / "response.json").exists()
    submission_enabled = True
    with urllib.request.urlopen(request, timeout=5) as response:
        confirmation = response.read().decode()
    thread.join(timeout=5)

    assert "回答已封存" in confirmation
    assert not thread.is_alive()
    recorded = json.loads((handle.artifact_dir / "participants" / "P001" / "response.json").read_text(encoding="utf-8"))
    assert recorded["collector"] == "questionnaire"
    assert recorded["participant_kind"] == "human"
    assert recorded["moderator_content_hint_count"] == 0
    assert recorded["eligibility"] == {"codecairn_contributor": False, "prior_codecairn_exposure": False, "target_learner": True}
    assert recorded["answers"]["remembered"]["answer"].startswith("记住了重试次数")
    assert recorded["presentation"]["hub_snapshot_path"] == PRESENTATION_SNAPSHOT_PATH

    reviewer = ReviewerQuestionnaire(campaign_dir=handle.artifact_dir, participant_id="P001", reviewer_id="R001")
    reviewer_thread = threading.Thread(target=reviewer.serve, daemon=True)
    reviewer_thread.start()
    with urllib.request.urlopen(reviewer.origin, timeout=5) as response:
        review_page = response.read().decode()
    assert "cli:v03-learn" in review_page
    assert "mem_" + "1" * 64 in review_page
    assert "记住了重试次数从 2 改成 4" in review_page
    review_csrf = html.unescape(re.search(r'name="csrf" value="([^"]+)"', review_page).group(1))  # type: ignore[union-attr]
    review_fields: dict[str, str] = {"csrf": review_csrf, "independent": "yes", "rubric_only": "yes"}
    for question_id in ("remembered", "provenance", "recall_reason", "lifecycle"):
        review_fields[f"{question_id}_verdict"] = "pass"
        review_fields[f"{question_id}_reason"] = "accurate"
    review_request = urllib.request.Request(
        f"{reviewer.origin}/submit",
        data=urllib.parse.urlencode(review_fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": reviewer.origin},
        method="POST",
    )
    with urllib.request.urlopen(review_request, timeout=5) as response:
        assert "评审已封存" in response.read().decode()
    reviewer_thread.join(timeout=5)

    review = json.loads((handle.artifact_dir / "reviews" / "P001.json").read_text(encoding="utf-8"))
    assert review["collector"] == "questionnaire"
    assert review["reviewer_id"] == "R001"
    assert len(review["response_sha256"]) == 64
    assert review["reviewer_attestation"]["independent_from_participant"] is True
    assert review["ratings"]["lifecycle"] == {"reason_code": "accurate", "verdict": "pass"}
