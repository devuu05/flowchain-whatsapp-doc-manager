# Privacy, data flows and retention

## Who receives what
| Recipient | Data | When | Controls |
|---|---|---|---|
| WasenderAPI | Outgoing message text; signed URL of a requested document (it downloads the file); incoming messages/media (it relays them) | Every WhatsApp interaction | Dedicated number; authorized senders only |
| Google Drive (client's account) | Original files, standardized names, folder structure | After processing | `drive.file` scope; private; client owns the account |
| Google Gemini | Extracted text, sender description, filename, and (if no text) the file bytes | Only if the workspace owner enabled AI, quota not exceeded, and the category allows AI | Off by default; consent record; sensitive categories excluded; paid tier required by policy |
| Hosting providers (API host, Supabase) | Everything above transiently + metadata | Always | TLS; encrypted secrets; minimal retention |

No document content is used for model training by Flow Chain. Gemini paid-tier terms state
prompts are not used to improve Google products; the free tier is not acceptable for client data.

## What we store (PostgreSQL)
- Document metadata: filenames, type, company/person, date/period, short summary (AI only),
  sender phone, sizes, hashes, Drive file id. **Not** the extracted full text.
- Transient file bytes (`media_blobs`) until upload (≤ `MEDIA_BLOB_TTL_HOURS`, hard cap 14 days).
- Conversation log: user captions/requests (≤1000 chars) and bot replies.
- Webhook events: normalized event (provider media handle removed after processing) and a
  redacted raw payload, pruned after 14 days.
- Audit events (no document content).

## Retention & deletion
| Data | Retention |
|---|---|
| Transient file copies | until stored; ≤72 h default; 14-day hard cap |
| Raw webhook payloads | 14 days |
| Search lists | 30 min active; rows retained until tenant deletion |
| Expired sessions | deleted 7 days after expiry; login attempts 30 days |
| Document metadata, messages | until deleted by the tenant owner |
| Audit events | retained (accountability), including after a data deletion |
| Files in Drive | owned by the client; optionally trashed by the deletion procedure |

Owner tools: *Export & deletion* page → JSON export; "Delete all data" (typed confirmation,
optional Drive trash). Database backups (provider-managed) age out per the provider's backup
retention — state this in the client agreement.

## Logs
JSON logs with recursive redaction of secrets/tokens; phone numbers masked in worker logs;
document text and file bytes are never logged.
