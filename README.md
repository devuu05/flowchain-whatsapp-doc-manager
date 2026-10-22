# Flow Chain — WhatsApp Document Manager

A multi-tenant SaaS that lets a business **send documents to a WhatsApp number** and have them
automatically **validated, read, classified and filed into the business's own Google Drive** —
then **get them back later by chatting** in plain language.

```
You (WhatsApp):  [photo of an invoice]  "invoice from ABC Traders"
Bot:             📥 Received scan.pdf. Processing it now…
Bot:             ✅ Saved Invoice_ABC-Traders_2026-09-15_36b65a.pdf
                 📁 Invoices/2026 · 🏢 ABC Traders · 📅 2026-09-15

You:             find invoices from ABC Traders
Bot:             🔎 1 result(s) — 1. Invoice · ABC Traders · 2026-09-15
                 Reply with a number to get the file.
You:             1
Bot:             📤 Sending Invoice_ABC-Traders_2026-09-15_36b65a.pdf…  [file]
```

> **Status: pilot-ready code, not yet deployed or verified against the real providers.**
> All automated tests pass against a real PostgreSQL database, but WasenderAPI, Google Drive and
> Gemini have only been exercised with mocked HTTP. See the [test report](docs/testing/test-report.md)
> and [manual provider tests](docs/testing/manual-provider-tests.md) before onboarding a client.

---

## Features

**WhatsApp intake**
- Photos (JPEG/PNG/WebP), PDF, DOCX, XLSX, XLS, PPTX, TXT, CSV; DOC/PPT stored as originals.
- Captions as descriptions; asks for a description when missing; handles several pending files
  without guessing (`2: receipt from Dev Construction`); `skip` / `cancel`; pending state expires safely.
- Only **authorized sender numbers** are served; everyone else is ignored and audited.

**Safety**
- File type detected from content (not the extension); rejects macros, zip bombs, encrypted
  files, executables/HTML/SVG; size limits.
- Extraction runs in an isolated child process with a timeout and memory cap; XXE-safe parsers.

**Classification**
- Default: a local, deterministic rules classifier (nothing leaves the server).
- Optional **Gemini** AI per workspace — off by default, owner-only with a recorded consent note,
  never used for sensitive categories (ID/KYC, bank statements, cheques).
- Output strictly validated: unknown values stay empty, categories clamped, filenames sanitized,
  extensions preserved.

**Storage — Google Drive**
- Least-privilege `drive.file` scope; private by default; `<Category>/<Year>/` folders.
- Idempotent uploads with reconciliation: retries never create duplicate files.
- "✅ Saved" is only sent after the upload *and* the database commit succeed.

**Retrieval**
- Deterministic search by type, company/person, date or period, filename, "latest", "all".
- Pagination (`more`), numbered selection with expiry, delivery through short-lived signed URLs
  (Drive links are never exposed).

**Reliability**
- Webhooks acknowledged in milliseconds; work runs on a durable PostgreSQL job queue with
  idempotency keys, leases + heartbeats, exponential backoff with jitter, dead-lettering and
  crash recovery.

**Dashboard (Next.js)**
- Sign in/out, overview, document search & details, corrections, failures with retry,
  WhatsApp & Drive connections, AI & categories, members & authorized senders, usage & quotas,
  audit log, data export & deletion. Responsive and accessible.

**Multi-tenant & security**
- Every query is scoped to the tenant on the server; roles owner / admin / member.
- Argon2id passwords, server-side sessions, CSRF tokens, rate limits, encrypted secrets,
  redacted logs, audit trail. See the [security checklist](docs/security/checklist.md).

---

## Architecture

```
WhatsApp ── WasenderAPI ──webhook──► FastAPI API ──► PostgreSQL (Supabase)
                                         │  ▲            ▲
                                         │  └─ jobs ─────┤  durable queue (SKIP LOCKED + leases)
                                         ▼               │
                                   Worker (embedded or separate process)
                                     ├─► WasenderAPI   (download media, send replies/files)
                                     ├─► Extractors    (PDF/Office/CSV/TXT)
                                     ├─► Gemini        (optional classification)
                                     └─► Google Drive  (client's own account)

Browser ──► Next.js dashboard ──/api proxy (same-origin cookie)──► FastAPI API
```

| Layer | Technology |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Database / queue | PostgreSQL 16 (job queue in Postgres — no Redis needed) |
| Dashboard | Next.js 16, React 19, TypeScript |
| Providers | WasenderAPI, Google Drive REST v3, Gemini REST (all behind interfaces) |
| Tests | pytest + respx (backend), Vitest + Testing Library (frontend) |
| Delivery | Docker, GitHub Actions CI, Render Blueprint |

Key decisions are recorded as ADRs in [`docs/architecture/adr`](docs/architecture/adr).

---

## Quick start (local)

**With Docker**
```bash
cp .env.example .env                       # fill in SECRET_KEY (see comments in the file)
docker compose up --build                  # db, migrations, api :8000, worker, dashboard :3000
docker compose run --rm api cli create-admin --email you@example.com      # prompts for a password
docker compose run --rm api cli create-tenant --name "Pilot Co" --owner-email you@example.com
```
Open http://localhost:3000 and sign in.

**Without Docker** — see [docs/setup/local-development.md](docs/setup/local-development.md).

```bash
# backend
cd backend && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --reload       # API
uv run python -m app.workers.runner        # worker (or EMBEDDED_WORKER=true)

# frontend
cd frontend && npm ci && npm run dev
```

---

## Deploy (₹0 pilot on Render + Supabase)

The repository includes a Render Blueprint ([`render.yaml`](render.yaml)) for two free web
services (API with embedded worker, and dashboard) plus a free Supabase database.

Follow **[docs/operations/render-deploy.md](docs/operations/render-deploy.md)** step by step.

Free-tier reality check: services sleep after ~15 min idle and take ~1 min to wake, so a WhatsApp
message arriving while asleep can be delayed or lost. Upgrading only the API to Render Starter
(~US$7/month) makes it always on — no code changes. Details: [hosting evaluation](docs/operations/hosting.md).

---

## Testing

```bash
cd backend  && uv run pytest && uv run ruff check . && uv run mypy app
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```

| Suite | Result (2026-10-22) |
|---|---|
| Backend (real PostgreSQL) | 191 passed, 87% coverage |
| Frontend | 23 passed |
| Dependency audit (pip-audit, npm audit) | no known vulnerabilities |
| Live WasenderAPI / Google Drive / Gemini | not yet run — needs credentials |

Full report: [docs/testing/test-report.md](docs/testing/test-report.md).

---

## Repository layout

```
backend/            FastAPI app, workers, providers, Alembic migrations, pytest suite
frontend/           Next.js dashboard and Vitest suite
infra/docker/       Dockerfiles and entrypoint
docs/architecture/  plan, ADRs, provider notes, event schema, conversation rules, phase status
docs/setup/         local development, WasenderAPI, Google Drive, Gemini
docs/security/      checklist, privacy & data flows, key management
docs/operations/    Render deploy, deployment plan & costs, runbook, incidents, backup, rollback, onboarding
docs/testing/       test report, manual provider tests
render.yaml         Render Blueprint
CLAUDE.md           conventions and rules for AI coding agents and contributors
```

## Documentation map

| Topic | Document |
|---|---|
| Deploy on Render (free) | [docs/operations/render-deploy.md](docs/operations/render-deploy.md) |
| WhatsApp (WasenderAPI) setup | [docs/setup/wasender.md](docs/setup/wasender.md) |
| Google Drive OAuth setup | [docs/setup/google-drive.md](docs/setup/google-drive.md) |
| Optional Gemini AI | [docs/setup/gemini.md](docs/setup/gemini.md) |
| Pilot onboarding checklist | [docs/operations/onboarding-checklist.md](docs/operations/onboarding-checklist.md) |
| Operator runbook / incidents | [runbook](docs/operations/runbook.md) · [incident response](docs/operations/incident-response.md) |
| Backup, restore, rollback | [backup-restore](docs/operations/backup-restore.md) · [rollback](docs/operations/rollback.md) |
| Privacy & data flows | [docs/security/privacy.md](docs/security/privacy.md) |
| Phase status & completeness criteria | [docs/architecture/phases.md](docs/architecture/phases.md) |

## Known limitations

- WasenderAPI is an unofficial WhatsApp integration — use a dedicated number; a future adapter
  for the official WhatsApp Business Platform can replace it without touching business logic.
- No local OCR: photos and scanned PDFs are read only when AI is enabled; otherwise they are
  classified from the caption and filename.
- No MFA or self-service password reset yet.
- Free hosting cannot guarantee 24/7 availability (see above).

## Security

Never commit `.env` files or credentials. Report vulnerabilities privately to the repository owner.
