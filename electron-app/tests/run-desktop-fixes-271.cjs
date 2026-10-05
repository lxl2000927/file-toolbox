const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');
const { root, desktop, cleanEnvironment, ensureRenderer, checkDependencies } = require('../../scripts/start_desktop.cjs');
(async () => {
  checkDependencies(); await ensureRenderer();
  const report = path.join(root, 'acceptance-samples/release-2.7.1/main-regressions.json');
  fs.rmSync(report, { force: true });
  const child = cp.spawnSync(path.join(desktop, 'node_modules/electron/dist/electron.exe'), ['tests/desktop-fixes-271.cjs'],
    { cwd: desktop, env: cleanEnvironment(), stdio: 'inherit', windowsHide: true, timeout: 60000 });
  if (child.error || child.status !== 0) throw child.error || new Error(`Regression exited with ${child.status}`);
  if (!JSON.parse(fs.readFileSync(report, 'utf8')).passed) throw new Error('Regression did not pass');
})().catch(error => { console.error(error); process.exitCode = 1; });
