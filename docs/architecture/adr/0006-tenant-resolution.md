# ADR 0006 — Tenant resolution for inbound WhatsApp traffic

Status: Accepted

Each WhatsApp connection (`integration_configs` row, kind `whatsapp`) belongs to exactly one
tenant and has an unguessable `connection_key` embedded in its webhook URL plus the provider's
webhook secret. Tenant = connection's tenant. The sender's phone number must be in the tenant's
`authorized_senders`; otherwise the event is recorded and ignored (no reply) to avoid
spam/abuse and accidental data disclosure. Conversation state (pending media, search sessions)
is keyed by `(tenant_id, connection_id, sender)`.
