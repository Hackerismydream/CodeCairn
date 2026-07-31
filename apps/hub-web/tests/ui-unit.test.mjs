import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const exampleUrl = new URL(
  "../../../contracts/hub-read/v1.example.json",
  import.meta.url,
);
let vite;

before(async () => {
  vite = await createServer({
    root,
    configFile: false,
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
});

after(async () => {
  await vite.close();
});

async function load(path) {
  return vite.ssrLoadModule(path);
}

async function contract() {
  return JSON.parse(await readFile(exampleUrl, "utf8"));
}

function response(payload, status = 200) {
  return new Response(
    typeof payload === "string" ? payload : JSON.stringify(payload),
    {
      status,
      headers: {
        "content-type": "application/json",
        "x-codecairn-request-id": "hubreq_test",
      },
    },
  );
}

async function capturedMemoryResponse({
  provider = "pico",
  sessionId = "pico:test",
  outcome = "success",
  exitCode = 0,
} = {}) {
  const { memories } = structuredClone((await contract()).responses);
  const reference = {
    fact_id: "fact_result",
    provider,
    session_id: sessionId,
    source_generation: 1,
    event_index: 1,
    event_id: "event_result",
    source_path_sha256: "a".repeat(64),
    event_sha256: "b".repeat(64),
  };
  memories.selected.detail.memory.origin = "capture";
  memories.selected.detail.memory.evidence = [reference];
  memories.selected.detail.memory.facts = [{
    schema_version: 1,
    fact_id: "fact_result",
    repo_key: memories.repo_key,
    episode_id: "ep_task_a",
    reference,
    fact_kind: "command_result",
    role: null,
    value: "python -m unittest -v",
    attributes: {
      command_fact_id: "fact_command",
      outcome,
      exit_code: exitCode,
    },
    fact_ordinal: 0,
  }];
  return { memories, reference };
}

test("the HTTP client accepts the checked-in version 1 responses", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const { responses } = await contract();
  const payloads = [
    responses.memories,
    responses.recall_admitted,
    responses.system,
  ];
  let index = 0;
  const client = createHttpHubClient(async () => response(payloads[index++]));

  assert.equal((await client.memories()).repo_key, responses.memories.repo_key);
  assert.equal(
    (await client.recall({ query: "test" })).result.sidecar.repo_key,
    responses.recall_admitted.result.sidecar.repo_key,
  );
  assert.equal((await client.system()).repo_key, responses.system.repo_key);
});

test("the HTTP client rejects invalid command-result exit codes", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  for (const exitCode of ["0", 0.5]) {
    const { memories } = await capturedMemoryResponse({ exitCode });
    const client = createHttpHubClient(async () => response(memories));

    await assert.rejects(client.memories(), (error) => {
      assert.equal(error.code, "invalid_response");
      return true;
    });
  }
});

test("the HTTP client rejects an unknown evidence provider", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const { memories } = await capturedMemoryResponse({
    provider: "untrusted-provider",
    sessionId: "foreign:test",
  });
  const client = createHttpHubClient(async () => response(memories));

  await assert.rejects(client.memories(), (error) => {
    assert.equal(error.code, "invalid_response");
    return true;
  });
});

test("the HTTP client rejects an unknown observed outcome", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const { memories } = await capturedMemoryResponse({ outcome: "passed" });
  const client = createHttpHubClient(async () => response(memories));

  await assert.rejects(client.memories(), (error) => {
    assert.equal(error.code, "invalid_response");
    return true;
  });
});

test("the HTTP client requires normalized command-result attributes", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  for (const attribute of ["outcome", "command_fact_id"]) {
    const { memories } = await capturedMemoryResponse();
    delete memories.selected.detail.memory.facts[0].attributes[attribute];
    const client = createHttpHubClient(async () => response(memories));

    await assert.rejects(client.memories(), (error) => {
      assert.equal(error.code, "invalid_response");
      return true;
    });
  }
});

test("the HTTP client rejects malformed successful responses without leaking them", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const malformed = [
    { schema_version: 2 },
    {
      schema_version: 1,
      repo_key: "repo",
      page: { schema_version: 1, repo_key: "repo", items: "secret-payload" },
      selected: null,
    },
    "not-json",
  ];

  for (const payload of malformed) {
    const client = createHttpHubClient(async () => response(payload));
    await assert.rejects(client.memories(), (error) => {
      assert.equal(error.code, "invalid_response");
      assert.equal(error.retryable, false);
      assert.doesNotMatch(error.message, /secret-payload/);
      return true;
    });
  }
});

test("the HTTP client requires explicit recall readiness", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const { system } = (await contract()).responses;
  const withoutReadiness = { ...system };
  delete withoutReadiness.recall_readiness;
  const client = createHttpHubClient(async () => response(withoutReadiness));

  await assert.rejects(client.system(), (error) => {
    assert.equal(error.code, "invalid_response");
    assert.equal(error.retryable, false);
    return true;
  });
});

test("the HTTP client rejects unknown recall explanation states", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const { recall_admitted: admitted } = (await contract()).responses;
  const mutations = [
    (sidecar) => { sidecar.freshness = "ready"; },
    (sidecar) => { sidecar.semantic_state = "disabled"; },
    (sidecar) => { sidecar.semantic_state = "pending"; },
    (sidecar) => { sidecar.admission_trace.reason = "always_return"; },
    (sidecar) => { sidecar.omissions = [{ memory_id: "mem_unknown", reason: "mystery" }]; },
  ];

  for (const mutate of mutations) {
    const payload = structuredClone(admitted);
    mutate(payload.result.sidecar);
    const client = createHttpHubClient(async () => response(payload));
    await assert.rejects(client.recall({ query: "test" }), (error) => {
      assert.equal(error.code, "invalid_response");
      return true;
    });
  }
});

test("the HTTP client preserves a structured server error", async () => {
  const { createHttpHubClient } = await load("/lib/hub/http-client.ts");
  const client = createHttpHubClient(async () =>
    response(
      {
        schema_version: 1,
        error: {
          code: "cursor_invalid",
          message: "bad cursor",
          retryable: true,
          remediation: "return to page one",
          request_id: "hubreq_body",
        },
      },
      400,
    ),
  );

  await assert.rejects(client.memories(), (error) => {
    assert.equal(error.code, "cursor_invalid");
    assert.equal(error.retryable, true);
    assert.equal(error.remediation, "return to page one");
    assert.equal(error.requestId, "hubreq_body");
    return true;
  });
});

test("the HTTP client rejects malformed error envelopes safely", async () => {
  const [{ createHttpHubClient }, { default: RequestError }] =
    await Promise.all([
      load("/lib/hub/http-client.ts"),
      load("/app/components/RequestError.tsx"),
    ]);
  const client = createHttpHubClient(async () =>
    response(
      {
        schema_version: 1,
        error: {
          code: "internal_error",
          message: "unsafe",
          retryable: "no",
          remediation: { secret: "must-not-render" },
          request_id: { bad: true },
        },
      },
      500,
    ),
  );

  let failure;
  try {
    await client.system();
    assert.fail("expected an invalid response error");
  } catch (error) {
    failure = error;
  }

  assert.equal(failure.code, "invalid_response");
  assert.equal(failure.retryable, false);
  const html = renderToStaticMarkup(
    React.createElement(RequestError, { error: failure }),
  );
  assert.doesNotMatch(html, /must-not-render|unsafe/);
  assert.doesNotMatch(html, /<button/);
});

test("RequestError removes retry controls for permanent errors", async () => {
  const [{ default: RequestError }, { HubApiError }] = await Promise.all([
    load("/app/components/RequestError.tsx"),
    load("/lib/hub/client.ts"),
  ]);
  const retry = () => {};
  const permanent = renderToStaticMarkup(
    React.createElement(RequestError, {
      error: new HubApiError("missing", {
        code: "provider_not_configured",
        retryable: false,
      }),
      retry,
    }),
  );
  const transient = renderToStaticMarkup(
    React.createElement(RequestError, {
      error: new HubApiError("offline", {
        code: "hub_unavailable",
        retryable: true,
      }),
      retry,
    }),
  );
  const unknown = renderToStaticMarkup(
    React.createElement(RequestError, {
      error: new Error("network"),
      retry,
    }),
  );
  const recoverableAction = renderToStaticMarkup(
    React.createElement(RequestError, {
      action: retry,
      actionLabel: "刷新列表",
      error: new HubApiError("removed", {
        code: "memory_not_found",
        retryable: false,
      }),
      retry,
    }),
  );

  assert.doesNotMatch(permanent, /<button/);
  assert.match(permanent, /请先完成检索提供方配置/);
  assert.match(transient, />重新读取</);
  assert.match(unknown, />重新读取</);
  assert.match(recoverableAction, />刷新列表</);
});

test("memory controls suppress no-op filters, in-flight paging, and stale responses", async () => {
  const {
    createRequestGate,
    memoryFilterDisabled,
    memoryPaginationDisabled,
    retryFromFirstPage,
  } = await load("/lib/hub/memory-state.ts");

  assert.equal(memoryFilterDisabled(false, false, true), true);
  assert.equal(memoryFilterDisabled(false, false, false), false);
  assert.equal(memoryPaginationDisabled(true, false, true), true);
  assert.equal(memoryPaginationDisabled(false, false, true), false);
  assert.equal(retryFromFirstPage("cursor_invalid"), true);
  assert.equal(retryFromFirstPage("hub_unavailable"), false);

  const gate = createRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
  gate.invalidate();
  assert.equal(gate.isCurrent(second), false);
});

test("recall processing distinguishes completed retrieval from later memory refinement", async () => {
  const { recallProcessingLabel } = await load(
    "/app/features/recall/RecallView.tsx",
  );

  assert.equal(
    recallProcessingLabel("semantic_pending", "pending"),
    "本次关键词与向量检索已完成；后续记忆提炼待处理",
  );
  assert.equal(
    recallProcessingLabel("semantic_pending", "failed"),
    "本次召回来自已同步索引；后续记忆提炼失败",
  );
  assert.equal(
    recallProcessingLabel("fresh", "complete"),
    "本次检索与后续记忆提炼均已完成",
  );
  assert.equal(
    recallProcessingLabel("fresh", "pending"),
    "本次关键词与向量检索已完成；后续记忆提炼待处理",
  );
});

test("navigation, unknown timestamps, and system labels stay human-readable", async () => {
  const [navigation, format, system, client] = await Promise.all([
    load("/lib/hub/navigation.ts"),
    load("/lib/hub/format.ts"),
    load("/lib/hub/system-display.ts"),
    load("/lib/hub/client.ts"),
  ]);

  assert.equal(navigation.parseHubView("recall"), "recall");
  assert.equal(navigation.parseHubView("unknown"), "memories");
  assert.equal(navigation.hubViewHref("memories"), "/");
  assert.equal(navigation.hubViewHref("system"), "/?view=system");
  assert.equal(format.formatTime(0), "源事件未提供可信时间");
  assert.equal(format.dateTimeValue(0), undefined);
  assert.equal(system.systemValueLabel("network"), "网络");
  assert.equal(system.systemValueLabel("memory text"), "记忆文本");
  assert.equal(
    system.recallReadinessLabel("configuration_ready", false),
    "已配置（未联机检查）",
  );
  assert.equal(
    system.recallReadinessIndicatorClass("configuration_ready", false),
    "status-quiet",
  );
  assert.equal(
    system.recallReadinessIndicatorClass("missing_key", false),
    "status-failed",
  );
  assert.equal(system.queueIndicatorClass("stale", 1), "status-failed");
  assert.equal(
    system.providerIndicatorClass("retrieval_state", "missing_key"),
    "status-failed",
  );
  assert.equal(
    client.isHubConnectionFailure(
      new client.HubApiError("mismatch", {
        code: "invalid_response",
        retryable: false,
      }),
    ),
    false,
  );
});

test("memory details separate recall lifecycle from event evidence", async () => {
  const { default: MemoryInspector } = await load(
    "/app/features/memories/MemoryInspector.tsx",
  );
  const { memories } = (await contract()).responses;
  const html = renderToStaticMarkup(
    React.createElement(MemoryInspector, { selected: memories.selected }),
  );

  assert.match(html, /召回状态：默认召回中/);
  assert.match(html, /证据状态：未附证据事实/);
  assert.match(html, /这是智能体声明，不代表已经附有证据事实/);
  assert.match(html, /<details class="payload-block"><summary>结构化内容<\/summary>/);
});

test("memory details foreground the source agent, session, and observed command result", async () => {
  const { default: MemoryInspector } = await load(
    "/app/features/memories/MemoryInspector.tsx",
  );
  const { memories, reference } = await capturedMemoryResponse({
    sessionId: "pico:synthetic-user-study:task-a",
  });
  const selected = memories.selected;
  const fileReference = {
    ...reference,
    fact_id: "fact_file",
    event_id: "event_file",
  };
  selected.detail.memory.evidence.push(fileReference);
  selected.detail.memory.facts.push({
    schema_version: 1,
    fact_id: "fact_file",
    repo_key: memories.repo_key,
    episode_id: "ep_task_a",
    reference: fileReference,
    fact_kind: "file_change",
    role: null,
    value: "retry_policy.py",
    attributes: { path: "retry_policy.py", change_kind: "update" },
    fact_ordinal: 1,
  });
  const html = renderToStaticMarkup(
    React.createElement(MemoryInspector, { selected }),
  );
  const superseded = renderToStaticMarkup(
    React.createElement(MemoryInspector, {
      selected: {
        ...selected,
        detail: {
          ...selected.detail,
          status: "superseded",
        },
      },
    },
  ),
  );

  assert.match(html, /证据状态：已附 2 条证据事实/);
  assert.match(html, /来源 Agent：Pico/);
  assert.match(html, /会话：.*pico:synthetic-user-study:task-a/);
  assert.match(html, /首条命令结果证据：.*python -m unittest -v/);
  assert.match(html, /该次结果：成功 · 退出码 0/);
  assert.match(html, /首条文件变更证据：.*retry_policy.py/);
  assert.match(superseded, /召回状态：默认不召回/);
  assert.match(superseded, /证据状态：已附 2 条证据事实/);
});
