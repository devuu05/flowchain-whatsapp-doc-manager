"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { useMembership } from "@/components/AppShell";
import { useAuth } from "@/components/AuthProvider";
import { ConfirmDialog, EmptyState, ErrorAlert, Field, Notice, Spinner } from "@/components/ui";
import { del, patch, post } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Member, Role, Sender, SenderRole } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function MembersPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const { me } = useAuth();
  const isOwner = useMembership(tenantId)?.role === "owner";
  const members = useApi<Member[]>(`/tenants/${tenantId}/members`);
  const senders = useApi<Sender[]>(`/tenants/${tenantId}/senders`);
  const [form, setForm] = useState<{ email: string; full_name: string; role: Role; initial_password: string }>({ email: "", full_name: "", role: "member", initial_password: "" });
  const [sender, setSender] = useState<{ phone: string; display_name: string; role: SenderRole }>({ phone: "", display_name: "", role: "member" });
  const [remove, setRemove] = useState<Member | null>(null);
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

  return (
    <>
      <h1>Members &amp; authorized senders</h1>
      {msg && <Notice kind="success">{msg}</Notice>}
      <ErrorAlert error={err ?? members.error ?? senders.error} />
      <section className="card">
        <h2>Dashboard members</h2>
        {members.loading && !members.data && <Spinner />}
        {members.data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Email</th><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Last sign-in</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {members.data.map((m) => (
                  <tr key={m.user_id}>
                    <td className="break">{m.email}</td>
                    <td>{m.full_name || "—"}</td>
                    <td>
                      {isOwner && m.user_id !== me?.id ? (
                        <select aria-label={`Role for ${m.email}`} value={m.role}
                          onChange={(e) => act(() => patch(`/tenants/${tenantId}/members/${m.user_id}`, { role: e.target.value }), "Role updated.", members.reload)}>
                          <option value="member">member</option><option value="admin">admin</option><option value="owner">owner</option>
                        </select>
                      ) : m.role}
                    </td>
                    <td>{formatDateTime(m.last_login_at)}</td>
                    <td>{m.user_id !== me?.id && <button type="button" className="btn btn-small btn-danger" onClick={() => setRemove(m)}>Remove</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <form className="form-grid" onSubmit={(e) => {
          e.preventDefault();
          void act(() => post(`/tenants/${tenantId}/members`, { ...form, initial_password: form.initial_password || undefined }), "Member added. Share the initial password with them privately.", members.reload)
            .then(() => setForm({ email: "", full_name: "", role: "member", initial_password: "" }));
        }}>
          <h3>Add member</h3>
          <Field label="Email">{(id) => <input id={id} type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />}</Field>
          <Field label="Name">{(id) => <input id={id} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />}</Field>
          <Field label="Role">{(id) => (
            <select id={id} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              <option value="member">member (view & download)</option><option value="admin">admin (manage)</option>
              {isOwner && <option value="owner">owner (everything)</option>}
            </select>
          )}</Field>
          <Field label="Initial password (new users only)" hint="At least 12 characters with upper/lower case and a digit. The user should change it after first sign-in.">
            {(id) => <input id={id} type="password" autoComplete="new-password" minLength={12} value={form.initial_password} onChange={(e) => setForm({ ...form, initial_password: e.target.value })} />}
          </Field>
          <div className="row-end"><button type="submit" className="btn btn-primary">Add member</button></div>
        </form>
      </section>
      <section className="card">
        <h2>Authorized WhatsApp senders</h2>
        <p className="muted">Only these numbers can upload or retrieve documents over WhatsApp. Messages from other numbers are ignored. <strong>WhatsApp admins</strong> can also rename and delete documents from the chat.</p>
        {senders.data && senders.data.filter((s) => s.is_active).length === 0 && <EmptyState title="No authorized senders" />}
        <ul className="activity">
          {senders.data?.filter((s) => s.is_active).map((s) => (
            <li key={s.id}>
              +{s.phone} {s.display_name && <span className="muted">({s.display_name})</span>}{" "}
              <label className="sr-only" htmlFor={`role-${s.id}`}>WhatsApp role for +{s.phone}</label>
              <select id={`role-${s.id}`} value={s.role} onChange={(e) => void act(() => patch(`/tenants/${tenantId}/senders/${s.id}`, { role: e.target.value }), "Sender role updated.", senders.reload)}>
                <option value="member">Member</option>
                <option value="admin">WhatsApp admin</option>
              </select>{" "}
              <button type="button" className="btn btn-small" onClick={() => act(() => del(`/tenants/${tenantId}/senders/${s.id}`), "Sender removed.", senders.reload)}>Remove</button>
            </li>
          ))}
        </ul>
        <form className="form-grid" onSubmit={(e) => {
          e.preventDefault();
          void act(() => post(`/tenants/${tenantId}/senders`, sender), "Sender authorized.", senders.reload).then(() => setSender({ phone: "", display_name: "", role: "member" }));
        }}>
          <Field label="Phone (with country code)">{(id) => <input id={id} required inputMode="tel" placeholder="+91 98765 43210" value={sender.phone} onChange={(e) => setSender({ ...sender, phone: e.target.value })} />}</Field>
          <Field label="Label">{(id) => <input id={id} value={sender.display_name} onChange={(e) => setSender({ ...sender, display_name: e.target.value })} />}</Field>
          <Field label="WhatsApp role">{(id) => (
            <select id={id} value={sender.role} onChange={(e) => setSender({ ...sender, role: e.target.value as SenderRole })}>
              <option value="member">Member (save, find, receive)</option>
              <option value="admin">Admin (also rename &amp; delete)</option>
            </select>
          )}</Field>
          <div className="row-end"><button type="submit" className="btn btn-primary">Authorize number</button></div>
        </form>
      </section>
      <ConfirmDialog open={remove !== null} title={`Remove ${remove?.email ?? ""}?`} danger confirmLabel="Remove"
        onCancel={() => setRemove(null)}
        onConfirm={() => {
          const m = remove;
          setRemove(null);
          if (m) void act(() => del(`/tenants/${tenantId}/members/${m.user_id}`), "Member removed.", members.reload);
        }} />
    </>
  );
}
