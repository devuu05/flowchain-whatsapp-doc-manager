"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ConfirmDialog, ErrorAlert, Notice, Spinner, StatusBadge } from "@/components/ui";
import { del, post } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Integration } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const RESULTS: Record<string, [string, "success" | "warning"]> = {
  connected: ["Google Drive connected.", "success"],
  denied: ["Google access was not granted.", "warning"],
  forbidden: ["Only workspace admins can connect Google Drive.", "warning"],
  invalid_state: ["The connection link expired. Please try again.", "warning"],
  session_mismatch: ["Please finish connecting Google Drive in the same browser session you started it from.", "warning"],
  no_refresh_token: ["Google did not grant offline access. Remove the app at myaccount.google.com/permissions and retry.", "warning"],
  error: ["Google Drive connection failed. Please try again.", "warning"],
};

function DriveSettingsView() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const params = useSearchParams();
  const { data, error, loading, reload } = useApi<Integration[]>(`/tenants/${tenantId}/integrations`);
  const drive = data?.find((i) => i.kind === "google_drive" && i.status !== "disconnected");
  const [err, setErr] = useState<unknown>();
  const [msg, setMsg] = useState<string>();
  const [confirm, setConfirm] = useState(false);
  const result = RESULTS[params.get("result") ?? ""];

  async function connect() {
    setErr(undefined);
    try {
      const r = await post<{ authorization_url: string }>(`/tenants/${tenantId}/integrations/google/start`);
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
      <h1>Google Drive</h1>
      {result && <Notice kind={result[1]}>{result[0]}</Notice>}
      {msg && <Notice kind="success">{msg}</Notice>}
      <Notice>
        Flow Chain requests only the <code>drive.file</code> permission: it can see and manage only the files and folders
        it creates (inside a &quot;Flow Chain Documents&quot; folder). Files stay private to the connected Google account.
      </Notice>
      <ErrorAlert error={err ?? error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && !drive && (
        <section className="card">
          <p>No Google Drive account is connected. Documents cannot be stored until one is.</p>
          <button type="button" className="btn btn-primary" onClick={() => void connect()}>Connect Google Drive</button>
        </section>
      )}
      {drive && (
        <section className="card">
          <h2>{drive.name} <StatusBadge status={drive.status} /></h2>
          <dl className="dl">
            <dt>Provider</dt><dd>{drive.provider}</dd>
            <dt>Root folder</dt><dd>{String(drive.settings.root_folder_name ?? "—")}</dd>
            <dt>Last check</dt><dd>{formatDateTime(drive.last_checked_at)} {drive.last_error && <span className="text-bad">— {drive.last_error}</span>}</dd>
          </dl>
          <div className="actions">
            <button type="button" className="btn" onClick={() => act(() => post(`/tenants/${tenantId}/integrations/${drive.id}/check`), "Drive checked.")}>Check connection</button>
            {drive.provider === "google_drive" && <button type="button" className="btn" onClick={() => void connect()}>Reconnect</button>}
            <button type="button" className="btn btn-danger" onClick={() => setConfirm(true)}>Disconnect</button>
          </div>
        </section>
      )}
      <ConfirmDialog open={confirm} title="Disconnect Google Drive?" danger confirmLabel="Disconnect"
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          if (drive) void act(() => del(`/tenants/${tenantId}/integrations/${drive.id}`), "Google Drive disconnected and access revoked.");
        }}>
        <p>New documents will fail to store until Drive is reconnected. Existing files remain in Google Drive.</p>
      </ConfirmDialog>
    </>
  );
}

export default function DriveSettings() {
  return (
    <Suspense fallback={<Spinner />}>
      <DriveSettingsView />
    </Suspense>
  );
}
