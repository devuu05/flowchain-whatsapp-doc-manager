import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/index.js';
import { createBot } from '../src/bot.js';
import { Store } from '../src/store.js';
import { FakeDrive, FakeWa, testConfig, USER } from './helpers.js';

test('webhook endpoint verifies the signature and dispatches messages', async () => {
  const config = testConfig();
  const wa = new FakeWa();
  const bot = createBot({ config, drive: new FakeDrive(), wa, store: new Store(null), log: { info() {} } });
  const app = createApp({ config, bot, log: { warn() {}, error() {} } });
  const server = app.listen(0);
  const url = `http://127.0.0.1:${server.address().port}/webhook`;
  const body = JSON.stringify({
    event: 'messages.upsert',
    data: { messages: { key: { id: 'w1', fromMe: false, remoteJid: `${USER}@s.whatsapp.net` }, message: { conversation: '/pwd' } } },
  });

  try {
    const bad = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', 'x-webhook-signature': 'nope' }, body });
    assert.equal(bad.status, 401);

    const ok = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', 'x-webhook-signature': 'secret' }, body });
    assert.equal(ok.status, 200);
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(wa.last.to, `+${USER}`);
    assert.match(wa.last.text, /Current folder/);
  } finally {
    server.close();
  }
});
