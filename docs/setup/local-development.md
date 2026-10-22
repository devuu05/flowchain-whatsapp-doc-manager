# Local development

## Prerequisites
Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22, PostgreSQL 16 (or Docker).

## Backend
```bash
createdb flowchain && createdb flowchain_test        # or use docker compose up db
cd backend
uv sync
cp ../.env.example ../.env   # the backend reads .env from its working dir: symlink or copy
ln -sf ../.env .env
uv run alembic upgrade head
printf 'Your-Strong-Pass-123\n' | uv run python -m app.cli create-admin --email you@example.com
uv run python -m app.cli create-tenant --name "Dev Co" --owner-email you@example.com
uv run python -m app.cli add-sender --tenant-slug dev-co --phone "+91 98765 43210"
uv run uvicorn app.main:app --reload            # API on :8000 (docs at /docs in non-production)
uv run python -m app.workers.runner             # worker (or set EMBEDDED_WORKER=true)
uv run pytest                                   # uses TEST_DATABASE_URL (default flowchain_test)
```

## Frontend
```bash
cd frontend && npm ci && npm run dev    # :3000, proxies /api/* to BACKEND_URL (default :8000)
```

## Trying the flow without real providers (FAKE providers, development only)
1. Sign in to the dashboard → *WhatsApp*: connections can only be created with the Wasender
   provider from the UI. For a fake one use the API:
   `POST /api/v1/tenants/{id}/integrations/whatsapp {"provider":"fake","phone_number":"+910000000000","webhook_secret":"dev-secret-123"}`
2. `POST /api/v1/tenants/{id}/integrations/fake-drive` creates an in-memory FAKE storage.
3. Send normalized events to `POST /api/v1/webhooks/fake/{connection_key}` with header
   `x-webhook-signature: dev-secret-123` and body `{"events":[NormalizedEvent...]}` — media bytes go
   in `media.provider_ref.b64`. See `backend/tests/conftest.py::media_event` for the shape.

Fake providers are refused when `ALLOW_FAKE_PROVIDERS=false` (mandatory in production).
