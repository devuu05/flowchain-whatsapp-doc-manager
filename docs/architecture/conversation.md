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

## Sender roles
Authorized senders are **members** (save, search, receive) or **WhatsApp admins** (additionally
rename and delete). Roles are set in the dashboard (*Members & authorized senders*), with
`cli add-sender --admin`, or `BOOTSTRAP_ADMIN_PHONES`. The role is checked when the message is
handled *and* again when the rename/delete job runs.

## Choosing the folder
A caption (or a pending-file description) that starts with `#` picks the folder:
`#Invoices/2026 bill from ABC` → stored in `Invoices/2026/` with description "bill from ABC".
`#Receipts` alone is enough (no description needed). Folder names with spaces end with a
colon: `#Bank Statements/2026: March`. Up to 3 levels; names are sanitized.
Without `#`, the folder is `<Category>/<Year>/` as before.

## Guided steps (`conversation_states`)
One open step per sender (TTL 10 min; delete confirmation 5 min), stored in Postgres:
`menu`, `ask_search`, `pick_rename`, `pick_delete`, `rename_name`, `confirm_delete`.
A new search closes any open step, so a later bare number always means the newest list.
A delete is only carried out on an explicit **YES** (also *y*, *confirm*, *haan*) while
`confirm_delete` is open; any other message cancels it.

## Incoming text — routing order
| # | Rule | Example |
|---|---|---|
| 0 | Open guided step (delete confirmation, new name, menu number, pick number) | `YES`, `5` |
| 0a | `menu`/`hi`/`hello` → numbered menu (admins see rename/delete) | `menu` |
| 0b | `folders` → folders with document counts | `folders` |
| 0c | Admin: `rename N to NAME`, `delete N` (N from the active list); `rename`/`delete` alone start a guided pick | `rename 2 to ABC Sept bill` |
| 1 | `help`/`?` | help text |
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
Rename and delete run as `rename_document` / `delete_document` jobs; "✏️ Renamed" and "🗑️ Deleted"
are queued only after the storage provider confirmed the change (delete = provider trash /
recycle bin), in the same transaction as the database update.

Every reply is an idempotent outbox row (`messages.idempotency_key`) sent by the `send_message`
job, so a retried job never sends the same reply twice. "✅ Saved" is only queued after the Drive
upload and the DB commit succeed.
