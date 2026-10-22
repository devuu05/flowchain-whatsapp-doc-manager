# Hosting Evaluation & Free-Tier Limitations

Research date 2026-10-22, from vendor docs/pricing excerpts via search (direct fetch was blocked
in the build sandbox — re-check before committing). Links in `docs/deployment/plan.md`.

| Provider | Relevant free-tier facts | Fit |
|---|---|---|
| Render free web | Spins down after 15 min without inbound traffic, ~1 min cold start, 750 instance-h/month, ephemeral disk; **no free background workers** (Starter $7/mo); free Postgres deleted after 30 days (+14 grace) | API + embedded worker: OK with caveats |
| Koyeb free | 1 web service, 512 MB/0.1 vCPU, Frankfurt/Washington only, scale-to-zero after 1 h idle | Alternative |
| Fly.io | No free allowance for new orgs (trial 2 machine-h / 7 days) | Paid only (~$2–3/mo) |
| Railway | $5 one-time trial, then $1/month credit | Not enough |
| Cloud Run | Free monthly quota, scale-to-zero, card required; CPU outside requests not guaranteed ⇒ in-process worker unreliable | Needs Cloud Tasks redesign |
| Supabase free | 500 MB DB, pauses after 1 week inactivity, IPv4 via shared pooler | **Database: chosen** |
| Neon free | 100 CU-h/month, a polling worker keeps compute awake ⇒ exhausted ~day 16 | Not suitable with polling worker |
| Upstash Redis free | 500k commands/month; idle Celery exceeds this ~7× | Not used (ADR 0001) |
| Vercel Hobby | **Non-commercial personal use only** — a paying client's dashboard violates it | Not for production dashboard |
| Cloudflare Workers (OpenNext) | 100k req/day, 3 MiB worker limit on free | Dashboard option |

## Honest consequences for the ₹0 pilot
- **No 24/7 guarantee.** Render free sleeps after 15 min idle. A WhatsApp webhook arriving to a
  sleeping service waits ~1 min for cold start; whether WasenderAPI retries webhooks that time
  out is **undocumented**, so messages can be lost during a cold start. We do **not** use
  artificial keep-alive traffic to evade the platform's limits.
- Background jobs only run while the instance is awake. Jobs persist in Postgres and resume on
  next wake, so nothing is lost, but retries scheduled during sleep are delayed.
- Supabase pauses after 7 idle days; real pilot usage normally prevents this. A paused project
  must be resumed from the Supabase dashboard.

## Recommendation
- **Pilot (₹0, best effort):** Render free web service (Docker, `EMBEDDED_WORKER=true`) +
  Supabase free Postgres + dashboard on Render free static/Node or Cloudflare Workers. Accept
  cold-start risk, document it to the client.
- **Reliable pilot (~$7/mo ≈ ₹600):** Render Starter web service (always on, embedded worker).
  This is the minimum we recommend before the client depends on it daily.
- **Growth (~$14/mo):** separate Starter worker service; Supabase Pro when >500 MB.
