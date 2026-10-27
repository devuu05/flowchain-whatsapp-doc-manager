import express from 'express';
import { loadConfig, validateConfig } from './config.js';
import { Store } from './store.js';
import { OneDrive } from './onedrive.js';
import { WaSender } from './wasender.js';
import { Assistant } from './assistant.js';
import { createBot } from './bot.js';
import { extractMessages, verifySignature } from './webhook.js';

export function createApp({ config, bot, drive, log = console }) {
  const app = express();
  app.use(express.json({ limit: '5mb' }));

  app.get('/', (_req, res) => {
    const webhook = config.publicUrl ? `${config.publicUrl}${config.webhookPath}` : `https://<this-server>${config.webhookPath}`;
    res.type('text').send(
      [
        'WhatsApp OneDrive bot is running.',
        '',
        `Webhook URL for WasenderAPI: ${webhook}`,
        `OneDrive: ${drive?.isConnected() ? 'connected' : 'not connected - an admin will get a sign-in code on WhatsApp (or send /connect)'}`,
      ].join('\n'),
    );
  });
  app.get('/health', (_req, res) => res.json({ ok: true, onedrive: drive ? drive.isConnected() : undefined }));

  app.post(config.webhookPath, (req, res) => {
    if (!verifySignature(req.get('x-webhook-signature'), config.wasender.webhookSecret)) {
      log.warn?.('[webhook] rejected request with invalid signature');
      return res.status(401).json({ error: 'invalid signature' });
    }
    // Acknowledge immediately; WasenderAPI retries slow webhooks.
    res.status(200).json({ received: true });

    for (const msg of extractMessages(req.body)) {
      bot(msg).catch((err) => log.error?.('[webhook] unhandled error:', err));
    }
  });

  return app;
}

async function main() {
  const config = loadConfig();
  const errors = validateConfig(config);
  if (errors.length) {
    console.error('Configuration problems:\n - ' + errors.join('\n - ') + '\nSee .env.example');
    process.exit(1);
  }

  const store = new Store(config.dataFile);
  const drive = new OneDrive(config, store);
  const wa = new WaSender({ ...config.wasender, showTyping: config.bot.showTyping });
  const assistant = config.ai.apiKey ? new Assistant(config.ai) : null;
  const bot = createBot({ config, drive, wa, store, assistant });

  const app = createApp({ config, bot, drive });
  const server = app.listen(config.port, () =>
    console.log(`[server] listening on :${config.port}, webhook at ${config.webhookPath}`),
  );
  console.log(`[ai] ${assistant ? `chat enabled (${config.ai.model})` : 'disabled – set ANTHROPIC_API_KEY for natural-language chat'}`);

  // Check OneDrive without crashing: the bot keeps answering, and admins can /connect.
  if (drive.isConnected()) {
    drive
      .ensureFolder(config.storage.defaultFolder)
      .then(() => console.log(`[onedrive] connected, main folder "/${config.storage.baseFolder}"`))
      .catch((err) => console.error('[onedrive] not reachable:', err.message));
  } else {
    // First start: send the admins a Microsoft sign-in code right away, no command needed.
    console.warn('[onedrive] not connected – sending a sign-in code to the admins on WhatsApp');
    const toAdmins = (text) =>
      Promise.all(config.access.admins.map((a) => wa.sendText(`+${a}`, text))).catch((err) =>
        console.error('[wasender] could not message admins:', err.message),
      );
    const hello = config.publicUrl ? `🤖 Bot is online.\nWebhook URL: ${config.publicUrl}${config.webhookPath}\n\n` : '🤖 Bot is online.\n\n';
    let first = true;
    const reply = (text) => {
      const out = first ? hello + text : text; // only the first message carries the intro
      first = false;
      return toAdmins(out);
    };
    bot
      .connect({ reply })
      .catch((err) => console.error('[onedrive] could not start sign-in:', err.message));
  }

  const stop = () => {
    store.save(true);
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error('[fatal]', err.message);
    process.exit(1);
  });
}
