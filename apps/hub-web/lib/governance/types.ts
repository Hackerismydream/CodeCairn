import type { LibraryContext } from "../hub/types";

export type PromotionResponse = {
  schema_version: 1;
  library_context: Omit<LibraryContext, "promotion_count">;
  receipt: {
    outcome: "created" | "already_promoted";
    promotion: {
      schema_version: 1;
      promotion_id: string;
      person_id: string;
      subject_key: string;
      source: {
        repository_key: string;
        memory_id: string;
        revision_sha256: string;
      };
      replaces_promotion_id: string | null;
      created_at_ms: number;
    };
  };
};
