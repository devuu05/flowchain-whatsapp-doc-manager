# WhatsApp conversation rules

State lives in Postgres (`pending_media`, `search_sessions`, `search_results`) keyed by
`(tenant_id, connection_id, sender)`, so it survives restarts and never leaks between senders.

## Incoming file
1. Unsupported kinds (video/audio/sticker) → explanation.
2. Quota / size checks → explanation.
3. Download via provider → content validation → transient copy in `media_blobs`.
4. Identical file (SHA-256) already stored for the tenant → "already saved", no second upload.
5. Usable caption (≥3 letters/digits) → processing starts, "📥 Received …".
6. Otherwise → pending; ask for a description. With several pending files, list them.

## Incoming text — routing order
| # | Rule | Example |
|---|---|---|
| 1 | `help`/`hi`/`menu` | help text |
| 2 | `cancel [N|all]`, `skip [N|all]` | discard / save without description |
| 3 | `more`/`next` | next page of the active result list |
| 4 | Explicit search (starts with find/search/show/list/get/send me…) | `find invoices from ABC Traders` |
| 5 | Pending files: 1 pending → text is its description; ≥2 → must be `N: description` (never guesses) | `2: receipt from Dev Construction` |
| 6 | Bare number → selection **only** from an active, unexpired list of the same sender | `3` |
| 7 | Implicit search (category or "documents" noun without a verb) | `invoices from ABC` |
| 8 | Anything else → help |  |

A bare number with no active list replies "no result list" or "that list has expired" — it is
never matched against an old list. A new search supersedes the previous list. Expired pending
files are saved without a description (marked for review) rather than discarded.

## Replies
Every reply is an idempotent outbox row (`messages.idempotency_key`) sent by the `send_message`
job, so a retried job never sends the same reply twice. "✅ Saved" is only queued after the Drive
upload and the DB commit succeed.
