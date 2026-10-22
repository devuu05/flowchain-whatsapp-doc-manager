# Deployment plan

**Nothing has been deployed.** Creating hosting resources, DNS, or production credentials requires
your accounts and approval. This plan is ready to execute once you approve it.

## Target architecture (pilot)
```
WhatsApp ─ WasenderAPI ─HTTPS─► flowchain-api (Render web, Docker, embedded worker) ─► Supabase Postgres
                                         │                         ├─► Google Drive (client account)
Browser ─HTTPS─► flowchain-dashboard (Render web, Next.js) ─/api proxy─┘  └─► Gemini (optional, paid key)
```
Hosting facts and limits: `docs/operations/hosting.md`.

## Option A — ₹0 "best effort" pilot
- Render **free** web services for API (with `EMBEDDED_WORKER=true`) and dashboard; Supabase free.
- Limitation: the API sleeps after ~15 min idle; the first WhatsApp message afterwards waits
  ~1 min for a cold start. If WasenderAPI does not retry webhooks (undocumented), **that message
  can be lost**. Background retries only run while the API is awake. Not suitable for promises
  of reliability to a paying client. We do not use artificial keep-alive traffic.

## Option B — recommended for a real pilot (~US$7/month ≈ ₹600)
- Same, but the API on Render **Starter** (always on). Dashboard can stay on free (sleeping is fine
  for admins). Supabase free.

## Steps (after approval)
1. **Supabase**: create project (region Mumbai `ap-south-1` if offered). Copy the *session pooler*
   connection string (IPv4) → convert prefix to `postgresql+psycopg://`. Enable daily backups
   (Pro plan for PITR; free plan: see backup doc).
2. **Secrets** (generate locally): `SECRET_KEY` (`python -c "import secrets;print(secrets.token_urlsafe(48))"`),
   `FIELD_ENCRYPTION_KEYS` (`uv run python -m app.cli gen-key`). Store in a password manager.
3. **Google OAuth** client per `docs/setup/google-drive.md` with redirect
   `https://<dashboard-host>/api/v1/integrations/google/callback` (the callback is proxied by the dashboard so the session cookie is present); publish consent screen.
4. **Render**: New → Blueprint → point at the GitHub repo → review `infra/deployment/render.yaml`
   → fill `sync: false` env vars in the dashboard (never in git). Choose plan per option A/B.
   Deploy API first; confirm `GET https://<api>/health/ready` returns `{"status":"ok"}`.
   Then set dashboard `BACKEND_URL` (build-time) and deploy it.
5. **Bootstrap**: Render shell on the API service:
   `python -m app.cli create-admin --email <you>` (enter password), `create-tenant`, `add-sender`.
6. **WhatsApp**: follow `docs/setup/wasender.md`; run `docs/testing/manual-provider-tests.md`.
7. Execute the pilot checklist `docs/operations/onboarding-checklist.md`.

## Environment variable checklist (production)
| Variable | Required | Notes |
|---|---|---|
| `APP_ENV=production` | ✔ | enables guards; disables /docs |
| `DATABASE_URL` | ✔ | Supabase pooler, `postgresql+psycopg://…?sslmode=require` |
| `SECRET_KEY` | ✔ | ≥32 random chars |
| `FIELD_ENCRYPTION_KEYS` | ✔ | Fernet key(s) |
| `COOKIE_SECURE=true` | ✔ | app refuses to start otherwise |
| `ALLOW_FAKE_PROVIDERS=false` | ✔ | app refuses to start otherwise |
| `PUBLIC_BASE_URL` | ✔ | public HTTPS URL of the API (webhooks, signed URLs, OAuth) |
| `DASHBOARD_URL` | ✔ | for OAuth redirect back |
| `EMBEDDED_WORKER` | ✔ | `true` on single-service hosting |
| `TRUSTED_PROXY_HOPS` | ✔ | number of proxies appending to X-Forwarded-For (Render: `1`; verify by checking audit IPs after deploy). Never trust the left-most XFF value |
| `RUN_MIGRATIONS_ON_START` | ✔ (free tier) | or run `migrate` as a pre-deploy step on paid plans |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | ✔ | |
| `GEMINI_API_KEY` | optional | billed project only |
| `INTERNAL_WEBHOOK_TOKEN` | ✘ | leave empty in production |
| `BACKEND_URL` (dashboard, build time) | ✔ | API origin for the `/api` proxy |

## Safe migrations
Migrations are additive and backward compatible within a release; destructive changes are split
across two releases (expand → migrate data → contract). Take a backup before each production
migration. `alembic downgrade -1` is available but data-destructive downgrades are avoided.

## Estimated monthly operating cost (assumptions: 1 tenant, 50 documents/day ≈ 1,500/month)
| Item | Option A | Option B | Notes |
|---|---|---|---|
| API hosting | $0 | ~$7 | Render free vs Starter |
| Dashboard hosting | $0 | $0 | Render free (sleeps) |
| PostgreSQL | $0 | $0 | Supabase free; metadata ≈ 2 KB/doc → ~3 MB/month |
| Google Drive | $0 | $0 | client's account (15 GB free); ~1–3 GB/year at 1–2 MB/doc |
| Gemini (if enabled, paid tier) | ~$2–4 | ~$2–4 | ~4k input + 200 output tokens/doc at published Flash-Lite prices (unverified) |
| WasenderAPI | your plan | your plan | existing subscription; price not researched |
| **Total (excl. Wasender)** | **$0–4** | **~$9–11** | |
