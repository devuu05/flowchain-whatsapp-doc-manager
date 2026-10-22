"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { useMembership } from "@/components/AppShell";
import { ConfirmDialog, ErrorAlert, Notice, Spinner, StatusBadge } from "@/components/ui";
import { del, patch, post } from "@/lib/api";
import { formatBytes, formatDateTime, maskPhone } from "@/lib/format";
import type { DocumentDetail } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function DocumentDetailPage() {
  const { tenantId, documentId } = useParams<{ tenantId: string; documentId: string }>();
  const router = useRouter();
  const membership = useMembership(tenantId);
  const isAdmin = membership?.role !== "member";
  const path = `/tenants/${tenantId}/documents/${documentId}`;
  const { data: doc, error, loading, reload } = useApi<DocumentDetail>(path);
  const [actionError, setActionError] = useState<unknown>();
  const [notice, setNotice] = useState<string>();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [edit, setEdit] = useState<{ document_type: string; company_or_person: string; date_or_period: string } | null>(null);

  async function run(fn: () => Promise<unknown>, done: string) {
    setActionError(undefined);
    try {
      await fn();
      setNotice(done);
      reload();
    } catch (e) {
      setActionError(e);
    }
  }

  if (loading && !doc) return <Spinner />;
  if (error) return <ErrorAlert error={error} onRetry={reload} />;
  if (!doc) return null;

  return (
    <>
      <h1 className="break">{doc.standardized_filename ?? doc.original_filename}</h1>
      <p><StatusBadge status={doc.status} /> {doc.review_required && <span className="badge badge-warn">needs review</span>}</p>
      {notice && <Notice kind="success">{notice}</Notice>}
      <ErrorAlert error={actionError} />
      {doc.failure_reason && <Notice kind="warning">Failure: {doc.failure_reason}</Notice>}
      <div className="actions">
        {doc.status === "stored" && (
          <a className="btn" href={`/api/v1${path}/download`}>Download original</a>
        )}
        {isAdmin && doc.status === "failed" && (
          <button type="button" className="btn btn-primary" onClick={() => run(() => post(`${path}/retry`), "Retry queued.")}>Retry processing</button>
        )}
        {isAdmin && doc.status === "stored" && !edit && (
          <button type="button" className="btn" onClick={() => setEdit({
            document_type: doc.document_type ?? "", company_or_person: doc.company_or_person ?? "", date_or_period: doc.date_or_period ?? "",
          })}>Correct details</button>
        )}
        {isAdmin && <button type="button" className="btn btn-danger" onClick={() => setConfirmDelete(true)}>Delete record</button>}
      </div>
      {edit && (
        <form className="card form-grid" onSubmit={(e) => {
          e.preventDefault();
          void run(() => patch(path, { ...edit, company_or_person: edit.company_or_person || null, date_or_period: edit.date_or_period || null }), "Details updated.").then(() => setEdit(null));
        }}>
          <label>Type<input value={edit.document_type} onChange={(e) => setEdit({ ...edit, document_type: e.target.value })} /></label>
          <label>Company / person<input value={edit.company_or_person} onChange={(e) => setEdit({ ...edit, company_or_person: e.target.value })} /></label>
          <label>Date / period<input value={edit.date_or_period} placeholder="2026-09-15 or 2026-09" onChange={(e) => setEdit({ ...edit, date_or_period: e.target.value })} /></label>
          <div className="row-end">
            <button type="button" className="btn" onClick={() => setEdit(null)}>Cancel</button>
            <button type="submit" className="btn btn-primary">Save</button>
          </div>
        </form>
      )}
      <section className="card">
        <h2>Details</h2>
        <dl className="dl">
          <dt>Original filename</dt><dd className="break">{doc.original_filename}</dd>
          <dt>Type</dt><dd>{doc.document_type ?? "—"}</dd>
          <dt>Company / person</dt><dd>{doc.company_or_person ?? "Unknown"}</dd>
          <dt>Date / period</dt><dd>{doc.date_or_period ?? "Unknown"}</dd>
          <dt>Folder</dt><dd>{doc.folder ?? "—"}</dd>
          <dt>Description from sender</dt><dd>{doc.description ?? "—"}</dd>
          <dt>Summary</dt><dd>{doc.summary ?? "—"}</dd>
          <dt>Classifier</dt><dd>{doc.classifier ?? "—"} {doc.confidence !== null && `(confidence ${(doc.confidence * 100).toFixed(0)}%)`}</dd>
          <dt>Extraction</dt><dd>{doc.extraction_status ?? "—"} {doc.page_count !== null && `· ${doc.page_count} pages/sheets/slides`}</dd>
          <dt>Size / type</dt><dd>{formatBytes(doc.size_bytes)} · {doc.mime_type}</dd>
          <dt>Sender</dt><dd>{maskPhone(doc.sender)}</dd>
          <dt>Received</dt><dd>{formatDateTime(doc.created_at)}</dd>
          <dt>Stored</dt><dd>{formatDateTime(doc.stored_at)}</dd>
          <dt>Storage</dt><dd>{doc.storage_provider ?? "—"}</dd>
        </dl>
        {doc.extraction_warnings.length > 0 && (
          <>
            <h3>Warnings</h3>
            <ul>{doc.extraction_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </>
        )}
      </section>
      <section className="card">
        <h2>Processing history</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Job</th><th scope="col">Status</th><th scope="col">Attempts</th><th scope="col">Last error</th><th scope="col">Created</th></tr></thead>
            <tbody>
              {doc.jobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.kind}</td><td><StatusBadge status={j.status} /></td><td>{j.attempts}/{j.max_attempts}</td>
                  <td className="break">{j.last_error ?? "—"}</td><td>{formatDateTime(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <ConfirmDialog open={confirmDelete} title="Delete this document record?" danger confirmLabel="Delete"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          void del(path).then(() => router.replace(`/t/${tenantId}/documents`)).catch(setActionError);
        }}>
        <p>This removes the metadata from Flow Chain. The original file stays in Google Drive.</p>
      </ConfirmDialog>
    </>
  );
}
