# Architecture & Implementation Plan

## 1. Goals
Receive documents over WhatsApp → validate → extract → classify (AI, optional) → store original
in the tenant's Google Drive → index metadata in PostgreSQL → retrieve via natural-language
WhatsApp chat. Multi-tenant from day one, one pilot tenant initially, ~₹0 infrastructure.

## 2. System context

```
 WhatsApp user ──► WA Sender (WasenderAPI) ──webhook──► FastAPI  /api/v1/webhooks/wasender/{connection_key}
                                                         │  verify signature, dedupe, INSERT inbound_event + job (1 tx), 200
                                                         ▼
                                                  PostgreSQL ◄──── Worker (same image; separate process
                                                    jobs table      or embedded thread on free tier)
                                                         │             │ claim job (FOR UPDATE SKIP LOCKED, lease)
                                                         │             ├─► WasenderAPI (download/decrypt media, send text/doc)
                                                         │             ├─► Extractors (pypdf, python-docx, openpyxl, python-pptx, xlrd)
                                                         │             ├─► Gemini API (classification / vision OCR) — optional per tenant
                                                         │             └─► Google Drive API (upload/download, drive.file scope)
 Admin ──► Next.js dashboard ──(/api proxy, same-origin cookie)──► FastAPI /api/v1/...
```

## 3. Key flows

**Inbound document**: webhook → `inbound_events` (unique `(connection_id, provider_event_id)`) +
`jobs(kind=handle_inbound)` → worker resolves tenant & authorized sender → downloads media via
provider → validates magic bytes / MIME / extension / size → stores bytes in `media_blobs`
(transient, Postgres) → creates `documents(status=received)` → if caption usable enqueue
`process_document`, else create `pending_media` and ask for a description.

**Description reply**: text from a sender with pending media → if exactly one pending, attach;
if several, list them and require `N: description`; `skip` processes without a description.
Pending state expires (default 24h) and lives in Postgres.

**process_document**: extract text (limits, no macros, XXE-safe) → classify (Gemini or rules
fallback, Pydantic-validated, clamped to tenant categories) → resolve tenant folder
(`<root>/<Category>/<YYYY>`) → upload with `appProperties.flowchainDocumentId` (lookup first ⇒
idempotent) → commit metadata → enqueue confirmation. Failures never produce a success reply.

**Search**: text with search intent → deterministic parser (+ optional AI parser into the same
validated filter schema) → tenant-scoped SQL with fixed filters → `search_sessions` +
`search_results` rows (tenant, sender, connection, expiry) → numbered reply. A bare number from
the same sender within expiry selects; otherwise normal intent handling.

**Delivery**: selection → `jobs(kind=deliver_document)` → authorize → Drive download → provider
send-document (provider-specific transport, see provider notes) → outbound message record.

## 4. Data model (all tenant-owned tables carry `tenant_id`)
tenants, users, tenant_memberships(role: owner|admin|member), user_sessions,
integration_configs (whatsapp|google_drive|ai; encrypted secrets), authorized_senders,
inbound_events, outbound_messages, messages (conversation log, no document contents),
jobs (queue + persistent status), media_blobs, documents, pending_media,
search_sessions, search_results, categories (per tenant), audit_events, login_attempts.

## 5. Phases
See `phases.md`. Each phase: smallest vertical slice → tests → docs → git checkpoint.

## 6. Assumptions
- One WA Sender session (phone number) belongs to the pilot tenant. Senders must be on the
  tenant's authorized-sender list; others are ignored (logged, no reply) to avoid spam abuse.
- Pilot volume: ≤ 50 documents/day, ≤ 16 MB per file (configurable).
- Google Drive is the client's own account, connected via OAuth (scope `drive.file`).
- AI processing is **off by default** per tenant until the client authorizes it and the
  Gemini plan's data terms are accepted (free tier allows Google to use content — see
  `docs/security/privacy.md`).

## 7. External dependencies & risks
| Dependency | Risk | Mitigation |
|---|---|---|
| WasenderAPI (unofficial WhatsApp Web based) | Number ban risk, API changes, webhook format drift | Adapter isolated; official Cloud API adapter possible later; raw payload stored for debugging |
| Gemini free tier | Data used for product improvement; low RPD | AI off by default; rules fallback; paid key recommended for real documents |
| Google OAuth "testing" mode | Refresh tokens expire after 7 days for apps in Testing status | Publish OAuth app (drive.file is non-sensitive → no verification video) — see setup doc |
| Free hosting | Sleep/cold start, no persistent worker | Postgres queue + embedded worker; honest limits in `docs/operations/hosting.md` |
| Supabase free | Pauses after inactivity; 500 MB | Keep blobs transient; weekly activity is real usage; backups doc |
