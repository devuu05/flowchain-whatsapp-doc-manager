# WhatsApp ↔ OneDrive Chatbot (WasenderAPI + Claude)

A WhatsApp assistant for your team's documents. People chat with it in plain language or through menus. It saves what they send into OneDrive, finds files and sends them back. Only **admin numbers** can rename, delete or create folders.

It runs in the cloud 24/7, so your PC doesn't need to be on. The OneDrive account is linked from WhatsApp with `/connect`, and you sign in on whatever phone or computer has that account.

```
WhatsApp ──► WasenderAPI ──webhook──► this bot (cloud) ──► Microsoft Graph ──► OneDrive
                                         │
                                         └──► Claude (understands free-text requests)
```

## What chatting with it looks like

```
Asha:  hi
Bot:   👋 Hi Asha! What would you like to do?
       1️⃣ Browse files  2️⃣ Find a file  3️⃣ My recent uploads
       4️⃣ Choose upload folder  5️⃣ Help
       Reply with a number, or just tell me what you need.

Asha:  [sends 3 PDFs]
Bot:   ✅ Saved 3 files in /Inbox:
       • bill-oct.pdf • bill-nov.pdf • quote.pdf
       Should they go somewhere else? Reply with a number:
       1. 📁 Main folder  2. 📁 Invoices  3. 📁 Clients
       …or type #NewFolderName. Ignore this to keep them here.
Asha:  2
Bot:   📦 Moved 3 files to /Invoices

Asha:  mujhe october wala bill bhejo
Bot:   [sends bill-oct.pdf]
       Ye raha October ka bill 📄 /Invoices/bill-oct.pdf

Admin: rename quote.pdf to "Acme quote 2026"
Bot:   ✏️ Renamed quote.pdf → Acme quote 2026.pdf

Admin: delete the november bill
Bot:   ⚠️ Delete /Invoices/bill-nov.pdf? Reply yes to delete or no to cancel.
Admin: yes
Bot:   🗑️ Deleted bill-nov.pdf. It stays in the OneDrive recycle bin for a while.
```

## Features

| | Everyone allowed | Admins |
|---|---|---|
| Save documents, photos, videos and audio | ✅ just send them | ✅ |
| Choose the folder after uploading, or with a caption (`/save Invoices`, `#Invoices`) | ✅ | ✅ |
| Natural-language chat in any language (needs `ANTHROPIC_API_KEY`) | ✅ | ✅ |
| Numbered menus and step-by-step flows (works without AI too) | ✅ | ✅ |
| Browse, search, get files back, share links, recent uploads | ✅ | ✅ |
| Move files they uploaded in the last 30 minutes | ✅ | ✅ anything |
| Create folders, rename, delete (delete always asks "yes?") | ⛔ | ✅ |
| Link OneDrive (`/connect`), connection and storage status (`/status`) | ⛔ | ✅ |

Safety:
- The bot only works inside one OneDrive folder (`ONEDRIVE_BASE_FOLDER`).
- Existing files are never overwritten. A repeated name is saved as `name 1.pdf`.
- Deleted items go to the OneDrive recycle bin.
- Admin rules are enforced in code, so the AI cannot bypass them.
- A delete only happens after the admin replies "yes" (or "haan", "ok"…), and the AI can't confirm it for them.
- Webhook signatures are checked and duplicate messages ignored. Every upload, rename, move and delete is written to the audit log.

## Setup – 4 steps, about 15 minutes, all in the browser

You need: a WasenderAPI account with your WhatsApp connected, a Microsoft account with OneDrive,
and a Render account (about $7/month for an always-on server).

### Step 1 – Microsoft (2 minutes)
1. Open <https://entra.microsoft.com> → **App registrations** → **New registration**.
2. Name: anything (e.g. *WhatsApp Bot*). Account types: **Accounts in any organizational directory
   and personal Microsoft accounts**. Leave Redirect URI empty → **Register**.
3. Copy the **Application (client) ID**.
4. Left menu **Authentication** → **Allow public client flows** → **Yes** → **Save**.

That's all – no secrets, no permissions to add.

### Step 2 – Deploy (5 minutes)
1. On <https://dashboard.render.com>: **New** → **Blueprint** → connect GitHub → pick
   `devuu05/flowchain-whatsapp-doc-manager`, branch **`simple-bot`**.
2. Fill in the boxes it shows:

| Box | What to paste |
|---|---|
| `WASENDER_API_KEY` | WasenderAPI → your session → API key |
| `WASENDER_WEBHOOK_SECRET` | same page → webhook secret |
| `ADMIN_NUMBERS` | your WhatsApp number with country code, digits only, e.g. `919876543210` |
| `MS_CLIENT_ID` | the ID from step 1 |
| `ANTHROPIC_API_KEY` | optional – lets people chat in plain language; leave empty for menus only |

3. Click **Apply** and wait for "Live".

### Step 3 – WasenderAPI webhook (1 minute)
Open your Render URL (e.g. `https://whatsapp-onedrive-bot.onrender.com`). The page shows your
**Webhook URL**. In WasenderAPI → your session → **Webhook**: paste it, tick **messages.upsert**, save.

### Step 4 – Connect OneDrive (2 minutes)
When the bot starts, it **messages your admin number on WhatsApp** with a link and a code.
Open the link on your phone, enter the code, sign in with the OneDrive account, tap **Accept**.
The bot replies "✅ OneDrive connected". (Missed it? Send `/connect`.)

Done – send **hi** to the bot. To let other people use it, add their numbers in Render →
Environment → `ALLOWED_NUMBERS` (comma separated). Messages from other numbers are ignored.

## Commands (optional shortcuts)

Users: `menu`, `/help`, `/ls [folder]`, `/cd <folder>`, `/get <name|number>` (`/get 1,3`), `/search <text>`, `/recent`, `/link <name|number>`, `/move <mine> -> <folder>`, `/pwd`, `/whoami`, `/reset`.

Admins: `/mkdir <folder>`, `/rename <item> -> <new name>`, `/move <item> -> <folder>`, `/delete <item>`, `/connect`, `/status`.

Upload captions: `/save Invoices/2026`, `#Invoices`, `/save Invoices as March bill`.

## Advanced

- **OneDrive for Business without a user sign-in:** set `MS_AUTH_MODE=app`, `MS_TENANT=<tenant id>`, `MS_CLIENT_SECRET` and `MS_DRIVE_USER=files@company.com`. Grant the *Application* permission `Files.ReadWrite.All` with admin consent. `/connect` is then not needed.
- **Groups:** with `ALLOW_GROUPS=true` the bot accepts files and slash commands in groups. Each sender's own number decides their role.
- **AI model and cost:** set with `CLAUDE_MODEL` (default `claude-opus-5-5`) and `CLAUDE_EFFORT` (default `low`, which is quick and inexpensive for chat). Each chat resets after `AI_SESSION_MINUTES` of quiet time.
- **Running locally:** `npm install && cp .env.example .env && npm start`. Expose the port with `ngrok http 3000`.
- **Tests:** `npm test` runs 27 tests and needs no network.

## Project layout

```
src/index.js       HTTP server, webhook endpoint, startup checks
src/bot.js         Chat logic: menus, guided flows, commands, permissions, uploads, AI tools
src/assistant.js   Claude conversation loop and tool definitions
src/onedrive.js    Microsoft Graph client (sign-in via /connect, upload, list, search, rename, move, delete)
src/wasender.js    WasenderAPI client (send queue, typing indicator, media download/decryption)
src/webhook.js     Webhook signature check and payload parsing
src/paths.js       Safe paths inside the main folder
src/store.js       Saved state (OneDrive sign-in, folders, pending confirmations)
render.yaml, railway.json, Dockerfile   Cloud deployment
```
