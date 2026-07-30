import type { Metadata } from "next";
import ConvergedHub, {
  type HubView,
  type InspectorTab,
} from "./ConvergedHub";

export const metadata: Metadata = {
  title: "CodeCairn 记忆中心",
  description:
    "CodeCairn 面向编码智能体的本地记忆系统只读原型。",
};

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{
    view?: string | string[];
    detail?: string | string[];
  }>;
}) {
  const params = await searchParams;
  const requestedView = Array.isArray(params.view) ? params.view[0] : params.view;
  const requestedDetail = Array.isArray(params.detail)
    ? params.detail[0]
    : params.detail;
  const initialView: HubView =
    requestedView === "memories" ||
    requestedView === "recall" ||
    requestedView === "system"
      ? requestedView
      : requestedView === "recalls"
        ? "recall"
        : requestedView === "activity"
          ? "system"
          : "overview";
  const initialInspectorTab: InspectorTab =
    requestedDetail === "source" || requestedDetail === "evolution"
      ? requestedDetail
      : "content";

  return (
    <ConvergedHub
      initialView={initialView}
      initialInspectorTab={initialInspectorTab}
    />
  );
}
