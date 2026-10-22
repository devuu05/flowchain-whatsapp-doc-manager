// Runtime reverse proxy from the dashboard's /api/* to the backend API.
// Keeps the session cookie first-party (ADR 0003) and reads BACKEND_URL at runtime, so the same
// image works on any host. Relays the browser IP to the API in X-Client-IP, authenticated with
// PROXY_SHARED_SECRET (the API ignores X-Client-IP without it).

const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
  "proxy-authorization", "proxy-authenticate", "host", "content-length",
]);
// Never forwarded from the browser: these are set only by this proxy or by real proxies.
const STRIP_INBOUND = new Set(["x-proxy-auth", "x-client-ip", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded"]);
const STRIP_OUTBOUND = new Set([...HOP_BY_HOP, "content-encoding", "set-cookie"]);

export interface ProxyConfig {
  backendUrl: string | undefined;
  sharedSecret: string | undefined;
  trustedHops: number;
}

export function configFromEnv(): ProxyConfig {
  return {
    backendUrl: process.env.BACKEND_URL,
    sharedSecret: process.env.PROXY_SHARED_SECRET,
    trustedHops: Number(process.env.DASHBOARD_TRUSTED_PROXY_HOPS ?? "0") || 0,
  };
}

/** The N-th X-Forwarded-For entry from the right (appended by our own hosting proxy). */
export function clientIpFrom(headers: Headers, hops: number): string | undefined {
  if (hops <= 0) return undefined;
  const parts = (headers.get("x-forwarded-for") ?? "").split(",").map((p) => p.trim()).filter(Boolean);
  return parts.length >= hops ? parts[parts.length - hops] : undefined;
}

function error(status: number, message: string): Response {
  return Response.json({ error: { code: "proxy_error", message } }, { status });
}

export async function proxyRequest(req: Request, segments: string[], cfg: ProxyConfig = configFromEnv()): Promise<Response> {
  if (!cfg.backendUrl) return error(500, "BACKEND_URL is not configured");
  if (segments.some((s) => s === "." || s === ".." || s.includes("/") || s.includes("\\"))) {
    return error(400, "Invalid path");
  }
  const incoming = new URL(req.url);
  const target = new URL(`/api/${segments.map(encodeURIComponent).join("/")}${incoming.search}`, cfg.backendUrl);

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    const k = key.toLowerCase();
    if (!HOP_BY_HOP.has(k) && !STRIP_INBOUND.has(k)) headers.set(key, value);
  });
  const ip = clientIpFrom(req.headers, cfg.trustedHops);
  if (cfg.sharedSecret && ip) {
    headers.set("x-proxy-auth", cfg.sharedSecret);
    headers.set("x-client-ip", ip);
  }

  const hasBody = !["GET", "HEAD"].includes(req.method.toUpperCase());
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: "manual",
      cache: "no-store",
      // Required by Node's fetch when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch {
    return error(502, "The API is unavailable. If it was asleep, retry in a minute.");
  }

  const out = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_OUTBOUND.has(key.toLowerCase())) out.set(key, value);
  });
  for (const cookie of upstream.headers.getSetCookie?.() ?? []) out.append("set-cookie", cookie);
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}
