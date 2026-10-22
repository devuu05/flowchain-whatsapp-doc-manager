"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { ConfirmDialog, EmptyState, ErrorAlert, Field, Notice, Spinner, StatusBadge } from "@/components/ui";
import { del, post } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Integration } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function WhatsAppSettings() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const { data, error, loading, reload } = useApi<Integration[]>(`/tenants/${tenantId}/integrations`);
  const conns = data?.filter((i) => i.kind === "whatsapp" && i.status !== "disconnected") ?? [];
  const [form, setForm] = useState({ name: "WhatsApp", phone_number: "", api_key: "", webhook_secret: "" });
  const [err, setErr] = useState<unknown>();
  const [msg, setMsg] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [disconnect, setDisconnect] = useState<Integration | null>(null);

  async function act(fn: () => Promise<unknown>, done: string) {
    setErr(undefined);
    setBusy(true);
    try {
      await fn();
      setMsg(done);
      reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>WhatsApp connection</h1>
      <Notice>
        Uses WasenderAPI. Enter the session&apos;s API key and webhook secret from the Wasender dashboard. They are
        encrypted on the server and never shown again. Then paste the webhook URL below into Wasender and enable the
        <em> messages.received</em> event.
      </Notice>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorAlert error={err ?? error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {conns.length === 0 && data && <EmptyState title="No WhatsApp number connected" />}
      {conns.map((c) => (
        <section className="card" key={c.id}>
          <h2>{c.name} <StatusBadge status={c.status} /></h2>
          <dl className="dl">
            <dt>Phone number</dt><dd>+{String(c.settings.phone_number ?? "")}</dd>
            <dt>Provider</dt><dd>{c.provider}</dd>
            <dt>Webhook URL</dt><dd><code className="break">{c.webhook_url}</code></dd>
            <dt>Secrets stored</dt><dd>{c.has_secrets ? "Yes (encrypted)" : "No"}</dd>
            <dt>Last check</dt><dd>{formatDateTime(c.last_checked_at)} {c.last_error && <span className="text-bad">— {c.last_error}</span>}</dd>
          </dl>
          <div className="actions">
            <button type="button" className="btn" disabled={busy} onClick={() => act(() => post(`/tenants/${tenantId}/integrations/${c.id}/check`), "Connection checked.")}>Check connection</button>
            <button type="button" className="btn btn-danger" onClick={() => setDisconnect(c)}>Disconnect</button>
          </div>
        </section>
      ))}
      <section className="card">
        <h2>Connect a number</h2>
        <form className="form-grid" onSubmit={(e) => {
          e.preventDefault();
          void act(() => post(`/tenants/${tenantId}/integrations/whatsapp`, { provider: "wasender", ...form }), "WhatsApp number connected. Configure the webhook URL in Wasender next.")
            .then(() => setForm({ name: "WhatsApp", phone_number: "", api_key: "", webhook_secret: "" }));
        }}>
          <Field label="Label">{(id) => <input id={id} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
          <Field label="WhatsApp number (with country code)">{(id) => <input id={id} required inputMode="tel" placeholder="+91 98765 43210" value={form.phone_number} onChange={(e) => setForm({ ...form, phone_number: e.target.value })} />}</Field>
          <Field label="Wasender session API key">{(id) => <input id={id} required type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />}</Field>
          <Field label="Wasender webhook secret">{(id) => <input id={id} required type="password" autoComplete="off" value={form.webhook_secret} onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })} />}</Field>
          <div className="row-end"><button type="submit" className="btn btn-primary" disabled={busy}>Connect</button></div>
        </form>
      </section>
      <ConfirmDialog open={disconnect !== null} title="Disconnect this WhatsApp number?" danger confirmLabel="Disconnect"
        onCancel={() => setDisconnect(null)}
        onConfirm={() => {
          const c = disconnect;
          setDisconnect(null);
          if (c) void act(() => del(`/tenants/${tenantId}/integrations/${c.id}`), "Disconnected. Stored secrets were deleted.");
        }}>
        <p>Incoming messages will be ignored and the stored API key and webhook secret are erased.</p>
      </ConfirmDialog>
    </>
  );
}
