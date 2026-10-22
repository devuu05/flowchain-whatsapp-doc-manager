# Gemini (optional AI classification)

AI is **off by default** for every workspace. Without it, documents are classified by a local
keyword/rule classifier from the sender's description, filename and extracted text.

## Before enabling (privacy gate)
- Gemini **free (unpaid) tier**: Google may use prompts/responses to improve its products and
  human reviewers may read them. Do **not** send client documents through it.
- **Paid tier** (billing enabled on the Google Cloud project): content is not used to improve
  products; retained ~55 days for abuse monitoring (per Google's published terms at the time of
  writing — verify at https://ai.google.dev/gemini-api/terms).
- Get the client's written authorization. The workspace owner records it in the dashboard
  (*AI & categories* → consent record) when enabling; this is audit-logged.
- Categories ID/KYC, Bank Statement and Cheque default to "never send to AI".

## Configure
1. https://aistudio.google.com → API key in a **billed** project.
2. Hosting env: `GEMINI_API_KEY`, optionally `GEMINI_MODEL` (default `gemini-3.5-flash-lite`;
   confirm availability at https://ai.google.dev/gemini-api/docs/models).
3. Dashboard → *AI & categories* → enable with consent note.
4. Quota per workspace: `monthly_ai_quota` (platform admin: `PATCH /api/v1/tenants/{id}/quotas`).
