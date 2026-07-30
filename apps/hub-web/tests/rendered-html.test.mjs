import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/", headers = {}) {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
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
  assert.match(body, />召回</);
  assert.match(body, />系统</);
  assert.match(body, /<a[^>]+aria-current="page"[^>]+href="\/"[^>]*>记忆</);
  assert.match(body, /<a[^>]+href="\/\?view=recall"[^>]*>召回</);
  assert.match(body, /<a[^>]+href="\/\?view=system"[^>]*>系统</);
  assert.match(body, /正在读取记忆/);
  assert.match(body, /数据来自本地 CodeCairn 服务/);
  assert.doesNotMatch(body, /示例数据/);
  assert.doesNotMatch(body, /只读原型/);
  assert.doesNotMatch(body, /单元测试足以证明连续性/);
  assert.doesNotMatch(body, /property="og:image"/);
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
