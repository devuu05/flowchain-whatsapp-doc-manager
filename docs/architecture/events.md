# Internal normalized event schema

Every WhatsApp provider adapter converts webhook payloads to `NormalizedEvent`
(`backend/app/schemas/events.py`). The rest of the system only sees this schema.

```json
{
  "provider": "wasender",
  "event_id": "3EB0C7A1F2",            // provider message id; unique per connection (dedupe key)
  "event_type": "message",             // message | status | other
  "sender": "919876543210",            // digits only, with country code
  "sender_name": "Ravi",
  "from_me": false,                    // true = sent by our own number → ignored
  "is_group": false,                   // group chats are ignored in the pilot
  "timestamp": "2026-10-22T10:00:00Z",
  "text": "invoice from ABC Traders",  // text body or media caption
  "media": {                           // optional
    "kind": "document",                // image | document | video | audio | sticker
    "mime_type": "application/pdf",
    "filename": "scan.pdf",
    "size_bytes": 20480,
    "provider_ref": { }                // opaque download handle; pruned after processing
  }
}
```

`POST /api/v1/webhooks/whatsapp` accepts `{"connection_key": "...", "event": NormalizedEvent}` with
header `X-Internal-Token` (enabled only when `INTERNAL_WEBHOOK_TOKEN` is set). It is intended for
integration testing and for future providers that are bridged externally. Extra fields are rejected.

Provider webhooks: `POST /api/v1/webhooks/{provider}/{connection_key}` (e.g. `wasender`).
Responses: `200 {"status":"ok","accepted":n,"duplicate":n,"ignored":n}`; `403` bad signature;
`404` unknown/disconnected connection or provider mismatch; `400` unparseable payload; `429` rate limit.
