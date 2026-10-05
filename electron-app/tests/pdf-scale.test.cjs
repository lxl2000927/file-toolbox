const { test } = require('node:test');
const assert = require('node:assert/strict');
const { componentFixture } = require('./component-fixture.cjs');
const { buildSync } = require('esbuild');
const path = require('node:path');
const vm = require('node:vm');
const source = i => ({ path: `C:/batch/${i}.pdf`, name: `${i}.pdf`, kind: 'pdf', signature: `sig-${i}`, size: 100, page_count: 1, pages: [{ index: 0 }] });
const result = (action, extra = {}) => ({ action, output_files: [], errors: [], cancelled: false, bytes_before: 0, bytes_after: 0, ...extra });
function bridge(run) {
  const listeners = new Set();
  return { onNotification(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    pdfTools: { async run(action, files, options, id) {
      const value = await run(action, files, options);
      setTimeout(() => listeners.forEach(fn => fn({ method: 'task.complete', params: { task_id: id, ok: true, result: value } })), 0);
      return { task_id: id };
    } }, cancelTask: async () => ({ cancelled: true }) };
}

test('visible preview sends only required sources with correctly remapped pages', async () => {
  const calls = [], sources = Array.from({ length: 3000 }, (_, i) => source(i));
  const f = componentFixture('components/common/PdfPageGrid.vue', { sources, pages: [
    { uid: 'last', source: 2999, index: 0, rotation: 0 }, { uid: 'first', source: 0, index: 0, rotation: 90 },
  ] }, { window: { engine: bridge(async (action, files, options) => { calls.push({ files, options }); return result(action, { thumbnails: [] }); }) } });
  try {
    await new Promise(r => setTimeout(r, 20));
    assert.deepEqual(Array.from(calls[0].files), [sources[2999].path, sources[0].path]);
    assert.deepEqual(Array.from(calls[0].options.pages, p => p.source), [0, 1]);
    assert.deepEqual(Array.from(calls[0].options.signatures), ['sig-2999', 'sig-0']);
  } finally { f.dispose(); }
});

test('bulk import inspects new files in bounded batches and retains incremental results', async () => {
  const calls = [], sizes = [];
  let f;
  const engine = bridge(async (action, files) => {
    calls.push(files); sizes.push(f.state.sources.length);
    return result(action, { sources: files.map(p => { const s = source(Number(p.match(/(\d+)\.pdf$/)[1])); return { ...s, pages: [] }; }), ocr: { available: true } });
  });
  f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine } });
  try {
    await f.state.addFiles(Array.from({ length: 45 }, (_, i) => source(i).path));
    assert.ok(calls.length > 1); assert.ok(calls.every(c => c.length <= 16));
    assert.ok(sizes.some(n => n > 0 && n < 45));
    assert.equal(f.state.pages.length, 45);
    f.state.undo(); assert.equal(f.state.pages.length, 0);
    f.state.redo(); assert.equal(f.state.pages.length, 45);
    f.state.selected = ['0:0']; f.state.rotate();
    calls.length = 0;
    await f.state.addFiles([source(0).path, source(45).path]);
    assert.deepEqual(calls.flat(), [source(45).path]);
    assert.equal(f.state.pages.length, 46);
    f.state.undo(); assert.equal(f.state.pages.length, 45); assert.equal(f.state.pages[0].rotation, 90);
    f.state.redo(); assert.equal(f.state.pages.length, 46);
  } finally { f.dispose(); }
});

test('cancel import keeps completed sources and does not start further batches', async () => {
  let calls = 0, f;
  const engine = bridge(async action => {
    calls++;
    queueMicrotask(() => f.state.cancelWork());
    return result(action, { cancelled: true, sources: [{ ...source(0), pages: [] }], ocr: { available: true } });
  });
  f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine } });
  try {
    await f.state.addFiles(Array.from({ length: 50 }, (_, i) => source(i).path));
    assert.equal(calls, 1); assert.equal(f.state.pages.length, 1);
    assert.equal(f.state.busy, false); assert.match(f.state.notice, /取消/);
  } finally { f.dispose(); }
});

test('partial import exposes failures while retaining valid documents', async () => {
  const engine = bridge(async action => result(action, { sources: [{ ...source(0), pages: [] }], errors: ['1.pdf: encrypted'] }));
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine } });
  try {
    await f.state.addFiles([source(0).path, source(1).path]);
    assert.equal(f.state.pages.length, 1); assert.match(f.state.importIssues.join(' '), /encrypted/);
  } finally { f.dispose(); }
});

test('over-limit import is rejected before dispatch and existing workspace is retained', async () => {
  let calls = 0;
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine: bridge(async a => { calls++; return result(a); }) } });
  try {
    await f.state.addFiles(Array.from({ length: 3001 }, (_, i) => source(i).path));
    assert.equal(calls, 0); assert.match(f.state.error, /3000/);
  } finally { f.dispose(); }
});

test('workspace page limit retains accepted files and reports the oversized remainder', async () => {
  const engine = bridge(async action => result(action, { sources: Array.from({ length: 11 }, (_, i) => ({ ...source(i), page_count: 10000, pages: [] })) }));
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine } });
  try {
    await f.state.addFiles(Array.from({ length: 11 }, (_, i) => source(i).path));
    assert.equal(f.state.pages.length, 100000); assert.equal(f.state.sources.length, 10);
    assert.match(f.state.importIssues.join(' '), /100000/);
  } finally { f.dispose(); }
});

test('thumbnail LRU retains revisited pages and enforces item and byte budgets', () => {
  const code = buildSync({ entryPoints: [path.join(__dirname, '../renderer/src/pdf-preview.ts')], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const module = { exports: {} }; vm.runInNewContext(code, { module, exports: module.exports });
  const cache = new module.exports.ThumbnailCache(2, 12);
  cache.set('a', '11'); cache.set('b', '22'); cache.get('a'); cache.set('c', '33');
  assert.equal(cache.get('b'), undefined); assert.equal(cache.get('a'), '11');
  cache.set('d', '44444');
  assert.ok(cache.size <= 2); assert.ok(cache.byteSize <= 12); assert.equal(cache.get('d'), '44444');
});

test('rapid preview navigation cancels the old request and displays only the latest batch', async () => {
  const listeners = new Set(), requests = [];
  let cancels = 0;
  const engine = {
    onNotification(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    pdfTools: { async run(action, files, options, id) { requests.push({ action, files, options, id }); return { task_id: id }; } },
    async cancelTask(id) {
      cancels++;
      queueMicrotask(() => listeners.forEach(fn => fn({ method: 'task.complete', params: { task_id: id, ok: false, cancelled: true, result: result('thumbnails', { cancelled: true }) } })));
      return { cancelled: true };
    },
  };
  const sources = Array.from({ length: 30 }, (_, i) => source(i));
  const f = componentFixture('components/common/PdfPageGrid.vue', { sources, pages: sources.map((s, i) => ({ uid: String(i), source: i, index: 0, rotation: 0 })) }, { window: { engine } });
  try {
    await f.flush(); f.state.batch = 3; await f.flush();
    await new Promise(r => setTimeout(r, 10));
    assert.equal(cancels, 1); assert.equal(requests.length, 2);
    const last = requests[1]; assert.equal(last.files[0], source(24).path);
    listeners.forEach(fn => fn({ method: 'task.complete', params: { task_id: last.id, ok: true, result: result('thumbnails', { thumbnails: last.options.pages.map(p => ({ ...p, data_url: 'fresh' })) }) } }));
    await f.flush();
    assert.equal(Object.keys(f.state.thumbnails).length, 6);
    assert.ok(Object.keys(f.state.thumbnails).every(key => !key.startsWith(source(0).path + ':')));
  } finally { f.dispose(); }
});

test('returning to cached previews clears an error from a different batch', async () => {
  const sources = Array.from({ length: 24 }, (_, i) => source(i));
  const engine = bridge(async (action, files, options) => {
    if (files[0] === source(12).path) throw Error('second batch failed');
    return result(action, { thumbnails: options.pages.map(page => ({ ...page, data_url: 'cached' })) });
  });
  const f = componentFixture('components/common/PdfPageGrid.vue', { sources, pages: sources.map((s, i) => ({ uid: String(i), source: i, index: 0, rotation: 0 })) }, { window: { engine } });
  try {
    await new Promise(r => setTimeout(r, 10));
    assert.equal(Object.keys(f.state.thumbnails).length, 12);
    f.state.batch = 2; await f.flush(); await new Promise(r => setTimeout(r, 10));
    assert.match(f.state.loadError, /second batch failed/);
    f.state.batch = 1; await f.flush();
    assert.equal(Object.keys(f.state.thumbnails).length, 12);
    assert.equal(f.state.loadError, '');
  } finally { f.dispose(); }
});
