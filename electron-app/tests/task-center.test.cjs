const { test } = require('node:test');
const assert = require('node:assert/strict');
const { componentFixture } = require('./component-fixture.cjs');
test('task center hides old refresh responses and retries only an explicit terminal task', async () => {
  const requests = [], actions = [];
  const tasks = { list: () => new Promise(r => requests.push(r)), retry: async id => actions.push(id), pause: async () => {}, resume: async () => {} };
  const f = componentFixture('components/panels/TaskCenterPanel.vue', {}, { window: { engine: { tasks, onNotification: () => () => {} } } });
  try {
    const next = f.state.refresh(); requests.at(-1)({ tasks: [{ task_id: 'new', state: 'failed', can_retry: true, output_files: [] }] }); await next;
    requests[0]({ tasks: [{ task_id: 'old', state: 'completed', output_files: [] }] }); await f.flush();
    assert.equal(f.state.records[0].task_id, 'new');
    await f.state.act('retry', f.state.records[0]);
    assert.deepEqual(actions, ['new']);
    for (const resolve of requests.slice(2)) resolve({ tasks: [] });
  } finally { f.dispose(); }
});
test('task center shows storage failure and does not claim an empty successful list', async () => {
  const f = componentFixture('components/panels/TaskCenterPanel.vue', {}, { window: { engine: { tasks: { list: async () => ({ tasks: [], storage_error: '任务文件损坏' }) }, onNotification: () => () => {} } } });
  await f.state.refresh(); await f.flush();
  assert.match(f.state.error, /任务文件损坏/); f.dispose();
});
