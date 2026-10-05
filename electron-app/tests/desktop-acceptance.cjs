// Isolated Electron integration test. The production main process, preload,
// renderer and Python engine run unchanged. Native dialogs return fixtures;
// shell launches and clipboard writes are recorded at their OS boundary.
// Run with Electron after npm run build and start dev:renderer on port 5173.
const { app, BrowserWindow, clipboard, dialog, shell } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const samples = path.join(root, 'acceptance-samples');
const evidence = path.join(samples, 'desktop-evidence');
const profile = path.join(samples, 'desktop-profile');
fs.mkdirSync(path.join(samples, 'desktop-results'), { recursive: true });
fs.mkdirSync(evidence, { recursive: true }); fs.mkdirSync(profile, { recursive: true });
process.env.APPDATA = profile;
app.setPath('userData', profile);
app.disableHardwareAcceleration();
let chosen = ['01-front.pdf', '02-back-reversed.pdf'].map(name => path.join(samples, name));
const savedLogs = [];
const nativeActions = [];
shell.openPath = async file => { nativeActions.push(['open', file]); return ''; };
shell.showItemInFolder = file => nativeActions.push(['reveal', file]);
clipboard.writeText = text => nativeActions.push(['copy', text]);
dialog.showOpenDialog = async (_window, options) => ({ canceled: false, filePaths: options.properties.includes('openDirectory') ? [path.join(samples, 'desktop-results')] : chosen });
dialog.showSaveDialog = async (_window, options) => {
  const filePath = path.join(samples, 'desktop-results', (options.title === '导出完整 OCR 文字' ? 'exported-' : '') + path.basename(options.defaultPath));
  savedLogs.push(filePath); return { canceled: false, filePath };
};
app.on('browser-window-created', (_event, window) => { window.show = () => window.showInactive(); window.webContents.setBackgroundThrottling(false); });
require('../dist/main/index.js');
let window;
const failures = [];
const checks = [];
const motionEvidence = {};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function js(code) { return window.webContents.executeJavaScript(code); }
async function until(code, timeout = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { if (await js(code)) return; await delay(80); }
  throw new Error(`Timed out: ${code}\n${await js('document.body.innerText')}`);
}
async function click(text, selector = 'button') {
  await js(`(() => { const e = [...document.querySelectorAll(${JSON.stringify(selector)})].find(e => e.textContent.trim() === ${JSON.stringify(text)}); if (!e || e.disabled) throw Error('Button unavailable: '+${JSON.stringify(text)}); e.click(); })()`);
  await delay(300);
}
async function input(selector, value) {
  await js(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); if(!e) throw Error('Input missing'); e.value=${JSON.stringify(value)}; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); })()`);
  await delay(80);
}
async function screenshot(name) {
  await delay(250);
  for (let attempt = 0; attempt < 3; attempt++) {
    try { fs.writeFileSync(path.join(evidence, name + '.png'), (await window.webContents.capturePage()).toPNG()); return; }
    catch (error) { if (attempt === 2) throw error; await delay(300); }
  }
}
async function check(name, run) { await run(); checks.push(name); console.log(`PASS ${name}`); }

app.whenReady().then(async () => {
  try {
    window = BrowserWindow.getAllWindows()[0];
    window.webContents.debugger.attach('1.3');
    await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });
    window.webContents.on('console-message', (details) => { if (['error', 'warning'].includes(details.level) && !details.message.includes('Electron Security Warning')) failures.push(details.message); });
    await until(`document.querySelector('.status-text')?.textContent.includes('已就绪')`);
    if (await js("document.querySelector('.nav-toggle')?.getAttribute('aria-expanded') === 'false'")) {
      await js("document.querySelector('.nav-toggle').click()"); await delay(400);
    }
    await click('PDF 工作台', '.nav-btn');
    await check('workbench minimum layout and empty state', async () => {
      window.setSize(1120, 720); await delay(200);
      assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true);
      await screenshot('workbench-empty-1120');
    });
    await check('directional navigation animates and rapid switching leaves one usable page', async () => {
      motionEvidence.navigation = await js(`(async () => {
        [...document.querySelectorAll('.nav-btn')].find(e=>e.textContent.trim()==='扫描拆分').click();
        // Vue installs transition targets on its second frame. Sample after it.
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(r))));
        return [...document.querySelector('.app-main').getAnimations({subtree:true})].map(a=>({property:a.transitionProperty || a.animationName,duration:a.effect.getTiming().duration}));
      })()`);
      assert.ok(motionEvidence.navigation.some(a => a.property === 'transform' && a.duration >= 200), JSON.stringify({ animations: motionEvidence.navigation, styles: await js("[...document.querySelector('.app-main').children].map(e=>({class:e.className,transition:getComputedStyle(e).transition,transform:getComputedStyle(e).transform}))") }));
      for (const name of ['普通拆分', '重命名', '扫描拆分', 'PDF 工作台']) {
        await js(`[...document.querySelectorAll('.nav-btn')].find(e=>e.textContent.trim()===${JSON.stringify(name)}).click()`); await delay(45);
      }
      await delay(500);
      assert.equal(await js("document.querySelector('.app-main').children.length"), 1);
      assert.equal(await js("Boolean(document.querySelector('.workbench')) && !document.querySelector('vite-error-overlay')"), true);
      assert.equal(await js("Math.abs(document.querySelector('.nav-current').getBoundingClientRect().top-document.querySelector('.nav-btn.active').getBoundingClientRect().top)<2"), true);
    });
    await click('添加 PDF / 图片');
    await until(`document.querySelectorAll('.page-card img').length===6`);
    await check('authorized native file selection renders six real PDF thumbnails', async () => {
      assert.equal(await js("document.querySelector('.workspace-status').textContent.includes('2 个来源')"), true);
      await screenshot('workbench-loaded-1120');
    });
    await check('84px navigation folds to 56px without losing loaded pages or active state', async () => {
      assert.equal(await js("Math.round(document.querySelector('.side-nav').getBoundingClientRect().width)"), 84);
      await js("document.querySelector('.nav-toggle').click()"); await delay(400);
      assert.equal(await js("Math.round(document.querySelector('.side-nav').getBoundingClientRect().width)"), 56);
      assert.equal(await js("document.querySelector('.nav-btn.active').getAttribute('aria-label')"), 'PDF 工作台');
      assert.equal(await js("document.querySelector('.nav-btn.active').title"), 'PDF 工作台');
      assert.equal(await js("document.querySelectorAll('.page-card img').length"), 6);
      assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true);
      await screenshot('navigation-collapsed-1120');
      await js("document.querySelector('.nav-toggle').click()"); await delay(400);
      assert.equal(await js("document.querySelectorAll('.page-card img').length"), 6);
    });
    await check('page rotation and undo update the rendered workspace', async () => {
      await js("document.querySelector('.page-top input').click()"); await click('旋转 90°');
      await until("document.querySelector('.page-caption').textContent.includes('90°')");
      await click('撤销');
      assert.equal(await js("document.querySelector('.page-caption').textContent.includes('90°')"), false);
    });
    await check('bulk range, odd/even, invert, stable move and undo use current page positions', async () => {
      await click('批量选页与移动 页码按当前排序计算', '.bulk-tools summary');
      await input('[aria-label="页码范围"]', '2,4'); await click('按页码选中');
      const selected = "[...document.querySelectorAll('.page-top input')].flatMap((e,i)=>e.checked?[i+1]:[])";
      assert.deepEqual(await js(selected), [2,4]);
      await input('[aria-label="页码范围"]', '0,99'); await click('按页码选中');
      assert.deepEqual(await js(selected), [2,4]);
      await click('奇数页'); assert.deepEqual(await js(selected), [1,3,5]);
      await click('偶数页'); assert.deepEqual(await js(selected), [2,4,6]);
      await click('反选'); assert.deepEqual(await js(selected), [1,3,5]);
      await input('[aria-label="页码范围"]', '2,4'); await click('按页码选中');
      const before = await js("[...document.querySelectorAll('.page-caption')].map(e=>e.textContent)");
      await input('#bulk-position', '4'); await click('移动所选');
      assert.deepEqual(await js("[...document.querySelectorAll('.page-caption')].map(e=>e.textContent)"), [before[0],before[2],before[4],before[1],before[3],before[5]]);
      await screenshot('bulk-selection-1120'); await click('撤销');
      assert.deepEqual(await js("[...document.querySelectorAll('.page-caption')].map(e=>e.textContent)"), before);
      await js("document.querySelector('.bulk-tools').open=false");
    });
    await check('thumbnail reorder has live motion and undo restores the actual page order', async () => {
      await js("document.querySelector('.page-top input').click()");
      motionEvidence.reorder = await js(`(async () => {
        [...document.querySelectorAll('.selection-toolbar button')].find(e=>e.textContent.trim()==='后移').click();
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(r))));
        return [...document.querySelector('.page-grid').getAnimations({subtree:true})].map(a=>({property:a.transitionProperty || a.animationName,duration:a.effect.getTiming().duration}));
      })()`);
      assert.ok(motionEvidence.reorder.some(a => a.property === 'transform' && a.duration >= 200));
      await delay(400);
      assert.equal(await js("document.querySelector('.page-caption').textContent.includes('原第 2 页')"), true);
      await click('撤销');
      assert.equal(await js("document.querySelector('.page-caption').textContent.includes('原第 1 页')"), true);
    });
    await check('rapid delete and undo during animation keeps six correctly positioned pages', async () => {
      await js("document.querySelector('.page-top input').click()");
      await js("[...document.querySelectorAll('.selection-toolbar button')].find(e=>e.textContent.trim()==='删除').click()");
      await delay(35);
      await js("[...document.querySelectorAll('.selection-toolbar button')].find(e=>e.textContent.trim()==='撤销').click()");
      await delay(500);
      assert.equal(await js("document.querySelectorAll('.page-card').length"), 6);
      assert.equal(await js("[...document.querySelectorAll('.page-card')].every(e=>getComputedStyle(e).position==='relative' && e.style.width==='')"), true);
      assert.equal(await js("new Set([...document.querySelectorAll('.page-card')].map(e=>{const r=e.getBoundingClientRect();return r.x+','+r.y})).size"), 6);
    });
    await check('internal drag gives insertion feedback and outside drops still reach the workbench', async () => {
      await js(`(() => { const cards=document.querySelectorAll('.page-card'), data=new DataTransfer();
        cards[0].dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:data}));
        cards[2].dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:data})); })()`);
      assert.equal(await js("Boolean(document.querySelector('.page-card.dragging')) && Boolean(document.querySelector('.page-card.drop-target'))"), true);
      await js("document.querySelectorAll('.page-card')[2].dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true}))");
      await delay(400);
      assert.equal(await js("document.querySelector('.page-caption').textContent.includes('原第 2 页')"), true);
      await click('撤销');
      assert.equal(await js(`(() => { let reached=false; document.querySelector('.workbench').addEventListener('drop',()=>{reached=true},{once:true}); document.querySelector('.page-card').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:new DataTransfer()})); return reached; })()`), true);
    });
    await check('reduced motion removes transition durations and stagger delays', async () => {
      await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
      await delay(80);
      motionEvidence.reduced = await js(`(() => {
        const selector='.nav-current, .page-card, .thumbnail img, .mode-options button';
        return {matches:matchMedia('(prefers-reduced-motion: reduce)').matches,styles:[...document.querySelectorAll(selector)].map(e=>({duration:getComputedStyle(e).transitionDuration,delay:getComputedStyle(e).transitionDelay,animationDelay:getComputedStyle(e).animationDelay}))};
      })()`);
      assert.equal(motionEvidence.reduced.matches, true);
      assert.ok(motionEvidence.reduced.styles.every(s => s.duration.split(',').every(d=>parseFloat(d)<=0.001) && s.delay.split(',').every(d=>parseFloat(d)===0) && s.animationDelay.split(',').every(d=>parseFloat(d)===0)));
      await window.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });
    });
    await check('reversed-back interleave preview', async () => {
      await click('正反面交错合并', 'summary'); await click('应用交错页序');
      assert.equal(await js("document.querySelectorAll('.page-caption')[1].textContent.includes('原第 3 页')"), true);
    });
    await check('high resolution page preview opens and closes', async () => {
      await js("document.querySelector('.thumbnail').click()");
      await until("document.querySelector('.page-preview-dialog[open] .large-page img')?.naturalWidth > 600");
      await screenshot('page-preview'); await click('关闭', '.page-preview-dialog button');
      assert.equal(await js("Boolean(document.querySelector('.page-preview-dialog[open]'))"), false);
    });
    await click('导出 PDF · 6 页');
    await until("document.querySelectorAll('.output-list li').length === 1");
    await check('real Electron IPC exports merged PDF and displays its path and size', async () => {
      const filename = await js("document.querySelector('.output-path').textContent.trim()");
      assert.ok(fs.statSync(filename).size > 0); await screenshot('workbench-output-1120');
    });
    await check('output open, reveal and copy use the real preload and validated native handlers', async () => {
      const file = await js("document.querySelector('.output-path').textContent.trim()");
      await click('打开', '.file-actions button'); await click('定位', '.file-actions button'); await click('复制路径', '.file-actions button');
      assert.deepEqual(nativeActions.slice(-3), [['open',file],['reveal',file],['copy',file]]);
      assert.ok(await js("document.querySelector('.size-comparison')?.textContent.includes('体积')"));
    });
    await check('saved presets use the real persistent store', async () => {
      await js("document.querySelector('.processing-presets summary').click()");
      await click('保存当前参数为方案'); await input('input[aria-label="方案名称"]', '桌面验收归档方案');
      await click('保存', '.processing-presets button');
      await until("document.querySelector('.processing-presets').textContent.includes('重启后仍可使用')");
      const data = JSON.parse(fs.readFileSync(path.join(profile, 'FileToolbox', 'presets.json'), 'utf8'));
      assert.ok(data.presets.some(p => p.name === '桌面验收归档方案'));
    });
    await click('扫描拆分', '.nav-btn'); chosen = [path.join(samples, '03-scan-markers.pdf')];
    await click('选择 PDF'); await click('选择目录');
    // The auto detector is the production default. Stamp-only is also covered
    // by the independent algorithm fixture; this exercises the full UI flow.
    await click('全量扫描并复核');
    await until("document.querySelectorAll('.scan-review .segment-item').length === 3", 60000);
    await check('full scan pauses for manual review without writing files', async () => {
      await until("document.querySelectorAll('.scan-review .page-card img').length === 6");
      await screenshot('scan-review-1120');
    });
    await click('合并下一份', '.merge-segments');
    await until("document.querySelectorAll('.scan-review .segment-item').length === 2");
    await click('确认导出 2 份');
    await until("document.querySelectorAll('.review-files li').length === 2");
    await check('manual boundary merge exports two real PDF files', async () => {
      const files = await js("[...document.querySelectorAll('.review-files li')].map(e=>e.textContent.trim())");
      files.forEach(file => assert.ok(fs.statSync(file).size > 0)); await screenshot('scan-review-exported-1120');
    });
    window.setSize(1280, 820); await click('PDF 工作台', '.nav-btn');
    await click('页面整理 · 6'); await screenshot('workbench-loaded-1280');
    await check('blank candidate review deletes only the approved page and supports undo', async () => {
      await click('清空'); await click('确定', '.dialog-actions button');
      chosen = [path.join(samples, '04-blank-and-black.pdf')]; await click('添加 PDF / 图片');
      await until("document.querySelectorAll('.page-card img').length===4");
      await click('查找空白页'); await until("document.querySelectorAll('.page-badge').length===1");
      await click('选中候选 1 页'); await click('删除', '.selection-toolbar button');
      assert.equal(await js("document.querySelectorAll('.page-card').length"), 3);
      await click('撤销'); assert.equal(await js("document.querySelectorAll('.page-card').length"), 4);
      await screenshot('blank-review-1280');
    });
    await check('Chinese image to searchable PDF through the real renderer and offline engine', async () => {
      await click('清空'); await click('确定', '.dialog-actions button');
      chosen = [path.join(samples, '05-chinese-scan.png')]; await click('添加 PDF / 图片');
      await until("document.querySelectorAll('.page-card img').length===1");
      await click('离线 OCR', '.mode-options strong'); await input('#wb-filename', 'desktop-chinese-ocr');
      await click('导出 PDF · 1 页');
      await until("document.querySelector('.ocr-text')?.textContent.replace(/\\s/g,'').includes('文件工具箱')");
      await screenshot('chinese-ocr-result-1280');
    });
    await check('OCR page preview copies explicit text and exports the complete TXT file', async () => {
      const preview = await js("document.querySelector('.ocr-text pre').textContent");
      await click('复制当前预览'); assert.equal(nativeActions.at(-1)[1], preview);
      await click('导出完整 TXT');
      assert.match(fs.readFileSync(savedLogs.at(-1), 'utf8').replace(/\s/g,''), /文件工具箱/);
      assert.ok(await js("document.querySelector('.action-feedback').textContent.includes('完整 TXT 已导出')"));
      await js("document.querySelector('.ocr-text').scrollIntoView({block:'start'})"); await screenshot('ocr-text-and-export-1280');
    });
    await check('compression presets update actual output settings and report measured size change', async () => {
      await click('压缩 PDF', '.mode-options strong'); await input('#wb-compression', 'raster');
      await input('#wb-quality-preset', 'compact');
      assert.deepEqual(await js("[...document.querySelectorAll('.field-pair input')].map(e=>Number(e.value))"), [100,60]);
      await input('.field-pair input', '101');
      assert.equal(await js("document.querySelector('#wb-quality-preset').value"), 'custom');
      await input('#wb-quality-preset', 'balanced');
      assert.deepEqual(await js("[...document.querySelectorAll('.field-pair input')].map(e=>Number(e.value))"), [150,78]);
      await input('#wb-filename', 'desktop-compressed'); await click('导出 PDF · 1 页'); await click('继续输出', '.dialog-actions button');
      await until("document.querySelector('.output-path')?.textContent.includes('desktop-compressed')");
      assert.ok(await js("document.querySelector('.size-comparison')?.textContent.includes('体积')"));
      await screenshot('compression-result-1280');
    });
    await check('page image export produces a real PNG and shows the output result', async () => {
      await click('导出页面图片', '.mode-options strong'); await click('导出图片 · 1 页');
      await until("document.querySelector('.output-path')?.textContent.trim().endsWith('.png')");
      const file = await js("document.querySelector('.output-path').textContent.trim()");
      assert.equal(fs.readFileSync(file).subarray(1,4).toString(), 'PNG');
    });
    await check('saved workflow survives a renderer restart', async () => {
      await js("document.querySelector('.nav-toggle').click()"); await delay(350);
      window.webContents.reload(); await until("document.querySelector('.status-text')?.textContent.includes('已就绪')");
      assert.equal(await js("document.querySelector('.nav-toggle').getAttribute('aria-expanded')"), 'false');
      await js("document.querySelector('.nav-toggle').click()"); await delay(350);
      await click('PDF 工作台', '.nav-btn'); await js("document.querySelector('.processing-presets summary').click()");
      await until("document.querySelector('.processing-presets select')?.textContent.includes('桌面验收归档方案')");
    });
    await click('设置', '.nav-btn');
    await until("document.querySelectorAll('.log-record').length > 0");
    await check('real operation logs can be searched and exported as filtered TXT and JSON', async () => {
      await input('input[aria-label="搜索日志"]', 'desktop-chinese-ocr');
      await until("document.querySelectorAll('.log-record').length > 0");
      assert.equal(await js("[...document.querySelectorAll('.log-record-detail')].every(e=>e.textContent.includes('desktop-chinese-ocr'))"), true);
      await js("document.querySelector('.log-record summary').click()");
      await screenshot('logs-filtered-1280');
      await click('导出日志', '.log-export-select button');
      await click('文本 TXT', '[role=option]');
      await until("document.querySelector('.toast-host')?.textContent.includes('已导出筛选后的日志')");
      assert.match(fs.readFileSync(savedLogs.at(-1), 'utf8'), /desktop-chinese-ocr/);
      await click('导出日志', '.log-export-select button'); await click('结构 JSON', '[role=option]');
      await delay(300);
      const entries = JSON.parse(fs.readFileSync(savedLogs.at(-1), 'utf8'));
      assert.ok(entries.length > 0 && entries.every(e => JSON.stringify(e).includes('desktop-chinese-ocr')));
    });
    await check('cancelling clear keeps logs and confirming clear updates the real history store', async () => {
      await click('清空', '.logs-toolbar button'); await click('取消', '.dialog-actions button');
      assert.ok(await js("document.querySelectorAll('.log-record').length > 0"));
      await click('清空', '.logs-toolbar button'); await click('清空', '.dialog-actions button');
      await until("document.querySelector('.log-placeholder')?.textContent.includes('暂无操作记录')");
      const history = await js('window.engine.history.get(100,{currentSession:false})');
      assert.equal(history.records.length, 0);
    });
    await check('update page renders progress, installer readiness and safe release notes', async () => {
      await click('更新', '[role=tab]');
      await until("document.querySelector('.update-unsupported')?.textContent.includes('开发')");
      const base = { current: '2.5.0', latest: '2.6.0', supported: true, packageType: 'installer', portable: false };
      window.webContents.send('app:update-status', { ...base, state: 'available', body: '## Release notes\n<script>window.__unsafeRelease=true</script><a href="javascript:alert(1)">unsafe</a>\n**Verified text**' });
      await until("document.querySelector('.release-body')?.textContent.includes('Verified text')");
      assert.equal(await js("Boolean(window.__unsafeRelease) || Boolean(document.querySelector('.release-body script, .release-body [href^=\"javascript:\"]'))"), false);
      window.webContents.send('app:update-status', { ...base, state: 'downloading', percent: 42, transferred: 4200, total: 10000 });
      await until("document.querySelector('.update-progress-meta')?.textContent.includes('42%')");
      assert.equal(await js("document.querySelector('.app-brand button').disabled"), true);
      window.webContents.send('app:update-status', { ...base, state: 'downloaded', percent: 100 });
      await until("[...document.querySelectorAll('.release-actions button')].some(e=>e.textContent.trim()==='重启并安装')");
      await screenshot('update-ready-simulated-1280');
      await click('重启并安装', '.release-actions button'); await click('取消', '.dialog-actions button');
      assert.equal(await js("document.querySelector('.release-actions button').textContent.trim()"), '重启并安装');
    });
    await js('window.engine.ping()');
    await delay(150);
    assert.deepEqual(failures, [], 'Renderer must not emit warnings or errors');
    fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify({ checks, motionEvidence, rendererErrors: failures, passed: true }, null, 2));
    console.log(`PASS ${checks.length} desktop acceptance checks`);
    app.quit();
  } catch (error) {
    console.error(error);
    if (window && !window.isDestroyed()) await screenshot('failure');
    fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify({ checks, motionEvidence, error: String(error), rendererErrors: failures, passed: false }, null, 2));
    process.exitCode = 1; app.quit();
  }
});
