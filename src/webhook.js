import crypto from 'node:crypto';

const MEDIA_TYPES = {
  documentMessage: 'document',
  imageMessage: 'image',
  videoMessage: 'video',
  audioMessage: 'audio',
};

const EXT_BY_MIME = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/webp': '.webp',
  'video/mp4': '.mp4',
  'video/3gpp': '.3gp',
  'audio/ogg': '.ogg',
  'audio/mpeg': '.mp3',
  'audio/mp4': '.m4a',
  'audio/aac': '.aac',
  'application/pdf': '.pdf',
};

export function verifySignature(header, secret) {
  if (!header || !secret) return false;
  const a = Buffer.from(String(header));
  const b = Buffer.from(String(secret));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

const isMessageEvent = (event) =>
  typeof event === 'string' && /^messages/.test(event) && /(upsert|received)$/.test(event);

/** Unwrap ephemeral / view-once / document-with-caption containers. */
function unwrap(message) {
  let m = message || {};
  for (let i = 0; i < 5; i++) {
    const inner =
      m.ephemeralMessage?.message ||
      m.viewOnceMessage?.message ||
      m.viewOnceMessageV2?.message ||
      m.viewOnceMessageV2Extension?.message ||
      m.documentWithCaptionMessage?.message;
    if (!inner) break;
    m = inner;
  }
  return m;
}

const digits = (s) => String(s || '').split('@')[0].split(':')[0].replace(/\D/g, '');
const isPn = (jid) => /@s\.whatsapp\.net$|@c\.us$/.test(jid || '');

function senderNumber(key, isGroup) {
  if (isGroup) {
    return digits(
      key.cleanParticipantPn || key.participantPn || (isPn(key.participant) ? key.participant : ''),
    );
  }
  return digits(
    key.cleanedSenderPn || key.cleanSenderPn || key.senderPn || (isPn(key.remoteJid) ? key.remoteJid : ''),
  );
}

/**
 * Turn a WasenderAPI webhook body into normalised incoming messages:
 * { id, from, chat, replyTo, isGroup, pushName, text, media, raw }
 */
export function extractMessages(body) {
  if (!body || !isMessageEvent(body.event)) return [];
  const data = body.data ?? {};
  let items = data.messages ?? data;
  if (!Array.isArray(items)) items = [items];

  const out = [];
  for (const raw of items) {
    const key = raw?.key;
    if (!key?.id || key.fromMe) continue;
    const chat = key.remoteJid || '';
    if (chat === 'status@broadcast' || chat.endsWith('@newsletter')) continue;
    const isGroup = chat.endsWith('@g.us');
    const from = senderNumber(key, isGroup);

    const m = unwrap(raw.message);
    let media = null;
    let text = m.conversation || m.extendedTextMessage?.text || raw.message?.messageBody || '';

    for (const [field, type] of Object.entries(MEDIA_TYPES)) {
      const node = m[field];
      if (!node) continue;
      const mimetype = (node.mimetype || '').split(';')[0].trim();
      media = {
        type,
        mimetype: mimetype || 'application/octet-stream',
        fileName: node.fileName || node.title || defaultName(type, mimetype, raw.messageTimestamp),
        size: Number(node.fileLength?.low ?? node.fileLength ?? 0) || 0,
        url: node.url,
        mediaKey: node.mediaKey,
      };
      text = node.caption || text;
      break;
    }
    if (!text && !media) continue;

    out.push({
      id: key.id,
      from,
      chat,
      isGroup,
      replyTo: isGroup ? chat : from ? `+${from}` : chat,
      pushName: raw.pushName || '',
      text: String(text || '').trim(),
      media,
      raw,
    });
  }
  return out;
}

function defaultName(type, mimetype, ts) {
  const d = ts ? new Date(Number(ts) * 1000) : new Date();
  const stamp = d.toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15);
  const prefix = { image: 'IMG', video: 'VID', audio: 'AUD', document: 'DOC' }[type];
  return `${prefix}_${stamp}${EXT_BY_MIME[mimetype] || ''}`;
}
