"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { useMembership } from "@/components/AppShell";
import { ConfirmDialog, ErrorAlert, Notice, Spinner } from "@/components/ui";
import { post } from "@/lib/api";
import type { Tenant } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function DataPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const isOwner = useMembership(tenantId)?.role === "owner";
  const tenant = useApi<Tenant>(`/tenants/${tenantId}`);
  const [trash, setTrash] = useState(false);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<unknown>();
  const [result, setResult] = useState<string>();

  if (tenant.loading && !tenant.data) return <Spinner />;
  const slug = tenant.data?.slug ?? "";
  return (
    <>
      <h1>Export &amp; deletion</h1>
      <ErrorAlert error={err ?? tenant.error} />
      {!isOwner && <Notice kind="warning">Only the workspace owner can export or delete data.</Notice>}
      <section className="card">
        <h2>Export</h2>
        <p>Download all document metadata, categories, authorized senders, message log and audit events as JSON. Original
          files are already in your Google Drive. Integration secrets are never exported.</p>
        {isOwner && <a className="btn btn-primary" href={`/api/v1/tenants/${tenantId}/export`}>Download export</a>}
      </section>
      <section className="card danger-zone">
        <h2>Delete workspace data</h2>
        <p>Permanently deletes document records, conversations, pending files and queued work for this workspace. Audit
          events are retained as a record of the deletion. This cannot be undone.</p>
        <label className="checkbox">
          <input type="checkbox" checked={trash} onChange={(e) => setTrash(e.target.checked)} disabled={!isOwner} />
          Also move the files Flow Chain created in Google Drive to the Drive trash
        </label>
        {result && <Notice kind="success">{result}</Notice>}
        <button type="button" className="btn btn-danger" disabled={!isOwner} onClick={() => setOpen(true)}>Delete all data…</button>
      </section>
      <ConfirmDialog open={open} title="Delete all workspace data?" danger confirmLabel="Delete permanently" requirePhrase={slug}
        onCancel={() => setOpen(false)}
        onConfirm={() => {
          setOpen(false);
          setErr(undefined);
          post<{ deleted: Record<string, number>; drive_trashed: number; drive_errors: number }>(`/tenants/${tenantId}/delete-data`, { confirm_slug: slug, trash_drive_files: trash })
            .then((r) => setResult(`Deleted ${r.deleted.documents ?? 0} documents; ${r.drive_trashed} Drive files trashed${r.drive_errors ? `, ${r.drive_errors} could not be trashed` : ""}.`))
            .catch(setErr);
        }}>
        <p>This permanently removes all document records for <strong>{tenant.data?.name}</strong>.</p>
      </ConfirmDialog>
    </>
  );
}
