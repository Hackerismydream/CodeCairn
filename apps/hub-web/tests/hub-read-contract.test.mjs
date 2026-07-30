import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const exampleUrl = new URL(
  "../../../contracts/hub-read/v1.example.json",
  import.meta.url,
);

async function example() {
  return JSON.parse(await readFile(exampleUrl, "utf8"));
}

test("version 1 example covers the three browser-facing operations", async () => {
  const contract = await example();

  assert.equal(contract.contract, "codecairn/hub-read-v1");
  assert.equal(contract.schema_version, 1);
  assert.deepEqual(Object.keys(contract.responses).sort(), [
    "memories",
    "recall_abstained",
    "recall_admitted",
    "system",
  ]);
  assert.equal(
    contract.evidence_boundary.browser_connected_to_runtime,
    false,
  );
});

test("memories example has the exact list and inspector fields the UI reads", async () => {
  const { memories } = (await example()).responses;
  const selected = memories.selected;

  assert.equal(memories.repo_key, memories.page.repo_key);
  assert.equal(memories.page.items.length, 2);
  assert.deepEqual(
    [...new Set(memories.page.items.map((item) => item.status))].sort(),
    ["active", "superseded"],
  );
  assert.equal(selected.detail.status, "active");
  assert.match(selected.detail.resource_uri, /^codecairn:\/\/memory\/mem_/);
  assert.equal(selected.history.evolutions.length, 1);
  assert.ok(selected.history.memories.length >= 2);
  assert.ok(selected.detail.memory.payload.subject_key);
});

test("recall and system examples preserve explanation and privacy fields", async () => {
  const { recall_admitted: admitted, recall_abstained: abstained, system } =
    (await example()).responses;
  const admittedSidecar = admitted.result.sidecar;
  const abstainedSidecar = abstained.result.sidecar;

  assert.equal(admittedSidecar.admission_trace.outcome, "admitted");
  assert.equal(
    admittedSidecar.context_trace.renderer,
    "codecairn/typed-excerpt-context-v2",
  );
  assert.ok(admittedSidecar.ranked[0].snippets[0].text);
  assert.match(admitted.result.markdown, /codecairn:\/\/memory\/mem_/);
  assert.equal(abstainedSidecar.admission_trace.outcome, "abstained");
  assert.equal(abstainedSidecar.ranked.length, 0);
  assert.ok(
    abstainedSidecar.omissions.some(
      (omission) => omission.reason === "relevance",
    ),
  );

  assert.equal(system.schema_version, 1);
  assert.equal(system.counts.memories, 2);
  assert.deepEqual(system.recall_readiness, {
    live_checked: false,
    profile: "deterministic-offline-test",
    remediation: null,
    state: "configuration_ready",
  });
  assert.ok(system.subsystems.markdown);
  assert.ok(system.providers.retrieval);
  assert.ok(system.privacy.storage);
  assert.equal(Object.hasOwn(system, "root"), false);
});
