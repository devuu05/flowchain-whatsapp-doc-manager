"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { useAuth } from "@/components/AuthProvider";
import { EmptyState, Spinner } from "@/components/ui";

function Home() {
  const { me, loading, logout } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const first = me?.memberships[0];

  useEffect(() => {
    if (first) {
      const drive = params.get("drive");
      router.replace(`/t/${first.tenant_id}${drive ? `/settings/drive?result=${drive}` : ""}`);
    }
  }, [first, params, router]);

  if (loading || !me || first) return <Spinner />;
  return (
    <main id="main" className="center-page">
      <EmptyState title="No workspace yet">
        Your account is not a member of any workspace. Ask your Flow Chain administrator to add you.
      </EmptyState>
      <button type="button" className="btn" onClick={() => void logout()}>
        Sign out
      </button>
    </main>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<Spinner />}>
      <Home />
    </Suspense>
  );
}
