import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const fixtureUrl = new URL("../fixtures/codecairn-contract.json", import.meta.url);

async function fixture() {
  return JSON.parse(await readFile(fixtureUrl, "utf8"));
}

test("checked-in executable fixture preserves the current CodeCairn recall contract", async () => {
  const snapshot = await fixture();
  const outputs = snapshot.outputs;
  const admitted = outputs.recall_admitted.sidecar;
  const abstained = outputs.recall_abstained.sidecar;

  assert.equal(snapshot.fixture_kind, "deterministic_read_contract");
  assert.equal(
    snapshot.evidence_boundary.browser_connected_to_runtime,
    false,
  );
  assert.equal(
    admitted.context_trace.renderer,
    "codecairn/typed-excerpt-context-v2",
  );
  assert.equal(admitted.admission_trace.outcome, "admitted");
  assert.equal(abstained.admission_trace.outcome, "abstained");
  assert.equal(abstained.admission_trace.reason, "below_threshold");
  assert.equal(
    admitted.query,
    outputs.memory_detail.memory.title,
  );
  assert.equal(
    admitted.ranked[0].title,
    outputs.memory_detail.memory.title,
  );
  assert.equal(
    admitted.ranked[0].summary,
    outputs.memory_detail.memory.content,
  );
  assert.match(
    outputs.recall_admitted.markdown,
    new RegExp(outputs.memory_detail.memory.memory_id),
  );
  assert.match(
    outputs.recall_admitted.markdown,
    new RegExp(outputs.memory_detail.memory.content),
  );
  assert.equal(abstained.ranked.length, 0);
  assert.ok(
    abstained.omissions.some((omission) => omission.reason === "relevance"),
  );
  assert.match(
    outputs.recall_abstained.markdown,
    /No relevant memory was admitted\./,
  );
});

test("snapshot covers the read surfaces selected for the prototype", async () => {
  const snapshot = await fixture();
  const outputs = snapshot.outputs;

  assert.deepEqual(Object.keys(outputs).sort(), [
    "doctor",
    "list",
    "memory_detail",
    "memory_history",
    "memory_page",
    "memory_show",
    "recall_abstained",
    "recall_admitted",
  ]);
  assert.equal(outputs.list.length, 2);
  assert.deepEqual(
    [...new Set(outputs.memory_page.items.map((item) => item.status))].sort(),
    ["active", "superseded"],
  );
  assert.equal(outputs.memory_detail.status, "active");
  assert.match(outputs.memory_detail.resource_uri, /^codecairn:\/\/memory\/mem_/);
  assert.equal(outputs.memory_history.evolutions.length, 1);
  assert.equal(outputs.doctor.repo_key, "github.com/Hackerismydream/CodeCairn");
  assert.equal(outputs.doctor.memories, 2);
});
