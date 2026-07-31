export type OnboardingClientKind = "codex" | "claude" | "pico";
export type CaptureClientKind = Exclude<OnboardingClientKind, "pico">;

export type HistoricalState =
  | "available"
  | "none_found"
  | "unsupported"
  | "unresolved";

export type ContinuousState =
  | "available"
  | "installed"
  | "not_detected"
  | "manual_setup_required"
  | "unsupported";

export type SourceCandidate = {
  source_id: string;
  session_label: string;
  raw_event_count: number;
  estimated_bytes: number;
  latest_activity_ms: number | null;
  import_state: "new" | "incremental" | "already_imported";
  selected: boolean;
};

export type SourcePreview = {
  client: OnboardingClientKind;
  historical_state: HistoricalState;
  continuous_state: ContinuousState;
  capture_selected: boolean;
  candidates: SourceCandidate[];
  unresolved_count: number;
  invalid_count: number;
  remediation: string | null;
};

export type OnboardingPreview = {
  schema_version: 1;
  repo_key: string;
  snapshot_id: string;
  expires_at_ms: number;
  consent_token: string | null;
  selected_import_count: number;
  truncated: boolean;
  retention: {
    revision: string;
    retained: string[];
    omitted: string[];
    source_content_egress: "none" | "memory_text_to_embedding";
  };
  sources: SourcePreview[];
  planned_writes: string[];
};

export type PreviewRequest = {
  selectedSourceIds?: string[] | null;
  installCaptureFor?: CaptureClientKind[];
};

export type ImportReport = {
  source_id: string;
  client: CaptureClientKind;
  outcome: "imported" | "noop" | "failed";
  created_memory_count: number;
  skipped_memory_count: number;
  error_code: string | null;
};

export type CaptureReport = {
  client: CaptureClientKind;
  outcome: "installed" | "already_installed" | "failed";
  event: "stop" | "session_end";
  error_code: string | null;
};

export type OnboardingApplyResult = {
  schema_version: 1;
  snapshot_id: string;
  repo_key: string;
  outcome: "complete" | "noop" | "partial" | "failed";
  imports: ImportReport[];
  capture: CaptureReport[];
  totals: {
    imported_sessions: number;
    created_memories: number;
    skipped_sessions: number;
    failed_actions: number;
  };
  index_state: "ready" | "pending" | "failed" | "not_requested";
  requires_new_preview: boolean;
};
