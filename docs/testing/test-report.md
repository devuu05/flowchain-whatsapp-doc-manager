# Test report — 2026-10-22

Environment: Linux container, Python 3.12, PostgreSQL 16 (real, local), Node 22, Chromium (Playwright).
External services (WasenderAPI, Google OAuth/Drive, Gemini) were **mocked** (respx HTTP mocks or the
labelled FAKE providers). No real customer data was used; all documents are generated synthetically.

## Summary
| Suite | Result | Command |
|---|---|---|
| Backend unit + integration (pytest, real Postgres) | **191 passed, 0 failed** | `cd backend && uv run pytest` |
| Backend coverage | 87% lines (`app/`) | `uv run pytest --cov=app` |
| Backend lint / format / types | ruff ✔, ruff format ✔, mypy ✔ | |
| Frontend (Vitest + Testing Library) | **23 passed, 0 failed** | `cd frontend && npm test` |
| Frontend lint / typecheck / production build | ✔ / ✔ / ✔ | |
| Dependency audit | pip-audit: no known vulns; npm audit (prod): 0 | |
| Docker images build + run (non-root) | ✔ backend 408 MB, dashboard 335 MB | |
| Full-stack smoke (containers + FAKE providers + headless browser) | ✔ | see below |
| Backup → restore rehearsal | ✔ row counts identical, secrets decrypt | see below |
| Real WasenderAPI / Google Drive / Gemini | **NOT RUN** — credentials not available | docs/testing/manual-provider-tests.md |
| CI on GitHub Actions | **NOT RUN** — repository not reachable from this session | .github/workflows/ci.yml |

## Pilot scenarios
| Scenario | Status | Test(s) |
|---|---|---|
| WhatsApp text round trip | PASS (mocked provider); live NOT RUN | `test_find_all_and_by_type_company_date`, smoke |
| Image & PDF uploads | PASS (mocked) | `test_pdf_with_caption_is_stored_and_confirmed`, `test_image_without_caption_asks_then_uses_reply` |
| Missing-caption workflow, multiple pending, skip/cancel, expiry | PASS | `test_multiple_pending_files_require_explicit_choice`, `test_skip_and_cancel`, `test_pending_state_survives_worker_restart_and_expires_safely` |
| DOCX, XLSX, PPTX, CSV, TXT handling | PASS | `test_office_formats_end_to_end`, `test_extract_*` |
| Corrupt / unsupported / macro / zip-bomb / oversized files | PASS | `test_files.py`, `test_invalid_files_rejected_with_clear_reply`, `test_oversized_file_rejected` |
| AI classification & malformed output | PASS (mocked Gemini) | `test_classifier.py`, `test_ai_*` |
| Google Drive upload failures (transient, permanent, exhaustion) | PASS (mocked) | `test_transient_drive_failure_retries_then_succeeds`, `test_permanent_drive_failure_*`, `test_retry_exhaustion_dead_letters` |
| Upload succeeded but DB commit failed → reconciliation, no duplicate | PASS | `test_reconciliation_when_commit_fails_after_upload` |
| Duplicate webhooks | PASS | `test_duplicate_webhook_delivery_is_processed_once`, smoke |
| Worker restart recovery (lease expiry) | PASS | `test_worker_crash_recovery_via_lease_expiry`, `test_attempt_budget_enforced_on_repeated_lease_expiry` |
| Concurrent processing | PASS | `test_concurrent_workers_never_double_process`, `test_concurrent_messages_from_same_sender_are_serialized` |
| Search by type, company, date; find-all; latest; filename; pagination | PASS | `test_search_delivery.py` |
| Valid / invalid selections; expired state; superseded lists | PASS | idem |
| Cross-tenant access attempts (API + WhatsApp) | PASS | `test_tenant_isolation.py`, `test_search_isolated_between_senders_and_tenants` |
| Unauthorized dashboard access | PASS | `test_unauthenticated_requests_rejected`, `test_csrf_required_for_state_changes`, frontend `shell.test.tsx` |
| Database failures | PASS | `test_database_error_inside_handler_is_transient`, `test_failure_recording_survives_db_outage` |
| Queue failures | PASS (queue = Postgres; covered by DB failure + lease tests) | |
| Provider timeouts | PASS (mocked) | `test_timeout_is_transient` (Wasender), Gemini/Drive timeout tests |
| Backup and restore | PASS (local rehearsal) | `pg_dump`/`pg_restore` of smoke DB; `alembic current` = head; counts equal; secrets decrypt |

## Full-stack smoke (real containers)
API container (embedded worker) + dashboard container + local Postgres, FAKE WhatsApp/storage:
PDF with caption → stored as `Invoice_ABC-Traders_2026-09-15_….pdf` in `Invoices/2026`; duplicate
delivery → `duplicate: 1`; bad signature → 403; `find invoices from ABC Traders` → 1 result; `1` →
document delivered (`messages.kind=document, status=sent`); unauthorized sender → ignored + audited.
Dashboard via headless Chromium at 1280×860 and 390×844: login → documents → overview → detail,
no horizontal overflow, anonymous API access 401.

## Independent review (2026-10-22)
A separate reviewer audited the backend without seeing my conclusions. It found 10 issues (2 reproduced).
All were fixed, each with a regression test in `backend/tests/test_review_fixes.py`:
| # | Finding | Fix |
|---|---|---|
| 1 | Lost lease → duplicate WhatsApp sends | lease heartbeat + fenced `ensure_lease` before every external side effect |
| 2 | Retrying a dead inbound job was a no-op; sender not told | retry resets the event; sender gets "couldn't process" |
| 3 | OAuth state not bound to the browser session | state bound to session hash; callback via dashboard proxy requires same session |
| 4 | Drive reconnect reused an inaccessible root folder | root verified with new token, recreated if needed |
| 5 | Client IP spoofable via `X-Forwarded-For` with `*` | `TRUSTED_PROXY_HOPS` uses right-most trusted hop |
| 6 | Media keys/thumbnails kept for ignored/failed events | stripped on ignore; sweep prunes (failed after 3-day retry window) |
| 7 | Members saw webhook URLs; connection key in request logs | admins only; key masked in logs |
| 8 | Adding an existing user disclosed their profile | minimal response for pre-existing users |
| 9 | Members saw audit activity on overview | omitted for members |
| 10 | Images without description could reach AI despite sensitive categories | bytes withheld unless described or no AI-blocked categories |
| + | Anyone could lock an account with 5 bad logins | lockout per (email, IP) at 5; per account at 20 |

## Known defects / limitations
1. Live provider behaviour unverified (Wasender payload shapes, decrypt-media response, Drive OAuth, Gemini model id).
2. No local OCR: images and scanned PDFs get text only via Gemini (when enabled); otherwise classified from caption/filename.
3. `.doc`/`.ppt` text extraction disabled by default (originals stored).
4. Rate limits are per-process (fine for one instance).
5. No MFA / self-service password reset.
6. Free hosting: cold starts can drop webhooks if the provider does not retry (undocumented).
7. Search results ordered by upload time, not document date.

## Production readiness verdict
**Not production-ready yet.** Code-level security and reliability tests pass, but criterion 14 (actual
provider integrations tested) is open and nothing has been deployed. After the manual provider tests
pass on a deployed instance (Option B hosting recommended), the system is ready for the single pilot.
