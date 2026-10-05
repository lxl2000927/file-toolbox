const assert = require('node:assert/strict');
const { test } = require('node:test');
const { componentFixture } = require('./component-fixture.cjs');

function task(cancelTask = async () => ({ cancelled: true })) {
  let listener;
  const f = componentFixture('components/panels/PdfSplitPanel.vue', {}, { window: {
    engine: { onNotification(fn) { listener = fn; return () => { listener = null; }; }, cancelTask },
  } });
  return { ...f, notify(method, params = {}) { listener?.({ method, params: { task_id: 'first', ...params } }); } };
}

test('a dequeued task remains busy before its first progress event', () => {
  const f = task();
  try {
    f.state.startTask('first'); f.state.markQueued(1);
    f.notify('task.queued', { queued: false });
    assert.equal(f.state.taskBusy, true);
    assert.equal(f.state.taskCancellable, true);
  } finally { f.dispose(); }
});

test('late queue acknowledgments cannot overwrite progress or resurrect completion', () => {
  const f = task();
  try {
    f.state.startTask('first');
    f.notify('task.progress', { phase: 'writing', current: 1, total: 3 });
    f.state.markQueued(1);
    assert.equal(f.state.taskState.running, true);
    assert.equal(f.state.taskState.phase, 'writing');
    f.notify('task.complete', { ok: true, result: { total: 1, successful: 1, failed: 0 } });
    f.state.markQueued(1);
    assert.equal(f.state.taskBusy, false);
    assert.equal(f.state.taskState.taskId, null);
  } finally { f.dispose(); }
});

test('cancelling a pending task stays busy and a stale reply cannot stop a new task', async () => {
  let resolve;
  let calls = 0;
  const f = task(() => { calls++; return new Promise(r => { resolve = r; }); });
  try {
    f.state.startTask('first'); f.state.cancelTask();
    assert.equal(f.state.taskBusy, true);
    assert.equal(calls, 0, 'cancellation waits until the backend has accepted the task');
    f.state.markSubmitted('first');
    assert.equal(calls, 1);
    f.notify('task.complete', { ok: false, cancelled: true, error: '已取消' });
    f.state.startTask('second');
    f.notify('task.progress', { task_id: 'second', phase: 'writing' });
    resolve({ cancelled: false }); await f.flush();
    assert.equal(f.state.taskBusy, true);
    assert.equal(f.state.taskState.phase, 'writing');
  } finally { f.dispose(); }
});

test('a failed cancellation request preserves task monitoring and permits retry', async () => {
  let calls = 0;
  const f = task(async () => { if (++calls === 1) throw new Error('transport unavailable'); return { cancelled: true }; });
  try {
    f.state.startTask('first');
    f.state.markSubmitted('first');
    await f.state.cancelTask();
    assert.equal(f.state.taskBusy, true);
    await f.state.cancelTask(); assert.equal(calls, 2);
    f.notify('task.complete', { ok: false, cancelled: true, error: '已取消' });
    assert.equal(f.state.taskBusy, false);
  } finally { f.dispose(); }
});
