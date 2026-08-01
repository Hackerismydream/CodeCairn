import { NextRequest } from "next/server";
import {
  proxyLoopbackRequest,
  type ProxyOperation,
} from "../../../../../lib/server/loopback-proxy";

export const dynamic = "force-dynamic";

const OPERATIONS = {
  memories: {
    method: "GET",
    upstreamPath: "/hub-read/v1/memories",
    timeoutMs: 5_000,
  },
  recall: {
    method: "POST",
    upstreamPath: "/hub-read/v1/recall",
    timeoutMs: 30_000,
  },
  system: {
    method: "GET",
    upstreamPath: "/hub-read/v1/system",
    timeoutMs: 5_000,
  },
} satisfies Record<string, ProxyOperation>;

function proxy(
  request: NextRequest,
  context: { params: Promise<{ operation: string }> },
) {
  return proxyLoopbackRequest(request, context, {
    operations: OPERATIONS,
    unknownMessage: "未知的 Hub 只读操作。",
    unavailableMessage: "本地 Hub 后端尚未启动。",
  });
}

export const GET = proxy;
export const POST = proxy;
