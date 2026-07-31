import { NextRequest, NextResponse } from "next/server";

export type ProxyOperation = {
  method: "GET" | "POST";
  upstreamPath: string;
  timeoutMs: number;
  maxResponseBytes?: number;
  allowQuery?: boolean;
};

type ProxyConfig = {
  operations: Readonly<Record<string, ProxyOperation>>;
  unknownMessage: string;
  unavailableMessage: string;
  fetcher?: typeof fetch;
};

const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]"]);
const MAX_REQUEST_BYTES = 64 * 1024;

function declaredTooLarge(headers: Headers, maximum: number): boolean {
  const value = headers.get("content-length");
  return value !== null && (!/^\d+$/.test(value) || Number(value) > maximum);
}

async function boundedBody(body: ReadableStream<Uint8Array> | null, maximum: number): Promise<ArrayBuffer | null> {
  if (!body) return new ArrayBuffer(0);
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maximum) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  const result = new ArrayBuffer(size);
  const bytes = new Uint8Array(result); let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function errorResponse(status: number, code: string, message: string, retryable = false) {
  return NextResponse.json(
    {
      schema_version: 1,
      error: {
        code,
        message,
        retryable,
        remediation: retryable ? "在仓库根目录运行 `make hub-dev`。" : null,
        request_id: "hub-proxy",
      },
    },
    {
      status,
      headers: {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      },
    },
  );
}

function loopbackUrl(value: string | null, authorityOnly = true): URL | null {
  if (!value || value.includes(",")) return null;
  try {
    const url = new URL(authorityOnly ? `http://${value}` : value);
    if (
      url.protocol !== "http:" ||
      [url.username, url.password, url.search, url.hash].some(Boolean) ||
      url.pathname !== "/" ||
      !LOOPBACK_HOSTNAMES.has(url.hostname.toLowerCase())
    ) return null;
    return url;
  } catch {
    return null;
  }
}

function hasTrustedBrowserSeam(request: NextRequest): boolean {
  const host = loopbackUrl(request.headers.get("host"));
  if (!host || request.headers.has("x-forwarded-host")) return false;
  const origin = request.headers.get("origin");
  if (origin) {
    const parsed = loopbackUrl(origin, false);
    if (!parsed || parsed.host.toLowerCase() !== host.host.toLowerCase()) {
      return false;
    }
  }
  const fetchSite = request.headers.get("sec-fetch-site");
  return fetchSite === null || fetchSite === "same-origin" || fetchSite === "none";
}

export async function proxyLoopbackRequest(
  request: NextRequest,
  context: { params: Promise<{ operation: string }> },
  config: ProxyConfig,
) {
  if (!hasTrustedBrowserSeam(request)) {
    return errorResponse(403, "untrusted_browser_origin", "Hub 只接受当前本机页面发起的请求。");
  }

  const { operation } = await context.params;
  const rule = Object.hasOwn(config.operations, operation)
    ? config.operations[operation]
    : undefined;
  if (!rule || request.method !== rule.method) {
    return errorResponse(404, "not_found", config.unknownMessage);
  }
  if (rule.allowQuery === false && request.nextUrl.search) {
    return errorResponse(400, "invalid_query", "接入请求不接受查询参数。");
  }
  if (declaredTooLarge(request.headers, MAX_REQUEST_BYTES)) {
    return errorResponse(413, "request_too_large", "Hub 请求超过大小限制。");
  }

  const token = process.env.CODECAIRN_HUB_TOKEN;
  const origin = loopbackUrl(process.env.CODECAIRN_HUB_API_URL ?? null, false);
  if (!token || !origin) {
    return errorResponse(503, "hub_unavailable", config.unavailableMessage, true);
  }

  const upstream = new URL(rule.upstreamPath, origin);
  upstream.search = request.nextUrl.search;
  try {
    const body = rule.method === "POST"
      ? await boundedBody(request.body, MAX_REQUEST_BYTES)
      : undefined;
    if (body === null) {
      return errorResponse(413, "request_too_large", "Hub 请求超过大小限制。");
    }
    const response = await (config.fetcher ?? fetch)(upstream, {
      method: rule.method,
      headers: {
        accept: "application/json",
        "content-type": request.headers.get("content-type") ?? "application/json",
        "x-codecairn-hub-token": token,
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(rule.timeoutMs),
    });
    const headers = new Headers({
      "cache-control": "no-store",
      "content-type": response.headers.get("content-type") ?? "application/json",
      "x-content-type-options": "nosniff",
    });
    const requestId = response.headers.get("x-codecairn-request-id");
    if (requestId) headers.set("x-codecairn-request-id", requestId);
    if (rule.maxResponseBytes === undefined) {
      return new NextResponse(response.body, { status: response.status, headers });
    }
    if (declaredTooLarge(response.headers, rule.maxResponseBytes)) {
      return errorResponse(502, "invalid_upstream_response", "本地 Hub 返回内容超过大小限制。");
    }
    const responseBody = await boundedBody(response.body, rule.maxResponseBytes);
    if (responseBody === null) {
      return errorResponse(502, "invalid_upstream_response", "本地 Hub 返回内容超过大小限制。");
    }
    return new NextResponse(responseBody, { status: response.status, headers });
  } catch {
    return errorResponse(503, "hub_unavailable", "本地 Hub 后端不可用。", true);
  }
}
