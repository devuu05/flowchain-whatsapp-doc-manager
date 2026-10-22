# Backup and restore

## What needs backing up
- **PostgreSQL** (all metadata, configuration, encrypted secrets, audit).
- **Encryption keys** (`FIELD_ENCRYPTION_KEYS`, `SECRET_KEY`) — offline in a password manager.
  A DB backup without the encryption key cannot decrypt integration secrets (reconnect instead).
- Original documents live in the client's Google Drive (client's responsibility / Google's durability).

## Backups
- Supabase free plan: no downloadable automatic backups on all plans/regions — verify in your
  project (Database → Backups). Do a weekly logical backup yourself:
  ```bash
  pg_dump --format=custom --no-owner --dbname "$DIRECT_DATABASE_URL" -f flowchain-$(date +%F).dump
  ```
  Store encrypted (e.g. `age`/`gpg`) outside the hosting provider. Keep 4 weekly + 3 monthly.
- Paid plans: enable daily backups / PITR.

## Restore (tested procedure, run on a scratch database first)
```bash
createdb flowchain_restore
pg_restore --no-owner --dbname "postgresql://.../flowchain_restore" flowchain-YYYY-MM-DD.dump
DATABASE_URL=postgresql+psycopg://.../flowchain_restore uv run alembic current   # expect head
```
Then point `DATABASE_URL` at the restored DB, deploy, verify `/health/ready`, sign in, open a
document, and `Check connection` for WhatsApp and Drive.

The restore procedure was rehearsed locally on 2026-10-22 (see test report §Backup/restore).
