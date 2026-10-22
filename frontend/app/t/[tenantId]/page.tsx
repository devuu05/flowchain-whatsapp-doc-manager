"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { DocumentTable } from "@/components/DocumentTable";
import { EmptyState, ErrorAlert, Spinner, Stat } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function OverviewPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const { data, error, loading, reload } = useApi<Overview>(`/tenants/${tenantId}/overview`);
  return (
    <>
      <h1>Overview</h1>
      <ErrorAlert error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && (
        <>
          <section className="stats" aria-label="Processing statistics">
            <Stat label="Documents stored" value={data.stored} hint={`${data.documents_this_month} received this month`} />
            <Stat label="Awaiting description" value={data.awaiting_description} />
            <Stat label="Needs review" value={data.review_required} />
            <Stat label="Failed / rejected" value={data.failed} />
            <Stat label="Jobs in queue" value={data.jobs_queued} />
            <Stat label="Dead jobs" value={data.jobs_dead} hint={data.jobs_dead ? "See Failures" : undefined} />
          </section>
          {Object.keys(data.by_type).length > 0 && (
            <section className="card">
              <h2>Stored by type</h2>
              <ul className="chips">
                {Object.entries(data.by_type).map(([k, v]) => (
                  <li key={k}><Link href={`/t/${tenantId}/documents?document_type=${encodeURIComponent(k)}`}>{k}: {v}</Link></li>
                ))}
              </ul>
            </section>
          )}
          <section className="card">
            <h2>Recent documents</h2>
            {data.recent_documents.length ? (
              <DocumentTable tenantId={tenantId} docs={data.recent_documents} />
            ) : (
              <EmptyState title="No documents yet">Send a file to your connected WhatsApp number to get started.</EmptyState>
            )}
          </section>
          <section className="card">
            <h2>Recent activity</h2>
            {data.recent_activity.length ? (
              <ul className="activity">
                {data.recent_activity.map((a) => (
                  <li key={a.id}><span className="muted">{formatDateTime(a.created_at)}</span> {a.action}</li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No activity yet" />
            )}
          </section>
        </>
      )}
    </>
  );
}
