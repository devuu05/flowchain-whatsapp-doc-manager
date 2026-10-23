import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LandingPage from "@/app/page";

describe("landing page", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("explains the product and links to sign-in without calling the API", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<LandingPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Send it on WhatsApp.");
    for (const h of ["Three steps. Zero filing.", "Your documents stay yours", "Questions, answered"]) {
      expect(screen.getByRole("heading", { name: h })).toBeInTheDocument();
    }
    expect(screen.getAllByRole("link", { name: /sign in/i })[0]).toHaveAttribute("href", "/login");
    expect(screen.getByRole("figure", { name: /example whatsapp conversation/i })).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows a pilot request link only when a contact email is configured", () => {
    render(<LandingPage />);
    expect(screen.queryByRole("link", { name: "Request a pilot" })).not.toBeInTheDocument();
    vi.stubEnv("CONTACT_EMAIL", "hello@example.com");
    const { container } = render(<LandingPage />);
    const links = within(container).getAllByRole("link", { name: "Request a pilot" });
    expect(links[0]?.getAttribute("href")).toMatch(/^mailto:hello@example\.com\?subject=/);
  });
});
