import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { LoginForm } from "@/app/login/page";
import { AuthProvider } from "@/components/AuthProvider";

import { ME, json, mockFetch, nav } from "./helpers";

describe("login", () => {
  beforeEach(() => {
    nav.pathname = "/login";
    nav.replace.mockReset();
  });

  it("signs in and redirects to a safe next path", async () => {
    nav.search = new URLSearchParams("next=/t/t1/documents");
    const calls = mockFetch({
      "/auth/me": json({ error: { code: "not_authenticated", message: "x" } }, 401),
      "/auth/login": json(ME),
    });
    render(<AuthProvider><LoginForm /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Correct-Horse-9");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/t/t1/documents"));
    const login = calls.find((c) => c.url.endsWith("/auth/login"))!;
    expect(JSON.parse(String(login.init?.body))).toEqual({ email: "owner@example.com", password: "Correct-Horse-9" });
  });

  it("rejects open redirects", async () => {
    nav.search = new URLSearchParams("next=//evil.example.com");
    mockFetch({ "/auth/me": json({}, 401), "/auth/login": json(ME) });
    render(<AuthProvider><LoginForm /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/app"));
  });

  it("shows the error and clears the password on failure", async () => {
    nav.search = new URLSearchParams();
    mockFetch({
      "/auth/me": json({}, 401),
      "/auth/login": json({ error: { code: "not_authenticated", message: "Invalid email or password" } }, 401),
    });
    render(<AuthProvider><LoginForm /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
});
