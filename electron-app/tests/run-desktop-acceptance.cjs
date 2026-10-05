const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const { root, desktop, cleanEnvironment, ensureRenderer, checkDependencies } = require('../../scripts/start_desktop.cjs');

async function main() {
  const scale = process.argv.includes('--scale');
  checkDependencies();
  const sample = path.join(root, 'acceptance-samples/01-front.pdf');
  if (!fs.existsSync(sample)) {
    const setup = cp.spawnSync(path.join(root, '.venv/Scripts/python.exe'), ['scripts/create_acceptance_samples.py'],
      { cwd: root, env: cleanEnvironment(), stdio: 'inherit', windowsHide: true });
    if (setup.error || setup.status !== 0) throw setup.error || new Error('Acceptance sample generation failed.');
  }
  await ensureRenderer();
  if (scale && !fs.existsSync(path.join(root, 'acceptance-samples/scale-inputs/document-3000.pdf'))) {
    const setup = cp.spawnSync(path.join(root, '.venv/Scripts/python.exe'), ['scripts/create_scale_samples.py'],
      { cwd: root, env: cleanEnvironment(), stdio: 'inherit', windowsHide: true });
    if (setup.error || setup.status !== 0) throw setup.error || new Error('Scale sample generation failed.');
  }
  const report = path.join(root, scale ? 'acceptance-samples/scale-evidence/desktop-scale.json' : 'acceptance-samples/desktop-evidence/report.json');
  fs.rmSync(report, { force: true });
  const result = cp.spawnSync(path.join(desktop, 'node_modules/electron/dist/electron.exe'), [scale ? 'tests/desktop-scale.cjs' : 'tests/desktop-acceptance.cjs'],
    { cwd: desktop, env: cleanEnvironment(), stdio: 'inherit', windowsHide: true, timeout: scale ? 360000 : 180000 });
  if (result.error || result.status !== 0) throw result.error || new Error(`Desktop acceptance exited with ${result.status}`);
  if (!fs.existsSync(report) || !JSON.parse(fs.readFileSync(report, 'utf8')).passed) throw new Error('Desktop acceptance did not pass.');
  console.log(`Evidence: ${report}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
