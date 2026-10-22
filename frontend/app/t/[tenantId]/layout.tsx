"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";

export default function TenantLayout({ children }: { children: React.ReactNode }) {
  const { tenantId } = useParams<{ tenantId: string }>();
  return <AppShell tenantId={tenantId}>{children}</AppShell>;
}
