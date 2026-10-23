"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ErrorAlert, Field, Spinner } from "@/components/ui";
import { post } from "@/lib/api";
import type { Me } from "@/lib/types";

function safeNext(next: string | null): string {
  // Only allow same-site relative paths to prevent open redirects.
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/login") || next === "/") return "/app";
  return next;
}

export function LoginForm() {
  const { setMe } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const me = await post<Me>("/auth/login", { email, password });
      setMe(me);
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      setError(err);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="center-page">
      <form className="card login-card" onSubmit={onSubmit} aria-describedby="login-title">
        <h1 id="login-title">Flow Chain Documents</h1>
        <p className="muted">Sign in to manage your workspace.</p>
        <ErrorAlert error={error} />
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <Field label="Password">
          {(id) => (
            <input id={id} type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          )}
        </Field>
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <LoginForm />
    </Suspense>
  );
}
