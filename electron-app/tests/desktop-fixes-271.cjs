// Actual Electron + preload + Python regression. Each run owns its data/files.
const { app, BrowserWindow, dialog } = require('electron');
const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const evidence = path.join(root, 'acceptance-samples/release-2.7.1');
fs.mkdirSync(evidence, { recursive: true });
const dir = fs.mkdtempSync(path.join(evidence, 'main-regressions-'));
const original = path.join(dir, 'original.txt');
fs.writeFileSync(original, 'test document');
process.env.APPDATA = path.join(dir, 'data'); fs.mkdirSync(process.env.APPDATA);
app.setPath('appData', process.env.APPDATA); app.setPath('userData', path.join(dir, 'profile'));
app.disableHardwareAcceleration();
const children = [], spawn = cp.spawn;
cp.spawn = function (command, args, options) {
  const child = spawn.apply(this, arguments);
  if (args?.some(arg => /[\\/]engine[\\/]server\.py$/.test(arg))) children.push(child);
  return child;
};
process.on('exit', () => { for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill(); });
const report = { passed: false, checks: [], messages: [] };
dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [original] });
dialog.showMessageBox = async (_, options) => { report.messages.push(options); return { response: 0 }; };
require(path.join(root, 'electron-app/dist/main/index.js'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const watchdog = setTimeout(() => { console.error('Regression timeout'); app.exit(2); }, 45000);
(async () => {
  await app.whenReady(); let window;
  for (let i = 0; i < 200; i++) {
    window = BrowserWindow.getAllWindows()[0];
    try { if (window && await window.webContents.executeJavaScript("document.querySelector('.status-text')?.textContent.includes('已就绪')")) break; } catch {}
    await delay(100);
  }
  assert.ok(window);
  const result = await window.webContents.executeJavaScript(`(async () => {
    const files = await window.electronAPI.openFileDialog({ multi: true });
    const renamed = await window.engine.rename.execute(files, [{ type: 'insert_text', text: 'renamed_' }], 'overwrite', '');
    const next = renamed.operations.find(operation => operation.success).new_path;
    const preview = await window.engine.rename.preview([next], [{ type: 'insert_text', text: 'again_' }]);
    const second = await window.engine.rename.execute([next], [{ type: 'insert_text', text: 'again_' }], 'overwrite', '');
    const undo = await window.engine.rename.undo(second.undo_token);
    const restoredPreview = await window.engine.rename.preview([next], []);
    return { next, preview, second, undo, restoredPreview };
  })()`);
  assert.equal(result.preview.length, 1); assert.equal(result.second.successful, 1);
  assert.equal(result.undo.failed.length, 0); assert.equal(result.restoredPreview.length, 1);
  assert.ok(fs.existsSync(result.next)); assert.equal(fs.existsSync(original), false);
  report.checks.push('native selection / rename / preview / rename again / undo / preview');
  const gone = new Promise(resolve => window.webContents.once('render-process-gone', (_, details) => resolve(details)));
  window.webContents.forcefullyCrashRenderer(); report.crash = await gone;
  const started = Date.now();
  const closed = new Promise(resolve => window.once('closed', resolve)); window.close(); await closed;
  report.closeMs = Date.now() - started;
  assert.ok(report.closeMs < 5000); assert.equal(report.messages.length, 0);
  report.checks.push('crashed renderer closes promptly without cancelled-close dialog');
  report.passed = true;
  fs.writeFileSync(path.join(evidence, 'main-regressions.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report)); clearTimeout(watchdog);
})().catch(error => { console.error(error); clearTimeout(watchdog); app.exit(1); });
