const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildSync } = require('esbuild');
const path = require('node:path');

function subject() {
  const source = path.join(__dirname, '../main/preview-engine.ts');
  const code = buildSync({ entryPoints: [source], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const m = { exports: {} }; new Function('module', 'exports', 'require', code)(m, m.exports, require);
  return m.exports.PreviewEngine;
}
function bridge() {
  const events = []; const b = { events, stopped: false, addNotificationHandler(fn) { b.notify = fn; }, addExitHandler(fn) { b.exit = fn; },
    async call(method, params) { events.push([method, params]); return { task_id: params.task_id }; }, async shutdown() { b.stopped = true; } }; return b;
}
test('preview startup is shared and cancellation routes only owned tasks', async () => {
  const PreviewEngine = subject(); const worker = bridge(); let starts = 0, resolve;
  const ready = new Promise(r => { resolve = r; });
  const channel = new PreviewEngine(async () => { starts++; await ready; return worker; }, () => {});
  const a = channel.call('pdf_tools.run', { task_id: 'preview_1' });
  const b = channel.call('pdf_tools.run', { task_id: 'preview_2' });
  resolve(); await Promise.all([a, b]);
  assert.equal(starts, 1); assert.equal(channel.owns('preview_1'), true); assert.equal(channel.owns('heavy_1'), false);
  await channel.cancel('preview_1'); assert.equal(worker.events.at(-1)[0], 'task.cancel');
  worker.notify('task.complete', { task_id: 'preview_1', ok: true }); assert.equal(channel.owns('preview_1'), false);
  await channel.shutdown(); assert.equal(worker.stopped, true);
});
test('preview failure settles its jobs without emitting a global engine failure', async () => {
  const PreviewEngine = subject(); const workers = []; const events = [];
  const channel = new PreviewEngine(async () => { const w = bridge(); workers.push(w); return w; }, (method, params) => events.push([method, params]));
  await channel.call('pdf_tools.run', { task_id: 'preview_1' }); workers[0].exit(Error('preview crashed'));
  assert.equal(events.length, 1); assert.equal(events[0][0], 'task.complete'); assert.equal(events[0][1].ok, false);
  assert.equal(channel.owns('preview_1'), false);
  await channel.call('pdf_tools.run', { task_id: 'preview_2' }); assert.equal(workers.length, 2);
  await channel.shutdown();
});
test('shutdown during preview startup does not leak the process', async () => {
  const PreviewEngine = subject(); let ready; const worker = bridge();
  const channel = new PreviewEngine(() => new Promise(resolve => { ready = resolve; }), () => {});
  const pending = channel.call('pdf_tools.run', { task_id: 'preview_1' });
  const closing = channel.shutdown(); ready(worker);
  await assert.rejects(pending, /关闭/); await closing; assert.equal(worker.stopped, true);
});
