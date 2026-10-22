# Phase Checklist

Status legend: ☑ done (tests executed and passing) · ◐ done with documented gaps · ⚠ blocked on human/credential

| # | Phase | Status | Notes |
|---|---|---|---|
| 0 | Repo inspection, CLAUDE.md, plan, ADRs | ☑ | Remote GitHub repo not reachable from the build session (GitHub not linked); built in a local git repo, ready to push |
| 1 | FastAPI foundation, health, logging, errors, Docker, CI | ☑ | Docker images built and run; CI defined (not yet run on GitHub) |
| 2 | DB models, migrations, auth, sessions, tenant isolation | ☑ | Alembic upgrade/downgrade round trip verified |
| 3 | WhatsApp provider interface + Wasender adapter | ⚠ | Mocked tests pass; adapter derived from Wasender SDKs; **live verification needs credentials** |
| 4 | Durable Postgres job queue + worker | ☑ | ADR 0001; crash/lease/concurrency/DB-failure tests |
| 5 | DocumentStorage + Google Drive adapter | ⚠ | Mocked tests pass; **live OAuth/Drive test needs Google OAuth client** |
| 6 | Secure inbound file handling + pending descriptions | ☑ | |
| 7 | Extractors | ◐ | No local OCR (images/scanned PDFs need AI vision); .doc/.ppt text optional via LibreOffice |
| 8 | Classifier (Gemini + rules) | ⚠ | Rules classifier is the default; Gemini mocked only; **needs billed key + client consent** |
| 9 | End-to-end document pipeline | ☑ | Also verified in real containers with FAKE providers |
| 10 | Search, numbered selection, delivery | ☑ | Deterministic parser; AI query interpretation intentionally not used |
| 11 | Dashboard (Next.js) | ☑ | Verified in headless Chromium (desktop + mobile) against the real API |
| 12 | Production-readiness / security review | ☑ | See docs/security/checklist.md; open items listed there |
| 13 | Deployment plan | ⚠ | Plan + blueprint ready; **deploying requires your approval and accounts** |
| 14 | Pilot test suite & report | ◐ | Automated suite done; manual provider tests NOT RUN (credentials) |
| 15 | Pilot onboarding docs | ☑ | Runbook, incident response, rollback, onboarding checklist |

## Functional-completeness criteria (from the brief)
| # | Criterion | Status |
|---|---|---|
| 1 | WhatsApp message reaches the deployed backend | ⚠ not deployed |
| 2 | Backend responds without your PC | ⚠ not deployed (container verified locally) |
| 3 | Supported documents received and validated | ☑ (mocked provider) |
| 4 | Classified and stored in Google Drive | ◐ classified ☑; Drive mocked only |
| 5 | Metadata searchable in PostgreSQL | ☑ |
| 6 | Retrieval through WhatsApp | ☑ (mocked provider) |
| 7 | Invalid selections / expired state handled | ☑ |
| 8 | Duplicate events → no duplicate side effects | ☑ |
| 9 | Tenant isolation tested and enforced | ☑ |
| 10 | Dashboard protected by authn/authz | ☑ |
| 11 | Failures observable and recoverable | ☑ |
| 12 | Backup, export, deletion documented | ☑ (restore rehearsed locally) |
| 13 | Relevant automated tests pass | ☑ |
| 14 | Actual provider integrations tested | ⚠ NOT YET — credentials required |
| 15 | Hosting limitations documented honestly | ☑ |
