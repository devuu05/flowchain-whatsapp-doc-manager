# OneDrive — Microsoft sign-in setup

Works with **personal OneDrive** (outlook.com / hotmail / live accounts) and **OneDrive for
Business** (Microsoft 365 work or school accounts). The OneDrive account does **not** need to be
on the server or on your PC: an admin signs in once from any browser (phone or laptop) through
the dashboard, and the server keeps an encrypted refresh token so it keeps working 24/7.

Permissions requested (delegated): `Files.ReadWrite` (the signed-in user's own files),
`offline_access` (refresh token) and `User.Read` (show which account is connected). Unlike
Google's `drive.file`, Microsoft has no "only files this app created" scope for normal folders,
so the app *could* read other files in that OneDrive; it only ever touches its own
"Flow Chain Documents" folder. If that is a concern, connect a dedicated OneDrive account.

## 1. Register the app (once, in your browser)
1. Go to https://entra.microsoft.com → **App registrations → New registration**.
   - Name: `Flow Chain Documents`.
   - **Supported account types**: "Accounts in any organizational directory and personal
     Microsoft accounts" (matches `MICROSOFT_TENANT=common`). Personal OneDrive only →
     "Personal Microsoft accounts only" and set `MICROSOFT_TENANT=consumers`. One company only →
     "this organizational directory only" and set `MICROSOFT_TENANT=<your tenant id>`.
   - **Redirect URI**: platform **Web**,
     `https://<your-dashboard-domain>/api/v1/integrations/microsoft/callback`
     (local: `http://localhost:3000/api/v1/integrations/microsoft/callback`). Like the Google
     callback it goes through the dashboard, so the flow finishes in the admin's signed-in
     browser session.
2. Copy the **Application (client) ID** → `MICROSOFT_CLIENT_ID`.
3. **Certificates & secrets → New client secret** (24 months) → copy the *Value* →
   `MICROSOFT_CLIENT_SECRET`. Put a calendar reminder before it expires (see
   `docs/security/key-management.md`).
4. **API permissions → Add → Microsoft Graph → Delegated**: `Files.ReadWrite`,
   `offline_access`, `User.Read`. (Work accounts: an admin may need to *Grant admin consent*.)
5. Add `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` and (if not `common`) `MICROSOFT_TENANT`
   to the API's environment on the host. Never commit them or paste them into chat.

## 2. Connect the OneDrive
Dashboard → **Cloud storage → Connect OneDrive** → sign in with the Microsoft account that should
hold the documents → **Accept**. A "Flow Chain Documents" folder is created at the top of that
OneDrive, and documents go into `<Category>/<Year>/` below it, or into the folder the sender
chose with a `#Folder` caption.

Only one storage provider is active per workspace. To switch from Google Drive, disconnect it
first (existing Drive files stay where they are; delivery of those older documents needs Drive
reconnected).

## Disconnect / revoke
Dashboard → **Cloud storage → Disconnect** erases the token locally. To revoke on Microsoft's
side as well: personal accounts at https://account.live.com/consent/Manage, work accounts at
https://myapps.microsoft.com → *Manage your applications*. Files remain in OneDrive.

## Behaviour notes
- Deleting a document from WhatsApp or the dashboard moves the file to the OneDrive **recycle
  bin** (restorable for a limited time).
- Uploads up to 4 MB use a single request; larger files use a resumable upload session.
- Retries never create a second copy of the same bytes: before uploading, the adapter compares
  name, size and QuickXorHash with what is already in the folder.
- Refresh tokens rotate; each new one is stored (encrypted) immediately. If Microsoft returns
  `invalid_grant` (password change, consent revoked, 90 days unused), uploads fail permanently
  and the dashboard shows the error: reconnect OneDrive.
