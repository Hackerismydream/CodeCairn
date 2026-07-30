import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const fixtureUrl = new URL("../fixtures/codecairn-contract.json", import.meta.url);

async function fixture() {
  return JSON.parse(await readFile(fixtureUrl, "utf8"));
}

function escaped(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
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

async function html(path) {
  const response = await render(path);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  return response.text();
}

test("defaults to the Chinese read-only memory view", async () => {
  const body = await html("/");
  const snapshot = await fixture();
  const detail = snapshot.outputs.memory_detail;

  assert.match(body, /<html lang="zh-CN">/i);
  assert.match(body, /<title>CodeCairn 记忆中心<\/title>/i);
  assert.match(body, />记忆</);
  assert.match(body, />召回</);
  assert.match(body, />系统</);
  assert.match(body, /只读原型/);
  assert.match(body, /示例数据/);
  assert.match(body, new RegExp(escaped(detail.memory.title)));
  assert.match(body, new RegExp(escaped(detail.memory.content)));
  assert.match(body, new RegExp(escaped(detail.resource_uri)));
  assert.match(body, /智能体声明/);
  assert.doesNotMatch(body, /捕获生成.*2023-11-14/s);
  assert.doesNotMatch(body, />概览</);
  assert.doesNotMatch(body, /发起召回/);
  assert.doesNotMatch(body, /Doctor 快照正常/);
  assert.doesNotMatch(body, /当前产品接入面/);
  assert.doesNotMatch(body, /Pico 集成证据/);
  assert.doesNotMatch(body, /origin\/main/);
  assert.doesNotMatch(body, /property="og:image"/);
});

test("renders the admitted recall contract without a fake run form", async () => {
  const body = await html("/?view=recall&sample=admitted");
  const snapshot = await fixture();
  const sidecar = snapshot.outputs.recall_admitted.sidecar;

  assert.match(body, /相关记忆已接纳/);
  assert.match(body, /无关查询已拒答/);
  assert.match(body, /sidecar\.admission_trace/);
  assert.match(body, new RegExp(escaped(sidecar.admission_trace.reason)));
  assert.match(body, new RegExp(escaped(sidecar.admission_trace.policy)));
  assert.match(body, new RegExp(escaped(sidecar.context_trace.renderer)));
  assert.match(body, new RegExp(escaped(sidecar.retrieval_profile)));
  assert.match(body, new RegExp(escaped(sidecar.ranked[0].memory_id)));
  assert.match(body, new RegExp(escaped(sidecar.query)));
  assert.match(body, new RegExp(escaped(sidecar.ranked[0].title)));
  assert.match(body, new RegExp(escaped(sidecar.ranked[0].snippets[0].text)));
  assert.ok(body.includes(snapshot.outputs.recall_admitted.markdown));
  assert.doesNotMatch(body, /运行召回/);
  assert.doesNotMatch(body, /<input\b/i);
});

test("renders the explicit unrelated-query abstention contract", async () => {
  const body = await html("/?view=recall&sample=abstained");
  const snapshot = await fixture();
  const sidecar = snapshot.outputs.recall_abstained.sidecar;

  assert.match(body, /没有记忆进入上下文/);
  assert.match(body, new RegExp(escaped(sidecar.query)));
  assert.match(body, new RegExp(escaped(sidecar.admission_trace.reason)));
  assert.match(body, /No relevant memory was admitted\./);
  assert.ok(body.includes(snapshot.outputs.recall_abstained.markdown));
  assert.match(body, /未接纳任何记忆/);
  assert.match(body, /相关性不足/);
});

test("renders Doctor as a point-in-time system snapshot", async () => {
  const body = await html("/?view=system");
  const snapshot = await fixture();
  const doctor = snapshot.outputs.doctor;

  assert.match(body, /核心存储与队列正常/);
  assert.match(body, /处理队列/);
  assert.match(body, /Provider 与隐私/);
  assert.match(body, new RegExp(escaped(doctor.schema)));
  assert.match(body, new RegExp(`${doctor.hook_receipts.total} 回执`));
  assert.doesNotMatch(body, /最近 Hook 回执/);
  assert.doesNotMatch(body, /Codex · stop/);
  assert.match(body, /不代表后台服务常驻或远程连接在线/);
  assert.doesNotMatch(body, /Hub 连接层/);
});

test("renders immutable evolution enums from the executable contract", async () => {
  const body = await html("/?view=memories&detail=evolution");
  const snapshot = await fixture();
  const evolution = snapshot.outputs.memory_history.evolutions[0];

  assert.match(body, new RegExp(escaped(evolution.relation_kind)));
  assert.match(body, new RegExp(escaped(evolution.proposer)));
  assert.match(body, new RegExp(escaped(evolution.reason)));
  assert.match(body, new RegExp(escaped(evolution.predecessor_id.slice(0, 12))));
  assert.match(body, new RegExp(escaped(evolution.successor_id.slice(0, 12))));
});
