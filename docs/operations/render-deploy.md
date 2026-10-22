# Deploy on Render (free plan) + Supabase (free) — step by step

Chosen pilot setup: **₹0**, Render free web services for API and dashboard, Supabase free Postgres.
You perform these steps in your own accounts. Never paste secrets into chat, tickets or git.

## What "free" means here (accept before starting)
- Each service **sleeps after ~15 min without traffic**; the next request waits ~1 min while it
  starts. A WhatsApp message arriving while the API sleeps wakes it; if WasenderAPI does not retry
  timed-out webhooks (undocumented), **that message can be lost** — the sender gets no reply and
  should resend. We do not use artificial pings to keep it awake.
- Queued work (processing, replies, retries) runs only while the API is awake; nothing is lost,
  it resumes on the next wake.
- 750 free instance-hours per workspace per month are shared by both services. Sleeping services
  don't consume hours, so normal pilot usage fits; heavy all-day traffic could exhaust them, after
  which Render suspends free services until the next month.
- Supabase free pauses a project after ~7 days with no activity; resume it from the Supabase dashboard.
- Upgrade path without code changes: switch `flowchain-api` to Starter (~$7/mo) → always on.

## 1. Supabase database (≈5 min)
1. https://supabase.com → New project. Region: **Mumbai (ap-south-1)** if offered, else Singapore.
   Set a strong DB password (save it in your password manager).
2. Project → **Connect** → *Session pooler* (works over IPv4). Copy the URI, e.g.
   `postgresql://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres`
3. Turn it into `DATABASE_URL` by changing the prefix and requiring TLS:
   `postgresql+psycopg://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require`

## 2. Generate secrets on your computer
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # PROXY_SHARED_SECRET
cd backend && uv run python -m app.cli gen-key                  # FIELD_ENCRYPTION_KEYS
```
Store all three in your password manager. Losing `FIELD_ENCRYPTION_KEYS` means reconnecting WhatsApp/Drive.

## 3. Push the repository to GitHub (private)
The code must be in `devuu05/flowchain-whatsapp-doc-manager` (CI runs there; Render deploys from it).

## 4. Create the services from the Blueprint
1. https://dashboard.render.com → **New → Blueprint** → connect GitHub → choose the repo.
2. Render reads `render.yaml` and proposes `flowchain-api` and `flowchain-dashboard` (plan free,
   Singapore). It asks for every `sync: false` value:

| Service | Variable | Value |
|---|---|---|
| api | `DATABASE_URL` | from step 1 |
| api | `SECRET_KEY`, `FIELD_ENCRYPTION_KEYS`, `PROXY_SHARED_SECRET` | from step 2 |
| api | `PUBLIC_BASE_URL` | `https://flowchain-api.onrender.com` (the URL Render shows for the service) |
| api | `DASHBOARD_URL` | `https://flowchain-dashboard.onrender.com` |
| api | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | step 6 (can be added later) |
| api | `GEMINI_API_KEY` | leave empty (AI off) unless billed key + client consent |
| dashboard | `BACKEND_URL` | `https://flowchain-api.onrender.com` |
| dashboard | `PROXY_SHARED_SECRET` | same as the API |

   If Render assigns different hostnames (name taken), use the real ones everywhere.
3. **Apply**. First build takes several minutes. Migrations run automatically on API start.
4. Check: `https://flowchain-api.onrender.com/health/ready` → `{"status":"ok",...}` and
   `https://flowchain-dashboard.onrender.com/login` shows the sign-in page.

## 5. Create your admin user and the pilot workspace
Render → `flowchain-api` → **Shell** (if the free plan does not offer Shell, run the same commands
locally with `DATABASE_URL` pointed at Supabase):
```bash
python -m app.cli create-admin --email you@yourdomain.com      # type a strong password
python -m app.cli create-tenant --name "Pilot Client" --owner-email you@yourdomain.com
python -m app.cli add-sender --tenant-slug pilot-client --phone "+91 98xxxxxxx"
```
Sign in at the dashboard URL.

## 6. Google Drive OAuth
Follow `docs/setup/google-drive.md` with redirect URI
`https://flowchain-dashboard.onrender.com/api/v1/integrations/google/callback`
(it goes through the dashboard on purpose). Add the client ID/secret to the API's environment
(Render → flowchain-api → Environment), redeploy, then dashboard → **Google Drive → Connect**.

## 7. WhatsApp (WasenderAPI)
Follow `docs/setup/wasender.md`. The webhook URL shown in the dashboard starts with
`https://flowchain-api.onrender.com/api/v1/webhooks/wasender/...`.

## 8. Verify
Run `docs/testing/manual-provider-tests.md`, then `docs/operations/onboarding-checklist.md`.
Also open the dashboard *Audit log* after signing in: the IP on `auth.login` should be your public
IP (confirms `TRUSTED_PROXY_HOPS`/proxy relay are right). If it shows a Render-internal address,
tell me and we adjust the hop count.

## Deploying updates
CI must be green on GitHub → Render → service → **Manual Deploy → Deploy latest commit**
(API first, then dashboard if it changed). Rollback: `docs/operations/rollback.md`.
