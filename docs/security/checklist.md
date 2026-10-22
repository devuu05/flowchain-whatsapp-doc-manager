# Security checklist

Legend: ✅ implemented + automated test · 🟡 implemented, not automatically tested / partial · ⛔ open

| Control | Status | Evidence |
|---|---|---|
| Webhook verification (shared secret, constant-time) | ✅ | `test_wasender.py::test_signature_verification`, `test_pipeline_e2e.py::test_invalid_signature_rejected` |
| Unguessable per-connection webhook URL; provider must match | ✅ | `test_invalid_signature_rejected` |
| Authentication (Argon2id, server-side sessions, idle+absolute expiry) | ✅ | `test_auth.py` |
| Login throttling in Postgres + per-IP rate limit | ✅ | `test_login_lockout_after_repeated_failures`, `test_media_and_login_rate_limited` |
| CSRF protection (session-bound token header + SameSite=Lax) | ✅ | `test_csrf_required_for_state_changes` |
| Authorization by role (owner/admin/member) | ✅ | `test_tenant_isolation.py::test_member_cannot_perform_admin_actions` |
| Tenant isolation (API) | ✅ | `test_tenant_a_cannot_access_tenant_b` (10 endpoints + cross-id probes) |
| Tenant/sender isolation (WhatsApp search/selection/delivery) | ✅ | `test_search_isolated_between_senders_and_tenants`, `test_tenants_with_separate_connections_are_isolated` |
| Unauthorized WhatsApp senders ignored, audited | ✅ | `test_unauthorized_sender_ignored_without_reply` |
| Rate limiting (webhook, media, login) | ✅ | `test_security_controls.py` — process-local counters (single instance) |
| Request body limit (1 MB) / file size limits | ✅ | `test_body_size_limit`, `test_oversized_file_rejected` |
| Content-based file validation; macros, zip bombs, encrypted, HTML/SVG/EXE rejected | ✅ | `test_files.py` |
| XXE-safe XML parsing | ✅ | `test_xxe_payload_is_not_resolved` |
| Extractor isolation (child process, timeout, 1 GB address-space cap) | ✅ | `test_isolated_extraction_*` |
| Secure session cookie (HttpOnly, SameSite=Lax, Secure in prod) | ✅ | `test_session_cookie_flags`; prod config refuses `COOKIE_SECURE=false` |
| Secrets encrypted at rest (Fernet/MultiFernet), never returned | ✅ | `test_integration_secrets_never_returned`, OAuth test asserts no token in responses |
| Log redaction (tokens, keys, cookies, thumbnails) | ✅ | `test_secrets_redacted_from_logs`, `test_whatsapp_thumbnails_and_media_keys_redacted` |
| uvicorn access log disabled (would log signed URLs / OAuth codes) | 🟡 | `infra/docker/entrypoint.sh` |
| Production config guards (secret key, encryption keys, fakes off) | ✅ | `test_production_settings_refuse_insecure_defaults` |
| Narrow CORS (disabled by default; same-origin proxy) | 🟡 | `app/main.py`, ADR 0003 |
| Security headers (API + dashboard CSP, frame-ancestors none) | 🟡 | middleware, `next.config.ts` |
| Open-redirect protection on login `next` | ✅ | frontend `login.test.tsx` |
| Signed, expiring, tenant-bound media URLs (no Drive links exposed) | ✅ | `test_signed_url_tampering_and_expiry` |
| Private-by-default Drive uploads, least-privilege scope `drive.file` | ✅ | `test_folder_path_find_or_create_and_upload_with_app_properties` |
| AI off by default, owner-only enable with consent record, sensitive categories never sent | ✅ | `test_ai_*` in `test_pipeline_e2e.py`, `test_ai_enable_requires_owner_and_consent_note` |
| Prompt-injection resistance (content treated as data; output validated, clamped) | ✅ | `test_ai_classification_validated_and_clamped`; model behaviour itself not testable offline |
| Temporary data cleanup (blobs TTL + hard cap, raw payload pruning, provider refs dropped) | ✅ | `test_provider_media_reference_pruned_after_processing`; sweeper in `services/maintenance.py` |
| Audit events for sensitive operations | ✅ | logins, member/role changes, integrations, exports, deletions, downloads, deliveries |
| Data export / deletion procedures | ✅ | `test_export_and_delete_tenant_data` |
| Dependency scanning | ✅ | `pip-audit` + `npm audit` in CI; both clean on 2026-10-22 |
| Container runs as non-root | ✅ | verified with `docker exec id` (uid 999) |
| Platform-admin access to all tenants | 🟡 | by design for the operator; every action is audited. Restrict the number of platform admins. |
| Signed media URL is a bearer token for ≤10 min | 🟡 | inherent to Wasender's send-by-URL; tokens are single-document, tenant-bound, rate-limited |
| Lease fencing before external side effects (no double sends) | ✅ | `test_lost_lease_prevents_second_send` |
| OAuth flow bound to initiating session; Drive root revalidated | ✅ | `test_oauth_callback_requires_same_session`, `test_reconnect_with_other_account_creates_new_root` |
| Spoof-resistant client IP (`TRUSTED_PROXY_HOPS`) | ✅ | `test_forwarded_for_uses_rightmost_trusted_hop` |
| Account-lockout DoS resistance (per email+IP, then per account) | ✅ | `test_lockout_is_per_ip_before_per_account` |
| Adding a user to a workspace without their consent | 🟡 | minimal data disclosed; an invite/accept flow is recommended before multi-client use |
| MFA for dashboard users | ⛔ | not implemented — recommended before onboarding more clients |
| Email-based password reset | ⛔ | not implemented; admins set initial passwords and users change them |
| Distributed rate limiting (multi-instance) | ⛔ | needed only when scaling beyond one API instance |
| Real-provider verification (Wasender, Drive, Gemini) | ⛔ | requires credentials — see `docs/testing/manual-provider-tests.md` |
