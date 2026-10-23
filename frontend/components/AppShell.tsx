"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { EmptyState, Spinner } from "@/components/ui";
import type { Membership } from "@/lib/types";

const NAV: { href: string; label: string; admin?: boolean }[] = [
  { href: "", label: "Overview" },
  { href: "/documents", label: "Documents" },
  { href: "/failures", label: "Failures" },
  { href: "/settings/whatsapp", label: "WhatsApp", admin: true },
  { href: "/settings/drive", label: "Google Drive", admin: true },
  { href: "/settings/ai", label: "AI & categories", admin: true },
  { href: "/members", label: "Members & senders", admin: true },
  { href: "/usage", label: "Usage" },
  { href: "/audit", label: "Audit log", admin: true },
  { href: "/data", label: "Export & deletion", admin: true },
];

export function useMembership(tenantId: string): Membership | undefined {
  const { me } = useAuth();
  return me?.memberships.find((m) => m.tenant_id === tenantId);
}

export function AppShell({ tenantId, children }: { tenantId: string; children: React.ReactNode }) {
  const { me, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const membership = useMembership(tenantId);

  if (loading || !me) return <Spinner />;
  if (!membership) {
    return (
      <main id="main" className="center-page">
        <EmptyState title="Workspace not found">You don&apos;t have access to this workspace.</EmptyState>
        <Link className="btn" href="/app">Back</Link>
      </main>
    );
  }
  const isAdmin = membership.role !== "member";
  const base = `/t/${tenantId}`;

  return (
    <div className="shell">
      <header className="topbar">
        <button type="button" className="btn btn-small menu-toggle" aria-expanded={open} aria-controls="sidenav"
          onClick={() => setOpen((o) => !o)}>
          Menu
        </button>
        <span className="brand">Flow Chain</span>
        {me.memberships.length > 1 ? (
          <label className="tenant-switch">
            <span className="sr-only">Workspace</span>
            <select value={tenantId} onChange={(e) => router.push(`/t/${e.target.value}`)}>
              {me.memberships.map((m) => (
                <option key={m.tenant_id} value={m.tenant_id}>{m.tenant_name}</option>
              ))}
            </select>
          </label>
        ) : (
          <span className="tenant-name">{membership.tenant_name}</span>
        )}
        <span className="spacer" />
        <span className="muted user-email">{me.email} · {membership.role}</span>
        <button type="button" className="btn btn-small" onClick={() => void logout()}>Sign out</button>
      </header>
      <nav id="sidenav" className={`sidenav ${open ? "open" : ""}`} aria-label="Workspace">
        <ul>
          {NAV.filter((n) => isAdmin || !n.admin).map((n) => {
            const href = `${base}${n.href}`;
            const active = n.href === "" ? pathname === base : pathname.startsWith(href);
            return (
              <li key={n.href}>
                <Link href={href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)}>
                  {n.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <main id="main" className="content">{children}</main>
    </div>
  );
}
