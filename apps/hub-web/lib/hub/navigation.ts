export type HubView = "memories" | "onboarding" | "recall" | "system" | "demo";

export function parseHubView(
  value: string | string[] | null | undefined,
): HubView {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate === "onboarding" ||
    candidate === "recall" ||
    candidate === "system" ||
    candidate === "demo"
    ? candidate
    : "memories";
}

export function hubViewHref(view: HubView): string {
  return view === "memories" ? "/" : `/?view=${view}`;
}
