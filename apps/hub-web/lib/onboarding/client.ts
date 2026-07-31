import type {
  ContinuousState,
  OnboardingApplyResult,
  OnboardingPreview,
  PreviewRequest,
} from "./types";

export const canInstallCapture = (state: ContinuousState) => state === "available";

export interface OnboardingClient {
  preview(
    request?: PreviewRequest,
    signal?: AbortSignal,
  ): Promise<OnboardingPreview>;
  apply(
    preview: OnboardingPreview,
    signal?: AbortSignal,
  ): Promise<OnboardingApplyResult>;
}
