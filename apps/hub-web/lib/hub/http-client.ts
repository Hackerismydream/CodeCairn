import { HubApiError, type HubClient } from "./client";
import type {
  MemoriesRequest,
  MemoriesView,
  RecallRequest,
  RecallView,
  SystemView,
} from "./types";
import {
  validateHubErrorBody,
  validateMemoriesView,
  validateRecallView,
  validateSystemView,
} from "./validate";

type Fetcher = typeof fetch;

async function decode<T>(
  response: Response,
  validate: (value: unknown, requestId: string | null) => T,
): Promise<T> {
  const payload: unknown = await response.json().catch(() => null);
  const requestId = response.headers.get("x-codecairn-request-id");
  if (!response.ok) {
    const error = validateHubErrorBody(payload, requestId);
    throw new HubApiError(error.message, {
      code: error.code,
      retryable: error.retryable,
      remediation: error.remediation,
      requestId: error.request_id,
    });
  }
  return validate(payload, requestId);
}

export function createHttpHubClient(fetcher: Fetcher = fetch): HubClient {
  return {
    async memories(
      request: MemoriesRequest = {},
      signal?: AbortSignal,
    ): Promise<MemoriesView> {
      const params = new URLSearchParams();
      if (request.memoryType) params.set("memory_type", request.memoryType);
      if (request.status) params.set("status", request.status);
      if (request.scope) params.set("scope", request.scope);
      if (request.limit) params.set("limit", String(request.limit));
      if (request.cursor) params.set("cursor", request.cursor);
      if (request.selectedMemoryId) {
        params.set("selected_memory_id", request.selectedMemoryId);
      }
      const query = params.size ? `?${params.toString()}` : "";
      return decode<MemoriesView>(
        await fetcher(`/api/hub-read/v1/memories${query}`, {
          cache: "no-store",
          signal,
        }),
        validateMemoriesView,
      );
    },

    async recall(
      request: RecallRequest,
      signal?: AbortSignal,
    ): Promise<RecallView> {
      return decode<RecallView>(
        await fetcher("/api/hub-read/v1/recall", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            query: request.query,
            limit: request.limit,
            include_superseded: request.includeSuperseded,
            workstream_key: request.workstreamKey,
            token_budget: request.tokenBudget,
          }),
          cache: "no-store",
          signal,
        }),
        validateRecallView,
      );
    },

    async system(signal?: AbortSignal): Promise<SystemView> {
      return decode<SystemView>(
        await fetcher("/api/hub-read/v1/system", {
          cache: "no-store",
          signal,
        }),
        validateSystemView,
      );
    },
  };
}
