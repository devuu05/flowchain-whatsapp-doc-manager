# Provider Notes (verification status)

Research date: 2026-10-22. The build sandbox's egress proxy **blocked direct access** to
wasenderapi.com, ai.google.dev and vendor pricing pages. Facts below come from secondary
sources as labelled. Every item marked **UNVERIFIED** must be confirmed against the live docs
/ a real account before the pilot (see `docs/testing/manual-provider-tests.md`).

## WasenderAPI (WA Sender)
Source: WasenderAPI's own client SDKs — `wasenderapi` npm 0.4.0, PyPI `wasenderapi` 0.3.5,
`github.com/wasenderapi/wasenderapi-laravel` — not the live API reference.

| Item | Value used by adapter | Status |
|---|---|---|
| Base URL | `https://www.wasenderapi.com/api` (configurable `WASENDER_BASE_URL`) | SDK default |
| Auth | `Authorization: Bearer <session API key>` for `/send-message`, `/upload`, `/decrypt-media`, `/status` | SDK code |
| Send text | `POST /send-message` `{"to": "+E164", "text": "..."}` → `data.msgId` | SDK code/models |
| Send document | `POST /send-message` `{"to","documentUrl","fileName","text"(caption)}` — **by URL only** | SDK code |
| Upload | `POST /upload` (base64 JSON or multipart `file`) — response shape **UNKNOWN** | not used |
| Webhook envelope | `{event, timestamp?, data, sessionId?}`; events `messages.received`, `messages.upsert`, … | SDK types |
| Message fields | `data.key.{id,fromMe,remoteJid,senderPn,cleanSenderPn}`, `data.message.{conversation, extendedTextMessage.text, imageMessage{caption,mimetype}, documentMessage{fileName,mimetype,caption?}}`, `data.pushName` | SDK types — **verbatim payload UNVERIFIED** |
| Webhook auth | header `x-webhook-signature` equals the dashboard webhook secret (plain equality, compared in constant time) | SDK code — **UNVERIFIED** |
| Media download | `POST /decrypt-media` `{"data": <webhook message data>}` → response URL field **UNKNOWN**; adapter accepts `publicUrl`/`url` at top level or under `data` | **UNVERIFIED** |
| Session status | `GET /status` → `{"status": connected|disconnected|need_scan|connecting|logged_out|expired}` | SDK types |
| Limits | image 5 MB, video/audio 16 MB, document 100 MB; 429 with `retry_after`; per-plan rate limits **UNKNOWN** | SDK comments |
| Webhook retries | **NOT DOCUMENTED** → we dedupe on `data.key.id` anyway | — |

Design consequences:
- Because documents are sent **by URL**, we never hand out Drive links. The backend issues a
  short-lived (default 10 min), HMAC-signed, single-document URL
  `GET /api/v1/media/{token}` that streams bytes we fetched from Drive. Tokens are bound to the
  tenant and document and expire; the endpoint is rate-limited.
- The webhook parser is defensive: unknown event types are recorded and ignored; messages with
  `fromMe=true` are ignored (prevents reply loops); group messages are ignored for the pilot.

## Google Drive (REST v3, httpx)
- OAuth 2.0 authorization-code flow with `access_type=offline`, `prompt=consent`.
- Scope: `https://www.googleapis.com/auth/drive.file` (non-sensitive; app can only see files it
  created or the user opened with it). Folders are created by the app, so this is sufficient.
- Upload: `POST https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=...`
  with `appProperties.flowchainDocumentId` for idempotent lookup (`files.list` with
  `q=appProperties has { key='flowchainDocumentId' and value='<uuid>' } and trashed=false`).
- Download: `GET /drive/v3/files/{id}?alt=media`. Metadata includes `md5Checksum`, `size`.
- Files are created with no permissions changes ⇒ private to the connected account.
- Apps left in OAuth "Testing" publishing status get refresh tokens that expire after 7 days —
  publish the consent screen to "In production" for the pilot.

## Gemini API
Source: search-engine excerpts of ai.google.dev (direct fetch blocked) — **UNVERIFIED in detail**.
- Model default `gemini-3.5-flash-lite` (reported GA 2026-07-21); configurable `GEMINI_MODEL`.
  `gemini-3.1-flash-lite` reportedly shuts down 2027-05-07.
- REST `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`,
  header `x-goog-api-key`. Structured output: `generationConfig.responseMimeType =
  "application/json"` + `responseJsonSchema` (`responseSchema` reportedly deprecated).
- Inline data: images/PDF as `inlineData{mimeType,data(base64)}`; we cap at 15 MB.
- Errors: 429 RESOURCE_EXHAUSTED / 503 / 504 / 408 → transient; 400/403 → permanent; 402 →
  permanent (billing).
- **Data use**: Unpaid tier content may be used to improve Google products and be human
  reviewed; Paid tier is not used for product improvement (55-day abuse-monitoring
  retention). ⇒ Real client documents only with a billed project and client consent.
