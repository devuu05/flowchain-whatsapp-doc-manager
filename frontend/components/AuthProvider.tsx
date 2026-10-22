"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, api, post, setCsrfToken } from "@/lib/api";
import type { Me } from "@/lib/types";

interface AuthValue {
  me: Me | null;
  loading: boolean;
  setMe: (me: Me | null) => void;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);
const PUBLIC_PATHS = ["/login"];

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMeState] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  const setMe = useCallback((value: Me | null) => {
    setCsrfToken(value?.csrf_token ?? null);
    setMeState(value);
  }, []);

  const loadMe = useCallback(
    () =>
      api<Me>("/auth/me")
        .then((m) => setMe(m))
        .catch((e: unknown) => {
          if (!(e instanceof ApiError) || e.status !== 401) console.error(e);
          setMe(null);
        })
        .finally(() => setLoading(false)),
    [setMe],
  );
  const refresh = useCallback(async () => {
    await loadMe();
  }, [loadMe]);

  useEffect(() => {
    let active = true;
    api<Me>("/auth/me")
      .then((m) => active && setMe(m))
      .catch((e: unknown) => {
        if (!(e instanceof ApiError) || e.status !== 401) console.error(e);
        if (active) setMe(null);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [setMe]);

  useEffect(() => {
    const onUnauthorized = () => setMe(null);
    window.addEventListener("fc:unauthorized", onUnauthorized);
    return () => window.removeEventListener("fc:unauthorized", onUnauthorized);
  }, [setMe]);

  useEffect(() => {
    if (!loading && !me && !PUBLIC_PATHS.includes(pathname)) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [loading, me, pathname, router]);

  const logout = useCallback(async () => {
    try {
      await post("/auth/logout");
    } finally {
      setMe(null);
      router.replace("/login");
    }
  }, [router, setMe]);

  const value = useMemo(() => ({ me, loading, setMe, logout, refresh }), [me, loading, setMe, logout, refresh]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
