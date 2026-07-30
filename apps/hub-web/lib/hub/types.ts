export type MemoryType =
  | "repository_knowledge"
  | "task_experience"
  | "work_state"
  | "user_preference";

export type MemoryStatus = "active" | "superseded";
export type MemoryOrigin = "capture" | "agent_asserted" | "restored";

export type MemorySummary = {
  memory_id: string;
  memory_type: MemoryType;
  title: string;
  status: MemoryStatus;
  created_at_ms: number;
};

export type EvidenceReference = {
  fact_id: string;
  provider: "codex" | "claude" | "pico";
  session_id: string;
  source_generation: number;
  event_index: number;
  event_id: string;
  source_path_sha256: string;
  event_sha256: string;
};

export type EvidenceFact = {
  schema_version: number;
  fact_id: string;
  repo_key: string;
  episode_id: string | null;
  reference: EvidenceReference;
  fact_kind: string;
  role: string | null;
  value: string;
  attributes: Record<string, unknown>;
  fact_ordinal: number;
};

export type SourceOrderKey = {
  trusted_timestamp_ms: number | null;
  provider: "codex" | "claude" | "pico";
  session_id: string;
  source_generation: number;
  event_index: number;
};

export type CodingMemory = {
  schema_version: number;
  memory_id: string;
  repo_key: string;
  memory_type: MemoryType;
  title: string;
  content: string;
  category: string;
  tags: string[];
  created_at_ms: number;
  episode_id: string | null;
  evidence: EvidenceReference[];
  facts: EvidenceFact[];
  origin: MemoryOrigin;
  restored_from: string | null;
  restore_predecessor_id: string | null;
  source_order_key: SourceOrderKey | null;
  payload: Record<string, unknown>;
};

export type EvolutionRecord = {
  schema_version: number;
  evolution_id: string;
  repo_key: string;
  relation_kind: string;
  predecessor_id: string;
  successor_id: string;
  proposal_id: string | null;
  supporting_fact_ids: string[];
  source_order_key: SourceOrderKey | null;
  proposer: string;
  reason: string;
  evidence: EvidenceReference[];
  created_at_ms: number;
};

export type MemoryHistory = {
  memories: CodingMemory[];
  evolutions: EvolutionRecord[];
  statuses: Array<[string, MemoryStatus]>;
};

export type MemoriesView = {
  schema_version: 1;
  repo_key: string;
  page: {
    schema_version: number;
    repo_key: string;
    items: MemorySummary[];
    next_cursor: string | null;
  };
  selected: {
    detail: {
      memory: CodingMemory;
      status: MemoryStatus;
      resource_uri: string;
    };
    history: MemoryHistory;
  } | null;
};

export type RankedRecall = {
  rank: number;
  memory_id: string;
  memory_type: MemoryType;
  title: string;
  summary: string;
  source_uri: string;
  content_sha256: string;
  candidate_sources: Array<"lexical" | "vector">;
  final_score: number;
  evidence: EvidenceReference[];
  status: MemoryStatus;
  pinned: boolean;
  snippets: Array<{
    document_id: string;
    text: string;
    final_score: number;
  }>;
};

export type RecallView = {
  schema_version: 1;
  result: {
    markdown: string;
    sidecar: {
      query: string;
      repo_key: string;
      limit: number;
      latency_ms: number;
      vector_candidate_count: number;
      lexical_candidate_count: number;
      ranked: RankedRecall[];
      context_trace: {
        renderer: string;
        rendered_memory_ids: string[];
        rendered_fact_ids: string[];
        omitted_snippet_count: number;
        type_caps: Array<[MemoryType, number]>;
        tokenizer: string;
        token_count: number;
        token_limit: number;
      } | null;
      admission_trace: {
        policy: string;
        outcome: "admitted" | "abstained";
        reason:
          | "relevant_candidate"
          | "pinned_work_state"
          | "no_candidates"
          | "below_threshold";
        vector_threshold: number;
        max_vector_score: number | null;
      } | null;
      retrieval_profile: string;
      include_superseded: boolean;
      workstream_key: string | null;
      omissions: Array<{
        memory_id: string;
        reason:
          | "historical_filter"
          | "relevance"
          | "type_cap"
          | "limit"
          | "token_budget";
      }>;
      source_cursor: number;
      index_cursor: number;
      semantic_state: string;
      freshness: "fresh" | "semantic_pending";
    };
  };
};

export type SystemView = {
  schema_version: 1;
  observed_at_ms: number;
  repo_key: string;
  status: "ok" | "degraded";
  runtime_schema: string;
  counts: {
    imports: number;
    observed_events: number;
    memories: number;
    pending_recovery: number;
    conflicted_recovery: number;
  };
  semantic_jobs: Record<string, number>;
  hook_receipts: {
    total: number;
    failed: number;
    latest_retry: string | null;
  };
  index_jobs: Record<string, number>;
  subsystems: Record<
    string,
    {
      status: "ok" | "degraded" | "not_configured";
      remediation: string;
    }
  >;
  providers: Record<string, string>;
  recall_readiness: {
    profile: string;
    state: "configuration_ready" | "missing_key" | "not_configured";
    live_checked: boolean;
    remediation: string | null;
  };
  privacy: Record<string, string>;
  remediation: string | null;
};

export type HubErrorBody = {
  schema_version: 1;
  error: {
    code: string;
    message: string;
    retryable: boolean;
    remediation: string | null;
    request_id: string;
  };
};

export type MemoriesRequest = {
  memoryType?: MemoryType;
  status?: MemoryStatus;
  limit?: number;
  cursor?: string;
  selectedMemoryId?: string;
};

export type RecallRequest = {
  query: string;
  limit?: number;
  includeSuperseded?: boolean;
  workstreamKey?: string;
  tokenBudget?: number;
};
