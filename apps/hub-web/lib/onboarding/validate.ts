import { HubApiError } from "../hub/client";
import type {
  CaptureClientKind,
  ContinuousState,
  HistoricalState,
  OnboardingApplyResult,
  OnboardingClientKind,
  OnboardingPreview,
} from "./types";

type JsonObject = Record<string, unknown>;
type RequestId = string | null;

const CLIENTS = new Set<OnboardingClientKind>(["codex", "claude", "pico"]);
const CAPTURE_CLIENTS = new Set<CaptureClientKind>(["codex", "claude"]);
const HISTORICAL_STATES = new Set<HistoricalState>(["available", "none_found", "unsupported", "unresolved"]);
const CONTINUOUS_STATES = new Set<ContinuousState>(["available", "installed", "not_detected", "manual_setup_required", "unsupported"]);
const IMPORT_STATES = new Set(["new", "incremental", "already_imported"]);
const IMPORT_OUTCOMES = new Set(["imported", "noop", "failed"]);
const CAPTURE_OUTCOMES = new Set(["installed", "already_installed", "failed"]);
const APPLY_OUTCOMES = new Set(["complete", "noop", "partial", "failed"]);
const INDEX_STATES = new Set(["ready", "pending", "failed", "not_requested"]);
const EGRESS_STATES = new Set(["none", "memory_text_to_embedding"]);
const CAPTURE_EVENTS = new Set(["stop", "session_end"]);
const EXPECTED_CAPTURE_EVENT = { codex: "stop", claude: "session_end" } as const;

const KEYS = {
  preview: ["schema_version", "repo_key", "snapshot_id", "expires_at_ms", "consent_token", "selected_import_count", "truncated", "retention", "sources", "planned_writes"],
  retention: ["revision", "retained", "omitted", "source_content_egress"],
  source: ["client", "historical_state", "continuous_state", "capture_selected", "candidates", "unresolved_count", "invalid_count", "remediation"],
  candidate: ["source_id", "session_label", "raw_event_count", "estimated_bytes", "latest_activity_ms", "import_state", "selected"],
  apply: ["schema_version", "snapshot_id", "repo_key", "outcome", "imports", "capture", "totals", "index_state", "requires_new_preview"],
  import: ["source_id", "client", "outcome", "created_memory_count", "skipped_memory_count", "error_code"],
  capture: ["client", "outcome", "event", "error_code"],
  totals: ["imported_sessions", "created_memories", "skipped_sessions", "failed_actions"],
} as const;

const RETAINED = ["local source locator and import cursor", "normalized Agent Trace facts", "bounded Evidence Facts", "derived Coding Memory"];
const OMITTED = ["provider credentials", "full provider-native transcript copy"];

function incompatible(requestId: RequestId): never {
  throw new HubApiError("Hub returned an incompatible onboarding response.", {
    code: "invalid_response",
    retryable: false,
    remediation: "请确认 Hub 前后端版本一致后重新启动。",
    requestId,
  });
}

function expect(condition: unknown, requestId: RequestId): asserts condition {
  if (!condition) incompatible(requestId);
}

function object(value: unknown, keys: readonly string[], requestId: RequestId): JsonObject {
  expect(typeof value === "object" && value !== null && !Array.isArray(value), requestId);
  const result = value as JsonObject;
  const actual = Object.keys(result).sort();
  const expected = [...keys].sort();
  expect(actual.length === expected.length && actual.every((key, index) => key === expected[index]), requestId);
  return result;
}

function string(value: unknown, requestId: RequestId): string {
  expect(typeof value === "string" && value.length > 0, requestId);
  return value;
}

function bounded(value: unknown, maximum: number, requestId: RequestId): string {
  const result = string(value, requestId);
  expect(result.length <= maximum && !/[\u0000-\u001f\u007f]/.test(result), requestId);
  return result;
}

function opaqueId(value: unknown, prefix: "src_" | "onb_", requestId: RequestId): string {
  const result = string(value, requestId);
  expect(result.startsWith(prefix) && /^[0-9a-f]{64}$/.test(result.slice(4)), requestId);
  return result;
}

function nullableString(value: unknown, requestId: RequestId): string | null {
  return value === null ? null : string(value, requestId);
}

function errorCode(value: unknown, requestId: RequestId): string | null {
  const result = nullableString(value, requestId);
  expect(result === null || /^[a-z][a-z0-9_]{0,63}$/.test(result), requestId);
  return result;
}

function count(value: unknown, requestId: RequestId): number {
  expect(typeof value === "number" && Number.isSafeInteger(value) && value >= 0, requestId);
  return value;
}

function boolean(value: unknown, requestId: RequestId): boolean {
  expect(typeof value === "boolean", requestId);
  return value;
}

function array(value: unknown, maximum: number, requestId: RequestId): unknown[] {
  expect(Array.isArray(value) && value.length <= maximum, requestId);
  return value;
}

function strings(value: unknown, requestId: RequestId): string[] {
  const result = array(value, 256, requestId).map((item) => string(item, requestId));
  expect(new Set(result).size === result.length, requestId);
  return result;
}

function literal<T extends string>(value: unknown, allowed: Set<T>, requestId: RequestId): T {
  expect(typeof value === "string" && allowed.has(value as T), requestId);
  return value as T;
}

export function validateOnboardingPreview(
  value: unknown,
  requestId: RequestId = null,
): OnboardingPreview {
  const root = object(value, KEYS.preview, requestId);
  expect(root.schema_version === 1, requestId);
  bounded(root.repo_key, 512, requestId);
  opaqueId(root.snapshot_id, "onb_", requestId);
  count(root.expires_at_ms, requestId);
  boolean(root.truncated, requestId);

  const consentToken = nullableString(root.consent_token, requestId);
  expect(
    consentToken === null ||
      (consentToken.length >= 16 && consentToken.length <= 256 && /^[A-Za-z0-9_-]+$/.test(consentToken)),
    requestId,
  );
  const selectedImportCount = count(root.selected_import_count, requestId);
  const retention = object(root.retention, KEYS.retention, requestId);
  expect(retention.revision === "codecairn.onboarding.retention.v1", requestId);
  expect(strings(retention.retained, requestId).join("\0") === RETAINED.join("\0"), requestId);
  expect(strings(retention.omitted, requestId).join("\0") === OMITTED.join("\0"), requestId);
  literal(retention.source_content_egress, EGRESS_STATES, requestId);

  const sourceValues = array(root.sources, CLIENTS.size, requestId);
  expect(sourceValues.length === CLIENTS.size, requestId);
  const seenClients = new Set<OnboardingClientKind>();
  const candidateIds = new Set<string>();
  const captureClients: CaptureClientKind[] = [];
  let selectedCandidates = 0;

  for (const value of sourceValues) {
    const source = object(value, KEYS.source, requestId);
    const client = literal(source.client, CLIENTS, requestId);
    expect(!seenClients.has(client), requestId);
    seenClients.add(client);
    const historical = literal(source.historical_state, HISTORICAL_STATES, requestId);
    const continuous = literal(source.continuous_state, CONTINUOUS_STATES, requestId);
    const selectedCapture = boolean(source.capture_selected, requestId);
    expect(
      !selectedCapture || (client !== "pico" && ["available", "installed"].includes(continuous)),
      requestId,
    );
    if (selectedCapture) captureClients.push(client as CaptureClientKind);

    const unresolvedCount = count(source.unresolved_count, requestId);
    count(source.invalid_count, requestId);
    if (source.remediation !== null) bounded(source.remediation, 256, requestId);
    const candidates = array(source.candidates, 256 - candidateIds.size, requestId);
    expect(
      (historical === "available") === (candidates.length > 0) &&
        (historical !== "none_found" || unresolvedCount === 0) &&
        (historical !== "unresolved" || unresolvedCount > 0) &&
        (client !== "pico" ||
          (historical === "unsupported" &&
            continuous === "manual_setup_required" &&
            !selectedCapture)),
      requestId,
    );

    for (const candidateValue of candidates) {
      const candidate = object(candidateValue, KEYS.candidate, requestId);
      const sourceId = opaqueId(candidate.source_id, "src_", requestId);
      expect(!candidateIds.has(sourceId), requestId);
      candidateIds.add(sourceId);
      const label = bounded(candidate.session_label, 32, requestId);
      const expectedLabel = `${client[0].toUpperCase()}${client.slice(1)} session ${sourceId.slice(-8)}`;
      expect(label === expectedLabel, requestId);
      count(candidate.raw_event_count, requestId);
      count(candidate.estimated_bytes, requestId);
      if (candidate.latest_activity_ms !== null) count(candidate.latest_activity_ms, requestId);
      literal(candidate.import_state, IMPORT_STATES, requestId);
      if (boolean(candidate.selected, requestId)) selectedCandidates += 1;
    }
  }

  expect(
    seenClients.size === CLIENTS.size &&
      captureClients.length <= CAPTURE_CLIENTS.size &&
      selectedCandidates === selectedImportCount &&
      (selectedCandidates > 0 || captureClients.length > 0) === (consentToken !== null),
    requestId,
  );
  const planned = strings(root.planned_writes, requestId);
  const importPlan = selectedCandidates
    ? `Import ${selectedCandidates} owned historical session${selectedCandidates === 1 ? "" : "s"}`
    : null;
  const capturePlans = new Set(
    captureClients.map((client) => `Install explicit ${client} continuous capture`),
  );
  const observedCapture = planned.slice(importPlan ? 1 : 0);
  expect(
    (importPlan === null || planned[0] === importPlan) &&
      observedCapture.length === capturePlans.size &&
      observedCapture.every((item) => capturePlans.has(item)),
    requestId,
  );
  return value as OnboardingPreview;
}

export function validateOnboardingApplyResult(
  value: unknown,
  activePreview: OnboardingPreview,
  requestId: RequestId = null,
): OnboardingApplyResult {
  const root = object(value, KEYS.apply, requestId);
  expect(root.schema_version === 1, requestId);
  expect(
    opaqueId(root.snapshot_id, "onb_", requestId) === activePreview.snapshot_id &&
      bounded(root.repo_key, 512, requestId) === activePreview.repo_key,
    requestId,
  );
  const outcome = literal(root.outcome, APPLY_OUTCOMES, requestId);
  const indexState = literal(root.index_state, INDEX_STATES, requestId);
  const requiresNewPreview = boolean(root.requires_new_preview, requestId);
  const selectedSources = new Map(
    activePreview.sources.flatMap((source) =>
      source.candidates
        .filter((candidate) => candidate.selected)
        .map((candidate) => [candidate.source_id, source.client] as const),
    ),
  );
  const importValues = array(root.imports, 256, requestId);
  expect(importValues.length === selectedSources.size, requestId);

  const importIds = new Set<string>();
  let importedSessions = 0;
  let createdMemories = 0;
  let skippedSessions = 0;
  let failedActions = 0;
  let changedActions = 0;
  let successfulImports = 0;
  for (const value of importValues) {
    const report = object(value, KEYS.import, requestId);
    const sourceId = opaqueId(report.source_id, "src_", requestId);
    expect(!importIds.has(sourceId), requestId);
    importIds.add(sourceId);
    const client = literal(report.client, CAPTURE_CLIENTS, requestId);
    expect(selectedSources.get(sourceId) === client, requestId);
    const itemOutcome = literal(report.outcome, IMPORT_OUTCOMES, requestId);
    const created = count(report.created_memory_count, requestId);
    count(report.skipped_memory_count, requestId);
    const code = errorCode(report.error_code, requestId);
    expect(
      (itemOutcome === "failed") === (code !== null) &&
        (itemOutcome === "imported") === (created > 0),
      requestId,
    );
    importedSessions += Number(itemOutcome === "imported");
    skippedSessions += Number(itemOutcome === "noop");
    failedActions += Number(itemOutcome === "failed");
    changedActions += Number(itemOutcome === "imported");
    successfulImports += Number(itemOutcome !== "failed");
    createdMemories += created;
  }

  const selectedCapture = new Set(
    activePreview.sources.filter((source) => source.capture_selected).map((source) => source.client),
  );
  const captureValues = array(root.capture, CAPTURE_CLIENTS.size, requestId);
  expect(captureValues.length === selectedCapture.size, requestId);
  const captureClients = new Set<CaptureClientKind>();
  for (const value of captureValues) {
    const report = object(value, KEYS.capture, requestId);
    const client = literal(report.client, CAPTURE_CLIENTS, requestId);
    expect(!captureClients.has(client) && selectedCapture.has(client), requestId);
    captureClients.add(client);
    const itemOutcome = literal(report.outcome, CAPTURE_OUTCOMES, requestId);
    const event = literal(report.event, CAPTURE_EVENTS, requestId);
    expect(event === EXPECTED_CAPTURE_EVENT[client], requestId);
    const code = errorCode(report.error_code, requestId);
    expect((itemOutcome === "failed") === (code !== null), requestId);
    failedActions += Number(itemOutcome === "failed");
    changedActions += Number(itemOutcome === "installed");
  }

  const totals = object(root.totals, KEYS.totals, requestId);
  const observedTotals = [
    count(totals.imported_sessions, requestId),
    count(totals.created_memories, requestId),
    count(totals.skipped_sessions, requestId),
    count(totals.failed_actions, requestId),
  ];
  const expectedTotals = [importedSessions, createdMemories, skippedSessions, failedActions];
  const completedActions = importValues.length + captureValues.length - failedActions;
  const expectedOutcome = failedActions
    ? completedActions
      ? "partial"
      : "failed"
    : changedActions
      ? "complete"
      : "noop";
  expect(
    observedTotals.every((item, index) => item === expectedTotals[index]) &&
      outcome === expectedOutcome &&
      requiresNewPreview === Boolean(failedActions) &&
      (successfulImports > 0 || indexState === "not_requested"),
    requestId,
  );
  return value as OnboardingApplyResult;
}
