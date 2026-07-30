import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/?view=overview", {
      headers: { accept: "text/html" },
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

test("server-renders the Chinese CodeCairn Hub review artifact", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /<title>CodeCairn 记忆中心<\/title>/i);
  assert.match(html, />概览</);
  assert.match(html, /只读原型/);
  assert.match(html, /示例数据/);
  assert.match(html, /从来源事件到可审计上下文/);
  assert.match(html, /当前边界/);
  assert.match(html, /尚无本地 HTTP 展示层/);
  assert.doesNotMatch(html, /Pico 已连接/);
  assert.doesNotMatch(html, /今天 12 次召回/);
  assert.doesNotMatch(html, /记忆服务在线/);
  assert.doesNotMatch(html, /DESIGN DIRECTION/);
  assert.doesNotMatch(html, /Your site is taking shape/);
});
