# Key management

| Secret | Where it lives | Rotation |
|---|---|---|
| `SECRET_KEY` (≥32 chars) | host env var | Rotating invalidates CSRF tokens, signed media URLs and OAuth state (users re-login) |
| `FIELD_ENCRYPTION_KEYS` (Fernet, comma separated) | host env var | Prepend new key → deploy → `python -m app.cli rotate-keys` → remove old key → deploy |
| `GOOGLE_CLIENT_SECRET` | host env var | Create new secret in Cloud Console, update env, delete old |
| `GEMINI_API_KEY` | host env var | Regenerate in AI Studio, update env |
| Wasender API key / webhook secret | encrypted in `integration_configs.encrypted_secrets` | Regenerate in Wasender, then dashboard → WhatsApp → update (PATCH endpoint) |
| Google refresh tokens | encrypted in DB | Reconnect Drive; disconnect revokes |
| DB credentials | host env var (`DATABASE_URL`) | Rotate in Supabase, update env |

Rules: never commit secrets; never paste them in chat or tickets; generate locally
(`uv run python -m app.cli gen-key`). Loss of `FIELD_ENCRYPTION_KEYS` means stored integration
secrets cannot be decrypted — reconnect WhatsApp and Drive. Keep an offline copy in a password
manager.
