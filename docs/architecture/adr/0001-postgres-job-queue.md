# ADR 0001 — PostgreSQL-backed durable job queue instead of Redis + Celery

Status: Accepted

## Context
The system needs durable background processing with persistent job status, retries,
idempotency, dead-lettering and recovery after restarts. The prompt suggests Redis + Celery
"or another documented durable queue". The cost target is ~₹0: free Redis offerings are
request-capped (Celery polls Redis constantly and burns quotas), and free hosts rarely offer
a free always-on worker.

## Decision
Use a `jobs` table in PostgreSQL as the queue:
- Claim with `SELECT ... FOR UPDATE SKIP LOCKED` ordered by `run_at`, set a lease
  (`locked_until`). Expired leases are reclaimable ⇒ crash recovery without process state.
- Status: `queued → running → succeeded | queued (retry) | dead`.
- Bounded retries, exponential backoff with full jitter, transient vs permanent error classes.
- Unique `idempotency_key` per logical job ⇒ duplicate webhooks cannot enqueue twice.
- Enqueue happens in the same transaction as the inbound-event insert (transactional outbox).
- The worker runs as `python -m app.workers.runner`, or embedded in the API process
  (`EMBEDDED_WORKER=true`) when the host only allows one free service.

## Consequences
+ One fewer service, exact transactional semantics, job history queryable by dashboard.
− Polling latency (default 1 s idle poll); fine for pilot volume (hundreds of jobs/day).
− Throughput ceiling far below Redis; revisit (ADR) beyond ~10 jobs/s.
