import { HubApiError } from "../hub/client";
import type { PromotionResponse } from "./types";

export function validatePromotionResponse(
  value: unknown,
  requestId: string | null,
  expectedMemoryId: string,
): PromotionResponse {
  const fail = (): never => {
    throw new HubApiError("Myna returned an incompatible promotion receipt.", {
      code: "invalid_response",
      retryable: false,
      requestId,
    });
  };
  if (!value || typeof value !== "object") fail();
  const root = value as Record<string, unknown>;
  const receipt = root.receipt as Record<string, unknown> | undefined;
  const promotion = receipt?.promotion as Record<string, unknown> | undefined;
  const source = promotion?.source as Record<string, unknown> | undefined;
  const context = root.library_context as Record<string, unknown> | undefined;
  const activeScopes = context?.active_scopes;
  if (
    root.schema_version !== 1 ||
    (receipt?.outcome !== "created" &&
      receipt?.outcome !== "already_promoted") ||
    promotion?.schema_version !== 1 ||
    !/^promotion_[0-9a-f]{64}$/.test(String(promotion?.promotion_id)) ||
    !/^person_[0-9a-f]{64}$/.test(String(promotion?.person_id)) ||
    typeof promotion?.promotion_id !== "string" ||
    typeof promotion?.person_id !== "string" ||
    typeof promotion?.subject_key !== "string" ||
    !promotion?.subject_key ||
    typeof source?.repository_key !== "string" ||
    source?.memory_id !== expectedMemoryId ||
    !/^[0-9a-f]{64}$/.test(String(source?.revision_sha256)) ||
    (promotion?.replaces_promotion_id !== null &&
      !/^promotion_[0-9a-f]{64}$/.test(String(promotion?.replaces_promotion_id))) ||
    !Number.isInteger(promotion?.created_at_ms) ||
    Number(promotion?.created_at_ms) < 0 ||
    !/^person_[0-9a-f]{64}$/.test(String(context?.person_id)) ||
    typeof context?.current_repository_key !== "string" ||
    promotion?.person_id !== context?.person_id ||
    source?.repository_key !== context?.current_repository_key ||
    !Array.isArray(activeScopes) ||
    !activeScopes.length ||
    new Set(activeScopes).size !== activeScopes.length ||
    activeScopes.some(
      (scope) => scope !== "global" && scope !== "repository",
    )
  ) {
    fail();
  }
  return value as PromotionResponse;
}
