import { describe, expect, it, vi } from "vitest";

import { clientIpFrom, proxyRequest } from "@/lib/proxy";

const cfg = { backendUrl: "https://api.example.com", sharedSecret: "s3cret", trustedHops: 1 };

function upstream(body: BodyInit | null, init: ResponseInit & { cookies?: string[] } = {}) {
  const headers = new Headers(init.headers);
  for (const c of init.cookies ?? []) headers.append("set-cookie", c);
  return new Response(body, { ...init, headers });
}

describe("dashboard API proxy", () => {
  it("forwards to the backend, strips spoofable headers and relays the client IP", async () => {
    const fetchMock = vi.fn(async () => upstream(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request("https://dash.example.com/api/v1/tenants?page=2", {
      headers: { cookie: "fc_session=abc", "x-forwarded-for": "6.6.6.6, 203.0.113.9", "x-client-ip": "1.1.1.1", "x-proxy-auth": "forged" },
    });
    const res = await proxyRequest(req, ["v1", "tenants"], cfg);
    expect(res.status).toBe(200);
    const call = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(String(call[0])).toBe("https://api.example.com/api/v1/tenants?page=2");
    const h = new Headers(call[1].headers);
    expect(h.get("cookie")).toBe("fc_session=abc");
    expect(h.get("x-client-ip")).toBe("203.0.113.9");
    expect(h.get("x-proxy-auth")).toBe("s3cret");
    expect(h.get("x-forwarded-for")).toBeNull();
    expect(call[1].redirect).toBe("manual");
  });

  it("passes cookies and redirects back to the browser", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => upstream(null, { status: 303, headers: { location: "https://dash.example.com/t/1/settings/drive?result=connected" }, cookies: ["a=1; HttpOnly", "b=2; Path=/"] })));
    const res = await proxyRequest(new Request("https://dash.example.com/api/v1/integrations/google/callback?state=x"), ["v1", "integrations", "google", "callback"], cfg);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toContain("result=connected");
    expect(res.headers.getSetCookie()).toEqual(["a=1; HttpOnly", "b=2; Path=/"]);
  });

  it("forwards request bodies for state-changing methods", async () => {
    const fetchMock = vi.fn(async () => upstream("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await proxyRequest(new Request("https://dash.example.com/api/v1/auth/login", { method: "POST", body: '{"email":"a"}', headers: { "content-type": "application/json" } }), ["v1", "auth", "login"], cfg);
    const init = (fetchMock.mock.calls[0] as unknown as [URL, RequestInit])[1];
    expect(init.method).toBe("POST");
    expect(await new Response(init.body).text()).toBe('{"email":"a"}');
  });

  it("rejects dot segments and reports a sleeping/unreachable API", async () => {
    expect((await proxyRequest(new Request("https://d/api/x"), ["..", "health"], cfg)).status).toBe(400);
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    const res = await proxyRequest(new Request("https://d/api/v1/auth/me"), ["v1", "auth", "me"], cfg);
    expect(res.status).toBe(502);
    expect((await proxyRequest(new Request("https://d/api/v1/x"), ["v1"], { ...cfg, backendUrl: undefined })).status).toBe(500);
  });

  it("does not relay an IP without a secret or trusted hop", () => {
    const h = new Headers({ "x-forwarded-for": "6.6.6.6" });
    expect(clientIpFrom(h, 0)).toBeUndefined();
    expect(clientIpFrom(h, 2)).toBeUndefined();
  });
});
