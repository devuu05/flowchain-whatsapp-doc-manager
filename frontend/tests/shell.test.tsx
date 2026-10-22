import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { AppShell } from "@/components/AppShell";
import { AuthProvider } from "@/components/AuthProvider";

import { ME, json, mockFetch, nav } from "./helpers";

describe("AppShell authorization UX", () => {
  beforeEach(() => {
    nav.pathname = "/t/t1";
    nav.replace.mockReset();
  });

  it("redirects unauthenticated users to login", async () => {
    mockFetch({ "/auth/me": json({ error: { code: "not_authenticated", message: "x" } }, 401) });
    render(<AuthProvider><AppShell tenantId="t1"><p>secret</p></AppShell></AuthProvider>);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?next=%2Ft%2Ft1"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("shows admin navigation to owners", async () => {
    mockFetch({ "/auth/me": json(ME) });
    render(<AuthProvider><AppShell tenantId="t1"><p>content</p></AppShell></AuthProvider>);
    expect(await screen.findByText("content")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit log" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });

  it("hides admin navigation from members", async () => {
    mockFetch({ "/auth/me": json({ ...ME, memberships: [{ tenant_id: "t1", tenant_name: "Acme", role: "member" }] }) });
    render(<AuthProvider><AppShell tenantId="t1"><p>content</p></AppShell></AuthProvider>);
    await screen.findByText("content");
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Documents" })).toBeInTheDocument();
  });

  it("does not render other tenants", async () => {
    mockFetch({ "/auth/me": json(ME) });
    render(<AuthProvider><AppShell tenantId="other"><p>content</p></AppShell></AuthProvider>);
    expect(await screen.findByText("Workspace not found")).toBeInTheDocument();
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });
});
