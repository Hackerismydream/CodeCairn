import type { MemoryScope } from "./types";

const SCOPE_LABELS: Record<MemoryScope, string> = {
  global: "所有仓库",
  repository: "当前仓库",
};

export function memoryScopeLabel(scope: MemoryScope | undefined): string {
  return scope === undefined ? "范围未报告" : SCOPE_LABELS[scope];
}

export function activeScopesLabel(
  scopes: readonly MemoryScope[] | undefined,
): string {
  return scopes?.length
    ? scopes.map((scope) => SCOPE_LABELS[scope]).join(" + ")
    : "范围未报告";
}
