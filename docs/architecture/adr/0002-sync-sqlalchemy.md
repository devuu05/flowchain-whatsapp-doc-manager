# ADR 0002 — Synchronous SQLAlchemy 2 with psycopg 3

Status: Accepted

FastAPI runs sync endpoints in a threadpool; the worker is a plain loop. Sync SQLAlchemy keeps
one code path for API, worker and tests, simplifies transactions/row locking, and avoids async
driver pitfalls. Async can be adopted later per-module if profiling demands it.
