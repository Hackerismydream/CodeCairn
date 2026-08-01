import { HubApiError } from "../hub/client";
import { validateHubErrorBody } from "../hub/validate";
import type { OnboardingClient } from "./client";
import type {
  OnboardingApplyResult,
  OnboardingPreview,
  PreviewRequest,
} from "./types";
import {
  validateOnboardingApplyResult,
  validateOnboardingPreview,
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

export function createHttpOnboardingClient(
  fetcher: Fetcher = fetch,
): OnboardingClient {
  return {
    async preview(
      request: PreviewRequest = {},
      signal?: AbortSignal,
    ): Promise<OnboardingPreview> {
      const body: Record<string, unknown> = {};
      if (request.selectedSourceIds !== undefined) {
        body.selected_source_ids = request.selectedSourceIds;
      }
      if (request.installCaptureFor !== undefined) {
        body.install_capture_for = request.installCaptureFor;
      }
      return decode(
        await fetcher("/api/hub-onboarding/v1/preview", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
          cache: "no-store",
          signal,
        }),
        validateOnboardingPreview,
      );
    },

    async apply(
      preview: OnboardingPreview,
      signal?: AbortSignal,
    ): Promise<OnboardingApplyResult> {
      return decode(
        await fetcher("/api/hub-onboarding/v1/apply", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ consent_token: preview.consent_token }),
          cache: "no-store",
          signal,
        }),
        (value, requestId) =>
          validateOnboardingApplyResult(value, preview, requestId),
      );
    },
  };
}
