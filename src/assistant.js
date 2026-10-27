import Anthropic from '@anthropic-ai/sdk';

const MAX_TOOL_ROUNDS = 8;

// Frozen system prompt: per-user details (role, folder, time) travel inside
// each user message instead, so the conversation stays append-only and the
// prompt cache keeps hitting.
const SYSTEM_PROMPT = `You are the WhatsApp assistant for a team's shared OneDrive document library. People chat with you to save, find, retrieve and organise their files. You are friendly, brief and practical.

How you work:
- Every user turn starts with a [context] line (sender, role, current folder, upload folder, local time) and may include [event] lines about things that already happened outside the chat, such as files the user just uploaded. Use them, but never repeat the brackets back.
- Use the tools for anything involving files or folders; never invent file names, paths, sizes or links. If a tool returns an error, explain it plainly and suggest what to do next.
- Paths are relative to the library's main folder. "/" is the main folder.
- When you show items from list_folder, search_files or recent_uploads, number them with the exact "n" value each item has, because the user can reply with just that number.
- To give someone a file, call send_files. The file itself arrives in WhatsApp, so do not paste download links unless they asked for a link (get_share_link).
- Files are saved automatically when the user sends them. You cannot receive or read file contents yourself.
- If a request is ambiguous (several matching files), show the options and ask which one.

Permissions (also enforced by the system, so don't try to work around them):
- role "user": may browse, search, receive files, get links, choose their upload folder, and move files they uploaded in the last 30 minutes.
- role "admin": additionally may create folders, rename, move anything and delete.
- If a user (not admin) asks to rename, delete or create folders, tell them only an admin can do that.
- Deleting always needs the person to reply "yes" afterwards. After calling request_delete, ask them to reply yes to confirm. Never claim something was deleted until a later [event] says so.

Style for WhatsApp:
- Reply in the same language the user writes in (English, Hindi, Hinglish, etc.).
- Keep replies short: a few lines, simple lists. Use *bold* for names. No markdown headings, tables or code blocks.
- Use at most one or two emojis per message.
- If someone just says hi or seems lost, briefly say what you can do and mention they can type "menu".`;

const ref = { type: 'string', description: 'A path like "Invoices/march.pdf", a file or folder name, or the number "n" shown in the last list' };

export const TOOLS = [
  {
    name: 'list_folder',
    description: "List the files and folders inside a folder.",
    input_schema: {
      type: 'object',
      properties: { path: { type: 'string', description: 'Folder path; "/" for the main folder, "" for the user\'s current folder' } },
      required: ['path'],
      additionalProperties: false,
    },
  },
  {
    name: 'search_files',
    description: 'Search all files and folders by (part of) name. Use short keywords, e.g. "invoice" or "march".',
    input_schema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'], additionalProperties: false },
  },
  {
    name: 'recent_uploads',
    description: 'Files this user uploaded recently, newest first.',
    input_schema: { type: 'object', properties: {}, required: [], additionalProperties: false },
  },
  {
    name: 'send_files',
    description: 'Send one or more files to the user in WhatsApp (max 10).',
    input_schema: { type: 'object', properties: { items: { type: 'array', items: ref } }, required: ['items'], additionalProperties: false },
  },
  {
    name: 'get_share_link',
    description: 'Create a OneDrive view link for a file or folder.',
    input_schema: { type: 'object', properties: { item: ref }, required: ['item'], additionalProperties: false },
  },
  {
    name: 'set_upload_folder',
    description: "Change the user's current folder; files they send afterwards are saved there. The folder must exist.",
    input_schema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'], additionalProperties: false },
  },
  {
    name: 'create_folder',
    description: 'Admin only. Create a folder (and any missing parent folders).',
    input_schema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'], additionalProperties: false },
  },
  {
    name: 'rename_item',
    description: 'Admin only. Rename a file or folder. The file extension is kept automatically if omitted.',
    input_schema: { type: 'object', properties: { item: ref, new_name: { type: 'string' } }, required: ['item', 'new_name'], additionalProperties: false },
  },
  {
    name: 'move_items',
    description: 'Move files or folders into a destination folder (created if missing). Admins can move anything; users only their own uploads from the last 30 minutes.',
    input_schema: {
      type: 'object',
      properties: { items: { type: 'array', items: ref }, destination: { type: 'string' } },
      required: ['items', 'destination'],
      additionalProperties: false,
    },
  },
  {
    name: 'request_delete',
    description: 'Admin only. Ask to delete a file or folder. Nothing is deleted until the person replies "yes".',
    input_schema: { type: 'object', properties: { item: ref }, required: ['item'], additionalProperties: false },
  },
].map((t) => ({ ...t, strict: true }));

/**
 * Claude-powered conversation layer. `runTool(name, input, ctx)` executes
 * a tool against OneDrive and returns a JSON-serialisable result.
 */
export class Assistant {
  constructor({ apiKey, model, effort, sessionMinutes, maxMessages, fallbacks }, { log = console, client } = {}) {
    this.client = client || new Anthropic({ apiKey });
    this.model = model;
    this.effort = effort;
    this.sessionMs = sessionMinutes * 60_000;
    this.maxMessages = maxMessages;
    this.fallbacks = fallbacks;
    this.log = log;
    this.sessions = new Map(); // user number -> { messages, updated, busy }
  }

  session(number) {
    let s = this.sessions.get(number);
    // Start a fresh conversation after a quiet period or when it gets long.
    // Replacing the whole history (rather than trimming it) keeps it valid.
    if (!s || Date.now() - s.updated > this.sessionMs || s.messages.length > this.maxMessages) {
      s = { messages: [], updated: Date.now(), busy: Promise.resolve() };
      this.sessions.set(number, s);
    }
    return s;
  }

  reset(number) {
    this.sessions.delete(number);
  }

  /** Answer one user message. Messages from the same user are handled in order. */
  reply(number, userText, runTool) {
    const s = this.session(number);
    const run = () => this.#reply(number, s, userText, runTool);
    const p = s.busy.then(run, run);
    s.busy = p.catch(() => {});
    return p;
  }

  async #reply(number, s, userText, runTool) {
    s.messages.push({ role: 'user', content: [{ type: 'text', text: userText }] });

    for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
      let response;
      try {
        response = await this.#create(s.messages);
      } catch (err) {
        if (err instanceof Anthropic.BadRequestError) {
          // Most likely a broken history; start over next time.
          this.reset(number);
        }
        throw err;
      }
      s.updated = Date.now();
      // Append the full content (thinking blocks included) unchanged.
      s.messages.push({ role: 'assistant', content: response.content });

      if (response.stop_reason === 'refusal') {
        this.reset(number);
        return "Sorry, I can't help with that one. Type *menu* to see what I can do.";
      }
      if (response.stop_reason === 'pause_turn') continue;

      const toolUses = response.content.filter((b) => b.type === 'tool_use');
      if (response.stop_reason !== 'tool_use' || !toolUses.length) {
        return textOf(response) || 'Done 👍';
      }

      const results = [];
      for (const tu of toolUses) {
        let content;
        let isError = false;
        try {
          content = JSON.stringify(await runTool(tu.name, tu.input ?? {}));
        } catch (err) {
          isError = true;
          content = err.userMessage || err.message || 'Tool failed';
          if (!err.userMessage) this.log.error?.(`[assistant] tool ${tu.name} failed:`, err);
        }
        results.push({ type: 'tool_result', tool_use_id: tu.id, content, ...(isError && { is_error: true }) });
      }
      // All results of one assistant turn go back in a single user message.
      s.messages.push({ role: 'user', content: results });
    }
    return 'That took more steps than expected. Could you say it a bit more simply?';
  }

  #create(messages) {
    const params = {
      model: this.model,
      max_tokens: 16000,
      system: SYSTEM_PROMPT,
      tools: TOOLS,
      messages,
      output_config: { effort: this.effort },
      cache_control: { type: 'ephemeral' },
    };
    if (this.fallbacks) {
      // Server-side fallback: if the model declines, the API retries on a fallback model.
      return this.client.beta.messages.create({
        ...params,
        betas: ['server-side-fallback-2026-07-01'],
        fallbacks: 'default',
      });
    }
    return this.client.messages.create(params);
  }
}

function textOf(response) {
  return response.content
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('\n')
    .trim();
}
