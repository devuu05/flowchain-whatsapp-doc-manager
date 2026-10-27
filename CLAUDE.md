# CLAUDE.md — Flow Chain WhatsApp Document Manager

Guidance for AI coding agents (Claude Code, Codex) and humans working in this repository.

## What this is

A multi-tenant SaaS that receives business documents over WhatsApp, extracts and classifies
them (Gemini, behind an interface), stores originals in the tenant's Google Drive, records
metadata in PostgreSQL, and lets authorized senders retrieve documents by chatting in natural
language. A Next.js dashboard lets tenant admins inspect documents, failures, integrations,
members, usage and audit events.

This repository is **separate** from the Flowchain website and existing n8n workflows. Never
modify those from here.

## Layout

```
backend/                FastAPI app (Python 3.12)
  app/api/              HTTP routers (v1 under /api/v1), dependencies
  app/core/             settings, logging, errors, request-id middleware
  app/db/               engine/session, declarative base
  app/models/           SQLAlchemy 2 ORM models
  app/schemas/          Pydantic request/response + internal event schemas
  app/repositories/     tenant-scoped query helpers (ALWAYS filter by tenant_id)
  app/services/         business logic (intake, conversation, guided chat, pipeline, search, ...)
  app/services/extraction/  DocumentExtractor implementations
  app/providers/whatsapp/   WhatsAppProvider interface + Wasender adapter + fake
  app/providers/storage/    DocumentStorage interface + Google Drive & OneDrive adapters + fake
  app/providers/ai/         DocumentClassifier interface + Gemini adapter + rules fallback
  app/workers/          Postgres-backed durable job queue, worker runner, job handlers
  app/security/         passwords, sessions, CSRF, encryption, redaction, rate limits
  app/observability/    health/readiness probes, metrics helpers
  migrations/           Alembic
  tests/                pytest (unit + integration against a real local Postgres)
frontend/               Next.js (App Router) + TypeScript dashboard, Vitest tests
infra/docker/           Dockerfiles; infra/deployment/ hosting configs
docs/                   architecture/ (plan, ADRs, phases), setup/, security/, operations/, testing/
```

## Commands

```bash
# backend (from backend/)
uv sync                                  # install deps (uv.lock is authoritative)
uv run alembic upgrade head              # apply migrations
uv run uvicorn app.main:app --reload     # API on :8000
uv run python -m app.workers.runner      # worker process
uv run pytest                            # tests (needs Postgres, see TEST_DATABASE_URL)
uv run ruff check . && uv run ruff format --check . && uv run mypy app

# frontend (from frontend/)
npm ci && npm run dev                    # dashboard on :3000 (proxies /api to backend)
npm test && npm run lint && npm run typecheck && npm run build

# everything locally
docker compose up --build
```

## Architecture rules (non-negotiable)

1. **Tenant isolation is enforced server-side.** Every query on tenant-owned data filters on
   `tenant_id` derived from the authenticated membership or the resolved WhatsApp connection —
   never from client input alone. Repositories take `tenant_id` as a required argument.
2. **Webhooks ack fast.** The webhook handler verifies, records the inbound event (unique on
   provider event id) and enqueues a job in one transaction, then returns 200. No downloads,
   AI calls or uploads inside the request.
3. **Idempotency everywhere side effects happen.** Inbound events are unique per
   `(connection_id, provider_event_id)`; jobs have unique `idempotency_key`; outbound WhatsApp
   messages have unique `idempotency_key`; Drive uploads carry `appProperties.flowchainDocumentId`
   and are looked up before re-uploading.
4. **Never report success before storage confirms it.** Confirmation messages are enqueued only
   after the Drive upload and the DB commit succeed.
5. **External providers live behind interfaces** (`WhatsAppProvider`, `DocumentStorage`,
   `DocumentClassifier`, `DocumentExtractor`). Business logic never imports httpx clients directly.
6. **AI output is untrusted.** Validate with Pydantic, clamp to allowed categories, sanitize
   filenames, preserve extensions, use `None`/"Unknown" instead of guessing. Document content is
   data, never instructions. AI never generates SQL.
7. **Secrets**: only from environment variables; integration tokens encrypted at rest with
   `FIELD_ENCRYPTION_KEY` (Fernet/MultiFernet). Never log or return secrets; the log redaction
   filter must stay enabled. Never ask humans to paste secrets into chat.
8. **No process-local state for correctness.** Conversation state, pending media, search
   sessions, rate-limit counters for login and the job queue all live in Postgres.
9. **Do not invent provider APIs.** Wasender, Google Drive and Gemini adapters follow the docs
   recorded in `docs/architecture/provider-notes.md`. Anything unverified is marked as such.

## Conventions

- Python: SQLAlchemy 2 typed `Mapped[...]` models, sync sessions (psycopg 3), Pydantic v2,
  `ruff` for lint/format, `mypy` on `app/`. UUID primary keys, `timestamptz` columns, UTC.
- Errors: raise `AppError` subclasses from `app.core.errors`; they map to JSON
  `{ "error": { "code", "message", "request_id" } }`.
- Jobs: raise `TransientJobError` (retry with backoff+jitter) or `PermanentJobError`
  (fail immediately → `dead`). Unknown exceptions are treated as transient until
  `max_attempts`.
- Tests: synthetic data only. External HTTP is mocked with `respx`; mocked tests are named and
  documented as mocked. Never put real customer documents in fixtures.
- Frontend: server-side authorization lives in the backend; the frontend only renders.
  All state-changing requests send the `X-CSRF-Token` header.

## Git

- Small commits per stable phase. Do not push, deploy, or touch production resources without
  explicit human approval. Never commit `.env` files or credentials.

## Phase tracking

See `docs/architecture/phases.md` for the live checklist. Update it at the end of every phase.
