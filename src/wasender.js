import crypto from 'node:crypto';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MAX_TEXT = 3500;

/** WasenderAPI client with a send queue that respects plan rate limits. */
export class WaSender {
  constructor({ apiKey, baseUrl, sendIntervalMs, showTyping = false }, { fetchImpl = fetch, log = console } = {}) {
    this.showTyping = showTyping;
    this.apiKey = apiKey;
    this.baseUrl = baseUrl;
    this.interval = sendIntervalMs;
    this.fetch = fetchImpl;
    this.log = log;
    this.queue = Promise.resolve();
    this.lastSend = 0;
  }

  async call(path, payload, attempt = 0) {
    const res = await this.fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 429 && attempt < 3) {
      const wait = Number(data.retry_after || res.headers.get('retry-after')) || 5;
      await sleep(Math.min(wait, 60) * 1000);
      return this.call(path, payload, attempt + 1);
    }
    if (!res.ok || data.success === false) {
      throw new Error(`WasenderAPI ${path} failed (${res.status}): ${data.message || JSON.stringify(data)}`);
    }
    return data;
  }

  /** Serialise outgoing messages and keep a minimum gap between them. */
  enqueue(payload) {
    const run = async () => {
      const wait = this.lastSend + this.interval - Date.now();
      if (wait > 0) await sleep(wait);
      try {
        return await this.call('/send-message', payload);
      } finally {
        this.lastSend = Date.now();
      }
    };
    const p = this.queue.then(run, run);
    this.queue = p.catch(() => {});
    return p;
  }

  async sendText(to, text) {
    for (const part of chunkText(text)) await this.enqueue({ to, text: part });
  }

  /** Best-effort "typing…" indicator; never throws. */
  typing(to) {
    if (!this.showTyping) return;
    const jid = to.startsWith('+') ? `${to.slice(1)}@s.whatsapp.net` : to;
    this.call('/send-presence-update', { jid, type: 'composing' }).catch(() => {});
  }

  sendDocument(to, documentUrl, fileName, caption) {
    const payload = { to, documentUrl, fileName };
    if (caption) payload.text = caption;
    return this.enqueue(payload);
  }

  /**
   * Download the decrypted bytes of an incoming media message.
   * Tries WasenderAPI's /decrypt-media first, then decrypts locally
   * with the message's mediaKey as a fallback.
   */
  async downloadMedia(rawMessage, media, maxBytes) {
    let buffer;
    try {
      const res = await this.call('/decrypt-media', { data: { messages: rawMessage } });
      const url = res.publicUrl || res.data?.publicUrl || res.data?.url || res.url;
      if (!url) throw new Error('no publicUrl in response');
      buffer = await fetchBuffer(this.fetch, url, maxBytes);
    } catch (err) {
      if (err.tooLarge) throw err;
      this.log.warn?.(`[wasender] decrypt-media failed (${err.message}); trying local decryption`);
      if (!media.url || !media.mediaKey) throw err;
      const enc = await fetchBuffer(this.fetch, media.url, maxBytes + 64);
      buffer = decryptWhatsAppMedia(enc, media.mediaKey, media.type);
    }
    if (buffer.length > maxBytes) throw tooLarge(maxBytes);
    return buffer;
  }
}

function tooLarge(max) {
  const e = new Error(`File is larger than ${Math.round(max / 1048576)} MB`);
  e.tooLarge = true;
  return e;
}

async function fetchBuffer(fetchImpl, url, maxBytes) {
  const res = await fetchImpl(url);
  if (!res.ok) throw new Error(`download failed (${res.status})`);
  const len = Number(res.headers.get('content-length'));
  if (len && len > maxBytes) throw tooLarge(maxBytes);
  return Buffer.from(await res.arrayBuffer());
}

export function chunkText(text, max = MAX_TEXT) {
  const parts = [];
  let rest = String(text);
  while (rest.length > max) {
    let cut = rest.lastIndexOf('\n', max);
    if (cut < max / 2) cut = max;
    parts.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n/, '');
  }
  if (rest) parts.push(rest);
  return parts;
}

// ---------- WhatsApp media decryption (mediaKey + HKDF + AES-256-CBC) ----------

const MEDIA_INFO = {
  image: 'WhatsApp Image Keys',
  video: 'WhatsApp Video Keys',
  audio: 'WhatsApp Audio Keys',
  document: 'WhatsApp Document Keys',
  sticker: 'WhatsApp Image Keys',
};

export function toKeyBuffer(key) {
  if (Buffer.isBuffer(key)) return key;
  if (typeof key === 'string') return Buffer.from(key, 'base64');
  if (Array.isArray(key)) return Buffer.from(key);
  if (key && typeof key === 'object') {
    if (Array.isArray(key.data)) return Buffer.from(key.data); // {type:'Buffer', data:[...]}
    return Buffer.from(Object.keys(key).sort((a, b) => a - b).map((k) => key[k]));
  }
  throw new Error('invalid mediaKey');
}

export function deriveMediaKeys(mediaKey, type) {
  const expanded = Buffer.from(
    crypto.hkdfSync('sha256', toKeyBuffer(mediaKey), Buffer.alloc(32), MEDIA_INFO[type] || MEDIA_INFO.document, 112),
  );
  return { iv: expanded.subarray(0, 16), cipherKey: expanded.subarray(16, 48), macKey: expanded.subarray(48, 80) };
}

export function decryptWhatsAppMedia(encrypted, mediaKey, type) {
  const { iv, cipherKey, macKey } = deriveMediaKeys(mediaKey, type);
  const file = encrypted.subarray(0, -10);
  const mac = encrypted.subarray(-10);
  const expected = crypto.createHmac('sha256', macKey).update(iv).update(file).digest().subarray(0, 10);
  if (!crypto.timingSafeEqual(mac, expected)) throw new Error('media MAC check failed');
  const decipher = crypto.createDecipheriv('aes-256-cbc', cipherKey, iv);
  return Buffer.concat([decipher.update(file), decipher.final()]);
}
