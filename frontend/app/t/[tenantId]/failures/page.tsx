"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { useMembership } from "@/components/AppShell";
import { EmptyState, ErrorAlert, Notice, Pagination, Spinner, StatusBadge } from "@/components/ui";
import { post, qs } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { DocumentItem, Job, Page } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function FailuresPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const isAdmin = useMembership(tenantId)?.role !== "member";
  const [page, setPage] = useState(1);
  const jobs = useApi<Page<Job>>(`/tenants/${tenantId}/jobs${qs({ status: "dead", page, page_size: 25 })}`);
  const failed = useApi<Page<DocumentItem>>(`/tenants/${tenantId}/documents${qs({ status: "failed", page_size: 50 })}`);
  const rejected = useApi<Page<DocumentItem>>(`/tenants/${tenantId}/documents${qs({ status: "rejected", page_size: 50 })}`);
  const [err, setErr] = useState<unknown>();
  const [msg, setMsg] = useState<string>();

  async function retry(id: string) {
    setErr(undefined);
    try {
      await post(`/tenants/${tenantId}/jobs/${id}/retry`);
      setMsg("Job re-queued.");
      jobs.reload();
      failed.reload();
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <>
      <h1>Processing failures</h1>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorAlert error={err} />
      <section className="card">
        <h2>Dead jobs (retries exhausted or permanent errors)</h2>
        <ErrorAlert error={jobs.error} onRetry={jobs.reload} />
        {jobs.loading && !jobs.data && <Spinner />}
        {jobs.data && jobs.data.items.length === 0 && <EmptyState title="No dead jobs" />}
        {jobs.data && jobs.data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table>
                <thead><tr><th scope="col">Kind</th><th scope="col">Status</th><th scope="col">Category</th><th scope="col">Attempts</th><th scope="col">Error</th><th scope="col">When</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>
                  {jobs.data.items.map((j) => (
                    <tr key={j.id}>
                      <td>{j.kind}{j.document_id && <> · <Link href={`/t/${tenantId}/documents/${j.document_id}`}>document</Link></>}</td>
                      <td><StatusBadge status={j.status} /></td>
                      <td>{j.error_category ?? "—"}</td>
                      <td>{j.attempts}/{j.max_attempts}</td>
                      <td className="break">{j.last_error ?? "—"}</td>
                      <td>{formatDateTime(j.finished_at ?? j.created_at)}</td>
                      <td>{isAdmin && <button type="button" className="btn btn-small" onClick={() => void retry(j.id)}>Retry</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={jobs.data.page} pageSize={jobs.data.page_size} total={jobs.data.total} onPage={setPage} />
          </>
        )}
      </section>
      {[{ title: "Failed documents", state: failed }, { title: "Rejected files", state: rejected }].map(({ title, state }) => (
        <section className="card" key={title}>
          <h2>{title}</h2>
          <ErrorAlert error={state.error} onRetry={state.reload} />
          {state.data && state.data.items.length === 0 && <EmptyState title={`No ${title.toLowerCase()}`} />}
          {state.data && state.data.items.length > 0 && (
            <ul className="activity">
              {state.data.items.map((d) => (
                <li key={d.id}>
                  <Link href={`/t/${tenantId}/documents/${d.id}`}>{d.original_filename}</Link>{" "}
                  <span className="muted">{formatDateTime(d.created_at)} — {d.failure_reason ?? "unknown reason"}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </>
  );
}
