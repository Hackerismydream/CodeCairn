"""Loopback-only Chinese questionnaire for the irreducible human evidence seam."""

from __future__ import annotations

import html
import secrets
import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json
from codecairn_v03_acceptance.campaign import (
    PRESENTATION_SNAPSHOT_PATH,
    QUESTION_IDS,
    _record_questionnaire_response,
    _record_questionnaire_review,
)


class _LoopbackQuestionnaire:
    _confirmation_html: str
    _server: ThreadingHTTPServer
    origin: str

    def _bind_server(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if not owner._trusted(self) or self.path != "/":
                    owner._text(self, 404, "页面不存在")
                    return
                owner._html(self, 200, owner._page())

            def do_POST(self) -> None:
                if not owner._trusted(self) or self.path != "/submit":
                    owner._text(self, 403, "请求来源不可信")
                    return
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    owner._text(self, 400, "提交格式无效")
                    return
                if content_type != "application/x-www-form-urlencoded" or not 0 < length <= 64 * 1024:
                    owner._text(self, 400, "提交格式无效")
                    return
                try:
                    fields = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True, strict_parsing=True)
                    owner._submit(fields)
                except (UnicodeDecodeError, ValueError) as error:
                    owner._text(self, 400, str(error))
                    return
                owner._html(self, 200, owner._confirmation_html)
                threading.Thread(target=owner._server.shutdown, daemon=True).start()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        port = cast(tuple[str, int], self._server.server_address)[1]
        self.origin = f"http://127.0.0.1:{port}"

    def serve(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.05)
        finally:
            self._server.server_close()

    def _trusted(self, request: BaseHTTPRequestHandler) -> bool:
        expected_authority = self.origin.removeprefix("http://")
        origin = request.headers.get("Origin")
        return request.headers.get("Host") == expected_authority and origin in {None, self.origin}

    def _page(self) -> str:
        raise NotImplementedError

    def _submit(self, fields: dict[str, list[str]]) -> None:
        raise NotImplementedError

    @staticmethod
    def _html(request: BaseHTTPRequestHandler, status: int, content: str) -> None:
        _LoopbackQuestionnaire._respond(request, status, content, "text/html; charset=utf-8")

    @staticmethod
    def _text(request: BaseHTTPRequestHandler, status: int, content: str) -> None:
        _LoopbackQuestionnaire._respond(request, status, content, "text/plain; charset=utf-8")

    @staticmethod
    def _respond(request: BaseHTTPRequestHandler, status: int, content: str, content_type: str) -> None:
        body = content.encode()
        request.send_response(status)
        request.send_header("Content-Type", content_type)
        request.send_header("Content-Length", str(len(body)))
        request.send_header("Cache-Control", "no-store")
        request.send_header(
            "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
        )
        request.send_header("X-Frame-Options", "DENY")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.end_headers()
        request.wfile.write(body)


class ParticipantQuestionnaire(_LoopbackQuestionnaire):
    """Serve one participant form and stop after its response is sealed."""

    _confirmation_html = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>回答已封存</title>"
        "<body><main><h1>回答已封存</h1><p>谢谢。请关闭这个页面，不要查看其他参与者的答案。</p></main></body></html>"
    )

    def __init__(
        self,
        *,
        campaign_dir: Path,
        participant_id: str,
        hub_origin: str,
        hub_snapshot: Path,
        submission_guard: Callable[[], None],
        moderator_content_hint_count: int = 0,
    ) -> None:
        if moderator_content_hint_count < 0:
            raise ValueError("moderator_content_hint_count must not be negative")
        parsed_hub = urllib.parse.urlsplit(hub_origin)
        if (
            parsed_hub.scheme != "http"
            or parsed_hub.hostname != "127.0.0.1"
            or parsed_hub.port is None
            or parsed_hub.path not in {"", "/"}
        ):
            raise ValueError("questionnaire Hub origin must be loopback HTTP")
        protocol = read_json(campaign_dir / "protocol.json")
        manifest = read_json(campaign_dir / "manifest.json")
        expected_snapshot = (campaign_dir / PRESENTATION_SNAPSHOT_PATH).resolve()
        if (
            hub_snapshot.resolve() != expected_snapshot
            or not expected_snapshot.is_file()
            or expected_snapshot.is_symlink()
            or not isinstance(manifest, dict)
        ):
            raise ValueError("questionnaire requires the frozen Campaign Hub snapshot")
        if not isinstance(protocol, dict) or not isinstance(protocol.get("questions"), list):
            raise ValueError("campaign protocol cannot render a questionnaire")
        scenario = protocol.get("scenario")
        if not isinstance(scenario, dict) or not isinstance(scenario.get("recall_query"), str):
            raise ValueError("campaign protocol cannot render the fixed Recall query")
        questions: list[tuple[str, str]] = []
        for value in protocol["questions"]:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not isinstance(value.get("prompt"), str):
                raise ValueError("campaign question cannot be rendered")
            questions.append((value["id"], value["prompt"]))
        if tuple(question_id for question_id, _prompt in questions) != QUESTION_IDS:
            raise ValueError("campaign questionnaire does not contain the frozen four questions")
        self._campaign_dir = campaign_dir
        self._participant_id = participant_id
        self._hub_origin = hub_origin
        self._candidate_sha256 = canonical_sha256(manifest)
        self._hub_snapshot_sha256 = file_sha256(expected_snapshot)
        self._moderator_content_hint_count = moderator_content_hint_count
        self._questions = tuple(questions)
        self._recall_query = scenario["recall_query"]
        self._submission_guard = submission_guard
        self._csrf_token = secrets.token_urlsafe(32)
        self._bind_server()

    def _submit(self, fields: dict[str, list[str]]) -> None:
        if fields.get("csrf") != [self._csrf_token]:
            raise ValueError("提交令牌无效，请刷新页面重试")
        answers: dict[str, dict[str, str]] = {}
        for question_id in QUESTION_IDS:
            values = fields.get(question_id)
            if values is None or len(values) != 1 or not values[0].strip() or len(values[0]) > 4_000:
                raise ValueError("四个问题都必须用自己的话回答")
            answers[question_id] = {"answer": values[0].strip()}
        self._submission_guard()
        _record_questionnaire_response(
            self._campaign_dir,
            {
                "schema_version": 1,
                "contract": "codecairn.v03-acceptance.participant-response.v1",
                "participant_id": self._participant_id,
                "participant_kind": "human",
                "moderator_content_hint_count": self._moderator_content_hint_count,
                "eligibility": {
                    "prior_codecairn_exposure": fields.get("no_prior_exposure") != ["yes"],
                    "codecairn_contributor": fields.get("not_contributor") != ["yes"],
                    "target_learner": fields.get("target_learner") == ["yes"],
                },
                "consent_to_local_evidence": fields.get("consent") == ["yes"],
                "presentation": {
                    "candidate_sha256": self._candidate_sha256,
                    "hub_snapshot_path": PRESENTATION_SNAPSHOT_PATH,
                    "hub_snapshot_sha256": self._hub_snapshot_sha256,
                },
                "answers": answers,
            },
        )

    def _page(self) -> str:
        questions = "".join(
            f"<label><strong>{index}. {html.escape(prompt)}</strong>"
            f"<textarea name='{html.escape(question_id)}' maxlength='4000' required></textarea></label>"
            for index, (question_id, prompt) in enumerate(self._questions, start=1)
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodeCairn 记忆理解验收</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }}
    body {{ margin: 0; background: #f5f5f7; color: #1d1d1f; }}
    main {{ max-width: 760px; margin: 48px auto; padding: 0 20px 64px; }}
    section {{ background: white; border: 1px solid #e5e5e7; border-radius: 18px; padding: 28px; }}
    h1 {{ font-size: 30px; margin: 0 0 8px; }}
    p {{ color: #6e6e73; line-height: 1.6; }}
    a {{ color: #06c; }}
    label {{ display: block; margin-top: 24px; line-height: 1.5; }}
    textarea {{ width: 100%; min-height: 92px; box-sizing: border-box; margin-top: 10px; padding: 12px;
      border: 1px solid #d2d2d7; border-radius: 12px; font: inherit; resize: vertical; }}
    .checks label {{ margin-top: 12px; }}
    button {{ margin-top: 28px; border: 0; border-radius: 999px; padding: 11px 20px;
      background: #0071e3; color: white; font: inherit; font-weight: 600; }}
  </style>
</head>
<body><main><section>
  <h1>记忆理解验收</h1>
  <p>参与者 {html.escape(self._participant_id)}。请先独立浏览
    <a href="{html.escape(self._hub_origin)}" target="_blank" rel="noopener">打开 CodeCairn 记忆中心</a>，
    再用自己的话回答。请勿向主持人询问答案。</p>
  <p>在“召回”页使用本次统一问题：<code>{html.escape(self._recall_query)}</code></p>
  <form method="post" action="/submit">
    <input type="hidden" name="csrf" value="{html.escape(self._csrf_token)}">
    {questions}
    <div class="checks">
      <label><input type="checkbox" name="no_prior_exposure" value="yes" required> 我此前从未接触 CodeCairn</label>
      <label><input type="checkbox" name="not_contributor" value="yes" required> 我从未为 CodeCairn 贡献代码、文档或产品设计</label>
      <label><input type="checkbox" name="target_learner" value="yes" required> 我属于本项目面向的学习者</label>
      <label><input type="checkbox" name="consent" value="yes" required> 我同意在本机保存匿名回答用于本次验收</label>
    </div>
    <button type="submit">封存回答</button>
  </form>
</section></main></body></html>"""


class ReviewerQuestionnaire(_LoopbackQuestionnaire):
    """Serve one anonymous participant response to one human blind reviewer."""

    _confirmation_html = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>评审已封存</title>"
        "<body><main><h1>评审已封存</h1><p>这份判断会与参与者原始回答分开保存。</p></main></body></html>"
    )

    def __init__(self, *, campaign_dir: Path, participant_id: str, reviewer_id: str) -> None:
        protocol = read_json(campaign_dir / "protocol.json")
        response = read_json(campaign_dir / "participants" / participant_id / "response.json")
        if not isinstance(protocol, dict) or not isinstance(protocol.get("questions"), list):
            raise ValueError("campaign protocol cannot render a review")
        if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
            raise ValueError("participant response cannot be reviewed")
        rubric = protocol.get("rubric")
        if not isinstance(rubric, dict) or not isinstance(rubric.get("id"), str):
            raise ValueError("campaign rubric cannot render a review")
        observation = read_json(campaign_dir / "machine" / "observation.json")
        if not isinstance(observation, dict):
            raise ValueError("campaign machine evidence cannot render a review")
        questions: list[tuple[str, str, str, str]] = []
        answers = cast(dict[str, object], response["answers"])
        for raw_question in protocol["questions"]:
            if (
                not isinstance(raw_question, dict)
                or not isinstance(raw_question.get("id"), str)
                or not isinstance(raw_question.get("prompt"), str)
                or not isinstance(raw_question.get("pass_criterion"), str)
            ):
                raise ValueError("campaign review question is invalid")
            question_id = raw_question["id"]
            answer = answers.get(question_id)
            if not isinstance(answer, dict) or not isinstance(answer.get("answer"), str):
                raise ValueError("participant answer cannot be reviewed")
            questions.append((question_id, raw_question["prompt"], raw_question["pass_criterion"], answer["answer"]))
        if tuple(question_id for question_id, _prompt, _criterion, _answer in questions) != QUESTION_IDS:
            raise ValueError("campaign review does not contain the frozen four questions")
        self._campaign_dir = campaign_dir
        self._participant_id = participant_id
        self._reviewer_id = reviewer_id
        self._rubric_id = rubric["id"]
        self._response_sha256 = file_sha256(campaign_dir / "participants" / participant_id / "response.json")
        self._questions = tuple(questions)
        self._ground_truth = _review_ground_truth(observation)
        self._csrf_token = secrets.token_urlsafe(32)
        self._bind_server()

    def _submit(self, fields: dict[str, list[str]]) -> None:
        if fields.get("csrf") != [self._csrf_token]:
            raise ValueError("提交令牌无效，请刷新页面重试")
        if fields.get("independent") != ["yes"] or fields.get("rubric_only") != ["yes"]:
            raise ValueError("评审人必须确认独立性并只使用冻结 Rubric")
        ratings: dict[str, dict[str, str]] = {}
        for question_id in QUESTION_IDS:
            verdict = fields.get(f"{question_id}_verdict")
            reason = fields.get(f"{question_id}_reason")
            if verdict is None or len(verdict) != 1 or reason is None or len(reason) != 1:
                raise ValueError("四个问题都必须完成判断")
            ratings[question_id] = {"verdict": verdict[0], "reason_code": reason[0]}
        _record_questionnaire_review(
            self._campaign_dir,
            {
                "schema_version": 1,
                "contract": "codecairn.v03-acceptance.review.v1",
                "participant_id": self._participant_id,
                "reviewer_id": self._reviewer_id,
                "reviewer_kind": "human",
                "reviewer_attestation": {"independent_from_participant": True, "used_frozen_rubric_only": True},
                "response_sha256": self._response_sha256,
                "rubric_id": self._rubric_id,
                "ratings": ratings,
            },
        )

    def _page(self) -> str:
        questions = "".join(
            f"""<article>
  <h2>{index}. {html.escape(prompt)}</h2>
  <p><strong>通过标准：</strong>{html.escape(criterion)}</p>
  <blockquote>{html.escape(answer)}</blockquote>
  <label>判断
    <select name="{html.escape(question_id)}_verdict" required>
      <option value="">请选择</option><option value="pass">通过</option><option value="fail">不通过</option>
    </select>
  </label>
  <label>理由代码
    <select name="{html.escape(question_id)}_reason" required>
      <option value="">请选择</option><option value="accurate">准确</option>
      <option value="inaccurate">不准确</option><option value="unsupported">缺少依据</option>
    </select>
  </label>
</article>"""
            for index, (question_id, prompt, criterion, answer) in enumerate(self._questions, start=1)
        )
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeCairn 盲审</title>
<style>
body {{ margin: 0; background: #f5f5f7; color: #1d1d1f; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }}
main {{ max-width: 760px; margin: 48px auto; padding: 0 20px 64px; }}
section {{ background: white; border: 1px solid #e5e5e7; border-radius: 18px; padding: 28px; }}
article {{ padding: 20px 0; border-top: 1px solid #e5e5e7; }} h1 {{ margin-top: 0; }} h2 {{ font-size: 18px; }}
p, blockquote {{ line-height: 1.6; }} blockquote {{ margin: 12px 0; padding: 14px; background: #f5f5f7; border-radius: 10px; }}
label {{ display: inline-block; margin: 8px 18px 0 0; }} select {{ margin-left: 8px; font: inherit; }}
button {{ margin-top: 24px; border: 0; border-radius: 999px; padding: 11px 20px;
  background: #0071e3; color: white; font: inherit; font-weight: 600; }}
</style></head><body><main><section>
<h1>匿名回答盲审</h1><p>参与者 {html.escape(self._participant_id)}。请只依据冻结 Rubric、机器事实和原始回答判断。</p>
<aside><h2>冻结的机器事实</h2><pre>{html.escape(self._ground_truth)}</pre></aside>
<form method="post" action="/submit"><input type="hidden" name="csrf" value="{html.escape(self._csrf_token)}">
{questions}
<label><input type="checkbox" name="independent" value="yes" required> 我不是该参与者，并独立完成本次评审</label>
<label><input type="checkbox" name="rubric_only" value="yes" required> 我只依据冻结 Rubric、机器事实和原始回答判断</label>
<button type="submit">封存评审</button></form>
</section></main></body></html>"""


def _review_ground_truth(observation: dict[str, object]) -> str:
    pico = observation.get("pico")
    codecairn = observation.get("codecairn")
    hub = observation.get("hub")
    if not isinstance(pico, dict) or not isinstance(codecairn, dict) or not isinstance(hub, dict):
        raise ValueError("campaign machine evidence is incomplete")
    task_a = pico.get("task_a")
    if not isinstance(task_a, dict):
        raise ValueError("campaign Pico evidence is incomplete")
    truth = {
        "learn_session_id": task_a.get("session_id"),
        "captured_memory_ids": task_a.get("captured_memory_ids"),
        "evidence_reference_memory_ids": codecairn.get("evidence_reference_memory_ids"),
        "hub_selected_memory_id": hub.get("selected_memory_id"),
        "hub_selected_evidence_fact_ids": hub.get("selected_evidence_fact_ids"),
        "hub_selected_evidence_references": hub.get("selected_evidence_references"),
        "recall_memory_ids": hub.get("recall_memory_ids"),
        "recall_admission": hub.get("recall_admission"),
        "recall_omissions": hub.get("recall_omissions"),
        "supersessions": hub.get("supersessions"),
    }
    return "\n".join(f"{key}: {json_value}" for key, json_value in truth.items())
