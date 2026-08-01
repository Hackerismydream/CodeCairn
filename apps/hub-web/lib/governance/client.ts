import type { PromotionResponse } from "./types";

export interface GovernanceClient {
  promotePreference(
    memoryId: string,
    signal?: AbortSignal,
  ): Promise<PromotionResponse>;
}
