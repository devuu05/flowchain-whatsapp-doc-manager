# Google Drive — OAuth setup

Scope requested: `https://www.googleapis.com/auth/drive.file` (non-sensitive). The app can only
see files/folders it created. Files are never shared or made public by the app.

## Google Cloud Console (you)
1. https://console.cloud.google.com → create project "Flow Chain Docs".
2. *APIs & Services → Library*: enable **Google Drive API**.
3. *APIs & Services → OAuth consent screen*: User type **External**; app name, support email;
   add scope `.../auth/drive.file`. Add the client's Google account as a test user initially.
4. **Publish** the consent screen ("In production") before the pilot: apps left in *Testing*
   get refresh tokens that expire after 7 days, which would silently stop uploads.
   `drive.file` is non-sensitive, so publishing does not require Google's verification review
   (the user sees an "unverified app" notice until branding verification, if any, is done).
5. *Credentials → Create credentials → OAuth client ID → Web application*.
   Authorized redirect URI: `https://<your-api-domain>/api/v1/integrations/google/callback`
   (local: `http://localhost:8000/api/v1/integrations/google/callback`).
6. Put the client ID/secret into the hosting provider's environment variables
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (never in git, never in chat).

## Connect the client's Drive
Dashboard → *Google Drive* → *Connect Google Drive* → sign in with the **client's** Google account
→ allow. The app creates a "Flow Chain Documents" folder and stores the encrypted refresh token.

## Disconnect
Dashboard → *Google Drive* → *Disconnect*: revokes the token at Google and erases it locally.
The client can also revoke at https://myaccount.google.com/permissions. Files remain in Drive.
