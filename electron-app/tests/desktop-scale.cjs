// Real main/preload/renderer/Python; native file selection alone is automated.
const { app, BrowserWindow, dialog } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const samples = path.join(root, 'acceptance-samples');
const evidence = path.join(samples, 'scale-evidence');
const profile = fs.mkdtempSync(path.join(samples, 'scale-profile-'));
fs.mkdirSync(evidence, { recursive: true }); fs.mkdirSync(profile, { recursive: true });
process.env.APPDATA = profile; app.setPath('userData', profile);
app.disableHardwareAcceleration();
let chosen = [];
dialog.showOpenDialog = async () => ({ canceled: false, filePaths: chosen });
app.on('browser-window-created', (_event, win) => { win.show = () => win.showInactive(); win.webContents.setBackgroundThrottling(false); });
require('../dist/main/index.js');
let win;
const errors = [], timings = [];
const delay = ms => new Promise(r => setTimeout(r, ms));
const js = async code => {
  try { return await win.webContents.executeJavaScript(code); }
  catch (error) { throw Error(`${error}\nScript: ${code}\nRenderer: ${errors.join('\n')}`); }
};
async function until(code, timeout = 120000) {
  const start = performance.now();
  while (performance.now() - start < timeout) { if (await js(code)) return; await delay(50); }
  throw Error(`Timed out ${code}\n${await js('document.body.innerText')}`);
}
async function click(text) {
  await until(`(() => { const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}); return b && !b.disabled; })()`, 20000);
  await js(`(() => { const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}); if(!b || b.disabled) throw Error('Unavailable button'); b.click(); })()`);
}
async function jump(batch) {
  await js(`(() => { const e=document.querySelector('[aria-label="页面组"]'); e.value=${batch}; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); })()`);
}
async function clear() {
  await click('清空'); await until(`document.querySelector('[role="dialog"]')`, 5000);
  await js(`document.querySelector('[role="dialog"] .btn-primary').click()`);
  await until(`document.querySelector('.workbench-empty')`, 5000);
}
app.whenReady().then(async () => {
  try {
    win = BrowserWindow.getAllWindows()[0]; win.setSize(1280, 820);
    win.webContents.on('console-message', details => { if (details.level === 'error') errors.push(details.message); });
    await until(`document.querySelector('.status-text')?.textContent.includes('已就绪')`);
    await click('PDF 工作台');
    for (const count of [1000, 3000]) {
      chosen = Array.from({ length: count }, (_, i) => path.join(samples, 'scale-inputs', `document-${String(i + 1).padStart(4, '0')}.pdf`));
      await js(`window.__scaleLongTasks=[]; window.__scaleObserver=new PerformanceObserver(list=>window.__scaleLongTasks.push(...list.getEntries().map(e=>e.duration))); window.__scaleObserver.observe({type:'longtask',buffered:false});`);
      const start = performance.now(); await click('添加 PDF / 图片');
      await until(`document.querySelectorAll('.page-card img').length===12`);
      const firstPreview = performance.now() - start;
      await until(`document.querySelector('.workspace-status')?.textContent.includes('${count} 个来源') && !document.querySelector('.export-footer progress')`);
      const imported = performance.now() - start;
      const batches = Math.ceil(count * 3 / 12);
      const jumpStart = performance.now(); await jump(batches);
      await until(`document.querySelector('.page-caption')?.textContent.includes('document-${String(count - 3).padStart(4, '0')}.pdf') && document.querySelectorAll('.page-card img').length===12`);
      const lastPreview = performance.now() - jumpStart;
      const revisit = performance.now(); await jump(1);
      await until(`document.querySelector('.page-caption')?.textContent.includes('document-0001.pdf') && document.querySelectorAll('.page-card img').length===12`);
      const cachedPreview = performance.now() - revisit;
      for (const batch of [2, 100, 3, batches]) { await jump(batch); await delay(20); }
      await until(`document.querySelector('.page-caption')?.textContent.includes('document-${String(count - 3).padStart(4, '0')}.pdf') && document.querySelectorAll('.page-card img').length===12`);
      assert.equal(await js(`document.querySelectorAll('.page-card').length`), 12);
      assert.equal(await js(`document.querySelectorAll('.grid-error').length`), 0);
      const runtime = await js(`(() => { window.__scaleObserver.disconnect(); return {heap_mb: performance.memory.usedJSHeapSize/1048576, long_tasks:window.__scaleLongTasks.length, longest_task_ms:Math.max(0,...window.__scaleLongTasks)}; })()`);
      timings.push({ files: count, pages: count * 3, first_preview_ms: Math.round(firstPreview), import_ms: Math.round(imported), last_preview_ms: Math.round(lastPreview), cached_preview_ms: Math.round(cachedPreview), ...runtime });
      fs.writeFileSync(path.join(evidence, `workspace-${count}.png`), (await win.webContents.capturePage()).toPNG());
      console.log(JSON.stringify(timings.at(-1)));
      await clear();
    }
    // Repeat real import cancellation, then resume the same workspace to 3000.
    chosen = Array.from({ length: 3000 }, (_, i) => path.join(samples, 'scale-inputs', `document-${String(i + 1).padStart(4, '0')}.pdf`));
    for (let attempt = 0; attempt < 3; attempt++) {
      await click('添加 PDF / 图片');
      await until(`document.querySelectorAll('.page-card img').length===12 && document.querySelector('.export-footer progress')`);
      await click('停止导入');
      await until(`!document.querySelector('.export-footer progress')`);
      assert.match(await js(`document.querySelector('.document-space').textContent`), /导入已取消/);
      assert.equal(await js(`document.querySelectorAll('.page-card img').length`), 12);
    }
    await click('添加 PDF / 图片');
    await until(`document.querySelector('.workspace-status')?.textContent.includes('3000 个来源') && !document.querySelector('.export-footer progress')`);
    await js(`(() => { document.querySelector('.bulk-tools').open=true; const e=document.querySelector('[aria-label="页码范围"]'); e.value='1-4500,8999-9000'; e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
    const selectionStart = performance.now(); await click('按页码选中');
    await until(`document.querySelector('.selection-toolbar')?.textContent.includes('已选 4502 页')`);
    const bulkSelectionMs = Math.round(performance.now() - selectionStart);
    await click('反选'); await until(`document.querySelector('.selection-toolbar')?.textContent.includes('已选 4498 页')`);
    await js(`(() => { const e=document.querySelector('#bulk-position'); e.value='1'; e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
    await click('移动所选'); await click('撤销');
    await jump(750); await until(`document.querySelector('.page-caption')?.textContent.includes('document-2997.pdf') && document.querySelectorAll('.page-card img').length===12`);
    assert.equal(await js(`document.querySelectorAll('.page-card').length`), 12);
    const storedPath = path.join(profile, 'FileToolbox/workspaces/workbench.json');
    let snapshot;
    for (let attempt = 0; attempt < 600; attempt++) {
      try { const saved = JSON.parse(fs.readFileSync(storedPath, 'utf8')); if (saved.state.sources.length === 3000 && saved.state.pages.length === 9000) { snapshot = saved.state; break; } } catch {}
      await delay(100);
    }
    assert.ok(snapshot, '3000-file workspace must reach persistent storage');
    const restoreStart = performance.now();
    const loaded = new Promise(resolve => win.webContents.once('did-finish-load', resolve));
    win.webContents.reload(); await loaded;
    await until(`document.querySelector('.status-text')?.textContent.includes('已就绪')`);
    await click('PDF 工作台');
    await until(`document.querySelector('.workspace-status')?.textContent.includes('3000 个来源') && document.querySelectorAll('.page-card img').length===12`);
    const restoreMs = Math.round(performance.now() - restoreStart);
    await js(`(() => { document.querySelector('.source-navigator').open=true; const input=document.querySelector('[aria-label="搜索来源"]'); input.value='document-3000'; input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
    await until(`document.querySelectorAll('.source-name').length===1`);
    await js(`document.querySelector('.source-name').click()`);
    await until(`document.querySelectorAll('.page-card img').length===3 && document.querySelector('.page-caption')?.textContent.includes('document-3000.pdf')`);
    await js(`(() => { const input=document.querySelector('[aria-label="来源原页码"]'); input.value='2'; input.dispatchEvent(new Event('input',{bubbles:true})); input.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',bubbles:true})); })()`);
    await until(`document.querySelectorAll('.page-card img').length===12`);
    assert.equal(await js(`document.querySelectorAll('.grid-error').length`), 0);
    fs.writeFileSync(path.join(evidence, 'workspace-3000-restored.png'), (await win.webContents.capturePage()).toPNG());
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(evidence, 'desktop-scale.json'), JSON.stringify({ passed: true, timings, errors, cancellation: '3 cancellations then resume to 3000 passed', bulk_selection_ms: bulkSelectionMs, bulk_move_undo: 'passed', restore_3000_ms: restoreMs, source_search_and_original_page_jump: 'passed' }, null, 2));
    console.log('PASS large workspace acceptance and cancellation'); app.quit();
  } catch (error) { console.error(error); app.exit(1); }
});
