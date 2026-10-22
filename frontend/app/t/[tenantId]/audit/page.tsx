"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { EmptyState, ErrorAlert, Pagination, Spinner } from "@/components/ui";
import { qs } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AuditEvent, Page } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function AuditPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState("");
  const { data, error, loading, reload } = useApi<Page<AuditEvent>>(`/tenants/${tenantId}/audit${qs({ page, page_size: 50, action: applied })}`);
  return (
    <>
      <h1>Audit log</h1>
      <form className="filters" role="search" onSubmit={(e) => { e.preventDefault(); setPage(1); setApplied(action); }}>
        <label>Action prefix<input value={action} placeholder="e.g. document. or auth." onChange={(e) => setAction(e.target.value)} /></label>
        <button type="submit" className="btn btn-primary">Filter</button>
      </form>
      <ErrorAlert error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && data.items.length === 0 && <EmptyState title="No audit events" />}
      {data && data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">When</th><th scope="col">Action</th><th scope="col">Actor</th><th scope="col">Target</th><th scope="col">Details</th></tr></thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id}>
                    <td>{formatDateTime(a.created_at)}</td>
                    <td><code>{a.action}</code></td>
                    <td>{a.actor_type}{a.ip ? ` · ${a.ip}` : ""}</td>
                    <td>{a.target_type ? `${a.target_type}` : "—"}</td>
                    <td className="break"><code>{JSON.stringify(a.details)}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
