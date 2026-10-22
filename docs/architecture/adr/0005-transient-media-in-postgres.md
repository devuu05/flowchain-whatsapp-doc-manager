# ADR 0005 — Transient media bytes stored in PostgreSQL until uploaded

Status: Accepted

Provider media URLs expire, free hosts have no persistent disk, and pending files (awaiting a
description) must survive worker restarts. Downloaded bytes are stored in `media_blobs` (bytea)
and deleted once the Drive upload is committed, or by the retention sweeper after
`MEDIA_BLOB_TTL_HOURS`. File size is capped (default 16 MB) so a 500 MB free database holds
the transient working set comfortably. Alternative (immediate upload to a Drive "_Inbox"
folder) was rejected because it requires Drive to be healthy before we can even acknowledge
the user and complicates partial-failure handling.
