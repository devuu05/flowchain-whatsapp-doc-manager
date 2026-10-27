import { UserError, resolvePath, sanitizeName, joinPath, parentOf, baseName, extOf, display } from './paths.js';
import { NotConnectedError } from './onedrive.js';

const LIST_LIMIT = 30;
const MAX_MULTI_GET = 10;
const FLOW_TTL_MS = 15 * 60_000;
const OWN_UPLOAD_MOVE_MS = 30 * 60_000;
const RECENT_KEEP = 20;

const YES = /^(yes|y|yeah|yep|yup|confirm|ok|okay|sure|do it|go ahead|haan|haa|ha|han|ji|ji haan|👍|✅)[.!\s]*$/i;
const NO = /^(no|n|nope|nah|cancel|stop|nahi|nahin|na|mat karo|❌)[.!\s]*$/i;
const GREETING = /^(hi+|hello+|hey+|hii+|helo|hola|namaste|namaskar|good (morning|afternoon|evening)|start)[.!\s]*$/i;
const MENU_WORDS = /^(menu|0|options|main menu|back)[.!\s]*$/i;

export const fmtSize = (n) => {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
};

const icon = (e) => (e.isFolder ? '📁' : '📄');
const line = (e, i) => `${i + 1}. ${icon(e)} ${e.name}${e.isFolder ? '/' : `  _(${fmtSize(e.size)})_`}`;
const lineWithFolder = (e, i) => `${line(e, i)}\n     ${display(parentOf(e.path))}`;

const USER_HELP = `*📂 How to use me*

*Save files* – just send any document, photo, video or audio.
• Caption \`/save Invoices/2026\` (or \`#Invoices\`) → saved in that folder
• Caption \`/save Invoices as March bill\` → saved with a new name

*Chat with me* – type *menu*, or just ask, e.g. "send me the March invoice".

*Shortcuts*
• \`/ls\` [folder] – list a folder
• \`/cd\` <folder> – choose the folder new uploads go to
• \`/get\` <name | number> – receive a file (\`/get 1,3\` for several)
• \`/search\` <text> – find files
• \`/recent\` – your latest uploads
• \`/link\` <name | number> – OneDrive link
• \`/pwd\`, \`/whoami\`, \`/reset\``;

const ADMIN_HELP = `

*Admin*
• \`/mkdir\` <folder>
• \`/rename\` <name | number> -> <new name>
• \`/move\` <name | number> -> <folder>
• \`/delete\` <name | number> (then reply *yes*)
• \`/connect\` – link the OneDrive account
• \`/status\` – OneDrive connection & storage`;

export function createBot({ config, drive, wa, store, assistant = null, log = console }) {
  const { access, storage } = config;
  const summaryDelay = config.bot?.uploadSummaryDelayMs ?? 4000;
  const timeZone = config.bot?.timeZone || 'UTC';
  const seen = new Map(); // message id -> time, for webhook retries/duplicates
  const batches = new Map(); // user -> { timer, files, explicit, ctx }
  let connecting = null;

  const roleOf = (num) => {
    if (!num) return null;
    if (access.admins.includes(num)) return 'admin';
    if (access.allowEveryone || access.allowed.includes(num)) return 'user';
    return null;
  };

  // u.cwd is null until the user picks a folder: they browse from the main
  // folder, and uploads go to DEFAULT_UPLOAD_FOLDER.
  const cwdOf = (u) => u.cwd ?? '';
  const uploadFolderOf = (u) => u.cwd ?? storage.defaultFolder;

  const remember = (u, entries) => {
    u.last = entries.slice(0, 200).map(({ id, name, isFolder, path }) => ({ id, name, isFolder, path }));
    store.save();
  };

  const setFlow = (u, kind, data = {}) => {
    u.flow = kind ? { kind, ...data, expires: Date.now() + FLOW_TTL_MS } : null;
    store.save();
  };

  const note = (u, text) => {
    if (!assistant) return;
    u.notes = [...(u.notes || []), text].slice(-10);
    store.save();
  };

  const dedupe = (id) => {
    if (seen.has(id)) return true;
    seen.set(id, Date.now());
    if (seen.size > 2000) {
      // Maps iterate in insertion order, so this drops the oldest ids.
      for (const k of [...seen.keys()].slice(0, 500)) seen.delete(k);
    }
    return false;
  };

  const requireAdmin = (ctx) => {
    if (ctx.role !== 'admin') throw new UserError('⛔ Only an admin can do that.');
  };

  // ---------- resolving user arguments to OneDrive items ----------

  async function resolveItem(u, arg) {
    if (arg && typeof arg === 'object' && arg.id) return drive.getById(arg.id);
    const a = String(arg ?? '').trim();
    if (!a) throw new UserError('Tell me which file or folder (name, or number from the last list).');

    if (/^\d+$/.test(a) && u.last?.[Number(a) - 1]) {
      const ref = u.last[Number(a) - 1];
      try {
        return await drive.getById(ref.id);
      } catch (e) {
        if (e.status === 404) throw new UserError(`"${ref.name}" no longer exists. Please list the folder again.`);
        throw e;
      }
    }

    const rel = resolvePath(cwdOf(u), a);
    if (!rel) return drive.getItem('');
    const exact = await drive.getItem(rel);
    if (exact) return exact;

    const name = baseName(rel).toLowerCase();
    const hits = await drive.search(baseName(rel));
    const same = hits.filter((h) => h.name.toLowerCase() === name);
    const pick = same.length === 1 ? same : hits.length === 1 ? hits : null;
    if (pick) return pick[0];
    if (!hits.length) throw new UserError(`I couldn't find anything called "${a}".`);
    remember(u, hits);
    throw new UserError(
      `I found several matches for "${a}" – reply with the number you mean:\n\n` +
        hits.slice(0, LIST_LIMIT).map(lineWithFolder).join('\n'),
    );
  }

  /** Split "a -> b" (also "a | b", "a → b"). */
  const splitArrow = (args, usage) => {
    const m = /^(.+?)\s*(?:->|→|=>|\|)\s*(.+)$/.exec(args);
    if (!m) throw new UserError(`Usage: ${usage}`);
    return [m[1].trim(), m[2].trim()];
  };

  // ---------- operations shared by commands, menus and the AI ----------

  const ops = {
    async list(ctx, target) {
      const t = String(target ?? '').trim();
      const folder = t ? await resolveItem(ctx.u, t) : await drive.getItem(cwdOf(ctx.u));
      if (!folder) return { folder: { path: cwdOf(ctx.u), name: baseName(cwdOf(ctx.u)) }, entries: [] };
      if (!folder.isFolder) throw new UserError(`"${folder.name}" is a file, not a folder.`);
      const entries = await drive.list(folder.path);
      remember(ctx.u, entries);
      return { folder, entries };
    },

    async search(ctx, query) {
      const q = String(query || '').trim();
      if (!q) throw new UserError('What should I search for?');
      const hits = await drive.search(q);
      remember(ctx.u, hits);
      return hits;
    },

    async recent(ctx) {
      const out = [];
      for (const r of ctx.u.recent || []) {
        try {
          out.push({ ...(await drive.getById(r.id)), uploadedAt: r.at });
        } catch (e) {
          if (e.status !== 404) throw e;
        }
      }
      remember(ctx.u, out);
      return out;
    },

    async send(ctx, refs) {
      const sent = [];
      for (const r of refs.slice(0, MAX_MULTI_GET)) {
        const it = await resolveItem(ctx.u, r);
        if (it.isFolder) throw new UserError(`"${it.name}" is a folder – open it to pick a file.`);
        if (it.size > 100 * 1024 * 1024) {
          const link = await drive.shareLink(it.id);
          await ctx.reply(`📄 *${it.name}* is ${fmtSize(it.size)} – too big for WhatsApp, here's a link:\n${link}`);
        } else {
          const url = it.downloadUrl || (await drive.downloadUrl(it.id));
          await wa.sendDocument(ctx.replyTo, url, it.name, `📄 ${display(it.path)}`);
        }
        sent.push(it);
      }
      return sent;
    },

    async link(ctx, ref) {
      const it = await resolveItem(ctx.u, ref);
      return { item: it, url: await drive.shareLink(it.id) };
    },

    async setFolder(ctx, target) {
      const t = String(target ?? '').trim() || '/';
      let rel;
      if (/^\d+$/.test(t) && ctx.u.last?.[Number(t) - 1]) {
        const it = await resolveItem(ctx.u, t);
        if (!it.isFolder) throw new UserError(`"${it.name}" is a file, not a folder.`);
        rel = it.path;
      } else {
        rel = resolvePath(cwdOf(ctx.u), t);
        if (rel) {
          const it = await drive.getItem(rel);
          if (!it) {
            throw new UserError(
              `The folder ${display(rel)} doesn't exist.${ctx.role === 'admin' ? ' Create it with /mkdir.' : ''}`,
            );
          }
          if (!it.isFolder) throw new UserError(`${display(rel)} is a file, not a folder.`);
          rel = it.path ?? rel;
        }
      }
      ctx.u.cwd = rel;
      store.save();
      return rel;
    },

    async mkdir(ctx, path) {
      requireAdmin(ctx);
      const rel = resolvePath(cwdOf(ctx.u), path);
      if (!rel) throw new UserError('That is the main folder.');
      await drive.ensureFolder(rel);
      return rel;
    },

    async rename(ctx, ref, to) {
      requireAdmin(ctx);
      const it = await resolveItem(ctx.u, ref);
      if (!it.path) throw new UserError('The main folder cannot be renamed.');
      let newName = sanitizeName(to);
      // Keep the original extension if it was left out.
      if (!it.isFolder && extOf(it.name) && !extOf(newName)) newName += extOf(it.name);
      if (newName === it.name) throw new UserError('That is already its name.');
      const updated = await drive.rename(it.id, newName);
      remember(ctx.u, []);
      if (it.isFolder) fixCwds(it.path, joinPath(parentOf(it.path), updated.name));
      log.info?.(`[audit] +${ctx.from} renamed ${display(it.path)} -> ${updated.name}`);
      return { from: it, to: updated };
    },

    async move(ctx, refs, destination) {
      const dest = resolvePath(cwdOf(ctx.u), destination);
      if (ctx.role !== 'admin' && !storage.usersCanCreateFolders && dest) {
        const exists = await drive.getItem(dest);
        if (!exists?.isFolder) throw new UserError(`The folder ${display(dest)} doesn't exist.`);
      }
      const moved = [];
      for (const r of refs) {
        const it = await resolveItem(ctx.u, r);
        if (!it.path) throw new UserError('The main folder cannot be moved.');
        if (ctx.role !== 'admin' && !isOwnRecent(ctx.u, it.id)) {
          throw new UserError(`⛔ Only an admin can move "${it.name}". You can only move files you uploaded in the last 30 minutes.`);
        }
        if (it.isFolder && (dest === it.path || dest.startsWith(`${it.path}/`))) {
          throw new UserError('A folder cannot be moved into itself.');
        }
        if (parentOf(it.path).toLowerCase() === dest.toLowerCase()) {
          moved.push(it);
          continue;
        }
        const updated = await drive.move(it.id, dest);
        if (it.isFolder) fixCwds(it.path, joinPath(dest, it.name));
        const rec = (ctx.u.recent || []).find((x) => x.id === it.id);
        if (rec) rec.path = joinPath(dest, it.name);
        moved.push({ ...it, path: updated.path ?? joinPath(dest, it.name) });
        log.info?.(`[audit] +${ctx.from} moved ${display(it.path)} -> ${display(dest)}`);
      }
      remember(ctx.u, []);
      return { moved, dest };
    },

    async requestDelete(ctx, ref) {
      requireAdmin(ctx);
      const it = await resolveItem(ctx.u, ref);
      if (!it.path) throw new UserError('The main folder cannot be deleted.');
      ctx.u.pending = {
        action: 'delete',
        id: it.id,
        name: it.name,
        path: it.path,
        isFolder: it.isFolder,
        expires: Date.now() + storage.confirmTimeoutMs,
      };
      setFlow(ctx.u, null);
      return it;
    },

    async confirm(ctx) {
      const p = ctx.u.pending;
      ctx.u.pending = null;
      store.save();
      if (!p) return ctx.reply('Nothing is waiting for confirmation.');
      requireAdmin(ctx);
      if (Date.now() > p.expires) return ctx.reply('⌛ That request expired. Please ask again.');
      await drive.remove(p.id);
      remember(ctx.u, []);
      if (p.isFolder) fixCwds(p.path, parentOf(p.path));
      log.info?.(`[audit] +${ctx.from} deleted ${display(p.path)}`);
      note(ctx.u, `[event] Deleted ${display(p.path)} after the user confirmed.`);
      return ctx.reply(`🗑️ Deleted *${p.name}*.\n_It stays in the OneDrive recycle bin for a while if you need it back._`);
    },

    cancel(ctx) {
      const had = !!ctx.u.pending;
      ctx.u.pending = null;
      store.save();
      if (had) note(ctx.u, '[event] The user cancelled the pending delete.');
      return ctx.reply(had ? '👌 Cancelled, nothing was deleted.' : 'Nothing to cancel.');
    },
  };

  const isOwnRecent = (u, id) => (u.recent || []).some((r) => r.id === id && Date.now() - r.at < OWN_UPLOAD_MOVE_MS);

  /** When a folder is renamed/moved/deleted, keep users' current folders valid. */
  function fixCwds(oldPath, newPath) {
    const lower = oldPath.toLowerCase();
    for (const u of Object.values(store.data.users)) {
      if (u.cwd === null || u.cwd === undefined) continue;
      const c = u.cwd.toLowerCase();
      if (c === lower) u.cwd = newPath;
      else if (c.startsWith(`${lower}/`)) u.cwd = joinPath(newPath, u.cwd.slice(oldPath.length + 1));
    }
    store.save();
  }

  // ---------- reply formatting ----------

  function listText({ folder, entries }, footer = '_Reply with a number to open it._') {
    if (!entries.length) return `📂 *${display(folder.path)}* is empty.`;
    const more = entries.length > LIST_LIMIT ? `\n…and ${entries.length - LIST_LIMIT} more` : '';
    return (
      `📂 *${display(folder.path)}* (${entries.length})\n\n` +
      entries.slice(0, LIST_LIMIT).map(line).join('\n') +
      more +
      `\n\n${footer}`
    );
  }

  const deleteQuestion = (it) => {
    const what = it.isFolder
      ? `the folder 📁 *${it.name}* and everything in it (${it.childCount} item(s))`
      : `📄 *${it.name}* (${fmtSize(it.size)})`;
    return `⚠️ Delete ${what}?\n📂 ${display(parentOf(it.path))}\n\nReply *yes* to delete or *no* to cancel.`;
  };

  async function folderOptions(exclude) {
    const top = (await drive.list('')).filter((e) => e.isFolder && e.path.toLowerCase() !== (exclude || '').toLowerCase());
    return [{ id: 'root', name: 'Main folder', isFolder: true, path: '' }, ...top].slice(0, 12);
  }

  // ---------- menus & guided flows ----------

  function menuText(ctx) {
    const hi = ctx.name ? `👋 Hi ${ctx.name}!` : '👋 Hi!';
    let t =
      `${hi} What would you like to do?\n\n` +
      `1️⃣ Browse files\n2️⃣ Find a file\n3️⃣ My recent uploads\n4️⃣ Choose upload folder\n5️⃣ Help`;
    if (ctx.role === 'admin') t += `\n6️⃣ New folder\n7️⃣ Rename\n8️⃣ Delete\n9️⃣ OneDrive status`;
    t += `\n\nReply with a number${assistant ? ', or just tell me what you need' : ''}. Send a file anytime to save it.`;
    return t;
  }

  async function showMenu(ctx) {
    setFlow(ctx.u, 'menu');
    return ctx.reply(menuText(ctx));
  }

  async function browse(ctx, target, nextFlow) {
    const res = await ops.list(ctx, target);
    setFlow(ctx.u, nextFlow || 'pick');
    if (!nextFlow) return ctx.reply(listText(res));
    return res;
  }

  /** Bare number after a list: open folders, send files. */
  async function openNumber(ctx, n) {
    const it = await resolveItem(ctx.u, n);
    if (it.isFolder) return browse(ctx, `/${it.path}`);
    await ops.send(ctx, [String(n)]);
  }

  async function searchReply(ctx, query) {
    const hits = await ops.search(ctx, query);
    setFlow(ctx.u, 'pick');
    if (!hits.length) return ctx.reply(`🔍 Nothing matches "${query}". Try another word, or type *menu*.`);
    const more = hits.length > LIST_LIMIT ? `\n…and ${hits.length - LIST_LIMIT} more` : '';
    return ctx.reply(
      `🔍 Found ${hits.length} for "${query}":\n\n${hits.slice(0, LIST_LIMIT).map(lineWithFolder).join('\n')}${more}\n\n_Reply with a number to get it._`,
    );
  }

  const menuActions = {
    1: (ctx) => browse(ctx, ''),
    2: (ctx) => {
      setFlow(ctx.u, 'search');
      return ctx.reply('🔍 What is the file called? Part of the name is enough.');
    },
    3: async (ctx) => {
      const items = await ops.recent(ctx);
      setFlow(ctx.u, 'pick');
      if (!items.length) return ctx.reply("You haven't uploaded anything recently. Send me a file to save it.");
      return ctx.reply(`🕑 *Your recent uploads*\n\n${items.map(lineWithFolder).join('\n')}\n\n_Reply with a number to get it._`);
    },
    4: async (ctx) => {
      const opts = await folderOptions();
      remember(ctx.u, opts);
      setFlow(ctx.u, 'choose-folder');
      return ctx.reply(
        `📥 Files you send now go to *${display(uploadFolderOf(ctx.u))}*.\nPick a new upload folder:\n\n` +
          opts.map((o, i) => `${i + 1}. 📁 ${o.name}`).join('\n') +
          `\n\n_Or type a folder path, e.g. Clients/Acme._`,
      );
    },
    5: (ctx) => ctx.reply(USER_HELP + (ctx.role === 'admin' ? ADMIN_HELP : '')),
    6: (ctx) => {
      requireAdmin(ctx);
      setFlow(ctx.u, 'mkdir');
      return ctx.reply(`📁 Name of the new folder? It will be created in *${display(cwdOf(ctx.u))}*.\n_Tip: use / for sub-folders, e.g. Clients/Acme._`);
    },
    7: async (ctx) => {
      requireAdmin(ctx);
      const res = await browse(ctx, '', 'rename-pick');
      return ctx.reply(listText(res, '✏️ Which one should I rename? Reply with its number.'));
    },
    8: async (ctx) => {
      requireAdmin(ctx);
      const res = await browse(ctx, '', 'delete-pick');
      return ctx.reply(listText(res, '🗑️ Which one should I delete? Reply with its number.'));
    },
    9: (ctx) => commands.status.run(ctx),
  };

  /** Returns true when the text was consumed by the active flow. */
  async function handleFlow(ctx, text) {
    const f = ctx.u.flow;
    if (!f) return false;
    if (Date.now() > f.expires) {
      setFlow(ctx.u, null);
      return false;
    }
    const isNum = /^\d+$/.test(text);

    switch (f.kind) {
      case 'menu': {
        if (!isNum) return false;
        const action = menuActions[Number(text)];
        if (!action) return false;
        await action(ctx);
        return true;
      }
      case 'search':
        await searchReply(ctx, text);
        return true;
      case 'mkdir': {
        const rel = await ops.mkdir(ctx, text);
        setFlow(ctx.u, null);
        await ctx.reply(`📁 Folder *${display(rel)}* is ready. Use /cd ${display(rel)} to save files there.`);
        return true;
      }
      case 'choose-folder': {
        const rel = await ops.setFolder(ctx, text);
        setFlow(ctx.u, null);
        await ctx.reply(`📥 Done! Files you send will now be saved in *${display(rel)}*.`);
        return true;
      }
      case 'rename-pick': {
        if (!isNum) return false;
        const it = await resolveItem(ctx.u, text);
        setFlow(ctx.u, 'rename-name', { id: it.id, name: it.name });
        await ctx.reply(`✏️ New name for *${it.name}*?${!it.isFolder && extOf(it.name) ? `\n_(${extOf(it.name)} is added automatically)_` : ''}`);
        return true;
      }
      case 'rename-name': {
        const { from, to } = await ops.rename(ctx, { id: f.id }, text);
        setFlow(ctx.u, null);
        await ctx.reply(`✏️ Renamed *${from.name}* → *${to.name}*`);
        return true;
      }
      case 'delete-pick': {
        if (!isNum) return false;
        const it = await ops.requestDelete(ctx, text);
        await ctx.reply(deleteQuestion(it));
        return true;
      }
      case 'after-upload': {
        // Offer shown after an upload: pick a folder number or "#New folder".
        const m = /^#\s*(.+)$/.exec(text);
        if (!isNum && !m) return false;
        let dest;
        if (isNum) {
          const opt = f.options[Number(text) - 1];
          if (!opt) return false;
          dest = `/${opt.path}`;
        } else dest = m[1];
        const { moved, dest: rel } = await ops.move(ctx, f.files, dest);
        setFlow(ctx.u, null);
        note(ctx.u, `[event] Moved ${moved.map((x) => x.name).join(', ')} to ${display(rel)}.`);
        await ctx.reply(`📦 Moved ${moved.length === 1 ? `*${moved[0].name}*` : `${moved.length} files`} to *${display(rel)}*`);
        return true;
      }
      default:
        return false;
    }
  }

  // ---------- uploads ----------

  async function handleMedia(ctx, msg) {
    const { media } = msg;
    let folder = uploadFolderOf(ctx.u);
    let name = media.fileName;
    let explicit = false;

    const cap = /^\s*[/!]save\b\s*(.*)$/is.exec(msg.text || '') || /^\s*#(\S.*)$/s.exec(msg.text || '');
    if (cap) {
      let rest = cap[1].trim();
      const as = /^(.*?)\s*\bas\s+(.+)$/is.exec(rest);
      if (as) {
        rest = as[1].trim();
        name = as[2].trim();
        if (!extOf(name) && extOf(media.fileName)) name += extOf(media.fileName);
      }
      if (rest) {
        folder = resolvePath(cwdOf(ctx.u), rest);
        explicit = true;
      }
    }
    name = sanitizeName(name);

    if (ctx.role !== 'admin' && !storage.usersCanCreateFolders && folder) {
      const exists = await drive.getItem(folder);
      if (!exists?.isFolder) throw new UserError(`The folder ${display(folder)} doesn't exist. Ask an admin to create it.`);
    }
    if (media.size && media.size > storage.maxFileBytes) {
      throw new UserError(`That file is larger than the ${Math.round(storage.maxFileBytes / 1048576)} MB limit.`);
    }

    const buffer = await wa.downloadMedia(msg.raw, media, storage.maxFileBytes).catch((e) => {
      if (e.tooLarge) throw new UserError(e.message);
      throw e;
    });
    const saved = await drive.upload(folder, name, buffer, media.mimetype);
    log.info?.(`[audit] +${ctx.from} uploaded ${display(saved.path)} (${buffer.length} bytes)`);

    ctx.u.recent = [{ id: saved.id, name: saved.name, path: saved.path, at: Date.now() }, ...(ctx.u.recent || [])].slice(0, RECENT_KEEP);
    note(ctx.u, `[event] The user uploaded ${display(saved.path)} (${fmtSize(buffer.length)}).`);
    store.save();
    queueSummary(ctx, { ...saved, size: buffer.length, requested: name }, explicit);
  }

  /** Collect uploads that arrive together and confirm them in one message. */
  function queueSummary(ctx, file, explicit) {
    const b = batches.get(ctx.from) || { files: [], explicit: true };
    clearTimeout(b.timer);
    b.files.push(file);
    b.explicit &&= explicit;
    b.ctx = ctx;
    b.timer = setTimeout(() => {
      batches.delete(ctx.from);
      sendSummary(b).catch((e) => log.error?.('[bot] upload summary failed:', e));
    }, summaryDelay);
    batches.set(ctx.from, b);
  }

  async function sendSummary({ files, explicit, ctx }) {
    const folders = [...new Set(files.map((f) => parentOf(f.path)))];
    const where = folders.length === 1 ? `in *${display(folders[0])}*` : 'in several folders';
    const renamed = files.filter((f) => f.name !== f.requested);
    let text =
      files.length === 1
        ? `✅ Saved *${files[0].name}* (${fmtSize(files[0].size)}) ${where}`
        : `✅ Saved ${files.length} files ${where}:\n${files.map((f) => `• ${f.name}`).join('\n')}`;
    if (renamed.length) text += `\n_(a file with the same name already existed, so I added a number)_`;

    if (!explicit && folders.length === 1) {
      try {
        const opts = (await folderOptions(folders[0])).filter((o) => o.path.toLowerCase() !== folders[0].toLowerCase());
        if (opts.length || ctx.role === 'admin' || storage.usersCanCreateFolders) {
          setFlow(ctx.u, 'after-upload', { files: files.map((f) => `/${f.path}`), options: opts });
          text +=
            `\n\nShould ${files.length === 1 ? 'it' : 'they'} go somewhere else? Reply with a number:\n` +
            opts.map((o, i) => `${i + 1}. 📁 ${o.name}`).join('\n') +
            (ctx.role === 'admin' || storage.usersCanCreateFolders ? `\n…or type *#NewFolderName*.` : '') +
            `\n_Ignore this to keep ${files.length === 1 ? 'it' : 'them'} here._`;
        }
      } catch (e) {
        log.warn?.('[bot] could not build folder options:', e.message);
      }
    }
    await ctx.reply(text);
  }

  // ---------- slash commands ----------

  const commands = {
    help: { run: (ctx) => ctx.reply(USER_HELP + (ctx.role === 'admin' ? ADMIN_HELP : '')) },
    menu: { run: (ctx) => showMenu(ctx) },

    whoami: {
      run: (ctx) =>
        ctx.reply(
          `📱 +${ctx.from}\n👤 Role: *${ctx.role}*\n📂 Folder: ${display(cwdOf(ctx.u))}\n📥 Uploads go to: ${display(uploadFolderOf(ctx.u))}`,
        ),
    },

    pwd: {
      run: (ctx) =>
        ctx.reply(`📂 Current folder: *${display(cwdOf(ctx.u))}*\n📥 Files you send are saved to: *${display(uploadFolderOf(ctx.u))}*`),
    },

    ls: { run: (ctx) => browse(ctx, ctx.args) },

    cd: {
      async run(ctx) {
        const rel = await ops.setFolder(ctx, ctx.args);
        setFlow(ctx.u, null);
        return ctx.reply(`📂 Now in *${display(rel)}*\nFiles you send will be saved here.`);
      },
    },

    get: {
      async run(ctx) {
        const nums = ctx.args.split(/[\s,]+/).filter(Boolean);
        const multi = nums.length > 1 && nums.every((n) => /^\d+$/.test(n));
        if (!multi) {
          const it = await resolveItem(ctx.u, ctx.args);
          if (it.isFolder) return browse(ctx, `/${it.path}`);
        }
        await ops.send(ctx, multi ? nums : [ctx.args]);
      },
    },

    search: {
      async run(ctx) {
        if (!ctx.args) {
          setFlow(ctx.u, 'search');
          return ctx.reply('🔍 What is the file called?');
        }
        return searchReply(ctx, ctx.args);
      },
    },

    recent: { run: (ctx) => menuActions[3](ctx) },

    link: {
      async run(ctx) {
        const { item, url } = await ops.link(ctx, ctx.args);
        return ctx.reply(`🔗 *${item.name}*\n${url}`);
      },
    },

    save: {
      run: (ctx) =>
        ctx.reply('Attach a file and write `/save <folder>` as its *caption*.\nOr use `/cd <folder>` so every file goes there.'),
    },

    reset: {
      run(ctx) {
        assistant?.reset(ctx.from);
        ctx.u.notes = [];
        setFlow(ctx.u, null);
        return ctx.reply('🔄 Fresh start! Type *menu* or tell me what you need.');
      },
    },

    mkdir: {
      admin: true,
      async run(ctx) {
        if (!ctx.args) return menuActions[6](ctx);
        const rel = await ops.mkdir(ctx, ctx.args);
        return ctx.reply(`📁 Folder *${display(rel)}* is ready.`);
      },
    },

    rename: {
      admin: true,
      async run(ctx) {
        if (!ctx.args) return menuActions[7](ctx);
        const [from, to] = splitArrow(ctx.args, '/rename <name | number> -> <new name>');
        const r = await ops.rename(ctx, from, to);
        return ctx.reply(`✏️ Renamed\n*${r.from.name}* → *${r.to.name}*\n📂 ${display(parentOf(r.from.path))}`);
      },
    },

    move: {
      async run(ctx) {
        const [from, to] = splitArrow(ctx.args, '/move <name | number> -> <folder>');
        const { moved, dest } = await ops.move(ctx, [from], to);
        return ctx.reply(`📦 Moved *${moved[0].name}* → *${display(dest)}*`);
      },
    },

    delete: {
      admin: true,
      async run(ctx) {
        if (!ctx.args) return menuActions[8](ctx);
        const it = await ops.requestDelete(ctx, ctx.args);
        return ctx.reply(deleteQuestion(it));
      },
    },

    yes: { run: (ctx) => ops.confirm(ctx) },
    no: { run: (ctx) => ops.cancel(ctx) },

    status: {
      admin: true,
      async run(ctx) {
        if (!drive.isConnected()) return ctx.reply('🔌 OneDrive is *not connected*. Send /connect to link it.');
        const a = await drive.account();
        const quota = a.total ? `\n💾 ${fmtSize(a.used)} of ${fmtSize(a.total)} used` : '';
        return ctx.reply(
          `✅ OneDrive connected\n👤 ${a.name}${a.email ? ` (${a.email})` : ''}${quota}\n📂 Main folder: /${storage.baseFolder}\n🤖 AI chat: ${assistant ? 'on' : 'off'}`,
        );
      },
    },

    connect: {
      admin: true,
      async run(ctx) {
        if (connecting) return ctx.reply('A sign-in is already in progress – use the code I sent earlier.');
        const login = await drive.startDeviceLogin();
        connecting = login.done
          .then(async (acct) => {
            await drive.ensureFolder(storage.defaultFolder).catch(() => {});
            await ctx.reply(`✅ OneDrive connected to *${acct.name}*${acct.email ? ` (${acct.email})` : ''}.\nFiles will be stored in */${storage.baseFolder}*.`);
          })
          .catch((e) => ctx.reply(`❌ OneDrive sign-in failed: ${e.message}\nSend /connect to try again.`))
          .finally(() => (connecting = null));
        return ctx.reply(
          `🔗 *Connect OneDrive*\n\n1. On any phone or computer open:\n${login.url}\n2. Enter this code: *${login.code}*\n3. Sign in with the Microsoft account that owns the OneDrive and tap *Accept*.\n\nI'll message you here when it's done (code valid ${login.expiresInMin} min).`,
        );
      },
    },
  };

  const aliases = {
    start: 'menu', options: 'menu', commands: 'help',
    list: 'ls', dir: 'ls', files: 'ls', browse: 'ls',
    open: 'cd', folder: 'cd', where: 'pwd',
    download: 'get', send: 'get', fetch: 'get',
    find: 'search', share: 'link', recents: 'recent', uploads: 'recent',
    newfolder: 'mkdir', md: 'mkdir',
    ren: 'rename', mv: 'move',
    del: 'delete', rm: 'delete', remove: 'delete',
    confirm: 'yes', cancel: 'no',
    login: 'connect', onedrive: 'status',
  };

  // ---------- AI tools ----------

  const aiItem = (e, i) => ({
    n: i + 1,
    name: e.name,
    type: e.isFolder ? 'folder' : 'file',
    path: display(e.path),
    ...(e.isFolder ? { items: e.childCount } : { size: fmtSize(e.size) }),
    ...(e.modified && { modified: e.modified.slice(0, 10) }),
  });

  async function runTool(ctx, name, input) {
    switch (name) {
      case 'list_folder': {
        const { folder, entries } = await ops.list(ctx, input.path);
        setFlow(ctx.u, 'pick');
        return { folder: display(folder.path), total: entries.length, items: entries.slice(0, 50).map(aiItem) };
      }
      case 'search_files': {
        const hits = await ops.search(ctx, input.query);
        setFlow(ctx.u, 'pick');
        return { total: hits.length, items: hits.slice(0, 30).map(aiItem) };
      }
      case 'recent_uploads': {
        const items = await ops.recent(ctx);
        setFlow(ctx.u, 'pick');
        return { items: items.map((e, i) => ({ ...aiItem(e, i), uploaded: new Date(e.uploadedAt).toISOString() })) };
      }
      case 'send_files': {
        const sent = await ops.send(ctx, input.items || []);
        return { sent: sent.map((s) => display(s.path)) };
      }
      case 'get_share_link': {
        const { item, url } = await ops.link(ctx, input.item);
        return { name: item.name, url };
      }
      case 'set_upload_folder':
        return { upload_folder: display(await ops.setFolder(ctx, input.path)) };
      case 'create_folder':
        return { created: display(await ops.mkdir(ctx, input.path)) };
      case 'rename_item': {
        const r = await ops.rename(ctx, input.item, input.new_name);
        return { renamed: display(r.from.path), new_name: r.to.name };
      }
      case 'move_items': {
        const { moved, dest } = await ops.move(ctx, input.items || [], input.destination);
        return { moved: moved.map((m) => m.name), destination: display(dest) };
      }
      case 'request_delete': {
        const it = await ops.requestDelete(ctx, input.item);
        return {
          awaiting_confirmation: true,
          item: display(it.path),
          type: it.isFolder ? 'folder' : 'file',
          ...(it.isFolder && { contains_items: it.childCount }),
          instruction: 'Ask the user to reply "yes" to delete or "no" to cancel.',
        };
      }
      default:
        throw new UserError(`Unknown tool ${name}`);
    }
  }

  function contextLine(ctx) {
    const now = new Intl.DateTimeFormat('en-GB', { dateStyle: 'full', timeStyle: 'short', timeZone }).format(new Date());
    return (
      `[context] sender: +${ctx.from}${ctx.name ? ` (${ctx.name})` : ''}; role: ${ctx.role}; ` +
      `current folder: ${display(cwdOf(ctx.u))}; uploads go to: ${display(uploadFolderOf(ctx.u))}; time: ${now} (${timeZone})`
    );
  }

  async function askAssistant(ctx, text) {
    const notes = ctx.u.notes || [];
    ctx.u.notes = [];
    store.save();
    wa.typing?.(ctx.replyTo);
    const prompt = [contextLine(ctx), ...notes, '', text].join('\n');
    const answer = await assistant.reply(ctx.from, prompt, async (name, input) => {
      try {
        return await runTool(ctx, name, input);
      } catch (e) {
        if (e instanceof UserError) e.userMessage = e.message;
        if (e instanceof NotConnectedError) e.userMessage = 'OneDrive is not connected. An admin must send /connect.';
        throw e;
      }
    });
    await ctx.reply(answer);
  }

  // ---------- entry point ----------

  async function route(ctx, msg) {
    if (msg.media) return handleMedia(ctx, msg);
    const text = msg.text;

    // 1. Answer a pending confirmation.
    if (ctx.u.pending && (YES.test(text) || NO.test(text))) {
      return YES.test(text) ? ops.confirm(ctx) : ops.cancel(ctx);
    }

    // 2. Slash commands.
    const m = /^[/!]([a-z]+)\b\s*([\s\S]*)$/i.exec(text);
    if (m) {
      const name = aliases[m[1].toLowerCase()] || m[1].toLowerCase();
      const cmd = commands[name];
      if (!cmd) return ctx.reply(`I don't know /${m[1]}. Type *menu* or /help.`);
      if (cmd.admin && ctx.role !== 'admin') {
        log.info?.(`[auth] +${ctx.from} tried admin command /${name}`);
        return ctx.reply('⛔ Only an admin can do that.');
      }
      ctx.args = m[2].trim();
      return cmd.run(ctx);
    }
    if (ctx.isGroup) return; // in groups, only commands and files

    // 3. Menu.
    if (MENU_WORDS.test(text) || (!assistant && GREETING.test(text))) return showMenu(ctx);

    // 4. Guided flow in progress.
    if (await handleFlow(ctx, text)) return;

    // 5. A bare number refers to the last list shown.
    if (/^\d+$/.test(text) && ctx.u.last?.[Number(text) - 1]) return openNumber(ctx, text);

    // 6. Free text: talk to the AI, or fall back to the menu.
    setFlow(ctx.u, null);
    if (assistant) return askAssistant(ctx, text);
    return ctx.reply(`🤔 I didn't get that.\n\n${menuText(ctx)}`).then(() => setFlow(ctx.u, 'menu'));
  }

  async function handle(msg) {
    if (dedupe(msg.id)) return;
    if (msg.isGroup && !access.allowGroups) return;

    const role = roleOf(msg.from);
    const reply = (text) => wa.sendText(msg.replyTo, text);
    if (!role) {
      log.info?.(`[auth] ignored message from unauthorised ${msg.from || msg.chat}`);
      if (access.replyToUnauthorized && msg.from) await reply('⛔ Sorry, you are not authorised to use this bot.');
      return;
    }

    const u = store.user(msg.from);
    const ctx = { role, from: msg.from, name: msg.pushName || '', replyTo: msg.replyTo, isGroup: msg.isGroup, u, reply, args: '' };

    try {
      await route(ctx, msg);
    } catch (err) {
      if (err instanceof UserError) return reply(`⚠️ ${err.message}`).catch(() => {});
      if (err instanceof NotConnectedError) {
        return reply(
          role === 'admin'
            ? '🔌 OneDrive is not connected yet. Send /connect to link it.'
            : "📴 File storage isn't connected right now. Please ask an admin to reconnect it.",
        ).catch(() => {});
      }
      log.error?.(`[bot] error handling ${msg.id} from +${msg.from}:`, err);
      return reply('❌ Something went wrong on my side. Please try again in a moment, or type *menu*.').catch(() => {});
    }
  }

  /** Start the OneDrive sign-in and report through `reply` (used at startup). */
  const connect = (reply) => commands.connect.run({ reply });

  return Object.assign(handle, { runTool, ops, connect });
}
