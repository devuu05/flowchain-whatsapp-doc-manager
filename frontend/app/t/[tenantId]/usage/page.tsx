"use client";

import { useParams } from "next/navigation";

import { ErrorAlert, Spinner, Stat } from "@/components/ui";
import { formatBytes, formatDateTime, percent } from "@/lib/format";
import type { Usage } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = percent(used, limit);
  return (
    <div className="meter-row">
      <div className="row-between"><span>{label}</span><span>{used} / {limit}</span></div>
      <meter min={0} max={100} value={pct} low={70} high={90} optimum={0} aria-label={`${label}: ${pct}% used`} />
    </div>
  );
}

export default function UsagePage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const { data, error, loading, reload } = useApi<Usage>(`/tenants/${tenantId}/usage`);
  return (
    <>
      <h1>Usage &amp; quotas</h1>
      <ErrorAlert error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && (
        <>
          <p className="muted">Current period since {formatDateTime(data.month_start)} (UTC month).</p>
          <section className="card">
            <Meter label="Documents this month" used={data.documents_this_month} limit={data.monthly_document_quota} />
            <Meter label="AI classifications this month" used={data.ai_classifications_this_month} limit={data.monthly_ai_quota} />
          </section>
          <section className="stats">
            <Stat label="WhatsApp messages sent" value={data.messages_sent_this_month} />
            <Stat label="Stored this month" value={formatBytes(data.storage_bytes_this_month)} hint="in Google Drive" />
            <Stat label="Temporary files held" value={formatBytes(data.transient_blob_bytes)} hint="awaiting processing" />
            <Stat label="AI processing" value={data.ai_enabled ? "On" : "Off"} hint={data.ai_provider_configured ? undefined : "no provider key"} />
          </section>
        </>
      )}
    </>
  );
}
