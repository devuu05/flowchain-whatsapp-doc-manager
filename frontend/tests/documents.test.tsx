import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { DocumentsView } from "@/app/t/[tenantId]/documents/page";

import { json, mockFetch, nav } from "./helpers";

const DOC = {
  id: "d1", status: "stored", original_filename: "scan.pdf", standardized_filename: "Invoice_ABC_2026-09.pdf",
  extension: "pdf", mime_type: "application/pdf", size_bytes: 1000, document_type: "Invoice",
  company_or_person: "ABC Traders", date_or_period: "2026-09", doc_date: null, folder: "Invoices/2026",
  review_required: true, confidence: 0.5, sender: "919876543210", created_at: "2026-10-01T10:00:00Z",
  stored_at: "2026-10-01T10:00:05Z", failure_reason: null,
};

describe("documents page", () => {
  beforeEach(() => {
    nav.params = { tenantId: "t1" };
    nav.search = new URLSearchParams();
    nav.push.mockReset();
  });

  it("renders loading then data", async () => {
    mockFetch({
      "/tenants/t1/documents": json({ items: [DOC], total: 1, page: 1, page_size: 25 }),
      "/tenants/t1/categories": json([{ id: "c1", name: "Invoice", folder_name: "Invoices", is_active: true, ai_allowed: true, keywords: [] }]),
    });
    render(<DocumentsView />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByRole("link", { name: "Invoice_ABC_2026-09.pdf" })).toHaveAttribute("href", "/t/t1/documents/d1");
    expect(screen.getByText("ABC Traders")).toBeInTheDocument();
    expect(screen.getByText(/review/)).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 1 · 1 total")).toBeInTheDocument();
  });

  it("renders the empty state", async () => {
    mockFetch({ "/tenants/t1/documents": json({ items: [], total: 0, page: 1, page_size: 25 }), "/categories": json([]) });
    render(<DocumentsView />);
    expect(await screen.findByText("No documents match these filters")).toBeInTheDocument();
  });

  it("renders errors with a retry button", async () => {
    mockFetch({ "/tenants/t1/documents": json({ error: { code: "internal_error", message: "Internal error", request_id: "abcdef123" } }, 500), "/categories": json([]) });
    render(<DocumentsView />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Internal error");
    expect(alert).toHaveTextContent("ref abcdef12");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("applies filters through the URL", async () => {
    mockFetch({ "/tenants/t1/documents": json({ items: [], total: 0, page: 1, page_size: 25 }), "/categories": json([]) });
    render(<DocumentsView />);
    await userEvent.type(screen.getByLabelText("Search"), "ABC");
    await userEvent.type(screen.getByLabelText("Period"), "2026-09");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(nav.push).toHaveBeenCalledWith("/t/t1/documents?q=ABC&period=2026-09&page=1");
  });
});
