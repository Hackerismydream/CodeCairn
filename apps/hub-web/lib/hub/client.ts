import type {
  MemoriesRequest,
  MemoriesView,
  RecallRequest,
  RecallView,
  SystemView,
} from "./types";

export interface HubClient {
  memories(request?: MemoriesRequest, signal?: AbortSignal): Promise<MemoriesView>;
  recall(request: RecallRequest, signal?: AbortSignal): Promise<RecallView>;
  system(signal?: AbortSignal): Promise<SystemView>;
}

export class HubApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly remediation: string | null;
  readonly requestId: string | null;

  constructor(
    message: string,
    {
      code = "hub_unavailable",
      retryable = true,
      remediation = null,
      requestId = null,
    }: {
      code?: string;
      retryable?: boolean;
      remediation?: string | null;
      requestId?: string | null;
    } = {},
  ) {
    super(message);
    this.name = "HubApiError";
    this.code = code;
    this.retryable = retryable;
    this.remediation = remediation;
    this.requestId = requestId;
  }
}

export function isHubConnectionFailure(error: unknown): boolean {
  return (
    !(error instanceof HubApiError) ||
    error.code === "hub_unavailable" ||
    error.code === "unauthorized"
  );
}
