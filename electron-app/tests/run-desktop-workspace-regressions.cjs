const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const { root, desktop, cleanEnvironment, ensureRenderer, checkDependencies } = require('../../scripts/start_desktop.cjs');

async function main() {
  checkDependencies(); await ensureRenderer();
  const base = path.join(root, 'acceptance-samples/workspace-regression-evidence'); fs.mkdirSync(base, { recursive: true });
  const run = fs.mkdtempSync(path.join(base, 'run-'));
  for (const name of ['inputs', 'profile']) fs.mkdirSync(path.join(run, name));
  for (const [sample, name] of [['01-front.pdf', 'front.pdf'], ['02-back-reversed.pdf', 'back.pdf']]) {
    fs.copyFileSync(path.join(root, 'acceptance-samples', sample), path.join(run, 'inputs', name));
  }
  const result = cp.spawnSync(path.join(desktop, 'node_modules/electron/dist/electron.exe'), ['tests/desktop-workspace-regressions.cjs'], {
    cwd: desktop, env: { ...cleanEnvironment(), FILE_TOOLBOX_WORKSPACE_REGRESSION_ROOT: run }, stdio: 'inherit', windowsHide: true, timeout: 150000,
  });
  const reportPath = path.join(run, 'report.json'); console.log(`Workspace desktop evidence: ${reportPath}`);
  if (result.error || result.status !== 0) throw result.error || Error(`Electron workspace regression exited ${result.status}`);
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
  if (!report.passed) throw Error(report.error || 'Workspace regression did not pass');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
