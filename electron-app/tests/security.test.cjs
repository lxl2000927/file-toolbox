const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const { buildSync } = require('esbuild');

// Run the real IPC handlers, replacing only Electron's native UI/transport.
function mainHarness() {
  const handlers = new Map();
  const appPath = path.resolve(__dirname, '../dist/main');
  const appUrl = pathToFileURL(path.join(appPath, '../renderer/index.html')).href;
  const frame = { url: appUrl };
  const contents = { mainFrame: frame };
  const window = { webContents: contents, isDestroyed: () => false };
  const event = { sender: contents, senderFrame: frame };
  let response = 1;
  let confirmations = 0;
  let lookups = 0;
  const actions = [];
  let savePath;
  const promises = new Proxy(fs.promises, {
    get(target, key) {
      if (key === 'realpath') return (...args) => { lookups++; return target.realpath(...args); };
      return target[key];
    },
  });
  const electron = {
    app: { isPackaged: true, requestSingleInstanceLock: () => true, getVersion: () => 'test', on() {}, whenReady: () => new Promise(() => {}) },
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    protocol: { registerSchemesAsPrivileged() {} },
    dialog: { showMessageBox: async () => { confirmations++; return { response }; }, showSaveDialog: async () => ({ canceled: !savePath, filePath: savePath }) },
    shell: { openPath: async p => { actions.push(['open', p]); return ''; }, showItemInFolder: p => actions.push(['reveal', p]) },
    clipboard: { writeText: text => actions.push(['copy', text]) },
  };
  const input = fs.readFileSync(path.join(__dirname, '../main/index.ts'), 'utf8');
  const code = buildSync({
    stdin: { contents: input + '\nexport const harness = { setupIPC, authorizePath, isAuthorizedPath, isAllowedAppUrl, init(w,b) { mainWindow=w; bridge=b; engineStatus="ready"; } };', resolveDir: path.join(__dirname, '../main'), loader: 'ts' },
    bundle: true, platform: 'node', format: 'cjs', write: false,
    external: ['electron', 'electron-updater'],
  }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module, exports: module.exports, __dirname: appPath, URL, Buffer, console, setTimeout, clearTimeout,
    process: { ...process, resourcesPath: appPath, on() {} },
    require: (name) => name === 'electron' ? electron : name === 'electron-updater' ? { autoUpdater: {} } : name === 'fs' ? { ...fs, promises } : require(name),
  });
  const api = module.exports.harness;
  api.init(window, { call: async (_method, params) => params });
  api.setupIPC();
  return {
    ...api, appUrl, event,
    actions, saveTo: p => { savePath = p; },
    invoke: (channel, arg, sender = event) => handlers.get(channel)(sender, arg),
    call: (method, params, sender = event) => handlers.get('engine:call')(sender, method, params),
    drop: (paths) => handlers.get('fs:authorizePaths')(event, paths),
    accept: () => { response = 0; },
    counts: () => ({ confirmations, lookups }),
  };
}

async function fixture(run) {
  const root = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'toolbox-security-'));
  try { await run(root); } finally { await fs.promises.rm(root, { recursive: true, force: true }); }
}

test('PDF batch rejects objects, scalars and nested lists before dispatch', async () => {
  const h = mainHarness();
  for (const pdf_paths of [{ 'C:\\private.pdf': true }, 'C:\\private.pdf', [['C:\\private.pdf']], [false]]) {
    await assert.rejects(h.call('pdf_split.preview_many', { pdf_paths, config: {} }));
  }
});

test('result actions only open authorized existing documents and copy explicit text', async () => fixture(async root => {
  const h = mainHarness();
  const pdf = path.join(root, 'result.pdf'), exe = path.join(root, 'unsafe.exe');
  fs.writeFileSync(pdf, '%PDF'); fs.writeFileSync(exe, 'exe');
  await assert.rejects(async () => h.invoke('document:open', pdf));
  await h.authorizePath(root);
  await h.invoke('document:open', pdf); await h.invoke('document:reveal', pdf);
  await h.invoke('clipboard:writeText', pdf);
  assert.deepEqual(h.actions.map(a => a[0]), ['open', 'reveal', 'copy']);
  assert.equal(h.actions[2][1], pdf);
  for (const p of [exe, root, path.join(root, 'missing.pdf'), '\\\\unselected.invalid\\share\\file.pdf']) {
    await assert.rejects(async () => h.invoke('document:open', p));
  }
  await assert.rejects(async () => h.invoke('document:open', pdf, { ...h.event, senderFrame: { url: h.appUrl } }));
  await assert.rejects(async () => h.invoke('clipboard:writeText', {}));
}));

test('full TXT export copies the source file, supports cancel, and rejects other types', async () => fixture(async root => {
  const h = mainHarness(), txt = path.join(root, 'full.txt'), target = path.join(root, 'copy.txt');
  fs.writeFileSync(txt, '完整文字\n' + 'abc'.repeat(2000000)); await h.authorizePath(txt);
  assert.equal((await h.invoke('document:saveTextCopy', txt)).saved, false);
  h.saveTo(target); assert.equal((await h.invoke('document:saveTextCopy', txt)).saved, true);
  assert.deepEqual(fs.readFileSync(target), fs.readFileSync(txt));
  await assert.rejects(async () => h.invoke('document:saveTextCopy', path.join(root, 'result.pdf')));
  fs.renameSync(txt, txt + '.old'); fs.writeFileSync(txt, 'replacement');
  await assert.rejects(async () => h.invoke('document:open', txt));
}));

test('native extracted image formats can be opened and revealed without allowing executables', async () => fixture(async root => {
  const h = mainHarness(); await h.authorizePath(root);
  for (const ext of ['jpx', 'jp2', 'jbig2', 'jb2', 'pnm', 'pam', 'pbm', 'pgm', 'ppm']) {
    const file = path.join(root, `extracted.${ext}`); fs.writeFileSync(file, 'image fixture');
    await h.invoke('document:open', file); await h.invoke('document:reveal', file);
    assert.equal(h.actions.at(-1)[1], file);
  }
  const executable = path.join(root, 'bad.exe'); fs.writeFileSync(executable, 'exe');
  await assert.rejects(h.invoke('document:open', executable));
}));

test('workbench validates every input and the nested output directory', async () => fixture(async (root) => {
  const h = mainHarness();
  const input = path.join(root, 'input.pdf');
  await fs.promises.writeFile(input, 'test');
  await h.authorizePath(input);
  for (const params of [
    { files: [[input]], options: {} },
    { files: [input], options: { output_dir: root } },
    { files: [path.join(root, 'unselected.pdf')], options: {} },
    { files: [input], options: [] },
  ]) await assert.rejects(h.call('pdf_tools.run', { action: 'assemble', ...params }));
  const result = await h.call('pdf_tools.run', { action: 'inspect', files: [input], options: {}, _input_identities: { hacked: true } });
  assert.ok(result._input_identities[input].canonical);
  assert.equal(result._input_identities.hacked, undefined);
}));

test('workbench accepts 3000 authorized inputs and rejects 3001', async () => fixture(async (root) => {
  const h = mainHarness();
  const file = path.join(root, 'selected.pdf');
  fs.writeFileSync(file, '%PDF scale fixture');
  await h.authorizePath(file);
  const accepted = await h.call('pdf_tools.run', { files: Array(3000).fill(file), action: 'inspect', options: {} });
  assert.equal(accepted.files.length, 3000);
  await assert.rejects(h.call('pdf_tools.run', { files: Array(3001).fill(file), action: 'inspect', options: {} }), /3000/);
}));

test('output directory types are rejected, including false and zero', async () => fixture(async (root) => {
  const h = mainHarness();
  const input = path.join(root, 'input.pdf');
  await fs.promises.writeFile(input, 'test');
  await h.authorizePath(input);
  for (const output_dir of [42, true, false, 0, [], {}]) {
    await assert.rejects(h.call('pdf_split.preview', { pdf_path: input, config: { output_dir } }));
  }
  const result = await h.call('pdf_split.preview', { pdf_path: input, config: { output_dir: '' } });
  assert.equal(result.pdf_path, input);
}));

test('unselected UNC paths are rejected without filesystem resolution', async () => {
  const h = mainHarness();
  assert.equal(await h.isAuthorizedPath('\\\\unselected.invalid\\share\\private.pdf'), false);
  assert.equal(h.counts().lookups, 0);
});

test('a nonexistent output under an escaping junction is denied; an ordinary new child is allowed', async () => fixture(async (root) => {
  const h = mainHarness();
  const selected = path.join(root, 'selected');
  const privateDir = path.join(root, 'private');
  await fs.promises.mkdir(selected); await fs.promises.mkdir(privateDir);
  await fs.promises.symlink(privateDir, path.join(selected, 'escape'), 'junction');
  await h.authorizePath(selected);
  assert.equal(await h.isAuthorizedPath(path.join(selected, 'new', 'child')), true);
  assert.equal(await h.isAuthorizedPath(path.join(selected, 'escape', 'new')), false);
}));

test('only the packaged document and its main frame can call IPC', async () => {
  const h = mainHarness();
  assert.equal(h.isAllowedAppUrl(h.appUrl + '#panel'), true);
  assert.equal(h.isAllowedAppUrl('file:///C:/Downloads/foreign.html'), false);
  await assert.rejects(h.call('ping', {}, { ...h.event, senderFrame: { url: h.appUrl } }));
  h.event.senderFrame.url = 'file:///C:/Downloads/foreign.html';
  await assert.rejects(h.call('ping', {}));
});

test('raw renderer paths need native consent; cancellation never grants access', async () => fixture(async (root) => {
  const h = mainHarness();
  const file = path.join(root, 'dropped.pdf');
  await fs.promises.writeFile(file, 'test');
  assert.equal((await h.drop([file])).length, 0);
  assert.equal(await h.isAuthorizedPath(file), false);
  assert.equal(h.counts().confirmations, 1);
  h.accept();
  assert.equal((await h.drop([file]))[0], file);
  assert.equal(await h.isAuthorizedPath(file), true);
}));

test('replacing a selected file invalidates its grant', async () => fixture(async (root) => {
  const h = mainHarness();
  const file = path.join(root, 'selected.pdf');
  await fs.promises.writeFile(file, 'original');
  await h.authorizePath(file);
  await fs.promises.rename(file, file + '.old');
  await fs.promises.writeFile(file, 'private');
  await assert.rejects(h.call('pdf_split.validate', { pdf_path: file }));
}));

test('renderer cannot supply the input identities used by deferred jobs', async () => fixture(async (root) => {
  const h = mainHarness();
  const file = path.join(root, 'selected.pdf');
  await fs.promises.writeFile(file, 'normal');
  await h.authorizePath(file);
  const result = await h.call('pdf_split.execute_async', { pdf_paths: [file], config: {}, _input_identities: { forged: { ino: '0' } } });
  assert.equal(result._input_identities.forged, undefined);
  assert.equal(result._input_identities[file].ino, String((await fs.promises.stat(file, { bigint: true })).ino));
  assert.equal(result._input_identities[file].canonical, await fs.promises.realpath(file));
}));
