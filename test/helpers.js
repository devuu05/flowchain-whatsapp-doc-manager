import { joinPath, parentOf, splitPath, UserError } from '../src/paths.js';
import { loadConfig } from '../src/config.js';

export const ADMIN = '911111111111';
export const USER = '922222222222';
export const STRANGER = '933333333333';

export function testConfig(extra = {}) {
  return loadConfig({
    WASENDER_API_KEY: 'k',
    WASENDER_WEBHOOK_SECRET: 'secret',
    ADMIN_NUMBERS: ADMIN,
    ALLOWED_NUMBERS: `${USER}`,
    MS_CLIENT_ID: 'c',
    ONEDRIVE_BASE_FOLDER: 'WhatsApp Files',
    DEFAULT_UPLOAD_FOLDER: 'Inbox',
    UPLOAD_SUMMARY_DELAY_MS: '5',
    ...extra,
  });
}

/** In-memory stand-in for OneDrive with the same interface. */
export class FakeDrive {
  constructor() {
    this.items = new Map(); // id -> {id,name,isFolder,size,parent,data}
    this.seq = 0;
    this.items.set('root', { id: 'root', name: '', isFolder: true, parent: null });
  }
  pathOf(it) {
    const parts = [];
    for (let c = it; c && c.id !== 'root'; c = this.items.get(c.parent)) parts.unshift(c.name);
    return parts.join('/');
  }
  view(it) {
    const kids = [...this.items.values()].filter((x) => x.parent === it.id);
    return {
      id: it.id, name: it.name, isFolder: it.isFolder, size: it.size || 0, childCount: kids.length,
      path: this.pathOf(it), downloadUrl: it.isFolder ? undefined : `https://dl.example/${it.id}`,
    };
  }
  find(rel) {
    let cur = this.items.get('root');
    for (const seg of splitPath(rel)) {
      cur = [...this.items.values()].find((x) => x.parent === cur.id && x.name.toLowerCase() === seg.toLowerCase());
      if (!cur) return null;
    }
    return cur;
  }
  isConnected() { return this.connected ?? true; }
  async account() { return { name: 'Test Owner', email: 'owner@example.com', used: 1024, total: 5 * 1024 ** 3 }; }
  async getItem(rel) { const it = this.find(rel); return it ? this.view(it) : null; }
  async getById(id) {
    const it = this.items.get(id);
    if (!it) throw Object.assign(new Error('nf'), { status: 404 });
    return this.view(it);
  }
  async list(rel) {
    const f = this.find(rel);
    return [...this.items.values()].filter((x) => x.parent === f.id).map((x) => this.view(x))
      .sort((a, b) => b.isFolder - a.isFolder || a.name.localeCompare(b.name));
  }
  async ensureFolder(rel) {
    let cur = this.items.get('root');
    for (const seg of splitPath(rel)) {
      let next = [...this.items.values()].find((x) => x.parent === cur.id && x.name.toLowerCase() === seg.toLowerCase());
      if (!next) { next = { id: `i${++this.seq}`, name: seg, isFolder: true, parent: cur.id }; this.items.set(next.id, next); }
      if (!next.isFolder) throw new UserError('file in the way');
      cur = next;
    }
    return this.view(cur);
  }
  async upload(rel, name, buffer) {
    const folder = await this.ensureFolder(rel);
    let final = name;
    for (let n = 1; this.find(joinPath(rel, final)); n++) final = name.replace(/(\.[^.]+)?$/, ` ${n}$1`);
    const it = { id: `i${++this.seq}`, name: final, isFolder: false, size: buffer.length, parent: folder.id, data: buffer };
    this.items.set(it.id, it);
    return this.view(it);
  }
  async rename(id, name) { this.items.get(id).name = name; return this.view(this.items.get(id)); }
  async move(id, dest) { const f = await this.ensureFolder(dest); this.items.get(id).parent = f.id; return this.view(this.items.get(id)); }
  async remove(id) {
    const kill = (i) => { for (const x of [...this.items.values()]) if (x.parent === i) kill(x.id); this.items.delete(i); };
    kill(id);
  }
  async search(q) {
    return [...this.items.values()].filter((x) => x.id !== 'root' && x.name.toLowerCase().includes(q.toLowerCase())).map((x) => this.view(x));
  }
  async downloadUrl(id) { return `https://dl.example/${id}`; }
  async shareLink(id) { return `https://1drv.ms/${id}`; }
}

export class FakeWa {
  constructor() { this.sent = []; }
  async sendText(to, text) { this.sent.push({ to, text }); }
  async sendDocument(to, url, fileName, caption) { this.sent.push({ to, url, fileName, caption }); }
  async downloadMedia() { return Buffer.from('file-bytes'); }
  get last() { return this.sent.at(-1); }
  clear() { this.sent = []; }
}

let n = 0;
export const textMsg = (from, text) => ({ id: `m${++n}`, from, replyTo: `+${from}`, chat: `${from}@s.whatsapp.net`, isGroup: false, pushName: 'Asha', text, media: null, raw: {} });
export const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));
export const fileMsg = (from, fileName, caption = '') => ({
  ...textMsg(from, caption),
  media: { type: 'document', mimetype: 'application/pdf', fileName, size: 10 },
});

export { parentOf };
