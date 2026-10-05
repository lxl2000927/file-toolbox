const { test } = require('node:test');
const assert = require('node:assert/strict');
const { componentFixture } = require('./component-fixture.cjs');
const status = (state, extra = {}) => ({ state, current: '2.5.0', supported: true, packageType: 'installer', ...extra });

test('navigation preference survives application cleanup and a fresh mount', async () => {
  const data = new Map([['file-toolbox.old-state', 'stale']]);
  const localStorage = { get length() { return data.size; }, key: i => [...data.keys()][i], getItem: key => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  const f = componentFixture('App.vue', {}, { localStorage });
  try {
    f.state.navCollapsed = true; await f.flush(); f.state.clearAppStateStorage();
    assert.equal(data.get('file-toolbox.ui.nav-collapsed'), '1');
    assert.equal(data.has('file-toolbox.old-state'), false);
  } finally { f.dispose(); }
  const restored = componentFixture('App.vue', {}, { localStorage });
  try { assert.equal(restored.state.navCollapsed, true); } finally { restored.dispose(); }
});

test('unavailable preference storage does not prevent navigation collapse', async () => {
  const localStorage = { length: 0, getItem() { throw Error('denied'); }, setItem() { throw Error('denied'); } };
  const f = componentFixture('App.vue', {}, { localStorage });
  try { f.state.navCollapsed = true; await f.flush(); assert.equal(f.state.navCollapsed, true); }
  finally { f.dispose(); }
});

test('a late initial update read cannot replace a completed manual check without notifications', async () => {
  let initial;
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: { setInterval, clearInterval,
    electronAPI: { update: { onStatus: () => () => {}, getStatus: () => new Promise(r => { initial = r; }),
      check: async () => status('available', { latest: '2.6.0' }) } },
  } });
  try {
    await f.state.checkUpdate(); initial(status('idle')); await f.flush();
    assert.equal(f.state.updateStatus.state, 'available');
    assert.equal(f.state.updateStatus.latest, '2.6.0');
  } finally { f.dispose(); }
});

test('repeated check clicks dispatch once before the first status notification', async () => {
  let checks = 0, finish;
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: { setInterval, clearInterval,
    electronAPI: { update: { onStatus: () => () => {}, getStatus: async () => status('idle'),
      check: () => { checks++; return new Promise(r => { finish = r; }); } } },
  } });
  try {
    await f.flush(); const first = f.state.checkUpdate(); const second = f.state.checkUpdate();
    assert.equal(checks, 1); assert.equal(f.state.updateBusy, true);
    finish(status('up-to-date')); await Promise.all([first, second]);
    assert.equal(f.state.updateBusy, false);
  } finally { f.dispose(); }
});

test('failure opening a release link preserves a downloaded installer state', async () => {
  const notices = [];
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: { setInterval, clearInterval,
    electronAPI: { openExternal: async () => { throw Error('no browser'); } },
  } });
  try {
    f.state.toast.error = message => notices.push(message);
    f.state.updateStatus = status('downloaded', { latest: '2.6.0', percent: 100 });
    await f.state.openUpdateRelease();
    assert.equal(f.state.updateStatus.state, 'downloaded'); assert.match(notices[0], /no browser/);
  } finally { f.dispose(); }
});

test('log search and text export include completed output paths and warnings', async () => {
  let saved;
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: { setInterval, clearInterval,
    electronAPI: { saveFile: async options => { saved = options; return { saved: true, path: 'logs.txt' }; } },
  } });
  try {
    f.state.logRaw = [{ id: 'one', operation_type: 'pdf_tools', message: 'PDF output', success: true,
      details: { output_files: ['C:/output/invoice-072.pdf'], warnings: ['back pages unequal'] } }];
    f.state.logSearch = 'invoice-072';
    assert.equal(f.state.filteredLogItems.length, 1);
    assert.equal(f.state.filteredLogItems[0].level, 'warning');
    await f.state.exportLogsTxt();
    assert.match(saved.content, /invoice-072.pdf/); assert.match(saved.content, /back pages unequal/);
  } finally { f.dispose(); }
});

test('clearing logs invalidates an older refresh and prevents duplicate clear requests', async () => {
  let resolveOld, clears = 0;
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: { setInterval, clearInterval,
    engine: { history: { get: () => new Promise(r => { resolveOld = r; }), clear: async () => { clears++; return { cleared: true }; } } },
  } });
  try {
    f.state.logRaw = [{ id: 'old', description: 'old record' }];
    f.state.dialog.confirm = async () => true;
    await Promise.all([f.state.clearLogs(), f.state.clearLogs()]);
    resolveOld({ records: [{ id: 'old', description: 'old record' }] }); await f.flush();
    assert.equal(clears, 1); assert.equal(f.state.logRaw.length, 0);
  } finally { f.dispose(); }
});

test('cancelled clear leaves logs and failed clear preserves records for retry', async () => {
  let clears = 0;
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: { setInterval, clearInterval,
    engine: { history: { get: async () => ({ records: [{ id: 'kept' }] }), clear: async () => { clears++; return { cleared: false, error: 'disk full' }; } } },
  } });
  try {
    await f.flush(); f.state.dialog.confirm = async () => false; await f.state.clearLogs();
    assert.equal(clears, 0); assert.equal(f.state.logRaw.length, 1);
    f.state.dialog.confirm = async () => true; await f.state.clearLogs();
    assert.equal(f.state.logRaw.length, 1); assert.match(f.state.logError, /disk full/);
    assert.equal(f.state.logClearing, false);
  } finally { f.dispose(); }
});
