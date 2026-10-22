# Operator runbook

## Daily/weekly checks
- `GET /health/ready` → `status: ok`, `queue.dead` = 0, `oldest_due_seconds` small.
- Dashboard → *Failures*: dead jobs, failed and rejected documents.
- Dashboard → *WhatsApp* / *Google Drive* → *Check connection*.
- Supabase usage (DB size < 400 MB; project not paused).

## Common tasks
| Task | How |
|---|---|
| Add workspace | `python -m app.cli create-tenant --name "X" --owner-email y` (platform admin) |
| Authorize a phone | Dashboard → Members & senders, or `cli add-sender` |
| Retry a failed document | Dashboard → document → *Retry processing* (original kept ≤14 days) |
| Retry a dead job | Dashboard → Failures → *Retry* |
| Adjust quotas | `PATCH /api/v1/tenants/{id}/quotas` as platform admin |
| Rotate encryption key | `docs/security/key-management.md` |
| Run housekeeping now | `python -m app.cli sweep` |
| Disconnect WhatsApp / Drive | Dashboard → respective page → *Disconnect* |
| Export / delete tenant data | Dashboard → *Export & deletion* (owner) |

## Interpreting job errors
| Error category | Meaning | Action |
|---|---|---|
| `transient` (queued again) | timeout, 429, 5xx, DB blip | wait; check provider status if persistent |
| `permanent` / `dead` | bad input, revoked token, 4xx | read `last_error`; fix cause (e.g. reconnect Drive: `invalid_grant`) then *Retry* |
| `attempt budget exhausted` | worker crashed repeatedly mid-job | check memory/timeouts in logs; retry once |

## Logs
JSON lines on stdout (Render → Logs). Filter by `request_id` (returned in every API response and
error) or `job_id`. Secrets are redacted; phone numbers masked.
