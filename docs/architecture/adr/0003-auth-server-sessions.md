# ADR 0003 — Server-side sessions with HttpOnly cookies, CSRF header, Argon2id

Status: Accepted

- Passwords hashed with Argon2id (`argon2-cffi`).
- Login creates a random 256-bit session token; only its SHA-256 is stored in `user_sessions`.
  Cookie `fc_session`: HttpOnly, Secure (prod), SameSite=Lax, Path=/. Idle + absolute expiry.
- Logout and password changes revoke sessions server-side (immediate, unlike JWTs).
- CSRF: per-session token returned by `/auth/me`, required in `X-CSRF-Token` for every
  state-changing request (double-submit tied to the server session) + SameSite=Lax + strict CORS.
- The dashboard is served through a Next.js rewrite proxy so the cookie is first-party.
- Login throttling is stored in Postgres (`login_attempts`), not process memory.

A third-party IdP (Auth0/Clerk/Supabase Auth) was rejected for the pilot: extra vendor, cost
tiers, and the tenant-membership model must live in our DB anyway.
