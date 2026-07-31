import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/", headers = {}, method = "GET") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { accept: "text/html", host: "localhost", ...headers },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

async function html(path) {
  const response = await render(path);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  return response.text();
}

test("renders a Chinese live-memory shell without checked-in product data", async () => {
  const body = await html("/");

  assert.match(body, /<html lang="zh-CN">/i);
  assert.match(body, /<title>CodeCairn 记忆中心<\/title>/i);
  assert.match(body, />记忆</);
  assert.match(body, />接入</);
  assert.match(body, />召回</);
  assert.match(body, />系统</);
  assert.match(body, /<a[^>]+aria-current="page"[^>]+href="\/"[^>]*>记忆</);
  assert.match(body, /<a[^>]+href="\/\?view=recall"[^>]*>召回</);
  assert.match(body, /<a[^>]+href="\/\?view=onboarding"[^>]*>接入</);
  assert.match(body, /<a[^>]+href="\/\?view=system"[^>]*>系统</);
  assert.match(body, /正在读取记忆/);
  assert.match(body, /数据来自本地 CodeCairn 服务/);
  assert.doesNotMatch(body, /示例数据/);
  assert.doesNotMatch(body, /只读原型/);
  assert.doesNotMatch(body, /单元测试足以证明连续性/);
  assert.doesNotMatch(body, /property="og:image"/);
});

test("renders a repository-bound onboarding journey", async () => {
  const body = await html("/?view=onboarding");

  assert.match(
    body,
    /<a[^>]+aria-current="page"[^>]+href="\/\?view=onboarding"[^>]*>接入</,
  );
  assert.match(body, /接入当前仓库的记忆/);
  assert.match(body, /正在发现本机历史/);
  assert.doesNotMatch(body, /选择文件|选择目录|输入路径/);
});

test("renders an isolated static demo as example data only", async () => {
  const body = await html("/?view=demo");

  assert.match(body, /示例演示/);
  assert.match(body, /不读取、不写入当前记忆命名空间/);
  assert.match(body, /来源记录/);
  assert.match(body, /编码记忆/);
  assert.match(body, /演化关系/);
  assert.match(body, /召回解释/);
  assert.match(body, /默认重试次数从 2 次改为 4 次/);
  assert.doesNotMatch(body, /正在读取系统状态|正在发现本机历史/);
});

test("never renders the private loopback token or adapter address", async () => {
  const token = "known-test-token-that-must-stay-server-side";
  const origin = "http://127.0.0.1:54321";
  process.env.CODECAIRN_HUB_TOKEN = token;
  process.env.CODECAIRN_HUB_API_URL = origin;
  try {
    const body = await html("/");

    assert.equal(body.includes(token), false);
    assert.equal(body.includes(origin), false);
  } finally {
    delete process.env.CODECAIRN_HUB_TOKEN;
    delete process.env.CODECAIRN_HUB_API_URL;
  }
});

test("renders a real recall form instead of fixed contract samples", async () => {
  const body = await html("/?view=recall");

  assert.match(body, /输入当前任务/);
  assert.match(
    body,
    /<a[^>]+aria-current="page"[^>]+href="\/\?view=recall"[^>]*>召回</,
  );
  assert.match(body, /<textarea[^>]+id="recall-query"/i);
  assert.match(body, />召回记忆</);
  assert.match(body, /不会预填固定的召回样例/);
  assert.doesNotMatch(body, /相关记忆已接纳/);
  assert.doesNotMatch(body, /无关查询已拒答/);
});

test("renders system diagnostics only as a point-in-time read", async () => {
  const body = await html("/?view=system");

  assert.match(body, /即时系统诊断快照/);
  assert.match(
    body,
    /<a[^>]+aria-current="page"[^>]+href="\/\?view=system"[^>]*>系统</,
  );
  assert.match(body, /正在读取系统状态/);
  assert.doesNotMatch(body, /后台服务常驻/);
  assert.doesNotMatch(body, /远程连接在线/);
});

test("same-origin gateway fails closed when the local backend is absent", async () => {
  const response = await render("/api/hub-read/v1/system");

  assert.equal(response.status, 503);
  const payload = await response.json();
  assert.equal(payload.error.code, "hub_unavailable");
  assert.match(payload.error.message, /尚未启动/);
});

test("same-origin gateway exposes no speculative mutation route", async () => {
  const response = await render("/api/hub-read/v1/remember");

  assert.equal(response.status, 404);
  const payload = await response.json();
  assert.equal(payload.error.code, "not_found");
});

test("onboarding gateway exposes only the two consent-bound operations", async () => {
  const preview = await render(
    "/api/hub-onboarding/v1/preview",
    { "content-type": "application/json" },
    "POST",
  );
  assert.equal(preview.status, 503);
  assert.equal((await preview.json()).error.code, "hub_unavailable");

  const query = await render(
    "/api/hub-onboarding/v1/preview?consent_token=must-not-be-forwarded",
    { "content-type": "application/json" },
    "POST",
  );
  assert.equal(query.status, 400);
  assert.equal((await query.json()).error.code, "invalid_query");

  for (const [path, method] of [
    ["/api/hub-onboarding/v1/preview", "GET"],
    ["/api/hub-onboarding/v1/apply", "GET"],
    ["/api/hub-onboarding/v1/discover", "POST"],
    ["/api/hub-read/v1/preview", "POST"],
  ]) {
    const rejected = await render(path, {}, method);
    assert.equal(rejected.status, 404);
    assert.equal((await rejected.json()).error.code, "not_found");
  }
});

test("same-origin gateway rejects DNS-rebinding and cross-origin requests", async () => {
  for (const headers of [
    { host: "attacker.example:3000" },
    {
      host: "127.0.0.1:3000",
      origin: "http://attacker.example:3000",
    },
    {
      host: "127.0.0.1:3000",
      origin: "http://127.0.0.1:4000",
    },
    {
      host: "127.0.0.1:3000",
      "sec-fetch-site": "cross-site",
    },
    {
      host: "127.0.0.1:3000",
      "x-forwarded-host": "127.0.0.1:3000",
    },
  ]) {
    const response = await render("/api/hub-read/v1/system", headers);

    assert.equal(response.status, 403);
    const payload = await response.json();
    assert.equal(payload.error.code, "untrusted_browser_origin");
  }
});
