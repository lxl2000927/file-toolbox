const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const { buildSync } = require('esbuild');
const { componentFixture } = require('./component-fixture.cjs');

function updater(options = {}) {
  const handlers = new Map();
  const events = new Map();
  const auto = { on: (name, fn) => events.set(name, fn), ...options.auto };
  const appPath = path.resolve(__dirname, '../dist/main');
  const mainFrame = { url: pathToFileURL(path.join(appPath, '../renderer/index.html')).href };
  const webContents = { mainFrame, send() {} };
  const electron = {
    app: { isPackaged: true, getVersion: () => '2.5.0', getName: () => 'File Toolbox',
      requestSingleInstanceLock: () => true, on() {}, whenReady: () => new Promise(() => {}) },
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    protocol: { registerSchemesAsPrivileged() {} },
  };
  const source = fs.readFileSync(path.join(__dirname, '../main/index.ts'), 'utf8');
  const code = buildSync({ stdin: { contents: source + '\nexport const fixture = { publishUpdateStatus, configureAutoUpdater, setRecovery(fn) { startEngine=fn; }, init(w,b) { mainWindow=w; bridge=b; } };', loader: 'ts', resolveDir: path.join(__dirname, '../main') },
    bundle: true, platform: 'node', format: 'cjs', write: false, external: ['electron', 'electron-updater'],
  }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, __dirname: appPath, URL, Buffer, console, setTimeout, clearTimeout,
    process: { ...process, env: { ...process.env, PORTABLE_EXECUTABLE_FILE: options.installed ? '' : 'portable.exe', PORTABLE_EXECUTABLE_DIR: '' }, resourcesPath: appPath, on() {} },
    require: name => name === 'electron' ? electron : name === 'electron-updater' ? { autoUpdater: auto }
      : name === 'fs' && options.installed ? { ...fs, existsSync: file => file.endsWith('.file-toolbox-installed') || fs.existsSync(file) }
      : name === 'https' ? { request() { throw new Error('unexpected network request'); } } : require(name),
  });
  const api = module.exports.fixture;
  api.init({ webContents, isDestroyed: () => false }, options.bridge || null);
  return { set: api.publishUpdateStatus, events, configure: api.configureAutoUpdater, recover: api.setRecovery,
    call: method => handlers.get('app:update:' + method)({ sender: webContents, senderFrame: mainFrame }) };
}

test('a synchronous download failure releases the request lock so retry can succeed', async () => {
  let calls = 0;
  const h = updater({ installed: true, auto: { downloadUpdate() {
    calls++; if (calls === 1) throw Error('download initialization failed');
    h.events.get('update-downloaded')({ version: '2.6.0' }); return Promise.resolve(['installer.exe']);
  } } });
  h.set({ state: 'available', latest: '2.6.0' });
  assert.equal((await h.call('download')).state, 'error');
  assert.equal((await h.call('download')).state, 'downloaded');
  assert.equal(calls, 2);
});

test('an installer launch failure restores the engine after it was shut down', async () => {
  let stopped = 0, restarted = 0;
  const h = updater({ installed: true, bridge: { shutdown: async () => { stopped++; } },
    auto: { quitAndInstall() { h.events.get('error')(Error('installer cannot launch')); } } });
  h.recover(async () => { restarted++; }); h.configure();
  h.set({ state: 'downloaded', latest: '2.6.0', percent: 100 });
  assert.equal((await h.call('install')).accepted, true);
  await new Promise(r => setTimeout(r, 20));
  assert.equal(stopped, 1); assert.equal(restarted, 1);
  const result = await h.call('getStatus');
  assert.equal(result.state, 'error'); assert.match(result.error, /installer cannot launch/);
});

test('reading portable update status preserves a discovered release', async () => {
  const h = updater(); h.set({ state: 'available', latest: '2.6.0' });
  const status = await h.call('getStatus');
  assert.equal(status.state, 'available'); assert.equal(status.latest, '2.6.0');
  assert.equal(status.supported, false); assert.equal(status.packageType, 'portable');
});

test('checking cannot reset an active download or a ready installer', async () => {
  const h = updater();
  for (const state of ['downloading', 'downloaded', 'installing']) {
    h.set({ state, latest: '2.6.0', percent: 63 });
    const status = await h.call('check');
    assert.equal(status.state, state); assert.equal(status.percent, 63);
  }
});

test('a late initial status response cannot overwrite a newer update event', async () => {
  let resolve, notify;
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: {
    setInterval, clearInterval, electronAPI: { update: {
      getStatus: () => new Promise(r => { resolve = r; }),
      onStatus(fn) { notify = fn; return () => {}; },
    } },
  } });
  try {
    notify({ state: 'downloading', percent: 25, current: '2.5.0' });
    resolve({ state: 'idle', current: '2.5.0' }); await f.flush();
    assert.equal(f.state.updateStatus.state, 'downloading');
    assert.equal(f.state.updateStatus.percent, 25);
  } finally { f.dispose(); }
});

test('partial rename undo keeps a retry token and locks execution while undoing', async () => {
  let resolve;
  const f = componentFixture('components/panels/RenamePanel.vue', {}, { window: {
    addEventListener() {}, removeEventListener() {}, setTimeout, clearTimeout,
    engine: { rename: { undo: () => new Promise(r => { resolve = r; }), preview: async () => [] } },
  } });
  try {
    f.state.dialog.confirm = async () => true;
    f.state.undoToken = 'undo-token';
    f.state.lastOperations = [{ success: true, original_path: 'a', new_path: 'b' }, { success: true, original_path: 'c', new_path: 'd' }];
    const undoing = f.state.undo(); await f.flush();
    assert.equal(f.state.executing, true);
    resolve({ restored: [{ from: 'b', to: 'a' }], failed: [{ path: 'd', error: 'locked' }] });
    await undoing;
    assert.equal(f.state.undoToken, 'undo-token'); assert.equal(f.state.canUndo, true);
    assert.equal(f.state.lastOperations.length, 1);
  } finally { f.dispose(); }
});

test('storage errors remain visible alongside in-memory logs and clear after recovery', async () => {
  let storage_error = 'disk full';
  const f = componentFixture('components/panels/AboutPanel.vue', {}, { window: {
    setInterval, clearInterval, engine: { history: { get: async () => ({
      records: [{ id: 'saved-in-memory', description: 'rename completed' }], storage_error,
    }) } },
  } });
  try {
    await f.flush();
    assert.match(f.state.logError, /disk full/);
    assert.equal(f.state.logRaw.length, 1);
    await f.state.refreshLogs();
    storage_error = null; await f.state.refreshLogs();
    assert.equal(f.state.logError, '');
    assert.equal(f.state.logRaw.length, 1);
  } finally { f.dispose(); }
});
