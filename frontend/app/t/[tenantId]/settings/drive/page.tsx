"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ConfirmDialog, ErrorAlert, Notice, Spinner, StatusBadge } from "@/components/ui";
import { del, post } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Integration } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Provider = "google" | "onedrive";

const LABEL: Record<Provider, string> = { google: "Google Drive", onedrive: "OneDrive" };

function resultMessage(result: string, provider: Provider): [string, "success" | "warning"] | undefined {
  const name = LABEL[provider];
  const messages: Record<string, [string, "success" | "warning"]> = {
    connected: [`${name} connected.`, "success"],
    denied: [`Access to ${name} was not granted.`, "warning"],
    forbidden: [`Only workspace admins can connect ${name}.`, "warning"],
    invalid_state: ["The connection link expired. Please try again.", "warning"],
    session_mismatch: [`Please finish connecting ${name} in the same browser session you started it from.`, "warning"],
    no_refresh_token:
      provider === "google"
        ? ["Google did not grant offline access. Remove the app at myaccount.google.com/permissions and retry.", "warning"]
        : ["Microsoft did not grant offline access. Please try again and accept all requested permissions.", "warning"],
    other_storage: ["Another storage provider is connected. Disconnect it first.", "warning"],
    error: [`${name} connection failed. Please try again.`, "warning"],
  };
  return messages[result];
}

function StorageSettingsView() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const params = useSearchParams();
  const { data, error, loading, reload } = useApi<Integration[]>(`/tenants/${tenantId}/integrations`);
  const storage = data?.find((i) => (i.kind === "google_drive" || i.kind === "onedrive") && i.status !== "disconnected");
  const [err, setErr] = useState<unknown>();
  const [msg, setMsg] = useState<string>();
  const [confirm, setConfirm] = useState(false);
  const resultProvider: Provider = params.get("provider") === "onedrive" ? "onedrive" : "google";
  const result = resultMessage(params.get("result") ?? "", resultProvider);
  const storageLabel = storage?.provider === "onedrive" ? "OneDrive" : "Google Drive";

  async function connect(provider: Provider) {
    setErr(undefined);
    try {
      const path = provider === "google" ? "google" : "microsoft";
      const r = await post<{ authorization_url: string }>(`/tenants/${tenantId}/integrations/${path}/start`);
      window.location.assign(r.authorization_url);
    } catch (e) {
      setErr(e);
    }
  }

  async function act(fn: () => Promise<unknown>, done: string) {
    setErr(undefined);
    try {
      await fn();
      setMsg(done);
      reload();
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <>
      <h1>Cloud storage</h1>
      {result && <Notice kind={result[1]}>{result[0]}</Notice>}
      {msg && <Notice kind="success">{msg}</Notice>}
      <Notice>
        Documents are stored in one cloud account per workspace, inside a &quot;Flow Chain Documents&quot; folder, and stay
        private to that account. <strong>OneDrive</strong> uses the <code>Files.ReadWrite</code> permission (the account&apos;s
        own files); <strong>Google Drive</strong> uses <code>drive.file</code> (only files Flow Chain creates). You can sign in
        from any device: the account does not need to be on this computer.
      </Notice>
      <ErrorAlert error={err ?? error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && !storage && (
        <section className="card">
          <p>No cloud storage is connected. Documents cannot be stored until one is.</p>
          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={() => void connect("onedrive")}>Connect OneDrive</button>
            <button type="button" className="btn" onClick={() => void connect("google")}>Connect Google Drive</button>
          </div>
        </section>
      )}
      {storage && (
        <section className="card">
          <h2>{storage.name} <StatusBadge status={storage.status} /></h2>
          <dl className="dl">
            <dt>Provider</dt><dd>{storage.provider}</dd>
            {storage.settings.account ? (<><dt>Account</dt><dd>{String(storage.settings.account)}</dd></>) : null}
            <dt>Root folder</dt><dd>{String(storage.settings.root_folder_name ?? "—")}</dd>
            <dt>Last check</dt><dd>{formatDateTime(storage.last_checked_at)} {storage.last_error && <span className="text-bad">— {storage.last_error}</span>}</dd>
          </dl>
          <div className="actions">
            <button type="button" className="btn" onClick={() => act(() => post(`/tenants/${tenantId}/integrations/${storage.id}/check`), "Storage checked.")}>Check connection</button>
            {storage.provider === "google_drive" && <button type="button" className="btn" onClick={() => void connect("google")}>Reconnect</button>}
            {storage.provider === "onedrive" && <button type="button" className="btn" onClick={() => void connect("onedrive")}>Reconnect</button>}
            <button type="button" className="btn btn-danger" onClick={() => setConfirm(true)}>Disconnect</button>
          </div>
        </section>
      )}
      <ConfirmDialog open={confirm} title={`Disconnect ${storageLabel}?`} danger confirmLabel="Disconnect"
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          if (storage) void act(() => del(`/tenants/${tenantId}/integrations/${storage.id}`), `${storageLabel} disconnected.`);
        }}>
        <p>New documents will fail to store until storage is reconnected. Existing files remain in {storageLabel}.</p>
      </ConfirmDialog>
    </>
  );
}

export default function DriveSettings() {
  return (
    <Suspense fallback={<Spinner />}>
      <StorageSettingsView />
    </Suspense>
  );
}
