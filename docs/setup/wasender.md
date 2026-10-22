# WhatsApp setup

Two providers are supported. **The pilot uses wasender.dev** (`app.wasender.dev`). WasenderAPI
(`wasenderapi.com`) is a different company; its adapter is kept for completeness.

## wasender.dev (pilot)
1. app.wasender.dev → **Numbers** → your connected number. Have its **channel token** (`wsk_...`)
   ready — it is shown once when the number is created; if lost, rotate it (old token stops working).
2. Find the **webhook signing secret** wasender.dev uses for `x-wasender-signature`
   (number → Webhook / settings, or `GET https://manage.wasender.dev/channels/{id}/webhook`).
   Unsigned or wrongly signed webhooks are rejected with 403 by design.
3. Flow Chain dashboard → **WhatsApp** → Provider **wasender.dev** → number, channel token, signing
   secret → **Connect**. Copy the **Webhook URL** shown
   (`https://<api>/api/v1/webhooks/wasender_dev/<key>`).
4. app.wasender.dev → number → **Webhook** → paste the URL → **Save webhook**.
5. Dashboard → **WhatsApp → Check connection** → `connected` (or `hibernated`, also healthy).
6. From an authorized phone send `hi` → help text.

Risks: unofficial WhatsApp client (use a dedicated number); first-contact messages to people who
never messaged you can get a number restricted — the bot only answers authorized senders.
Verified vs. unverified API details: `docs/architecture/provider-notes.md`.

## WasenderAPI (wasenderapi.com) — not used for the pilot

> The adapter was built from WasenderAPI's official SDKs because the live API reference was not
> reachable from the build environment. Verify each item in
> `docs/testing/manual-provider-tests.md` §WhatsApp with your real account before the pilot.

## Risks to accept consciously
- WasenderAPI automates a regular WhatsApp account (WhatsApp Web protocol). It is **not** the
  official WhatsApp Business Platform. Numbers used this way can be restricted or banned by
  WhatsApp; use a dedicated number, not the client's primary business line.
- Per-plan rate limits and webhook retry behaviour are not documented in the SDKs. If the API is
  asleep (free hosting) when a webhook arrives, the message may be lost.
- A future `WhatsAppProvider` adapter for the official Cloud API can replace Wasender without
  changing business logic (ADR 0004).

## Steps (you, in the Wasender dashboard — never paste these secrets into chat)
1. Create/connect a WhatsApp session for the dedicated number (scan the QR code).
2. Copy the session **API key** and the **webhook secret** (Settings of that session).
3. In the Flow Chain dashboard → *WhatsApp* → *Connect a number*: enter the number, API key and
   webhook secret. They are encrypted at rest and never displayed again.
4. Copy the **Webhook URL** shown (`https://<api>/api/v1/webhooks/wasender/<connection-key>`) into
   the Wasender session's webhook URL, enable webhooks and select the event
   `messages.received` (optionally `messages.upsert`; duplicates are de-duplicated).
5. Dashboard → *Members & senders*: add every staff phone number allowed to use the bot.
6. Dashboard → *WhatsApp* → *Check connection* should show `connected`.
7. Send "hi" from an authorized number → expect the help text.

## What the adapter assumes (verify!)
| Assumption | Where |
|---|---|
| `Authorization: Bearer <session api key>` | `providers/whatsapp/wasender.py` |
| `POST /send-message {to:"+E164", text}` → `data.msgId` | idem |
| Documents are sent by URL (`documentUrl`, `fileName`) — our API serves a 10-minute signed URL | idem + `api/v1/media.py` |
| Webhook header `x-webhook-signature` equals the secret | idem |
| `POST /decrypt-media {"data": <webhook message>}` returns `publicUrl` or `url` (top level or under `data`) | idem |
| Message fields under `data.messages` or `data` (`key.id`, `key.remoteJid`, `key.cleanedSenderPn`, `message.*`) | idem |

If any differs, adjust only `wasender.py` and its tests (`backend/tests/test_wasender.py`).
