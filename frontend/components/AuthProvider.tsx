"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

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
// Pages anyone may view. The marketing home page never calls the API (so it does not wake a
// sleeping free-tier backend for every visitor).
const MARKETING_PATHS = ["/"];
const PUBLIC_PATHS = ["/login", ...MARKETING_PATHS];

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

  const isMarketing = MARKETING_PATHS.includes(pathname);
  const attempted = useRef(false);

  useEffect(() => {
    if (isMarketing || attempted.current) return;
    attempted.current = true;
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
      attempted.current = false; // allow the re-run (React strict mode) to fetch again
    };
  }, [setMe, isMarketing]);

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
