# Incident response

## Severity
- **SEV1**: data exposure across tenants/senders, leaked secret, documents delivered to the wrong person.
- **SEV2**: intake or retrieval down > 1 h; Drive uploads failing for all documents.
- **SEV3**: degraded (slow, some failures, single document issues).

## SEV1 — suspected data exposure or leaked secret
1. **Contain**: disconnect affected WhatsApp connection(s) (dashboard) and/or suspend the tenant
   (`PATCH /tenants/{id}/quotas {"status":"suspended"}`); rotate the exposed secret
   (`docs/security/key-management.md`). If the API itself is suspect, scale to zero in Render.
2. **Preserve evidence**: export audit events (`/export`), keep logs (Render log retention is short —
   download now), note request ids.
3. **Assess**: audit log (`document.delivered_whatsapp`, `document.downloaded`, `auth.*`), messages table.
4. **Notify**: the client owner without undue delay; legal obligations under India's DPDP Act 2023
   may apply — consult counsel.
5. **Fix + verify** with a regression test; post-mortem within 5 working days.

## SEV2 playbooks
| Symptom | Check | Fix |
|---|---|---|
| No replies on WhatsApp | `/health/ready`; Wasender session status (`Check connection`); webhook URL configured; Render service awake | reconnect session (QR), fix URL, wake/redeploy |
| All uploads failing `invalid_grant` | Drive refresh token revoked/expired (OAuth app still in Testing?) | publish consent screen; reconnect Drive; retry dead jobs |
| Queue backlog growing | worker heartbeat in `/health/ready`; logs for crashes | redeploy; raise `WORKER_CONCURRENCY` cautiously |
| DB errors | Supabase status / paused project / size limit | resume project; free space; upgrade plan |
