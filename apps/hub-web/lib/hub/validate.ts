import { HubApiError } from "./client";
import type {
  HubErrorBody,
  MemoriesView,
  MemoryOrigin,
  MemoryStatus,
  MemoryType,
  RecallView,
  SystemView,
} from "./types";

type JsonObject = Record<string, unknown>;

const MEMORY_TYPES = new Set<MemoryType>([
  "repository_knowledge",
  "task_experience",
  "work_state",
  "user_preference",
]);
const MEMORY_STATUSES = new Set<MemoryStatus>(["active", "superseded"]);
const PROVIDERS = new Set(["codex", "claude", "pico"]);
const OUTCOMES = new Set(["success", "failure", "unknown"]);
const FRESHNESS = new Set(["fresh", "semantic_pending"]);
const SEMANTIC_STATES = new Set(["complete", "pending", "failed"]);
const ADMISSION_REASONS = new Set(["relevant_candidate", "pinned_work_state", "no_candidates", "below_threshold"]);
const OMISSION_REASONS = new Set(["historical_filter", "relevance", "type_cap", "limit", "token_budget"]);
const MEMORY_ORIGINS = new Set<MemoryOrigin>([
  "capture",
  "agent_asserted",
  "restored",
]);

function incompatible(requestId: string | null): never {
  throw new HubApiError("Hub returned an incompatible version 1 response.", {
    code: "invalid_response",
    retryable: false,
    remediation: "请确认 Hub 前后端版本一致后重新启动。",
    requestId,
  });
}

function object(value: unknown, requestId: string | null): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    incompatible(requestId);
  }
  return value as JsonObject;
}

function string(value: unknown, requestId: string | null): string {
  if (typeof value !== "string") incompatible(requestId);
  return value;
}

function nullableString(
  value: unknown,
  requestId: string | null,
): string | null {
  if (value !== null && typeof value !== "string") incompatible(requestId);
  return value as string | null;
}

function number(value: unknown, requestId: string | null): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    incompatible(requestId);
  }
  return value;
}

function boolean(value: unknown, requestId: string | null): boolean {
  if (typeof value !== "boolean") incompatible(requestId);
  return value;
}

function nonnegativeNumber(value: unknown, requestId: string | null): number {
  const result = number(value, requestId);
  if (result < 0) incompatible(requestId);
  return result;
}

function array(value: unknown, requestId: string | null): unknown[] {
  if (!Array.isArray(value)) incompatible(requestId);
  return value;
}

function literal<T extends string>(
  value: unknown,
  allowed: Set<T>,
  requestId: string | null,
): T {
  if (typeof value !== "string" || !allowed.has(value as T)) {
    incompatible(requestId);
  }
  return value as T;
}

function versionOne(root: JsonObject, requestId: string | null): void {
  if (root.schema_version !== 1) incompatible(requestId);
}

export function validateHubErrorBody(
  value: unknown,
  requestId: string | null = null,
): HubErrorBody["error"] {
  const root = object(value, requestId);
  versionOne(root, requestId);
  const error = object(root.error, requestId);
  return {
    code: string(error.code, requestId),
    message: string(error.message, requestId),
    retryable: boolean(error.retryable, requestId),
    remediation: nullableString(error.remediation, requestId),
    request_id: string(error.request_id, requestId),
  };
}

function validateReference(value: unknown, requestId: string | null): void {
  const reference = object(value, requestId);
  for (const key of [
    "fact_id",
    "provider",
    "session_id",
    "event_id",
    "source_path_sha256",
    "event_sha256",
  ]) {
    if (key === "provider") literal(reference[key], PROVIDERS, requestId);
    else string(reference[key], requestId);
  }
  nonnegativeNumber(reference.source_generation, requestId);
  nonnegativeNumber(reference.event_index, requestId);
}

function validateFact(value: unknown, requestId: string | null): void {
  const fact = object(value, requestId);
  const attributes = object(fact.attributes, requestId);
  string(fact.fact_id, requestId);
  string(fact.fact_kind, requestId);
  nullableString(fact.role, requestId);
  string(fact.value, requestId);
  validateReference(fact.reference, requestId);
  if (fact.fact_kind === "command_result") string(attributes.command_fact_id, requestId);
  if (fact.fact_kind === "command_result" || attributes.outcome !== undefined) literal(attributes.outcome, OUTCOMES, requestId);
  if (attributes.exit_code !== undefined && !Number.isInteger(number(attributes.exit_code, requestId))) incompatible(requestId);
}

function validateMemory(value: unknown, requestId: string | null): void {
  const memory = object(value, requestId);
  versionOne(memory, requestId);
  string(memory.memory_id, requestId);
  string(memory.repo_key, requestId);
  literal(memory.memory_type, MEMORY_TYPES, requestId);
  string(memory.title, requestId);
  string(memory.content, requestId);
  string(memory.category, requestId);
  nonnegativeNumber(memory.created_at_ms, requestId);
  nullableString(memory.episode_id, requestId);
  literal(memory.origin, MEMORY_ORIGINS, requestId);
  object(memory.payload, requestId);
  for (const tag of array(memory.tags, requestId)) string(tag, requestId);
  for (const fact of array(memory.facts, requestId)) {
    validateFact(fact, requestId);
  }
}

function validateMemorySummary(
  value: unknown,
  requestId: string | null,
): void {
  const memory = object(value, requestId);
  string(memory.memory_id, requestId);
  literal(memory.memory_type, MEMORY_TYPES, requestId);
  string(memory.title, requestId);
  literal(memory.status, MEMORY_STATUSES, requestId);
  nonnegativeNumber(memory.created_at_ms, requestId);
}

function validateSelectedMemory(
  value: unknown,
  requestId: string | null,
): void {
  const selected = object(value, requestId);
  const detail = object(selected.detail, requestId);
  validateMemory(detail.memory, requestId);
  literal(detail.status, MEMORY_STATUSES, requestId);
  string(detail.resource_uri, requestId);

  const history = object(selected.history, requestId);
  for (const memory of array(history.memories, requestId)) {
    validateMemory(memory, requestId);
  }
  for (const value of array(history.statuses, requestId)) {
    const status = array(value, requestId);
    if (status.length !== 2) incompatible(requestId);
    string(status[0], requestId);
    literal(status[1], MEMORY_STATUSES, requestId);
  }
  for (const value of array(history.evolutions, requestId)) {
    const evolution = object(value, requestId);
    string(evolution.evolution_id, requestId);
    string(evolution.relation_kind, requestId);
    string(evolution.proposer, requestId);
    string(evolution.reason, requestId);
    nonnegativeNumber(evolution.created_at_ms, requestId);
  }
}

export function validateMemoriesView(
  value: unknown,
  requestId: string | null = null,
): MemoriesView {
  const root = object(value, requestId);
  versionOne(root, requestId);
  const repoKey = string(root.repo_key, requestId);
  const page = object(root.page, requestId);
  versionOne(page, requestId);
  if (string(page.repo_key, requestId) !== repoKey) incompatible(requestId);
  for (const item of array(page.items, requestId)) {
    validateMemorySummary(item, requestId);
  }
  nullableString(page.next_cursor, requestId);
  if (root.selected !== null) validateSelectedMemory(root.selected, requestId);
  return value as MemoriesView;
}

function validateRankedRecall(
  value: unknown,
  requestId: string | null,
): void {
  const candidate = object(value, requestId);
  nonnegativeNumber(candidate.rank, requestId);
  string(candidate.memory_id, requestId);
  literal(candidate.memory_type, MEMORY_TYPES, requestId);
  string(candidate.title, requestId);
  string(candidate.summary, requestId);
  string(candidate.source_uri, requestId);
  number(candidate.final_score, requestId);
  for (const source of array(candidate.candidate_sources, requestId)) {
    if (source !== "lexical" && source !== "vector") incompatible(requestId);
  }
  for (const snippetValue of array(candidate.snippets, requestId)) {
    const snippet = object(snippetValue, requestId);
    string(snippet.document_id, requestId);
    string(snippet.text, requestId);
    number(snippet.final_score, requestId);
  }
}

export function validateRecallView(
  value: unknown,
  requestId: string | null = null,
): RecallView {
  const root = object(value, requestId);
  versionOne(root, requestId);
  const result = object(root.result, requestId);
  string(result.markdown, requestId);
  const sidecar = object(result.sidecar, requestId);
  string(sidecar.query, requestId);
  string(sidecar.repo_key, requestId);
  number(sidecar.latency_ms, requestId);
  nonnegativeNumber(sidecar.vector_candidate_count, requestId);
  nonnegativeNumber(sidecar.lexical_candidate_count, requestId);
  string(sidecar.retrieval_profile, requestId);
  number(sidecar.source_cursor, requestId);
  number(sidecar.index_cursor, requestId);
  const semanticState = literal(sidecar.semantic_state, SEMANTIC_STATES, requestId);
  const freshness = literal(sidecar.freshness, FRESHNESS, requestId);
  if ((semanticState === "complete") !== (freshness === "fresh")) incompatible(requestId);
  for (const candidate of array(sidecar.ranked, requestId)) {
    validateRankedRecall(candidate, requestId);
  }
  for (const omissionValue of array(sidecar.omissions, requestId)) {
    const omission = object(omissionValue, requestId);
    string(omission.memory_id, requestId);
    literal(omission.reason, OMISSION_REASONS, requestId);
  }
  if (sidecar.admission_trace !== null) {
    const admission = object(sidecar.admission_trace, requestId);
    string(admission.policy, requestId);
    if (admission.outcome !== "admitted" && admission.outcome !== "abstained") {
      incompatible(requestId);
    }
    literal(admission.reason, ADMISSION_REASONS, requestId);
    number(admission.vector_threshold, requestId);
    if (admission.max_vector_score !== null) {
      number(admission.max_vector_score, requestId);
    }
  }
  if (sidecar.context_trace !== null) {
    const context = object(sidecar.context_trace, requestId);
    string(context.renderer, requestId);
    nonnegativeNumber(context.token_count, requestId);
    nonnegativeNumber(context.token_limit, requestId);
  }
  return value as RecallView;
}

function validateNumberRecord(
  value: unknown,
  requestId: string | null,
): void {
  for (const count of Object.values(object(value, requestId))) {
    nonnegativeNumber(count, requestId);
  }
}

export function validateSystemView(
  value: unknown,
  requestId: string | null = null,
): SystemView {
  const root = object(value, requestId);
  versionOne(root, requestId);
  string(root.repo_key, requestId);
  if (root.status !== "ok" && root.status !== "degraded") {
    incompatible(requestId);
  }
  string(root.runtime_schema, requestId);
  nonnegativeNumber(root.observed_at_ms, requestId);
  const counts = object(root.counts, requestId);
  for (const key of [
    "imports",
    "observed_events",
    "memories",
    "pending_recovery",
    "conflicted_recovery",
  ]) {
    nonnegativeNumber(counts[key], requestId);
  }
  validateNumberRecord(root.index_jobs, requestId);
  validateNumberRecord(root.semantic_jobs, requestId);
  for (const subsystemValue of Object.values(
    object(root.subsystems, requestId),
  )) {
    const subsystem = object(subsystemValue, requestId);
    if (
      subsystem.status !== "ok" &&
      subsystem.status !== "degraded" &&
      subsystem.status !== "not_configured"
    ) {
      incompatible(requestId);
    }
    string(subsystem.remediation, requestId);
  }
  for (const value of Object.values(object(root.providers, requestId))) {
    string(value, requestId);
  }
  const recallReadiness = object(root.recall_readiness, requestId);
  string(recallReadiness.profile, requestId);
  if (
    recallReadiness.state !== "configuration_ready" &&
    recallReadiness.state !== "missing_key" &&
    recallReadiness.state !== "not_configured"
  ) {
    incompatible(requestId);
  }
  boolean(recallReadiness.live_checked, requestId);
  nullableString(recallReadiness.remediation, requestId);
  for (const value of Object.values(object(root.privacy, requestId))) {
    string(value, requestId);
  }
  nullableString(root.remediation, requestId);
  return value as SystemView;
}
