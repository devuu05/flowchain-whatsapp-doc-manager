import test from 'node:test';
import assert from 'node:assert/strict';
import { Assistant, TOOLS } from '../src/assistant.js';

const cfg = { apiKey: 'x', model: 'claude-opus-5-5', effort: 'low', sessionMinutes: 30, maxMessages: 80, fallbacks: true };

function fakeClient(responses) {
  const requests = [];
  const create = async (params) => {
    requests.push(structuredClone(params));
    return responses.shift();
  };
  return { requests, client: { beta: { messages: { create } }, messages: { create } } };
}

test('runs the tool loop and keeps the history append-only', async () => {
  const thinking = { type: 'thinking', thinking: '', signature: 'sig' };
  const { client, requests } = fakeClient([
    {
      stop_reason: 'tool_use',
      content: [thinking, { type: 'tool_use', id: 't1', name: 'search_files', input: { query: 'invoice' } }],
    },
    { stop_reason: 'end_turn', content: [{ type: 'text', text: 'Here you go: 1. invoice.pdf' }] },
    { stop_reason: 'end_turn', content: [{ type: 'text', text: 'Sent!' }] },
  ]);
  const a = new Assistant(cfg, { client, log: { error() {} } });
  const calls = [];
  const runTool = async (name, input) => {
    calls.push([name, input]);
    return { total: 1 };
  };

  assert.equal(await a.reply('1', 'find invoice', runTool), 'Here you go: 1. invoice.pdf');
  assert.deepEqual(calls, [['search_files', { query: 'invoice' }]]);
  assert.equal(await a.reply('1', 'send 1', runTool), 'Sent!');

  // Each request extends the previous one; thinking blocks are passed back unchanged.
  const [r1, r2, r3] = requests;
  assert.deepEqual(r2.messages.slice(0, r1.messages.length), r1.messages);
  assert.deepEqual(r3.messages.slice(0, r2.messages.length), r2.messages);
  assert.deepEqual(r2.messages[1].content[0], thinking);
  assert.equal(r2.messages[2].content[0].type, 'tool_result');
  assert.equal(r1.system, r3.system);
  assert.equal(r1.model, 'claude-opus-5-5');
  assert.deepEqual(r1.output_config, { effort: 'low' });
  assert.equal(r1.fallbacks, 'default');
  assert.deepEqual(r1.betas, ['server-side-fallback-2026-07-01']);
});

test('tool errors go back to the model as is_error results', async () => {
  const { client, requests } = fakeClient([
    { stop_reason: 'tool_use', content: [{ type: 'tool_use', id: 't1', name: 'request_delete', input: { item: 'x' } }] },
    { stop_reason: 'end_turn', content: [{ type: 'text', text: 'Only admins can delete.' }] },
  ]);
  const a = new Assistant(cfg, { client });
  const out = await a.reply('1', 'delete x', async () => {
    throw Object.assign(new Error('denied'), { userMessage: '⛔ Only an admin can do that.' });
  });
  assert.equal(out, 'Only admins can delete.');
  const result = requests[1].messages[2].content[0];
  assert.equal(result.is_error, true);
  assert.equal(result.content, '⛔ Only an admin can do that.');
});

test('a stale conversation starts fresh', async () => {
  const { client, requests } = fakeClient([
    { stop_reason: 'end_turn', content: [{ type: 'text', text: 'a' }] },
    { stop_reason: 'end_turn', content: [{ type: 'text', text: 'b' }] },
  ]);
  const a = new Assistant({ ...cfg, sessionMinutes: 0 }, { client });
  await a.reply('1', 'one', async () => ({}));
  await new Promise((r) => setTimeout(r, 2));
  await a.reply('1', 'two', async () => ({}));
  assert.equal(requests[1].messages.length, 1);
});

test('tool schemas are strict and closed', () => {
  for (const t of TOOLS) {
    assert.equal(t.strict, true, t.name);
    assert.equal(t.input_schema.additionalProperties, false, t.name);
    assert.ok(Array.isArray(t.input_schema.required), t.name);
  }
});
