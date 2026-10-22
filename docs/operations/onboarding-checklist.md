# Pilot client onboarding checklist

Do not onboard additional clients until every item here and the tenant-isolation tests pass.

## Before
- [ ] Written agreement covering: documents processed, providers used (WasenderAPI, Google, optional Gemini), retention, deletion, incident notification.
- [ ] Client authorizes the dedicated WhatsApp number and the Google account to connect.
- [ ] Decision on AI: off (default) or on with written consent + **billed** Gemini key.
- [ ] Production env vars set per `deployment-plan.md`; `ALLOW_FAKE_PROVIDERS=false`.
- [ ] `docs/testing/manual-provider-tests.md` executed and recorded.

## Setup (≈30 min)
- [ ] `cli create-tenant --name "<Client>" --owner-email <client owner>` (create the user first via dashboard members or `create-admin` for yourself).
- [ ] Quotas: documents/month, AI/month, max file size (default 16 MB).
- [ ] WhatsApp connection + webhook URL configured in Wasender; *Check connection* = connected.
- [ ] Authorized senders: list every staff number.
- [ ] Google Drive connected with the **client's** account; "Flow Chain Documents" folder visible to them.
- [ ] Categories reviewed; sensitive categories have AI disabled.

## Verification with synthetic/approved documents
- [ ] Send "hi" → help text.
- [ ] Send a synthetic invoice PDF with caption → "✅ Saved", file in Drive `Invoices/<year>/`.
- [ ] Send an image without caption → asked for description → reply → saved.
- [ ] "find all invoices" → list; reply "1" → file arrives in WhatsApp with correct name.
- [ ] Reply "9" → "between 1 and N"; wait 30+ min → "1" → "expired".
- [ ] Message from a non-authorized number → no reply; audit shows `whatsapp.unauthorized_sender`.
- [ ] Dashboard: document visible; download works; audit log entries present.
- [ ] Logs reviewed: no secrets, no document text.

## Handover
- [ ] Client owner can sign in, change password, add members.
- [ ] Explain limitations (hosting sleep on free tier, unofficial WhatsApp API, DOC/PPT text, image OCR requires AI).
- [ ] Explain disconnect (WhatsApp/Drive pages) and export/delete (owner).
