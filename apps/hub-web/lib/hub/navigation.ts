export type HubView = "memories" | "recall" | "system";

export function parseHubView(
  value: string | string[] | null | undefined,
): HubView {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate === "recall" || candidate === "system"
    ? candidate
    : "memories";
}

export function hubViewHref(view: HubView): string {
  return view === "memories" ? "/" : `/?view=${view}`;
}
