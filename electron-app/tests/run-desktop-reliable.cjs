// Two actual Electron launches share only this run's isolated application data.
// Native input fixtures are copied before any missing/replacement tests.
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const { root, desktop, cleanEnvironment, ensureRenderer, checkDependencies } = require('../../scripts/start_desktop.cjs');

async function main() {
  checkDependencies();
  await ensureRenderer();
  const runs = path.join(root, 'acceptance-samples/reliable-evidence');
  fs.mkdirSync(runs, { recursive: true });
  const run = fs.mkdtempSync(path.join(runs, 'run-'));
  for (const directory of ['inputs', 'outputs', 'profile']) fs.mkdirSync(path.join(run, directory));
  const samples = path.join(root, 'acceptance-samples');
  for (const [original, copied] of [['01-front.pdf', 'front.pdf'], ['02-back-reversed.pdf', 'back.pdf'], ['06-chinese-scan.pdf', 'ocr.pdf']]) {
    fs.copyFileSync(path.join(samples, original), path.join(run, 'inputs', copied));
  }
  const report = { run, passed: false, startedAt: new Date().toISOString(), phases: [] };
  const reportPath = path.join(runs, 'report.json');
  const env = { ...cleanEnvironment(), FILE_TOOLBOX_RELIABLE_ROOT: run };
  const electron = path.join(desktop, 'node_modules/electron/dist/electron.exe');
  function phase(number) {
    const result = cp.spawnSync(electron, ['tests/desktop-reliable.cjs', `--phase=${number}`], {
      cwd: desktop, env, stdio: 'inherit', windowsHide: true, timeout: number === 1 ? 180000 : 360000,
    });
    const phaseReport = path.join(run, `phase-${number}.json`);
    if (fs.existsSync(phaseReport)) report.phases.push(JSON.parse(fs.readFileSync(phaseReport, 'utf8')));
    if (result.error || result.status !== 0) throw result.error || Error(`Electron phase ${number} exited ${result.status}`);
    if (!report.phases.at(-1)?.passed) throw Error(`Phase ${number} did not produce a successful report`);
  }
  try {
    phase(1);
    // All paths are literal children of this newly-created run directory.
    const inputs = path.join(run, 'inputs');
    fs.renameSync(path.join(inputs, 'front.pdf'), path.join(inputs, 'relocated-front.pdf'));
    fs.copyFileSync(path.join(inputs, 'back.pdf'), path.join(inputs, 'relocated-back.pdf'));
    fs.copyFileSync(path.join(inputs, 'ocr.pdf'), path.join(inputs, 'back.pdf'));
    phase(2);
    report.passed = true;
  } catch (error) { report.error = String(error); throw error; }
  finally {
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    fs.writeFileSync(path.join(run, 'report.json'), JSON.stringify(report, null, 2));
    console.log(`Reliable desktop evidence: ${reportPath}`);
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
