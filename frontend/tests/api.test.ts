import { describe, expect, it, vi } from "vitest";

import { ApiError, api, post, qs, setCsrfToken } from "@/lib/api";

import { json, mockFetch } from "./helpers";

describe("api client", () => {
  it("sends CSRF header only on state-changing requests", async () => {
    const calls = mockFetch({ "/api/v1/x": json({ ok: true }) });
    setCsrfToken("tok-1");
    await api("/x");
    await post("/x", { a: 1 });
    const get = new Headers(calls[0]!.init?.headers);
    const p = new Headers(calls[1]!.init?.headers);
    expect(get.get("X-CSRF-Token")).toBeNull();
    expect(p.get("X-CSRF-Token")).toBe("tok-1");
    expect(p.get("Content-Type")).toBe("application/json");
    expect(calls[1]!.init?.credentials).toBe("same-origin");
  });

  it("raises ApiError with backend error details", async () => {
    mockFetch({ "/api/v1/bad": json({ error: { code: "forbidden", message: "No access", request_id: "rid123" } }, 403) });
    await expect(api("/bad")).rejects.toMatchObject({ status: 403, code: "forbidden", message: "No access", requestId: "rid123" });
  });

  it("broadcasts unauthorized on 401", async () => {
    mockFetch({ "/api/v1/me": json({ error: { code: "not_authenticated", message: "x" } }, 401) });
    const listener = vi.fn();
    window.addEventListener("fc:unauthorized", listener);
    await expect(api("/me")).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledOnce();
  });

  it("builds query strings without empty values", () => {
    expect(qs({ a: "1", b: "", c: null, d: undefined, e: 0, f: false })).toBe("?a=1&e=0&f=false");
    expect(qs({})).toBe("");
  });
});
