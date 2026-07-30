import type { Metadata } from "next";
import ConvergedHub, {
  type HubView,
  type InspectorTab,
  type RecallSampleKey,
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
    sample?: string | string[];
  }>;
}) {
  const params = await searchParams;
  const requestedView = Array.isArray(params.view) ? params.view[0] : params.view;
  const requestedDetail = Array.isArray(params.detail)
    ? params.detail[0]
    : params.detail;
  const requestedSample = Array.isArray(params.sample)
    ? params.sample[0]
    : params.sample;
  const initialView: HubView =
    requestedView === "recall" || requestedView === "system"
      ? requestedView
      : "memories";
  const initialInspectorTab: InspectorTab =
    requestedDetail === "source" || requestedDetail === "evolution"
      ? requestedDetail
      : "content";
  const initialRecallSample: RecallSampleKey =
    requestedSample === "abstained" ? "abstained" : "admitted";

  return (
    <ConvergedHub
      initialView={initialView}
      initialInspectorTab={initialInspectorTab}
      initialRecallSample={initialRecallSample}
    />
  );
}
