"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DocumentTable } from "@/components/DocumentTable";
import { EmptyState, ErrorAlert, Pagination, Spinner } from "@/components/ui";
import { qs } from "@/lib/api";
import type { Category, DocumentItem, Page } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STATUSES = ["", "stored", "awaiting_description", "queued", "processing", "failed", "rejected"];

export function DocumentsView() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const router = useRouter();
  const params = useSearchParams();
  const [form, setForm] = useState({
    q: params.get("q") ?? "",
    document_type: params.get("document_type") ?? "",
    status: params.get("status") ?? "",
    period: params.get("period") ?? "",
  });
  const page = Number(params.get("page") ?? "1") || 1;
  const filters = {
    q: params.get("q"),
    document_type: params.get("document_type"),
    status: params.get("status"),
    period: params.get("period"),
    review_required: params.get("review_required"),
    page,
    page_size: 25,
  };
  const { data, error, loading, reload } = useApi<Page<DocumentItem>>(`/tenants/${tenantId}/documents${qs(filters)}`);
  const cats = useApi<Category[]>(`/tenants/${tenantId}/categories`);

  function apply(next: Record<string, string | number>) {
    router.push(`/t/${tenantId}/documents${qs({ ...form, ...next })}`);
  }

  return (
    <>
      <h1>Documents</h1>
      <form className="filters" role="search" onSubmit={(e) => { e.preventDefault(); apply({ page: 1 }); }}>
        <label>
          Search
          <input value={form.q} placeholder="filename, company, description" onChange={(e) => setForm({ ...form, q: e.target.value })} />
        </label>
        <label>
          Type
          <select value={form.document_type} onChange={(e) => setForm({ ...form, document_type: e.target.value })}>
            <option value="">All types</option>
            {cats.data?.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <label>
          Status
          <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replaceAll("_", " ") : "Any status"}</option>)}
          </select>
        </label>
        <label>
          Period
          <input value={form.period} placeholder="2026-09 or 2026" onChange={(e) => setForm({ ...form, period: e.target.value })} />
        </label>
        <button type="submit" className="btn btn-primary">Apply</button>
      </form>
      <ErrorAlert error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && data.items.length === 0 && <EmptyState title="No documents match these filters" />}
      {data && data.items.length > 0 && (
        <>
          <DocumentTable tenantId={tenantId} docs={data.items} />
          <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => apply({ page: p })} />
        </>
      )}
    </>
  );
}

function KeyedDocumentsView() {
  // Remount when the URL changes (e.g. back button) so the filter form mirrors the URL.
  const params = useSearchParams();
  return <DocumentsView key={params.toString()} />;
}

export default function DocumentsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <KeyedDocumentsView />
    </Suspense>
  );
}
