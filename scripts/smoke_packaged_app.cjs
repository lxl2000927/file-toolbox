// Launch a real packaged app with isolated data, then inspect its own renderer
// over a loopback-only DevTools session. No source app files are substituted.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const cp = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const option = (name, fallback) => { const i = args.indexOf(name); return i < 0 ? fallback : args[i + 1]; };
const executable = path.resolve(option('--exe', path.join(root, 'electron-app/release/win-unpacked/File Toolbox.exe')));
const expectedType = option('--type', 'archive');
const report = path.resolve(option('--report', path.join(root, 'acceptance-samples/packaged-app.json')));
const version = option('--version', require('../electron-app/package.json').version);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve));
  const profile = option('--profile', '') ? path.resolve(option('--profile', '')) : fs.mkdtempSync(path.join(os.tmpdir(), 'toolbox-package-smoke-'));
  fs.mkdirSync(profile, { recursive: true });
  const environment = { ...process.env, APPDATA: profile, LOCALAPPDATA: profile };
  delete environment.ELECTRON_RUN_AS_NODE; delete environment.NODE_OPTIONS; delete environment.PYTHONPATH; delete environment.PYTHONHOME;
  const child = cp.spawn(executable, [`--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${path.join(profile, 'user-data')}`, '--disable-gpu'], { cwd: profile, env: environment, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const diagnostics = [];
  child.stderr.on('data', data => diagnostics.push(data.toString())); child.stdout.on('data', data => diagnostics.push(data.toString()));
  let socket, browserSocket;
  const failures = [];
  try {
    let target;
    for (let attempt = 0; attempt < 120; attempt++) {
      try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page' && t.url.startsWith('file:')); } catch {}
      if (target) break;
      if (child.exitCode !== null) throw Error(`Application exited ${child.exitCode}\n${diagnostics.join('')}`);
      await pause(500);
    }
    assert.ok(target, 'packaged renderer did not start');
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
    const waiting = new Map(); let next = 0;
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') failures.push(message.params.exceptionDetails.text);
      if (message.id) { const request = waiting.get(message.id); if (!request) return; waiting.delete(message.id); clearTimeout(request.timer); message.error ? request.reject(Error(JSON.stringify(message.error))) : request.resolve(message.result); }
    });
    const call = (method, params = {}, timeout = 15000) => new Promise((resolve, reject) => {
      const id = ++next;
      const timer = setTimeout(() => { waiting.delete(id); reject(Error(`DevTools timeout: ${method}`)); }, timeout);
      waiting.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async (expression, timeout) => {
      const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, timeout);
      if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    await call('Runtime.enable');
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await evaluate("document.querySelector('.status-text')?.textContent.includes('已就绪')")) break;
      if (attempt === 79) throw Error(await evaluate('document.body.innerText'));
      await pause(500);
    }
    const status = await evaluate('window.electronAPI.update.getStatus()');
    assert.equal(status.current, version); assert.equal(status.packageType, expectedType);
    assert.equal((await evaluate('window.engine.ping()')).pong, true);
    let publicUpdate = null;
    if (option('--update-version', '')) {
      publicUpdate = await evaluate('window.electronAPI.update.check()', 120000);
      assert.equal(publicUpdate.state, 'available', JSON.stringify(publicUpdate));
      assert.equal(publicUpdate.latest.replace(/^v/, ''), option('--update-version', ''));
      if (args.includes('--download-update')) {
        publicUpdate = await evaluate('window.electronAPI.update.download()', 600000);
        assert.equal(publicUpdate.state, 'downloaded', JSON.stringify(publicUpdate));
        assert.equal(publicUpdate.percent, 100);
      }
    }
    if (args.includes('--expect-collapsed')) assert.equal(await evaluate("document.querySelector('.nav-toggle').getAttribute('aria-expanded')"), 'false', 'upgrade must retain the navigation preference');
    if (args.includes('--set-collapsed') && await evaluate("document.querySelector('.nav-toggle').getAttribute('aria-expanded')") !== 'false') {
      await evaluate("document.querySelector('.nav-toggle').click()"); await pause(350);
    }
    const navigation = ['PDF 工作台', '扫描拆分', '普通拆分', '重命名', '设置'];
    if (!args.includes('--legacy-navigation')) navigation.push('任务中心');
    for (const name of navigation) {
      await evaluate(`(() => { const e=[...document.querySelectorAll('.nav-btn')].find(e=>e.textContent.trim()===${JSON.stringify(name)} || e.getAttribute('aria-label')===${JSON.stringify(name)}); if(!e) throw Error('Missing navigation'); e.click(); })()`);
      await pause(450);
      assert.equal(await evaluate("Boolean(document.querySelector('.app-main')) && !document.querySelector('vite-error-overlay')"), true);
      assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true);
    }
    assert.deepEqual(failures, []);
    fs.mkdirSync(path.dirname(report), { recursive: true });
    const capture = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(report.replace(/\.json$/, '.png'), Buffer.from(capture.data, 'base64'));
    fs.writeFileSync(report, JSON.stringify({ passed: true, executable, version, packageType: status.packageType, publicUpdate,
      checks: ['packaged file renderer', 'bundled engine ready and ping', 'version and distribution type', `${navigation.length} navigation destinations`, 'no horizontal overflow or renderer exceptions', ...(args.includes('--expect-collapsed') ? ['upgrade retains navigation preference'] : [])], rendererErrors: failures }, null, 2));
    console.log(`PASS packaged ${expectedType} ${version}: ${report}`);
    // Browser.close terminates this isolated application, including its engine.
    const browser = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
    browserSocket = new WebSocket(browser.webSocketDebuggerUrl);
    await new Promise(resolve => browserSocket.addEventListener('open', resolve, { once: true }));
    browserSocket.send(JSON.stringify({ id: 1, method: 'Browser.close' }));
    for (let attempt = 0; attempt < 30 && child.exitCode === null; attempt++) await pause(250);
  } finally {
    socket?.close(); browserSocket?.close();
    if (child.exitCode === null) {
      // Only terminate the exact test process we created and its children.
      cp.spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
