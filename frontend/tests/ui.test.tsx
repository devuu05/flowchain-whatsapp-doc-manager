import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, StatusBadge } from "@/components/ui";
import { formatBytes, maskPhone, percent } from "@/lib/format";

describe("ConfirmDialog", () => {
  it("requires the exact phrase before confirming destructive actions", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open title="Delete all?" requirePhrase="acme-pilot" danger confirmLabel="Delete" onConfirm={onConfirm} onCancel={() => {}} />);
    const btn = screen.getByRole("button", { name: "Delete", hidden: true });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Type/, { selector: "input" }), "acme-pilo");
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Type/, { selector: "input" }), "t");
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("formatting", () => {
  it("formats bytes, phones and percentages", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(16 * 1024 * 1024)).toBe("16 MB");
    expect(maskPhone("919876543210")).toBe("+91******3210");
    expect(percent(5, 10)).toBe(50);
    expect(percent(5, 0)).toBe(100);
  });

  it("renders readable status badges", () => {
    render(<StatusBadge status="awaiting_description" />);
    expect(screen.getByText("awaiting description")).toHaveClass("badge-warn");
  });
});
