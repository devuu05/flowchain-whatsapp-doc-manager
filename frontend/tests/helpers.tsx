import { vi } from "vitest";

export const nav = {
  push: vi.fn(),
  replace: vi.fn(),
  pathname: "/t/t1",
  params: { tenantId: "t1" } as Record<string, string>,
  search: new URLSearchParams(),
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  usePathname: () => nav.pathname,
  useParams: () => nav.params,
  useSearchParams: () => nav.search,
}));

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export type Route = (url: string, init?: RequestInit) => Response | undefined;

/** Install a fetch mock routing by URL substring; records calls. */
export function mockFetch(routes: Record<string, Response | (() => Response)>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.includes(k));
    if (!key) return json({ error: { code: "not_found", message: `no mock for ${url}` } }, 404);
    const r = routes[key];
    const res = typeof r === "function" ? r() : r;
    return res!.clone();
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export const ME = {
  id: "u1",
  email: "owner@example.com",
  full_name: "Owner",
  is_platform_admin: false,
  csrf_token: "csrf-abc",
  memberships: [{ tenant_id: "t1", tenant_name: "Acme", role: "owner" }],
};
