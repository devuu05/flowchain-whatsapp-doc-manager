import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { resolvePath, sanitizeName } from '../src/paths.js';
import { extractMessages, verifySignature } from '../src/webhook.js';
import { decryptWhatsAppMedia, deriveMediaKeys, chunkText } from '../src/wasender.js';
import { OneDrive } from '../src/onedrive.js';
import { Store } from '../src/store.js';
import { testConfig } from './helpers.js';

test('resolvePath', () => {
  assert.equal(resolvePath('A/B', 'C'), 'A/B/C');
  assert.equal(resolvePath('A/B', '..'), 'A');
  assert.equal(resolvePath('A/B', '/X/Y/'), 'X/Y');
  assert.equal(resolvePath('A', '/'), '');
  assert.throws(() => resolvePath('', '..'));
  assert.equal(sanitizeName('  bad:name?.pdf '), 'bad_name_.pdf');
});

test('signature check', () => {
  assert.ok(verifySignature('abc', 'abc'));
  assert.ok(!verifySignature('abd', 'abc'));
  assert.ok(!verifySignature(undefined, 'abc'));
});

test('extractMessages handles text, captioned documents and LID senders', () => {
  const text = extractMessages({
    event: 'messages.upsert',
    data: { messages: { key: { id: '1', fromMe: false, remoteJid: '919876543210@s.whatsapp.net' }, message: { conversation: '/ls' } } },
  });
  assert.equal(text[0].from, '919876543210');
  assert.equal(text[0].replyTo, '+919876543210');
  assert.equal(text[0].text, '/ls');

  const doc = extractMessages({
    event: 'messages.received',
    data: {
      messages: {
        key: { id: '2', fromMe: false, remoteJid: '123@lid', cleanedSenderPn: '919876543210' },
        message: {
          documentWithCaptionMessage: {
            message: { documentMessage: { fileName: 'a.pdf', mimetype: 'application/pdf', caption: '/save Bills', url: 'u', mediaKey: 'k' } },
          },
        },
      },
    },
  });
  assert.equal(doc[0].from, '919876543210');
  assert.equal(doc[0].media.fileName, 'a.pdf');
  assert.equal(doc[0].text, '/save Bills');

  const img = extractMessages({
    event: 'messages.upsert',
    data: { messages: { key: { id: '3', remoteJid: '9@s.whatsapp.net' }, message: { imageMessage: { mimetype: 'image/jpeg' } }, messageTimestamp: 1700000000 } },
  });
  assert.match(img[0].media.fileName, /^IMG_20231114_\d{6}\.jpg$/);

  assert.equal(extractMessages({ event: 'messages.upsert', data: { messages: { key: { id: '4', fromMe: true, remoteJid: 'x' }, message: { conversation: 'hi' } } } }).length, 0);
  assert.equal(extractMessages({ event: 'messages.update', data: {} }).length, 0);
});

test('WhatsApp media decryption round-trips', () => {
  const mediaKey = crypto.randomBytes(32);
  const plain = Buffer.from('hello pdf bytes '.repeat(100));
  const { iv, cipherKey, macKey } = deriveMediaKeys(mediaKey, 'document');
  const c = crypto.createCipheriv('aes-256-cbc', cipherKey, iv);
  const enc = Buffer.concat([c.update(plain), c.final()]);
  const mac = crypto.createHmac('sha256', macKey).update(iv).update(enc).digest().subarray(0, 10);
  const out = decryptWhatsAppMedia(Buffer.concat([enc, mac]), mediaKey.toString('base64'), 'document');
  assert.deepEqual(out, plain);
  assert.throws(() => decryptWhatsAppMedia(Buffer.concat([enc, Buffer.alloc(10)]), mediaKey, 'document'), /MAC/);
});

test('chunkText splits long replies', () => {
  const parts = chunkText('x\n'.repeat(5000), 1000);
  assert.ok(parts.every((p) => p.length <= 1000));
  assert.equal(parts.join('\n').replace(/\n+/g, '').length, 5000);
});

test('OneDrive builds Graph paths inside the base folder and maps items back', async () => {
  const calls = [];
  const fakeFetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET' });
    if (url.includes('oauth2')) return Response.json({ access_token: 't', expires_in: 3600, refresh_token: 'r2' });
    return Response.json({
      id: 'x', name: 'a b.pdf', size: 5, file: { mimeType: 'application/pdf' },
      parentReference: { path: '/drive/root:/WhatsApp%20Files/Inv%2026' },
    });
  };
  const store = new Store(null);
  const cfg = testConfig({ MS_REFRESH_TOKEN: 'r1' });
  const od = new OneDrive(cfg, store, { fetchImpl: fakeFetch });
  const it = await od.getItem('Inv 26/a b.pdf');
  assert.equal(calls[1].url, 'https://graph.microsoft.com/v1.0/me/drive/root:/WhatsApp%20Files/Inv%2026/a%20b.pdf:');
  assert.equal(it.path, 'Inv 26/a b.pdf');
  assert.equal(store.refreshToken, 'r2', 'rotated refresh token persisted');
  assert.equal(od.relFromDrive('/Other/x'), null);
});
