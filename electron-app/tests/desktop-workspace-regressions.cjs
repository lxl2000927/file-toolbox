// Real Electron main/preload/Vue/Python. Inject only a temporary workspace read
// failure and native dialog responses; all edits use the rendered controls.
const { app, BrowserWindow, dialog } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const run = process.env.FILE_TOOLBOX_WORKSPACE_REGRESSION_ROOT;
if (!run || !path.isAbsolute(run)) throw Error('Use run-desktop-workspace-regressions.cjs');
const profile = path.join(run, 'profile'), inputs = path.join(run, 'inputs');
process.env.APPDATA = profile; app.setPath('appData', profile); app.setPath('userData', path.join(profile, 'electron'));
app.disableHardwareAcceleration();
const savedPath = path.join(profile, 'FileToolbox/workspaces/workbench.json');
const original = JSON.stringify({ version: 1, savedAt: new Date().toISOString(), identities: [],
  state: { version: 1, sources: [], pages: [], settings: { filename: '恢复前保留的设置' } } });
fs.mkdirSync(path.dirname(savedPath), { recursive: true }); fs.writeFileSync(savedPath, original);
let readBlocked = true, finished = false, window;
const readFile = fs.promises.readFile.bind(fs.promises);
fs.promises.readFile = async (file, ...args) => {
  if (readBlocked && path.resolve(String(file)).toLowerCase() === savedPath.toLowerCase()) throw Object.assign(Error('temporary workspace EACCES'), { code: 'EACCES' });
  return readFile(file, ...args);
};
const report = { passed: false, checks: [], rendererErrors: [], nativeMessages: [] }, children = [];
const originalSpawn = cp.spawn;
cp.spawn = function(command, args, options) { const child = originalSpawn.apply(this, arguments);
  if (args?.some(arg => /[\\/]engine[\\/]server\.py$/.test(arg)) || /file-toolbox-engine\.exe$/i.test(command)) children.push(child);
  return child;
};
const cleanup = () => { for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill(); };
process.on('exit', cleanup);
dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [path.join(inputs, 'front.pdf'), path.join(inputs, 'back.pdf')] });
dialog.showMessageBox = async (_window, options) => { report.nativeMessages.push(options); return { response: 0 }; };
app.on('browser-window-created', (_event, win) => { win.show = () => win.showInactive(); win.webContents.setBackgroundThrottling(false); });
require('../dist/main/index.js');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const js = code => window.webContents.executeJavaScript(code);
async function until(code, timeout = 30000) {
  const start = Date.now(); while (Date.now() - start < timeout) { if (await js(code)) return; await delay(100); }
  throw Error(`Timed out: ${code}\n${await js('document.body.innerText')}`);
}
async function click(text, selector = 'button') {
  await js(`(() => { const e=[...document.querySelectorAll(${JSON.stringify(selector)})].find(e=>e.textContent.trim()===${JSON.stringify(text)}); if(!e || e.disabled) throw Error('Unavailable '+${JSON.stringify(text)}); e.click(); })()`);
  await delay(160);
}
async function input(selector, value) {
  await js(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); if(!e)throw Error('Missing input'); e.value=${JSON.stringify(value)}; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); })()`);
  await delay(40);
}
function saveReport() { fs.writeFileSync(path.join(run, 'report.json'), JSON.stringify(report, null, 2)); }
async function fail(error) {
  if (finished) return; finished = true; clearTimeout(watchdog); report.error = error.stack || String(error);
  console.error(error); try { fs.writeFileSync(path.join(run, 'failure.png'), (await window.webContents.capturePage()).toPNG()); } catch {}
  saveReport(); cleanup(); app.exit(1);
}
const watchdog = setTimeout(() => void fail(Error('Workspace desktop watchdog expired')), 120000);
app.whenReady().then(async () => {
  try {
    while (!BrowserWindow.getAllWindows().length) await delay(50);
    window = BrowserWindow.getAllWindows()[0];
    window.webContents.on('console-message', details => { if (details.level === 'error') report.rendererErrors.push(details.message); });
    await until("document.querySelector('.status-text')?.textContent.includes('已就绪')");
    if (await js("document.querySelector('.nav-toggle')?.getAttribute('aria-expanded')==='false'")) await js("document.querySelector('.nav-toggle').click()");
    await click('PDF 工作台', '.nav-btn');
    await until("[...document.querySelectorAll('.wb-message button')].some(e=>e.textContent.trim()==='重试恢复')");
    assert.equal(await js("document.querySelector('#wb-filename').disabled"), true);
    window.close();
    for (let tries = 0; !report.nativeMessages.length && tries < 100; tries++) await delay(50);
    assert.equal(window.isDestroyed(), false); assert.match(report.nativeMessages[0]?.message || '', /关闭已取消/);
    assert.equal(fs.readFileSync(savedPath, 'utf8'), original);
    report.checks.push('failed restore locks editing, rejects actual native close, and preserves unread disk state');
    readBlocked = false; await click('重试恢复');
    await until("document.querySelector('#wb-filename')?.value==='恢复前保留的设置' && !document.querySelector('#wb-filename').disabled");
    report.checks.push('retry recovery restores the previously unread settings without reopening the app');
    await click('添加 PDF / 图片'); await until("document.querySelectorAll('.page-card img').length===6");
    await js("document.querySelector('.bulk-tools').open=true; document.querySelectorAll('.page-top input')[3].click()");
    await input('#bulk-position', 2); await click('移动所选');
    await until("document.querySelectorAll('.page-caption')[1]?.textContent.includes('back.pdf')");
    await js("document.querySelector('.source-navigator').open=true; document.querySelector('.source-name').click()");
    await until("document.querySelectorAll('.page-card').length===3");
    await js("document.querySelector('.page-top input').click()");
    await js("document.querySelectorAll('.page-top input')[2].dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true}))");
    await until("document.querySelector('.selection-toolbar').textContent.includes('已选 3 页')");
    await click('旋转 90°', '.selection-toolbar button');
    await click('全部页面', '.source-controls button'); await until("document.querySelectorAll('.page-card').length===6");
    assert.equal(await js("[...document.querySelectorAll('.page-caption')].filter(e=>e.textContent.includes('back.pdf')).some(e=>e.textContent.includes('90°'))"), false);
    report.checks.push('filtered Shift selection rotates only visible source pages despite an interleaved hidden page');
    await click('撤销', '.selection-toolbar button'); await click('撤销', '.selection-toolbar button');
    await js("document.querySelectorAll('.source-name')[1].click()"); await until("document.querySelectorAll('.page-card').length===3");
    await click('全选', '.selection-toolbar button');
    assert.equal(await js("document.querySelector('.selection-toolbar').textContent.includes('已选 3 页')"), true);
    await click('删除', '.selection-toolbar button');
    await until("document.querySelectorAll('.page-card').length===3 && [...document.querySelectorAll('.page-caption')].every(e=>e.textContent.includes('front.pdf'))");
    report.checks.push('filtered select all deletes only that source and returns to the remaining visible pages');
    await click('全选', '.selection-toolbar button'); await click('删除', '.selection-toolbar button');
    await until("document.querySelectorAll('.page-card').length===0");
    await click('清空', '.workspace-header button'); await click('添加 PDF / 图片');
    await until("document.querySelectorAll('.page-card img').length===6");
    assert.equal(await js("document.querySelectorAll('.source-problem').length"), 0);
    report.checks.push('clear and native reimport show all six pages without stale source problems');
    fs.writeFileSync(path.join(run, 'restored-and-reimported.png'), (await window.webContents.capturePage()).toPNG());
    window.once('closed', () => {
      try { const saved = JSON.parse(fs.readFileSync(savedPath, 'utf8')); assert.equal(saved.state.pages.length, 6);
        assert.equal(saved.state.settings.filename, '恢复前保留的设置'); report.checks.push('final native close persists the recovered and reimported workspace');
        report.passed = true; finished = true; clearTimeout(watchdog); saveReport(); console.log(`PASS workspace desktop: ${report.checks.length} checks`);
      } catch (error) { report.error = String(error); saveReport(); cleanup(); app.exit(1); }
    });
    window.close();
  } catch (error) { await fail(error); }
});
