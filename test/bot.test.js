import test from 'node:test';
import assert from 'node:assert/strict';
import { createBot } from '../src/bot.js';
import { Store } from '../src/store.js';
import { NotConnectedError } from '../src/onedrive.js';
import { FakeDrive, FakeWa, testConfig, textMsg, fileMsg, settle, ADMIN, USER, STRANGER } from './helpers.js';

const quiet = { info() {}, warn() {}, error() {} };

function setup(env, extra = {}) {
  const drive = new FakeDrive();
  const wa = new FakeWa();
  const store = new Store(null);
  const bot = createBot({ config: testConfig(env), drive, wa, store, log: quiet, ...extra });
  return { drive, wa, store, bot };
}

// Uploads are confirmed in one summary message after a short delay.
async function upload(bot, msg) {
  await bot(msg);
  await settle();
}

test('documents go to the default folder, or the folder named in the caption', async () => {
  const { bot, wa, drive } = setup();
  await upload(bot, fileMsg(USER, 'invoice.pdf'));
  assert.match(wa.last.text, /Saved \*invoice\.pdf\*.*in \*\/Inbox\*/s);
  assert.ok(drive.find('Inbox/invoice.pdf'));

  await upload(bot, fileMsg(USER, 'scan.pdf', '/save Invoices/2026 as March bill'));
  assert.ok(drive.find('Invoices/2026/March bill.pdf'), 'saved with new name + kept extension');
  assert.doesNotMatch(wa.last.text, /somewhere else/, 'no folder question when the folder was chosen');

  await upload(bot, fileMsg(USER, 'x.pdf', '#Receipts'));
  assert.ok(drive.find('Receipts/x.pdf'));
});

test('several files sent together get one summary, and a number moves them', async () => {
  const { bot, wa, drive } = setup();
  await drive.ensureFolder('Invoices');
  await bot(fileMsg(USER, 'a.pdf'));
  await bot(fileMsg(USER, 'b.pdf'));
  await settle();
  assert.equal(wa.sent.length, 1);
  assert.match(wa.last.text, /Saved 2 files in \*\/Inbox\*/);
  assert.match(wa.last.text, /1\. 📁 Main folder\n2\. 📁 Invoices/);

  await bot(textMsg(USER, '2'));
  assert.match(wa.last.text, /Moved 2 files to \*\/Invoices\*/);
  assert.ok(drive.find('Invoices/a.pdf') && drive.find('Invoices/b.pdf'));

  await upload(bot, fileMsg(USER, 'c.pdf'));
  await bot(textMsg(USER, '#Clients/Acme'));
  assert.ok(drive.find('Clients/Acme/c.pdf'), '#Name creates a new folder');
});

test('duplicate names are auto-renamed and the user is told', async () => {
  const { bot, wa } = setup();
  await upload(bot, fileMsg(USER, 'a.pdf'));
  await upload(bot, fileMsg(USER, 'a.pdf'));
  assert.match(wa.last.text, /\*a 1\.pdf\*.*already existed/s);
});

test('greeting shows the menu; numbers drive it', async () => {
  const { bot, wa, drive } = setup();
  await upload(bot, fileMsg(USER, 'report.pdf'));
  await bot(textMsg(USER, 'hi'));
  assert.match(wa.last.text, /Hi Asha!.*1️⃣ Browse files/s);
  assert.doesNotMatch(wa.last.text, /Delete/, 'no admin items for users');

  await bot(textMsg(USER, '1'));
  assert.match(wa.last.text, /1\. 📁 Inbox\//);
  await bot(textMsg(USER, '1'));
  assert.match(wa.last.text, /📂 \*\/Inbox\*.*1\. 📄 report\.pdf/s);
  await bot(textMsg(USER, '1'));
  assert.equal(wa.last.fileName, 'report.pdf');

  await bot(textMsg(USER, 'menu'));
  await bot(textMsg(USER, '2'));
  assert.match(wa.last.text, /What is the file called/);
  await bot(textMsg(USER, 'repo'));
  assert.match(wa.last.text, /Found 1 for "repo".*report\.pdf/s);

  await drive.ensureFolder('Clients');
  await bot(textMsg(USER, 'menu'));
  await bot(textMsg(USER, '4'));
  assert.match(wa.last.text, /Pick a new upload folder/);
  await bot(textMsg(USER, 'Clients'));
  assert.match(wa.last.text, /now be saved in \*\/Clients\*/);
});

test('admin menu: rename and delete step by step, with a plain "yes"', async () => {
  const { bot, wa, drive } = setup();
  await upload(bot, fileMsg(ADMIN, 'a.pdf', '#Docs'));
  await bot(textMsg(ADMIN, '/cd Docs'));

  await bot(textMsg(ADMIN, 'menu'));
  assert.match(wa.last.text, /8️⃣ Delete/);
  await bot(textMsg(ADMIN, '7'));
  assert.match(wa.last.text, /Which one should I rename/);
  await bot(textMsg(ADMIN, '1'));
  assert.match(wa.last.text, /New name for \*a\.pdf\*/);
  await bot(textMsg(ADMIN, 'Final contract'));
  assert.match(wa.last.text, /\*a\.pdf\* → \*Final contract\.pdf\*/);

  await bot(textMsg(ADMIN, 'menu'));
  await bot(textMsg(ADMIN, '8'));
  await bot(textMsg(ADMIN, '1'));
  assert.match(wa.last.text, /Delete 📄 \*Final contract\.pdf\*.*Reply \*yes\*/s);
  assert.ok(drive.find('Docs/Final contract.pdf'), 'not deleted before confirmation');
  await bot(textMsg(ADMIN, 'Yes'));
  assert.match(wa.last.text, /Deleted/);
  assert.equal(drive.find('Docs/Final contract.pdf'), null);
});

test('rename / delete / mkdir are admin only; users may move only their fresh uploads', async () => {
  const { bot, wa, drive } = setup();
  await upload(bot, fileMsg(ADMIN, 'admin.pdf'));
  await upload(bot, fileMsg(USER, 'mine.pdf'));
  for (const cmd of ['/rename mine.pdf -> b.pdf', '/delete mine.pdf', '/mkdir X', '/rm mine.pdf']) {
    await bot(textMsg(USER, cmd));
    assert.match(wa.last.text, /Only an admin/, cmd);
  }
  await bot(textMsg(USER, '/move admin.pdf -> X'));
  assert.match(wa.last.text, /Only an admin can move/);
  assert.ok(drive.find('Inbox/admin.pdf'));

  await bot(textMsg(USER, '/move mine.pdf -> X'));
  assert.match(wa.last.text, /Moved \*mine\.pdf\* → \*\/X\*/);
});

test('/cd changes where uploads land and blocks escaping the main folder', async () => {
  const { bot, wa, drive } = setup();
  await drive.ensureFolder('Clients/Acme');
  await bot(textMsg(USER, '/cd Clients/Acme'));
  assert.match(wa.last.text, /Now in \*\/Clients\/Acme\*/);
  await upload(bot, fileMsg(USER, 'po.pdf'));
  assert.ok(drive.find('Clients/Acme/po.pdf'));

  await bot(textMsg(USER, '/cd ../../..'));
  assert.match(wa.last.text, /cannot go above/);
  await bot(textMsg(USER, '/cd Nope'));
  assert.match(wa.last.text, /doesn't exist/);
});

test('/get supports numbers, several numbers and names found by search', async () => {
  const { bot, wa } = setup();
  await upload(bot, fileMsg(USER, 'b.pdf'));
  await upload(bot, fileMsg(USER, 'a.pdf', '/save Deep/Nested'));
  await bot(textMsg(USER, '/ls Inbox'));
  await bot(textMsg(USER, '/get 1'));
  assert.equal(wa.last.fileName, 'b.pdf');
  await bot(textMsg(USER, '/get a.pdf'));
  assert.equal(wa.last.fileName, 'a.pdf');
  await bot(textMsg(USER, '/recent'));
  wa.clear();
  await bot(textMsg(USER, '/get 1,2'));
  assert.deepEqual(wa.sent.map((s) => s.fileName), ['a.pdf', 'b.pdf']);
});

test('delete confirmation expires; "no" cancels', async () => {
  const { bot, wa, drive } = setup({ CONFIRM_TIMEOUT_SECONDS: '0' });
  await upload(bot, fileMsg(ADMIN, 'a.pdf'));
  await bot(textMsg(ADMIN, '/delete a.pdf'));
  await settle(5);
  await bot(textMsg(ADMIN, 'yes'));
  assert.match(wa.last.text, /expired/);

  const s2 = setup();
  await upload(s2.bot, fileMsg(ADMIN, 'a.pdf'));
  await s2.bot(textMsg(ADMIN, '/delete a.pdf'));
  await s2.bot(textMsg(ADMIN, 'nahi'));
  assert.match(s2.wa.last.text, /Cancelled/);
  assert.ok(drive.find('Inbox/a.pdf') && s2.drive.find('Inbox/a.pdf'));
});

test('folder rename and move keep users current folder valid', async () => {
  const { bot, wa, drive, store } = setup();
  await drive.ensureFolder('Old');
  await bot(textMsg(USER, '/cd Old'));
  await bot(textMsg(ADMIN, '/rename /Old -> New'));
  assert.equal(store.user(USER).cwd, 'New');
  await bot(textMsg(ADMIN, '/move /New -> /New/x'));
  assert.match(wa.last.text, /into itself/);
});

test('unauthorised numbers are ignored; duplicates are processed once', async () => {
  const { bot, wa } = setup();
  await bot(textMsg(STRANGER, '/help'));
  assert.equal(wa.sent.length, 0);
  const m = textMsg(USER, '/pwd');
  await bot(m);
  await bot(m);
  assert.equal(wa.sent.length, 1);
});

test('only admins and listed numbers by default; ALLOW_EVERYONE opens non-admin features', async () => {
  const closed = setup({ ALLOWED_NUMBERS: '' });
  await closed.bot(textMsg(STRANGER, '/whoami'));
  assert.equal(closed.wa.sent.length, 0);

  const { bot, wa } = setup({ ALLOWED_NUMBERS: '', ALLOW_EVERYONE: 'true' });
  await bot(textMsg(STRANGER, '/whoami'));
  assert.match(wa.last.text, /Role: \*user\*/);
  await bot(textMsg(STRANGER, '/delete x'));
  assert.match(wa.last.text, /Only an admin/);
});

test('when OneDrive is not connected people get a clear message', async () => {
  const { bot, wa, drive } = setup();
  drive.list = async () => {
    throw new NotConnectedError();
  };
  await bot(textMsg(USER, '/ls'));
  assert.match(wa.last.text, /ask an admin to reconnect/);
  await bot(textMsg(ADMIN, '/ls'));
  assert.match(wa.last.text, /Send \/connect/);
});

test('/connect sends a sign-in code and reports success', async () => {
  const { bot, wa, drive } = setup();
  let finish;
  drive.startDeviceLogin = async () => ({
    code: 'ABCD-1234',
    url: 'https://microsoft.com/devicelogin',
    expiresInMin: 15,
    done: new Promise((r) => (finish = r)),
  });
  await bot(textMsg(USER, '/connect'));
  assert.match(wa.last.text, /Only an admin/);
  await bot(textMsg(ADMIN, '/connect'));
  assert.match(wa.last.text, /microsoft\.com\/devicelogin.*\*ABCD-1234\*/s);
  finish({ name: 'Shiva', email: 's@example.com' });
  await settle();
  assert.match(wa.last.text, /OneDrive connected to \*Shiva\*/);
});

test('free text goes to the AI assistant with context and event notes', async () => {
  const calls = [];
  const assistant = {
    async reply(number, prompt, runTool) {
      calls.push(prompt);
      const listed = await runTool('search_files', { query: 'invoice' });
      await runTool('send_files', { items: ['1'] });
      let denied;
      try {
        await runTool('request_delete', { item: '1' });
      } catch (e) {
        denied = e.userMessage;
      }
      return `found ${listed.total}; delete: ${denied}`;
    },
    reset() {},
  };
  const { bot, wa } = setup({}, { assistant });
  await upload(bot, fileMsg(USER, 'invoice-march.pdf'));
  await bot(textMsg(USER, 'send me the march invoice please'));

  assert.match(calls[0], /^\[context\] sender: \+922222222222 \(Asha\); role: user; current folder: \/; uploads go to: \/Inbox/);
  assert.match(calls[0], /\[event\] The user uploaded \/Inbox\/invoice-march\.pdf/);
  assert.match(calls[0], /send me the march invoice please$/);
  assert.equal(wa.sent.at(-2).fileName, 'invoice-march.pdf');
  assert.match(wa.last.text, /found 1; delete: ⛔ Only an admin/);

  // With the AI on, "hi" goes to the AI but "menu" still shows the menu.
  await bot(textMsg(USER, 'hi'));
  assert.equal(calls.length, 2);
  await bot(textMsg(USER, 'menu'));
  assert.match(wa.last.text, /1️⃣ Browse files.*or just tell me what you need/s);
});

test('AI delete request still needs the person to say yes', async () => {
  const assistant = {
    async reply(_n, _p, runTool) {
      const r = await runTool('request_delete', { item: 'Inbox/a.pdf' });
      return `Delete ${r.item}? Reply yes.`;
    },
    reset() {},
  };
  const { bot, wa, drive } = setup({}, { assistant });
  await upload(bot, fileMsg(ADMIN, 'a.pdf'));
  await bot(textMsg(ADMIN, 'delete the a pdf'));
  assert.ok(drive.find('Inbox/a.pdf'));
  await bot(textMsg(ADMIN, 'haan'));
  assert.match(wa.last.text, /Deleted \*a\.pdf\*/);
  assert.equal(drive.find('Inbox/a.pdf'), null);
});
