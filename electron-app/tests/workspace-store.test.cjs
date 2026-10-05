const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { buildSync } = require('esbuild');
function moduleUnderTest() {
  const code = buildSync({ entryPoints: [path.join(__dirname, '../main/workspace-store.ts')], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const mod = { exports: {} }; vm.runInNewContext(code, { module: mod, exports: mod.exports, require, Buffer, process, console }); return mod.exports;
}
const source = (index = 0) => ({ path: `C:/docs/${index}.pdf`, name: `${index}.pdf`, kind: 'pdf', page_count: 1, signature: `sig-${index}`, size: 12 });
const state = (count = 1) => ({ version: 1, sources: Array.from({ length: count }, (_, i) => source(i)), pages: Array.from({ length: count }, (_, i) => ({ uid: `${i}:0`, source: i, index: 0, rotation: 0 })), settings: { filename: 'saved', outputDir: '' }, blankIds: [] });
const identity = s => ({ canonical: s.path, dev: '1', ino: s.path, size: String(s.size), mtime_ns: '2', signature: s.signature, sha256: 'a'.repeat(64) });
async function fixture(options = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'toolbox-workspace-'));
  const { WorkspaceStore } = moduleUnderTest();
  const store = new WorkspaceStore(dir, { capture: async s => identity(s), verify: async () => ({ status: 'ready' }), relocate: async (s, id, p) => ({ source: { ...s, path: p }, identity: { ...id, canonical: p } }), ...options });
  return { dir, store, cleanup: () => fs.rm(dir, { recursive: true, force: true }) };
}
test('ordered atomic save keeps latest edit and flush includes pending capture', async () => {
  let release; const gate = new Promise(r => release = r); let captures = 0;
  const f = await fixture({ capture: async s => { captures++; await gate; return identity(s); } });
  try { const first = state(); const second = state(); second.pages[0].rotation = 90;
    const a = f.store.save('workbench', first); const b = f.store.save('workbench', second);
    let flushed = false; const flush = f.store.flush().then(() => flushed = true);
    await new Promise(r => setTimeout(r, 10)); assert.equal(flushed, false); release();
    await Promise.all([a,b,flush]); const loaded = await f.store.load('workbench');
    assert.equal(loaded.state.pages[0].rotation, 90); assert.equal(captures, 1);
    const raw = await fs.readFile(path.join(f.dir, 'workbench.json'), 'utf8'); assert.ok(!raw.includes('data:image')); assert.ok(!JSON.stringify(loaded).includes('mtime_ns'));
  } finally { await f.cleanup(); }
});
test('save validates bounds and excludes unexpected data and credentials', async () => {
  const f = await fixture(); try { const invalid = state(); invalid.pages[0].source = 4; await assert.rejects(f.store.save('workbench', invalid), /page|页面/i);
    await assert.rejects(f.store.save('workbench', state(3001)), /3000/);
    const input = state(); input.settings.auth_token = 'secret'; input.thumbnails = ['data:image/png;base64,secret']; await f.store.save('workbench', input);
    const raw = await fs.readFile(path.join(f.dir, 'workbench.json'), 'utf8'); assert.ok(!raw.includes('secret'));
  } finally { await f.cleanup(); }
});
test('restores 3000 sources and keeps missing and changed pages for explicit repair', async () => {
  const f = await fixture({ verify: async s => ({ status: s.path.endsWith('0.pdf') ? 'missing' : s.path.endsWith('1.pdf') ? 'changed' : 'ready' }) });
  try { await f.store.save('workbench', state(3000)); const loaded = await f.store.load('workbench');
    assert.equal(loaded.state.sources.length, 3000); assert.equal(loaded.state.pages.length, 3000);
    assert.equal(loaded.sources[0].status, 'missing'); assert.equal(loaded.sources[1].status, 'changed');
    await f.store.save('workbench', loaded.state); assert.equal((await f.store.load('workbench')).state.pages.length, 3000);
  } finally { await f.cleanup(); }
});
test('3000 files and 100000 reordered pages round-trip without page payloads', async () => {
  const f = await fixture();
  try {
    const input = state(3000); input.pages = [];
    input.sources.forEach((source, index) => { source.page_count = index < 1000 ? 34 : 33; for (let page = 0; page < source.page_count; page++) input.pages.push({ uid: `${index}:${page}`, source: index, index: page, rotation: page % 4 * 90 }); });
    input.pages.reverse(); await f.store.save('workbench', input); const restored = (await f.store.load('workbench')).state;
    assert.equal(restored.pages.length, 100000); assert.equal(restored.pages[0].uid, '2999:32'); assert.equal(restored.pages.at(-1).uid, '0:0'); assert.equal(restored.pages[1].rotation, 270);
    assert.ok((await fs.stat(path.join(f.dir, 'workbench.json'))).size < 12 * 1024 * 1024);
  } finally { await f.cleanup(); }
});
test('new renderer source cannot acquire a saved identity when capture rejects authorization', async () => {
  let allowed = true;
  const f = await fixture({ capture: async s => { if (!allowed) throw Error('Unauthorized source'); return identity(s); } });
  try { await f.store.save('workbench', state()); allowed = false; const forged = state(); forged.sources[0].path = 'C:/secret.pdf'; forged.identities = [identity(forged.sources[0])];
    await assert.rejects(f.store.save('workbench', forged), /Unauthorized/); assert.equal((await f.store.load('workbench')).state.sources[0].path, source().path);
  } finally { await f.cleanup(); }
});
test('relocation uses main saved identity and rejects changed content without mutating saved path', async () => {
  let received;
  const f = await fixture({ relocate: async (s, id, p) => { received = id; if (p.includes('wrong')) throw Error('内容不一致'); return { source: { ...s, path: p }, identity: { ...id, canonical: p } }; } });
  try { await f.store.save('workbench', state()); await assert.rejects(f.store.relocate('workbench', 0, 'C:/wrong.pdf'), /内容/); assert.equal((await f.store.load('workbench')).state.sources[0].path, source().path);
    const loaded = await f.store.relocate('workbench', 0, 'C:/moved.pdf'); assert.equal(received.sha256, 'a'.repeat(64)); assert.equal(loaded.state.sources[0].path, 'C:/moved.pdf');
  } finally { await f.cleanup(); }
});
test('failed atomic replace retains previous document and reports failure from flush', async () => {
  let fail = false;
  const f = await fixture({ atomicWrite: async (file, content) => { if (fail) throw Error('disk full'); await fs.writeFile(file, content); } });
  try { await f.store.save('workbench', state()); fail = true; const changed = state(); changed.pages[0].rotation = 180;
    await assert.rejects(f.store.save('workbench', changed), /disk full/); await assert.rejects(f.store.flush(), /disk full/);
    assert.equal((await f.store.load('workbench')).state.pages[0].rotation, 0);
    fail = false; await f.store.save('workbench', changed); await f.store.flush(); assert.equal((await f.store.load('workbench')).state.pages[0].rotation, 180);
  } finally { await f.cleanup(); }
});
