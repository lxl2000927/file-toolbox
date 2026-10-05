// Local development / acceptance launcher. Does not install or update software.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');
const root = path.resolve(__dirname, '..');
const desktop = path.join(root, 'electron-app');

function cleanEnvironment() {
  const env = { ...process.env };
  for (const key of ['ELECTRON_RUN_AS_NODE', 'ELECTRON_NO_ASAR', 'NODE_OPTIONS']) delete env[key];
  return env;
}

function launch(executable, args, name, visible = false) {
  const log = path.join(os.tmpdir(), `file-toolbox-${name}-${Date.now()}.log`);
  const fd = fs.openSync(log, 'a');
  const child = cp.spawn(executable, args, {
    cwd: desktop, env: cleanEnvironment(), detached: true, windowsHide: !visible,
    stdio: ['ignore', fd, fd],
  });
  fs.closeSync(fd);
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('spawn', () => { child.unref(); resolve({ pid: child.pid, log }); });
  });
}

async function rendererRunning() {
  let response;
  try { response = await fetch('http://localhost:5173/', { signal: AbortSignal.timeout(1200) }); }
  catch { return false; }
  if (!response.ok || !(await response.text()).includes('<title>File Toolbox</title>')) {
    throw new Error('Port 5173 is occupied by another application. Close that server and retry.');
  }
  return true;
}

async function ensureRenderer() {
  if (await rendererRunning()) return;
  const vite = path.join(desktop, 'node_modules/vite/bin/vite.js');
  if (!fs.existsSync(vite)) throw new Error('Missing frontend dependencies: run npm install in electron-app.');
  const server = await launch(process.execPath, [vite, '--config', 'vite.renderer.config.ts'], 'renderer');
  for (let attempt = 0; attempt < 50; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 200));
    if (await rendererRunning()) return;
  }
  throw new Error(`Renderer did not start. See ${server.log}`);
}

function checkDependencies() {
  for (const relative of ['.venv/Scripts/python.exe', 'electron-app/node_modules/electron/dist/electron.exe',
    'electron-app/dist/main/index.js', 'electron-app/dist/preload/index.js',
    'assets/tessdata/eng.traineddata', 'assets/tessdata/chi_sim.traineddata']) {
    if (!fs.existsSync(path.join(root, relative))) throw new Error(`Missing ${relative}. Follow the development setup in README.md.`);
  }
}

async function main() {
  checkDependencies();
  await ensureRenderer();
  const started = await launch(path.join(desktop, 'node_modules/electron/dist/electron.exe'),
    ['.', '--user-data-dir=.dev-user-data'], 'desktop', true);
  console.log(JSON.stringify(started));
}

module.exports = { root, desktop, cleanEnvironment, ensureRenderer, checkDependencies };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
