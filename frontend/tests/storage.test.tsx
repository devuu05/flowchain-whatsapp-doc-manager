import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DriveSettings from "@/app/t/[tenantId]/settings/drive/page";

import { json, mockFetch, nav } from "./helpers";

const ONEDRIVE = {
  id: "i1", kind: "onedrive", provider: "onedrive", name: "OneDrive", status: "connected",
  settings: { root_folder_name: "Flow Chain Documents", account: "owner@outlook.com" },
  last_error: null, last_checked_at: null, has_secrets: true, webhook_url: null,
  created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-01T10:00:00Z",
};

describe("cloud storage settings", () => {
  beforeEach(() => {
    nav.params = { tenantId: "t1" };
    nav.search = new URLSearchParams();
  });

  it("offers OneDrive and Google Drive when nothing is connected", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const calls = mockFetch({
      "/tenants/t1/integrations/microsoft/start": json({ authorization_url: "https://login.microsoftonline.com/x" }),
      "/tenants/t1/integrations": json([]),
    });
    render(<DriveSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Connect OneDrive" }));
    expect(screen.getByRole("button", { name: "Connect Google Drive" })).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/integrations/microsoft/start") && c.init?.method === "POST")).toBe(true);
    expect(assign).toHaveBeenCalledWith("https://login.microsoftonline.com/x");
  });

  it("shows the connected OneDrive account and the callback result", async () => {
    nav.search = new URLSearchParams("result=connected&provider=onedrive");
    mockFetch({ "/tenants/t1/integrations": json([ONEDRIVE]) });
    render(<DriveSettings />);
    expect(await screen.findByText("owner@outlook.com")).toBeInTheDocument();
    expect(screen.getByText("OneDrive connected.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
  });
});
