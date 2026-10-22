"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { useMembership } from "@/components/AppShell";
import { ErrorAlert, Field, Notice, Spinner } from "@/components/ui";
import { patch, post } from "@/lib/api";
import type { Category, Tenant, Usage } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function AiSettings() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const isOwner = useMembership(tenantId)?.role === "owner";
  const tenant = useApi<Tenant>(`/tenants/${tenantId}`);
  const usage = useApi<Usage>(`/tenants/${tenantId}/usage`);
  const cats = useApi<Category[]>(`/tenants/${tenantId}/categories`);
  const [consent, setConsent] = useState("");
  const [newCat, setNewCat] = useState({ name: "", folder_name: "", keywords: "" });
  const [err, setErr] = useState<unknown>();
  const [msg, setMsg] = useState<string>();

  async function act(fn: () => Promise<unknown>, done: string, reload: () => void) {
    setErr(undefined);
    try {
      await fn();
      setMsg(done);
      reload();
    } catch (e) {
      setErr(e);
    }
  }

  const t = tenant.data;
  return (
    <>
      <h1>AI processing &amp; categories</h1>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorAlert error={err ?? tenant.error ?? cats.error} />
      {(tenant.loading || cats.loading) && <Spinner />}
      {t && (
        <section className="card">
          <h2>AI classification</h2>
          <p>
            Status: <strong>{t.ai_enabled ? "Enabled" : "Disabled"}</strong>
            {usage.data && !usage.data.ai_provider_configured && <span className="text-bad"> — no AI provider key is configured on the server</span>}
          </p>
          <Notice kind="warning">
            When enabled, document text and images are sent to Google Gemini for classification. On Google&apos;s free
            tier, Google may use this content to improve its products and humans may review it. Enable only after the
            client has authorized it and a paid (billed) Gemini project is configured. Categories marked
            &quot;never send to AI&quot; are always classified locally.
          </Notice>
          {t.ai_consent_note && <p className="muted">Consent record: {t.ai_consent_note}</p>}
          {isOwner ? (
            t.ai_enabled ? (
              <button type="button" className="btn" onClick={() => act(() => patch(`/tenants/${tenantId}`, { ai_enabled: false }), "AI processing disabled.", tenant.reload)}>Disable AI processing</button>
            ) : (
              <form className="form-grid" onSubmit={(e) => {
                e.preventDefault();
                void act(() => patch(`/tenants/${tenantId}`, { ai_enabled: true, ai_consent_note: consent }), "AI processing enabled.", tenant.reload);
              }}>
                <Field label="Consent record" hint="Who authorized AI processing, and when (stored in the audit log).">
                  {(id) => <input id={id} required minLength={10} value={consent} onChange={(e) => setConsent(e.target.value)} />}
                </Field>
                <div className="row-end"><button type="submit" className="btn btn-primary">Enable AI processing</button></div>
              </form>
            )
          ) : (
            <p className="muted">Only the workspace owner can change AI processing.</p>
          )}
        </section>
      )}
      <section className="card">
        <h2>Categories</h2>
        {cats.data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Name</th><th scope="col">Drive folder</th><th scope="col">Active</th><th scope="col">AI allowed</th></tr></thead>
              <tbody>
                {cats.data.map((c) => (
                  <tr key={c.id}>
                    <td>{c.name}</td>
                    <td>{c.folder_name}</td>
                    <td>
                      <input type="checkbox" aria-label={`${c.name} active`} checked={c.is_active} disabled={c.name === "Other"}
                        onChange={(e) => act(() => patch(`/tenants/${tenantId}/categories/${c.id}`, { is_active: e.target.checked }), "Category updated.", cats.reload)} />
                    </td>
                    <td>
                      <input type="checkbox" aria-label={`${c.name} may be sent to AI`} checked={c.ai_allowed}
                        onChange={(e) => act(() => patch(`/tenants/${tenantId}/categories/${c.id}`, { ai_allowed: e.target.checked }), "Category updated.", cats.reload)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <form className="form-grid" onSubmit={(e) => {
          e.preventDefault();
          void act(() => post(`/tenants/${tenantId}/categories`, {
            name: newCat.name, folder_name: newCat.folder_name || undefined,
            keywords: newCat.keywords.split(",").map((k) => k.trim()).filter(Boolean),
          }), "Category added.", cats.reload).then(() => setNewCat({ name: "", folder_name: "", keywords: "" }));
        }}>
          <h3>Add category</h3>
          <Field label="Name">{(id) => <input id={id} required value={newCat.name} pattern="[A-Za-z0-9 /&\-]+" onChange={(e) => setNewCat({ ...newCat, name: e.target.value })} />}</Field>
          <Field label="Drive folder (optional)">{(id) => <input id={id} value={newCat.folder_name} onChange={(e) => setNewCat({ ...newCat, folder_name: e.target.value })} />}</Field>
          <Field label="Keywords (comma separated)">{(id) => <input id={id} value={newCat.keywords} onChange={(e) => setNewCat({ ...newCat, keywords: e.target.value })} />}</Field>
          <div className="row-end"><button type="submit" className="btn btn-primary">Add</button></div>
        </form>
      </section>
    </>
  );
}
