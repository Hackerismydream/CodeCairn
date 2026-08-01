import { HubApiError } from "../hub/client";
import { validateHubErrorBody } from "../hub/validate";
import type { GovernanceClient } from "./client";
import type { PromotionResponse } from "./types";
import { validatePromotionResponse } from "./validate";

export function createHttpGovernanceClient(
  fetcher: typeof fetch = fetch,
): GovernanceClient {
  return {
    async promotePreference(
      memoryId: string,
      signal?: AbortSignal,
    ): Promise<PromotionResponse> {
      const response = await fetcher(
        "/api/hub-governance/v1/preferences/promote",
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ memory_id: memoryId }),
          cache: "no-store",
          signal,
        },
      );
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
      return validatePromotionResponse(payload, requestId, memoryId);
    },
  };
}
