import type { Metadata } from "next";
import HubShell from "./components/HubShell";
import { parseHubView } from "../lib/hub/navigation";

export const metadata: Metadata = {
  title: "CodeCairn 记忆中心",
  description:
    "CodeCairn 本地 Memory OS 的记忆查看与接入中心。",
};

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{
    view?: string | string[];
  }>;
}) {
  const params = await searchParams;
  const initialView = parseHubView(params.view);

  return <HubShell key={initialView} initialView={initialView} />;
}
