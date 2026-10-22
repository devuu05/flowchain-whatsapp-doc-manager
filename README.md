# Flow Chain — WhatsApp Document Manager

Multi-tenant SaaS that lets a business send documents to a WhatsApp number, have them validated,
read, classified and filed into the business's own Google Drive, and get them back later by
chatting in plain language ("find invoices from ABC Traders", then "2").

> Status: **pilot-ready codebase, not yet production-verified.** All automated tests pass against a
> real PostgreSQL database, but the WasenderAPI, Google Drive and Gemini integrations have only
> been exercised with mocked HTTP. See [docs/testing/test-report.md](docs/testing/test-report.md)
> and the manual verification steps before onboarding a client.

## Features
- **WhatsApp intake** (WasenderAPI adapter): images, PDF, DOCX/XLSX/PPTX, legacy XLS (DOC/PPT stored,
  text optional), TXT, CSV. Captions, follow-up descriptions, several pending files, `skip`/`cancel`.
- **Safety**: content-based type detection, macro/zip-bomb/encrypted rejection, size limits,
  extractor sandboxing (child process, timeout, memory cap), XXE-safe parsers.
- **Classification**: deterministic rules by default; Gemini (opt-in per tenant with recorded
  consent, never for sensitive categories). Output strictly validated; unknown → `null`.
- **Storage**: Google Drive (`drive.file` scope), `Category/Year` folders, idempotent uploads
  with reconciliation; private by default.
- **Retrieval**: deterministic search (type, company, date/period, filename, latest, all),
  pagination, numbered selection with expiry, delivery via short-lived signed URLs.
- **Reliability**: Postgres-backed durable queue (idempotency keys, leases, backoff + jitter,
  dead-lettering, crash recovery). Webhooks ack in milliseconds.
- **Dashboard** (Next.js): documents, failures & retry, integrations, AI/categories, members &
  authorized senders, usage/quotas, audit log, export & deletion.
- **Multi-tenant**: every query scoped server-side; roles owner/admin/member.

## Quick start (local)
```bash
cp .env.example .env               # fill SECRET_KEY etc. (see comments)
docker compose up --build          # db, migrate, api :8000, worker, dashboard :3000
docker compose run --rm api cli create-admin --email you@example.com   # prompts for password
docker compose run --rm api cli create-tenant --name "Pilot Co" --owner-email you@example.com
open http://localhost:3000
```
Without Docker see [docs/setup/local-development.md](docs/setup/local-development.md).

## Repository layout
See [CLAUDE.md](CLAUDE.md). Architecture: [docs/architecture/plan.md](docs/architecture/plan.md),
ADRs in [docs/architecture/adr](docs/architecture/adr), phase status in
[docs/architecture/phases.md](docs/architecture/phases.md).

## Documentation map
| Topic | Doc |
|---|---|
| Local dev | docs/setup/local-development.md |
| WhatsApp (WasenderAPI) setup | docs/setup/wasender.md |
| Google Drive OAuth setup | docs/setup/google-drive.md |
| Gemini (optional AI) | docs/setup/gemini.md |
| Deployment plan, env checklist, costs | docs/operations/deployment-plan.md |
| Free-tier limitations | docs/operations/hosting.md |
| Runbook, incidents, backup/restore, rollback | docs/operations/*.md |
| Pilot onboarding checklist | docs/operations/onboarding-checklist.md |
| Security checklist, privacy & data flows, key management | docs/security/*.md |
| Test report, manual provider tests | docs/testing/*.md |

## Commands
```bash
cd backend && uv sync && uv run pytest && uv run ruff check . && uv run mypy app
cd frontend && npm ci && npm test && npm run lint && npm run typecheck && npm run build
```
