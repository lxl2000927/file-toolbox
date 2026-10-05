// Real main/preload/renderer/Python integration. Only the native file dialog and
// child-process spawn boundary are instrumented; processing is never mocked.
const { app, BrowserWindow, dialog } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const phase = Number(process.argv.find(arg => arg.startsWith('--phase='))?.split('=')[1]);
const run = process.env.FILE_TOOLBOX_RELIABLE_ROOT;
if (!run || !path.isAbsolute(run) || ![1, 2].includes(phase)) throw Error('Run through run-desktop-reliable.cjs');
const profile = path.join(run, 'profile'), inputs = path.join(run, 'inputs'), outputs = path.join(run, 'outputs');
process.env.APPDATA = profile;
app.setPath('appData', profile);
app.setPath('userData', path.join(profile, 'electron'));
app.disableHardwareAcceleration();
const report = { phase, passed: false, checks: [], timings: {}, children: [], nativeDialogs: [], rendererMessages: [] };
const trackedChildren = [];
const originalSpawn = cp.spawn;
cp.spawn = function (command, args, options) {
  const child = originalSpawn.apply(this, arguments);
  if (args?.some(arg => /[\\/]engine[\\/]server\.py$/.test(arg)) || /file-toolbox-engine\.exe$/i.test(command)) {
    const preview = options?.env?.FILE_TOOLBOX_PREVIEW_ENGINE === '1';
    const entry = { pid: child.pid, preview, exited: false, spawnedAt: new Date().toISOString() };
    trackedChildren.push({ child, entry }); report.children.push(entry);
    child.once('exit', (code, signal) => { entry.exited = true; entry.code = code; entry.signal = signal; });
  }
  return child;
};
function cleanupChildren() {
  // Never enumerate system processes. These exact ChildProcess objects were
  // created by this test instance's real PythonBridge.
  for (const { child } of trackedChildren) if (child.exitCode === null && child.signalCode === null) child.kill();
}
process.on('exit', cleanupChildren);
let chosen = [path.join(inputs, 'front.pdf'), path.join(inputs, 'back.pdf')];
dialog.showOpenDialog = async (_window, options) => {
  const filePaths = options.properties.includes('openDirectory') ? [outputs] : [...chosen];
  report.nativeDialogs.push({ title: options.title || '', directory: options.properties.includes('openDirectory'), filePaths });
  return { canceled: false, filePaths };
};
// A production close-flush error must fail this acceptance run, not hang behind
// an unattended native message box.
dialog.showMessageBox = async (_window, options) => {
  report.unexpectedNativeMessage = { title: options.title, message: options.message, detail: options.detail };
  throw Error(`Unexpected native message: ${options.message}`);
};
app.on('browser-window-created', (_event, window) => { window.show = () => window.showInactive(); window.webContents.setBackgroundThrottling(false); });
require('../dist/main/index.js');
let window, finished = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const watchdog = setTimeout(() => { void fail(Error('Reliable desktop watchdog expired')); }, phase === 1 ? 150000 : 330000);
function writeReport() { fs.writeFileSync(path.join(run, `phase-${phase}.json`), JSON.stringify(report, null, 2)); }
app.on('will-quit', () => {
  if (!report.passed) return;
  const remaining = trackedChildren.filter(({ child }) => child.exitCode === null && child.signalCode === null);
  report.normalShutdownChildCleanup = remaining.length === 0;
  if (remaining.length) { report.passed = false; report.error = `Python children still live after normal shutdown: ${remaining.map(({ child }) => child.pid).join(', ')}`; process.exitCode = 1; }
  else report.checks.push('normal native shutdown waits for all captured primary and preview children to exit');
  writeReport();
});
async function js(code) { return window.webContents.executeJavaScript(code); }
async function until(code, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { if (await js(code)) return; await delay(100); }
  throw Error(`Timed out: ${code}\n${await js('document.body.innerText')}`);
}
async function waitFor(predicate, label, timeout = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { if (await predicate()) return; await delay(60); }
  throw Error(`Timed out: ${label}`);
}
async function click(text, selector = 'button') {
  await js(`(() => { const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(e=>e.textContent.trim()===${JSON.stringify(text)}); if(!el || el.disabled) throw Error('Unavailable button: '+${JSON.stringify(text)}); el.click(); })()`);
  await delay(140);
}
async function input(selector, value) {
  await js(`(() => { const el=document.querySelector(${JSON.stringify(selector)}); if(!el) throw Error('Missing input: '+${JSON.stringify(selector)}); el.value=${JSON.stringify(value)}; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); })()`);
  await delay(40);
}
async function screenshot(name) {
  if (!window || window.isDestroyed()) return;
  await delay(100);
  fs.writeFileSync(path.join(run, `phase-${phase}-${name}.png`), (await window.webContents.capturePage()).toPNG());
}
async function check(name, operation) { await operation(); report.checks.push(name); console.log(`PASS phase ${phase}: ${name}`); }
async function fail(error) {
  if (finished) return;
  finished = true; clearTimeout(watchdog);
  report.error = error.stack || String(error); report.passed = false;
  console.error(error);
  try { await screenshot('failure'); } catch (captureError) { report.captureError = String(captureError); }
  writeReport(); cleanupChildren(); app.exit(1);
}
function hash(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function completedPdfs() { return fs.readdirSync(outputs).filter(name => /^reliable-ocr_\d+\.pdf$/.test(name)).sort(); }
async function installToolHelpers() {
  await js(`(() => {
    window.__reliableEvents=[];
    window.engine.onNotification(event=>{ if(event.method.startsWith('task.') || event.method==='engine.status') window.__reliableEvents.push({method:event.method,params:{...event.params,result:undefined}}); });
    window.__reliableRun=(action,files,options,id)=>new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>{off();reject(Error('Tool timeout: '+action));},60000);
      const off=window.engine.onNotification(event=>{ if(event.method!=='task.complete' || event.params.task_id!==id)return; clearTimeout(timeout);off();event.params.ok ? resolve(event.params.result) : reject(Error(event.params.error || 'Tool failed')); });
      window.engine.pdfTools.run(action,files,options,id).catch(error=>{clearTimeout(timeout);off();reject(error);});
    });
  })()`);
}
async function tool(action, files, options = {}) {
  const id = `reliable-${action}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return js(`window.__reliableRun(${JSON.stringify(action)},${JSON.stringify(files)},${JSON.stringify(options)},${JSON.stringify(id)})`);
}
async function initialPhase() {
  await click('添加 PDF / 图片');
  await until("document.querySelectorAll('.page-card img').length===6");
  await check('real native imports produce six thumbnails and original page metadata', async () => {
    assert.equal(await js("document.querySelector('.workspace-status').textContent.includes('2 个来源')"), true);
    assert.equal(report.nativeDialogs.length, 1);
  });
  await input('#wb-filename', '重启保留最后编辑');
  await input('#wb-output-mode', 'chunks');
  await input('.option-section input[type=number][max="10000"]', 3);
  await js("document.querySelector('.page-top input').click()");
  await click('后移', '.selection-toolbar button');
  await check('page reorder changes the real workbench order before native close', async () => {
    assert.equal(await js("document.querySelector('.page-caption').textContent.includes('原第 2 页')"), true);
  });
  await screenshot('before-close');
  // No debounce wait and no direct persistence API: this edit is followed by
  // BrowserWindow.close(), exercising the production close/renderer handshake.
  await js("[...document.querySelectorAll('.selection-toolbar button')].find(e=>e.textContent.trim()==='旋转 90°').click()");
  const closedAt = Date.now();
  window.once('closed', () => {
    try {
      const stored = JSON.parse(fs.readFileSync(path.join(profile, 'FileToolbox/workspaces/workbench.json'), 'utf8'));
      assert.equal(stored.state.pages[0].uid, '0:1'); assert.equal(stored.state.pages[1].uid, '0:0');
      assert.equal(stored.state.pages[1].rotation, 90); assert.equal(stored.state.settings.filename, '重启保留最后编辑');
      assert.equal(stored.state.settings.chunk_pages, 3); assert.equal(stored.state.settings.output_mode, 'chunks');
      report.timings.nativeCloseFlushMs = Date.now() - closedAt;
      report.checks.push('native close immediately after rotate persists the final edit');
      report.persistedPages = stored.state.pages; report.persistedSettings = stored.state.settings;
      report.passed = true; finished = true; clearTimeout(watchdog); writeReport();
      console.log('PASS phase 1: immediate native close persisted final edit');
    } catch (error) { report.error = String(error); writeReport(); cleanupChildren(); app.exit(1); }
  });
  window.close();
}
async function restorePhase() {
  await until("document.querySelectorAll('.page-card').length===6 && document.querySelector('.wb-message.error')?.textContent.includes('2 份来源')");
  await check('actual relaunch restores ordering, rotation, parameters and classifies missing/replaced sources', async () => {
    const data = await js("window.electronAPI.workspace.load('workbench')");
    assert.deepEqual(data.state.pages.map(p => p.uid), ['0:1','0:0','0:2','1:0','1:1','1:2']);
    assert.equal(data.state.pages[1].rotation, 90);
    assert.deepEqual(data.sources.map(s => s.status), ['missing', 'changed']);
    assert.equal(await js("document.querySelector('#wb-filename').value"), '重启保留最后编辑');
    assert.equal(await js("document.querySelector('#wb-output-mode').value"), 'chunks');
    assert.equal(await js("document.querySelectorAll('.page-card img').length"), 0);
    await screenshot('missing-and-changed');
  });
  await js("document.querySelector('.source-navigator').open=true");
  await check('wrong relocation content is rejected without overwriting source identity or edits', async () => {
    chosen = [path.join(inputs, 'ocr.pdf')];
    await click('重新定位', '.source-row button');
    await until("document.querySelector('.wb-message.error')?.textContent.includes('重新定位失败')");
    const data = await js("window.electronAPI.workspace.load('workbench')");
    assert.equal(data.state.sources[0].path, path.join(inputs, 'front.pdf'));
    assert.equal(data.state.pages[1].rotation, 90);
  });
  await check('native relocation accepts identical content for both missing and same-path replaced sources', async () => {
    chosen = [path.join(inputs, 'relocated-front.pdf')]; await click('重新定位', '.source-row button');
    await until("document.querySelectorAll('.source-row .source-problem').length===1");
    await until("![...document.querySelectorAll('.source-row button')].some(e=>e.textContent.trim()==='重新定位' && e.disabled)");
    chosen = [path.join(inputs, 'relocated-back.pdf')]; await click('重新定位', '.source-row button');
    await until("document.querySelectorAll('.source-row .source-problem').length===0 && document.querySelectorAll('.page-card img').length===6");
    assert.equal(await js("document.querySelectorAll('.page-caption')[1].textContent.includes('90°')"), true);
    await screenshot('relocated');
  });
  await check('source search and original-page jump navigate the restored reordered workspace', async () => {
    await input('[aria-label="搜索来源"]', 'relocated-front');
    assert.equal(await js("document.querySelectorAll('.source-row').length"), 1);
    await js("document.querySelector('.source-name').click()");
    await until("document.querySelectorAll('.page-card').length===3");
    await input('[aria-label="来源原页码"]', 1); await click('定位原页');
    assert.equal(await js("Number(document.querySelector('[aria-label=\"工作区页码\"]').value)"), 2);
    await until("document.querySelectorAll('.page-card').length===6");
    await input('[aria-label="工作区页码"]', 6); await click('跳转', '.source-jumps button');
    assert.ok(await js("document.querySelectorAll('.page-card').length<=12"));
  });
  await installToolHelpers();
  chosen = [path.join(inputs, 'ocr.pdf')];
  const authorized = await js("window.electronAPI.openFileDialog({multi:false,title:'Reliable OCR source'})");
  assert.equal(authorized[0], chosen[0]);
  const authorizedOutput = await js("window.electronAPI.openDirectoryDialog({title:'Reliable OCR output'})");
  assert.equal(authorizedOutput, outputs);
  const inspected = await tool('inspect', authorized, { compact_inspect: true });
  assert.equal(inspected.ocr.available, true, 'Offline OCR data must be available');
  const signature = inspected.sources[0].signature;
  const taskId = `reliable-long-${Date.now()}`;
  const options = { pages: Array.from({ length: 16 }, () => ({ source: 0, index: 0, rotation: 0 })), signatures: [signature],
    output_dir: outputs, filename: 'reliable-ocr', output_mode: 'chunks', chunk_pages: 1, compression: 'lossless', ocr: true, ocr_text: true, language: 'chi_sim+eng', dpi: 150 };
  await js(`window.engine.pdfTools.run('assemble',${JSON.stringify(authorized)},${JSON.stringify(options)},${JSON.stringify(taskId)})`);
  await click('任务中心', '.nav-btn');
  await until("document.querySelector('.task-center') && document.querySelector('.task-card[data-state=running]')");
  await waitFor(() => completedPdfs().length > 0, 'at least one committed OCR PDF', 90000);
  await js(`window.engine.tasks.pause(${JSON.stringify(taskId)})`);
  await until(`(async()=> (await window.engine.tasks.list()).tasks.some(t=>t.task_id===${JSON.stringify(taskId)} && t.state==='paused'))()`, 90000);
  await check('long real OCR task pauses at a safe boundary with committed output retained', async () => {
    const tasks = await js('window.engine.tasks.list()');
    const current = tasks.tasks.find(t => t.task_id === taskId); assert.equal(current.state, 'paused');
    assert.ok(completedPdfs().length > 0 && completedPdfs().length < 16);
    await click('刷新列表'); await until("document.querySelector('.task-card[data-state=paused]')");
    await screenshot('task-paused'); report.pausedTask = current;
  });
  await check('thumbnails and inspection remain responsive in a separate long-lived preview process while OCR is paused', async () => {
    const previewBefore = trackedChildren.find(entry => entry.entry.preview && !entry.entry.exited);
    const primary = trackedChildren.find(entry => !entry.entry.preview && !entry.entry.exited);
    assert.ok(previewBefore && primary && previewBefore.child.pid !== primary.child.pid, 'Two real distinct Python children required');
    const start = Date.now();
    const preview = await tool('thumbnails', authorized, { signatures: [signature], pages: [{ source: 0, index: 0 }], thumbnail_size: 300 });
    const metadata = await tool('inspect', authorized, { compact_inspect: true });
    report.timings.previewDuringPauseMs = Date.now() - start;
    assert.ok(preview.thumbnails[0].data_url.startsWith('data:image/')); assert.equal(metadata.sources[0].page_count, 1);
    assert.ok(report.timings.previewDuringPauseMs < 15000, 'Preview should not wait on paused processing');
    assert.equal(trackedChildren.filter(entry => entry.entry.preview && !entry.entry.exited).length, 1);
    assert.equal((await js('window.engine.tasks.list()')).tasks.find(t => t.task_id === taskId).state, 'paused');
  });
  await check('continue resumes paused OCR and a second pause retains the next complete output unit', async () => {
    const previous = completedPdfs().length;
    await js(`window.engine.tasks.resume(${JSON.stringify(taskId)})`);
    await waitFor(() => completedPdfs().length > previous, 'next output after resume', 60000);
    await js(`window.engine.tasks.pause(${JSON.stringify(taskId)})`);
    await until(`(async()=> (await window.engine.tasks.list()).tasks.some(t=>t.task_id===${JSON.stringify(taskId)} && t.state==='paused'))()`, 60000);
    assert.ok(completedPdfs().length > previous && completedPdfs().length < 16);
    report.completedUnitsBeforeCrash = completedPdfs().length;
  });
  const retained = Object.fromEntries(fs.readdirSync(outputs).filter(name => /\.(pdf|txt)$/.test(name)).map(name => {
    const file = path.join(outputs, name); return [name, { hash: hash(file), mtimeMs: fs.statSync(file).mtimeMs }];
  }));
  assert.ok(Object.keys(retained).some(name => name.endsWith('.txt')), 'Pause must preserve TXT companions too');
  await check('killing only the captured primary engine produces interruption and exposes manual retry after restart', async () => {
    const primary = trackedChildren.find(entry => !entry.entry.preview && !entry.entry.exited);
    assert.ok(primary); report.killedPrimaryPid = primary.child.pid;
    assert.equal(primary.child.kill(), true);
    await until("(async()=> (await window.engine.status()).status==='error')()", 15000);
    await js('window.electronAPI.restartEngine()');
    await until("(async()=> (await window.engine.status()).status==='ready')()", 30000);
    const tasks = await js('window.engine.tasks.list()');
    const interrupted = tasks.tasks.find(t => t.task_id === taskId);
    assert.equal(interrupted.state, 'interrupted'); assert.equal(interrupted.can_retry, true);
    report.interruptedTask = interrupted;
    await click('刷新列表'); await until("document.querySelector('.task-card[data-state=interrupted]')");
    await screenshot('task-interrupted');
  });
  await check('journal-authorized retry resumes remaining OCR units and does not duplicate completed PDF or TXT outputs', async () => {
    const resumed = await js(`window.engine.tasks.retry(${JSON.stringify(taskId)})`);
    report.retryTaskId = resumed.task_id;
    await until(`(async()=>{const task=(await window.engine.tasks.list()).tasks.find(t=>t.task_id===${JSON.stringify(resumed.task_id)}); if(task && ['failed','cancelled'].includes(task.state))throw Error(JSON.stringify(task)); return task?.state==='completed';})()`, 180000);
    const files = fs.readdirSync(outputs).sort();
    const pdfs = files.filter(name => name.endsWith('.pdf')), txts = files.filter(name => name.endsWith('.txt'));
    assert.equal(pdfs.length, 16, JSON.stringify(files)); assert.equal(txts.length, 16, JSON.stringify(files));
    assert.deepEqual(pdfs, Array.from({ length: 16 }, (_, i) => `reliable-ocr_${String(i + 1).padStart(3, '0')}.pdf`));
    for (const [name, saved] of Object.entries(retained)) {
      const file = path.join(outputs, name); assert.equal(hash(file), saved.hash, `Completed content changed: ${name}`);
      assert.equal(fs.statSync(file).mtimeMs, saved.mtimeMs, `Completed file was rewritten: ${name}`);
    }
    for (const name of txts) assert.match(fs.readFileSync(path.join(outputs, name), 'utf8').replace(/\s/g, ''), /文件工具箱/);
    // Use the real engine to parse all 16 generated PDFs after native directory
    // selection has granted their containing output directory.
    const verified = await tool('inspect', pdfs.map(name => path.join(outputs, name)), { compact_inspect: true });
    assert.equal(verified.sources.length, 16); assert.ok(verified.sources.every(source => source.page_count === 1));
    report.outputFiles = files; report.retainedOutputFiles = retained;
    await click('刷新列表'); await screenshot('task-complete');
  });
  report.passed = true; finished = true; clearTimeout(watchdog);
  writeReport(); console.log(`PASS phase 2: ${report.checks.length} reliable desktop checks`);
  // A final normal close also validates that source-repair saves can flush.
  window.once('closed', () => { writeReport(); });
  window.close();
}
app.whenReady().then(async () => {
  try {
    await waitFor(() => BrowserWindow.getAllWindows().length > 0, 'main window', 10000);
    window = BrowserWindow.getAllWindows()[0];
    window.webContents.on('console-message', details => { if (['warning', 'error'].includes(details.level) && !details.message.includes('Electron Security Warning')) report.rendererMessages.push(details.message); });
    await until("document.querySelector('.status-text')?.textContent.includes('已就绪')", 30000);
    if (await js("document.querySelector('.nav-toggle')?.getAttribute('aria-expanded')==='false'")) await js("document.querySelector('.nav-toggle').click()");
    await click('PDF 工作台', '.nav-btn');
    if (phase === 1) await initialPhase(); else await restorePhase();
  } catch (error) { await fail(error); }
});
