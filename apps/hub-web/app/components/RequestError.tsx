import { HubApiError } from "../../lib/hub/client";
import { errorLabel, errorRemediation } from "../../lib/hub/format";

export default function RequestError({
  action,
  actionLabel,
  error,
  retry,
}: {
  action?: () => void;
  actionLabel?: string;
  error: Error;
  retry?: () => void;
}) {
  const hubError = error instanceof HubApiError ? error : null;
  const remediation = errorRemediation(
    hubError?.code ?? "unknown",
    hubError?.remediation ?? null,
  );
  const canRetry = Boolean(retry && (!hubError || hubError.retryable));
  const primaryAction = action ?? (canRetry ? retry : undefined);
  const primaryActionLabel = action ? (actionLabel ?? "继续") : "重新读取";
  return (
    <div className="request-error" role="alert">
      <strong>{errorLabel(hubError?.code ?? "unknown")}</strong>
      {remediation ? <p>{remediation}</p> : null}
      {hubError?.requestId ? <code>{hubError.requestId}</code> : null}
      {primaryAction ? (
        <button type="button" onClick={primaryAction}>
          {primaryActionLabel}
        </button>
      ) : null}
    </div>
  );
}
