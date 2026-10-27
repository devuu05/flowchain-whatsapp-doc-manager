# Manual provider verification (requires real credentials)

Automated tests mock WasenderAPI, Google and Gemini HTTP. These steps verify the real services.
Record date, tester, result and any deviation in the table at the bottom. Use **synthetic**
documents only (e.g. `backend/tests/synthetic.py` → `pdf_with_text([...])`), never client files
until the client approves.

## WhatsApp (WasenderAPI)
Preconditions: API deployed with public HTTPS; connection configured per `docs/setup/wasender.md`.
1. **Status**: Dashboard → WhatsApp → *Check connection* → `connected`. If `unauthorized`, the API key is wrong.
2. **Webhook auth**: `curl -X POST https://<api>/api/v1/webhooks/wasender/<key> -H 'x-webhook-signature: wrong' -d '{}'` → 403.
3. **Text round trip**: from an authorized phone send `hi` → help text within seconds (if the
   service was asleep, note the cold-start delay and whether the message arrived at all).
   - In logs find `inbound_accepted`; in DB `inbound_events.raw_payload` shows the real payload —
     compare with the assumed shape in `providers/whatsapp/wasender.py`. **Record any differences.**
4. **Image with caption** `receipt from Test Vendor` → "📥 Received" then "✅ Saved"; file in Drive.
   If it fails at download: inspect the `/decrypt-media` response shape (job `last_error`) and fix
   the URL field mapping in `download_media`.
5. **PDF without caption** → asked for description → reply → saved.
6. **Duplicate webhooks**: in Wasender enable both `messages.received` and `messages.upsert`; send one
   file → exactly one document and one confirmation.
7. **Retrieval**: `find all documents` → list; reply `1` → the document arrives with the right filename.
   This verifies that Wasender can fetch our signed URL (`/api/v1/media/...`).
8. **Unauthorized sender**: message from a number not on the list → no reply; audit event present.
9. **Rate limits**: send 10 messages quickly; note any 429 from Wasender in job errors (they are retried).

## Google Drive
1. Connect via dashboard; Google consent screen lists only "See, edit, create, and delete only the specific Google Drive files you use with this app".
2. "Flow Chain Documents" folder appears in the client's Drive; uploaded file under `<Category>/<Year>/`.
3. File sharing settings: *Restricted* (not "Anyone with the link").
4. Disconnect → `https://myaccount.google.com/permissions` no longer lists the app.
5. Reconnect; upload again; the earlier root folder is reused.
6. After 8 days, uploads still work (proves the consent screen is *In production*, not *Testing*).

## OneDrive
1. Dashboard → Cloud storage → Connect OneDrive from a **phone** browser (not the server, not your
   PC); Microsoft's consent screen lists "Have full access to your files", "Maintain access" and
   "Sign you in and read your profile".
2. "Flow Chain Documents" appears at the top of the OneDrive; a captioned upload lands in
   `<Category>/<Year>/`; a `#Clients/Acme` caption lands in `Clients/Acme/`.
3. Send a file > 4 MB → stored (upload session path).
4. `find all documents` → `1` → the file arrives in WhatsApp (download via `downloadUrl`).
5. As a WhatsApp admin: `rename 1 to Test rename` → name changes in OneDrive; `delete 1` → `YES`
   → file is in the OneDrive recycle bin. As a member: both are refused.
6. Leave it a day, send another file → still stored (rotated refresh token was persisted).

## WhatsApp chat (any storage)
1. `hi` → numbered menu; member sees 4 options, admin 6. `1` → latest documents; `3` → folders.
2. Admin `delete 1`, then any other message, then `yes` → nothing deleted (confirmation closed).

## Gemini (only with a billed key and client consent)
1. Enable AI for the test workspace with a consent note.
2. Send a synthetic invoice image without text layer → document classified by `gemini` with sensible
   fields; `extraction_warnings` empty of "AI classification unavailable".
3. Send a document described as "PAN card" → classifier `rules` (sensitive category never sent).
4. Temporarily set an invalid `GEMINI_API_KEY` → documents still stored via rules with a warning.

## Results log
| Date | Tester | Step | Result | Notes |
|---|---|---|---|---|
| — | — | — | NOT RUN | credentials not yet available |
