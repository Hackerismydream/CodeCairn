import { NextRequest } from "next/server";
import {
  proxyLoopbackRequest,
  type ProxyOperation,
} from "../../../../../../lib/server/loopback-proxy";

export const dynamic = "force-dynamic";

const OPERATIONS = {
  promote: {
    method: "POST",
    upstreamPath: "/hub-governance/v1/preferences/promote",
    timeoutMs: 10_000,
    maxResponseBytes: 256 * 1024,
    allowQuery: false,
  },
} satisfies Record<string, ProxyOperation>;

function proxy(
  request: NextRequest,
  context: { params: Promise<{ operation: string }> },
) {
  return proxyLoopbackRequest(request, context, {
    operations: OPERATIONS,
    unknownMessage: "未知的 Myna 偏好治理操作。",
    unavailableMessage: "本地 Myna 治理模块尚未启动。",
  });
}

export const GET = proxy;
export const POST = proxy;
