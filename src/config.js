import 'dotenv/config';

const numbers = (v) =>
  (v || '')
    .split(',')
    .map((s) => s.replace(/\D/g, ''))
    .filter(Boolean);

const bool = (v, def) => (v === undefined || v === '' ? def : /^(1|true|yes|on)$/i.test(v));
const int = (v, def) => (v === undefined || v === '' || Number.isNaN(Number(v)) ? def : Number(v));
const trimSlashes = (s) => (s || '').replace(/^[\s/\\]+|[\s/\\]+$/g, '');

export function loadConfig(env = process.env) {
  return {
    port: int(env.PORT, 3000),
    // Render sets RENDER_EXTERNAL_URL automatically; elsewhere set PUBLIC_URL.
    publicUrl: (env.PUBLIC_URL || env.RENDER_EXTERNAL_URL || '').replace(/\/$/, ''),
    webhookPath: env.WEBHOOK_PATH || '/webhook',

    wasender: {
      apiKey: env.WASENDER_API_KEY || '',
      webhookSecret: env.WASENDER_WEBHOOK_SECRET || '',
      baseUrl: (env.WASENDER_BASE_URL || 'https://www.wasenderapi.com/api').replace(/\/$/, ''),
      sendIntervalMs: int(env.WASENDER_SEND_INTERVAL_MS, 1500),
    },

    access: {
      admins: numbers(env.ADMIN_NUMBERS),
      allowed: numbers(env.ALLOWED_NUMBERS),
      // Off by default: otherwise anyone who finds the number could download files.
      allowEveryone: bool(env.ALLOW_EVERYONE, false),
      replyToUnauthorized: bool(env.REPLY_TO_UNAUTHORIZED, false),
      allowGroups: bool(env.ALLOW_GROUPS, false),
    },

    graph: {
      authMode: (env.MS_AUTH_MODE || 'delegated').toLowerCase(),
      clientId: env.MS_CLIENT_ID || '',
      clientSecret: env.MS_CLIENT_SECRET || '',
      tenant: env.MS_TENANT || 'common', // personal and work accounts
      refreshToken: env.MS_REFRESH_TOKEN || '',
      driveUser: env.MS_DRIVE_USER || '',
      driveId: env.MS_DRIVE_ID || '',
    },

    storage: {
      baseFolder: trimSlashes(env.ONEDRIVE_BASE_FOLDER ?? 'WhatsApp Files'),
      defaultFolder: trimSlashes(env.DEFAULT_UPLOAD_FOLDER ?? 'Inbox'),
      usersCanCreateFolders: bool(env.USERS_CAN_CREATE_FOLDERS, true),
      maxFileBytes: int(env.MAX_FILE_MB, 100) * 1024 * 1024,
      confirmTimeoutMs: int(env.CONFIRM_TIMEOUT_SECONDS, 120) * 1000,
    },

    ai: {
      apiKey: env.ANTHROPIC_API_KEY || '',
      model: env.CLAUDE_MODEL || 'claude-opus-5-5',
      effort: env.CLAUDE_EFFORT || 'low',
      sessionMinutes: int(env.AI_SESSION_MINUTES, 30),
      maxMessages: int(env.AI_MAX_MESSAGES, 80),
      fallbacks: bool(env.CLAUDE_FALLBACKS, true),
    },

    bot: {
      timeZone: env.BOT_TIMEZONE || 'UTC',
      uploadSummaryDelayMs: int(env.UPLOAD_SUMMARY_DELAY_MS, 4000),
      showTyping: bool(env.SHOW_TYPING, true),
    },

    dataFile:
      env.DATA_FILE ||
      (env.DATA_DIR ? `${env.DATA_DIR.replace(/\/$/, '')}/state.json` : new URL('../data/state.json', import.meta.url).pathname),
  };
}

export function validateConfig(cfg) {
  const errors = [];
  if (!cfg.wasender.apiKey) errors.push('WASENDER_API_KEY is required');
  if (!cfg.wasender.webhookSecret) errors.push('WASENDER_WEBHOOK_SECRET is required');
  if (!cfg.access.admins.length) errors.push('ADMIN_NUMBERS needs at least one number');
  if (!cfg.graph.clientId) errors.push('MS_CLIENT_ID is required');
  if (cfg.graph.authMode === 'app') {
    if (!cfg.graph.clientSecret) errors.push('MS_CLIENT_SECRET is required in app mode');
    if (!cfg.graph.driveUser && !cfg.graph.driveId) errors.push('MS_DRIVE_USER or MS_DRIVE_ID is required in app mode');
    if (['consumers', 'common', 'organizations'].includes(cfg.graph.tenant)) {
      errors.push('MS_TENANT must be your tenant id in app mode');
    }
  } else if (cfg.graph.authMode !== 'delegated') {
    errors.push('MS_AUTH_MODE must be "delegated" or "app"');
  }
  return errors;
}
