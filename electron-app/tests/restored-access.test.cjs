const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { buildSync } = require('esbuild');
function api() {
  const code = buildSync({ entryPoints: [path.join(__dirname, '../main/restored-access.ts')], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const module = { exports: {} }; new Function('module', 'exports', 'require', code)(module, module.exports, require); return module.exports;
}
test('restored input checks original identity and full content; relocated file checks content', async () => {
  const { captureDocument, verifyIdentity, relocateDocument } = api();
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'toolbox-restore-'));
  try {
    const a = path.join(dir, 'a.pdf'), b = path.join(dir, 'b.pdf'); await fs.writeFile(a, 'first content');
    const saved = await captureDocument(a, 100);
    await verifyIdentity(a, saved, 'file');
    await fs.copyFile(a, b); const relocated = await relocateDocument(b, saved, 100); assert.equal(relocated.sha256, saved.sha256);
    await fs.writeFile(a, 'other content'); await assert.rejects(verifyIdentity(a, saved, 'file'), /变化/);
    await assert.rejects(relocateDocument(a, saved, 100), /内容/);
    await assert.rejects(captureDocument(b, 3), /大小/);
    await assert.rejects(verifyIdentity(b, {}, 'file'), /身份/);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});
test('restored output directory tolerates changed contents but rejects directory replacement', async () => {
  const { captureIdentity, verifyIdentity } = api(); const root = await fs.mkdtemp(path.join(os.tmpdir(), 'toolbox-output-'));
  try {
    const dir = path.join(root, 'out'); await fs.mkdir(dir); const saved = await captureIdentity(dir, 'directory');
    await fs.writeFile(path.join(dir, 'result.pdf'), 'valid'); await verifyIdentity(dir, saved, 'directory');
    await fs.rename(dir, path.join(root, 'old')); await fs.mkdir(dir); await assert.rejects(verifyIdentity(dir, saved, 'directory'), /变化/);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
