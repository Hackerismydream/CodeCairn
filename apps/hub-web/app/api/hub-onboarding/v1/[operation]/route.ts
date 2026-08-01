import { NextRequest } from "next/server";
import {
  proxyLoopbackRequest,
  type ProxyOperation,
} from "../../../../../lib/server/loopback-proxy";

export const dynamic = "force-dynamic";

const OPERATIONS = {
  preview: {
    method: "POST",
    upstreamPath: "/hub-onboarding/v1/preview",
    timeoutMs: 30_000,
    maxResponseBytes: 1024 * 1024,
    allowQuery: false,
  },
  apply: {
    method: "POST",
    upstreamPath: "/hub-onboarding/v1/apply",
    timeoutMs: 60_000,
    maxResponseBytes: 1024 * 1024,
    allowQuery: false,
  },
} satisfies Record<string, ProxyOperation>;

function proxy(
  request: NextRequest,
  context: { params: Promise<{ operation: string }> },
) {
  return proxyLoopbackRequest(request, context, {
    operations: OPERATIONS,
    unknownMessage: "未知的接入操作。",
    unavailableMessage: "本地接入模块尚未启动。",
  });
}

export const GET = proxy;
export const POST = proxy;
