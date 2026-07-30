import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const GET_OPERATIONS = new Set(["memories", "system"]);
const POST_OPERATIONS = new Set(["recall"]);
const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]"]);

function errorResponse(
  status: number,
  code: string,
  message: string,
  retryable: boolean,
) {
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

function unavailable(message: string) {
  return errorResponse(503, "hub_unavailable", message, true);
}

function loopbackUrl(authority: string | null): URL | null {
  if (!authority || authority.includes(",")) return null;
  try {
    const url = new URL(`http://${authority}`);
    if (
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash ||
      !LOOPBACK_HOSTNAMES.has(url.hostname.toLowerCase())
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function hasTrustedBrowserBoundary(request: NextRequest): boolean {
  const host = loopbackUrl(request.headers.get("host"));
  if (!host) return false;

  // The foreground Hub has no supported reverse-proxy deployment. Accepting a
  // forwarded authority here would reopen the DNS-rebinding boundary.
  if (request.headers.has("x-forwarded-host")) return false;

  const origin = request.headers.get("origin");
  if (origin) {
    try {
      const parsed = new URL(origin);
      if (
        parsed.protocol !== "http:" ||
        !LOOPBACK_HOSTNAMES.has(parsed.hostname.toLowerCase()) ||
        parsed.host.toLowerCase() !== host.host.toLowerCase()
      ) {
        return false;
      }
    } catch {
      return false;
    }
  }

  const fetchSite = request.headers.get("sec-fetch-site");
  return (
    fetchSite === null || fetchSite === "same-origin" || fetchSite === "none"
  );
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ operation: string }> },
) {
  if (!hasTrustedBrowserBoundary(request)) {
    return errorResponse(
      403,
      "untrusted_browser_origin",
      "Hub 只接受当前本机页面发起的请求。",
      false,
    );
  }

  const { operation } = await context.params;
  const allowed =
    (request.method === "GET" && GET_OPERATIONS.has(operation)) ||
    (request.method === "POST" && POST_OPERATIONS.has(operation));
  if (!allowed) {
    return NextResponse.json(
      {
        schema_version: 1,
        error: {
          code: "not_found",
          message: "未知的 Hub 只读操作。",
          retryable: false,
          remediation: null,
          request_id: "hub-proxy",
        },
      },
      { status: 404, headers: { "cache-control": "no-store" } },
    );
  }

  const token = process.env.CODECAIRN_HUB_TOKEN;
  const origin = process.env.CODECAIRN_HUB_API_URL;
  if (!token || !origin) {
    return unavailable("本地 Hub 后端尚未启动。");
  }

  const upstream = new URL(`/hub-read/v1/${operation}`, origin);
  upstream.search = request.nextUrl.search;
  try {
    const response = await fetch(upstream, {
      method: request.method,
      headers: {
        "content-type": request.headers.get("content-type") ?? "application/json",
        "x-codecairn-hub-token": token,
      },
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(operation === "recall" ? 30_000 : 5_000),
    });
    const headers = new Headers({
      "cache-control": "no-store",
      "content-type":
        response.headers.get("content-type") ?? "application/json",
    });
    const requestId = response.headers.get("x-codecairn-request-id");
    if (requestId) headers.set("x-codecairn-request-id", requestId);
    return new NextResponse(response.body, {
      status: response.status,
      headers,
    });
  } catch {
    return unavailable("本地 Hub 后端不可用。");
  }
}

export const GET = proxy;
export const POST = proxy;
