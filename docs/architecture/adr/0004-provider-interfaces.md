# ADR 0004 — Provider interfaces for WhatsApp, storage, AI and extraction

Status: Accepted

`WhatsAppProvider` (Wasender now, official WhatsApp Business Platform later), `DocumentStorage`
(Google Drive), `DocumentClassifier` (Gemini, deterministic rules fallback) and
`DocumentExtractor` (per format). Adapters are thin HTTP clients (httpx) and are tested with
`respx`-mocked HTTP. Business services depend only on interfaces, resolved per tenant from
`integration_configs` through a provider registry. Fakes exist for tests and local dev and are
clearly labelled; they are never selected in production (`APP_ENV=production` refuses fakes).

Gemini and Drive are called through their REST APIs with httpx instead of the heavyweight SDKs
to keep the image small and HTTP behaviour (timeouts, retries, error classification) explicit.
