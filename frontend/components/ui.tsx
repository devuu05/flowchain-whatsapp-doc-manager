"use client";

import { useEffect, useId, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="spinner" role="status" aria-live="polite">
      <span className="spinner-dot" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorAlert({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : "Something went wrong";
  const rid = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div className="alert alert-error" role="alert">
      <strong>Error:</strong> {message}
      {rid && <span className="muted"> (ref {rid.slice(0, 8)})</span>}
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "success" | "warning"; children: React.ReactNode }) {
  return (
    <div className={`alert alert-${kind}`} role={kind === "success" ? "status" : "note"}>
      {children}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  stored: "ok", succeeded: "ok", connected: "ok", sent: "ok", active: "ok",
  queued: "info", processing: "info", running: "info", received: "info", pending: "info",
  awaiting_description: "warn", error: "warn",
  failed: "bad", dead: "bad", rejected: "bad", disconnected: "muted", cancelled: "muted",
};

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? "muted";
  return <span className={`badge badge-${tone}`}>{status.replaceAll("_", " ")}</span>;
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint muted">{hint}</div>}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <nav className="pagination" aria-label="Pagination">
      <button type="button" className="btn btn-small" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span aria-live="polite">
        Page {page} of {pages} · {total} total
      </span>
      <button type="button" className="btn btn-small" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </nav>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => React.ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <p className="hint muted">{hint}</p>}
    </div>
  );
}

/** Confirmation dialog; optionally requires typing a phrase before confirming. */
export function ConfirmDialog({
  open, title, children, confirmLabel = "Confirm", requirePhrase, danger, onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  children?: React.ReactNode;
  confirmLabel?: string;
  requirePhrase?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  const inputId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setTyped("");
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) {
      if (typeof d.close === "function") d.close();
      else d.removeAttribute("open");
    }
  }, [open]);
  const blocked = requirePhrase !== undefined && typed !== requirePhrase;
  return (
    <dialog ref={ref} className="dialog" aria-labelledby={`${inputId}-title`} onCancel={onCancel}>
      <h2 id={`${inputId}-title`}>{title}</h2>
      {children}
      {requirePhrase !== undefined && (
        <div className="field">
          <label htmlFor={inputId}>
            Type <code>{requirePhrase}</code> to confirm
          </label>
          <input id={inputId} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </div>
      )}
      <div className="row-end">
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={blocked} onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
