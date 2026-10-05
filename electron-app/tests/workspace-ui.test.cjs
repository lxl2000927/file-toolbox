const { test } = require('node:test');
const assert = require('node:assert/strict');
const { componentFixture } = require('./component-fixture.cjs');
const source = i => ({ path: `C:/batch/${i}.pdf`, name: `${i}.pdf`, kind: 'pdf', signature: `sig-${i}`, size: 100, page_count: 2, pages: [] });
const saved = { version: 1, sources: [source(0), source(1)], pages: [{ uid: '1:1', source: 1, index: 1, rotation: 90 }, { uid: '0:0', source: 0, index: 0, rotation: 0 }], settings: { filename: '上次编辑', dpi: 220, compression: 'lossless' }, blankIds: ['0:0'] };
const load = (state = saved) => ({ state, savedAt: '2026-10-05T00:00:00.000Z', sources: state.sources.map((s, index) => ({ index, path: s.path, status: index === 1 ? 'missing' : 'ready' })) });
const api = (overrides = {}) => ({ load: async () => load(), save: async () => ({ savedAt: 'now' }), clear: async () => {}, flush: async () => {}, relocate: async () => null, ...overrides });
test('restore completes before any save and preserves page order, rotations, missing source and settings', async () => {
  let resolve, saves = []; const pending = new Promise(r => resolve = r);
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: { workspace: api({ load: () => pending, save: async (_, state) => { saves.push(state); return { savedAt: 'now' }; } }) } } });
  try { await f.flush(); assert.equal(saves.length, 0); assert.equal(f.state.busy, true); resolve(load()); await new Promise(r => setTimeout(r, 5)); await f.flush();
    assert.equal(f.state.filename, '上次编辑'); assert.equal(f.state.pages[0].rotation, 90); assert.equal(f.state.pages[0].uid, '1:1'); assert.equal(f.state.sourceProblems.length, 1); assert.equal(saves.length, 0);
    f.state.selected = ['0:0']; f.state.rotate(); await f.flush(); await f.state.workspace.flush();
    assert.equal(saves.at(-1).pages[1].rotation, 90); assert.equal(saves.at(-1).sources.length, 2);
  } finally { f.dispose(); }
});
test('workspace source navigation finds original page after reorder and limits source DOM slice', async () => {
  const sources = Array.from({ length: 3000 }, (_, i) => source(i));
  const f = componentFixture('components/common/WorkspaceSources.vue', { sources, pages: [{ uid: 'end', source: 2999, index: 1, rotation: 0 }, { uid: 'first', source: 0, index: 0, rotation: 0 }], statuses: [{ index: 2999, path: sources[2999].path, status: 'missing' }] });
  try { assert.ok(f.state.visibleSources.length <= 40); f.state.search = '2999'; await f.flush(); assert.equal(f.state.matchingSources.length, 1);
    f.state.sourceIndex = 2999; f.state.originalPage = 2; f.state.jumpOriginal(); assert.equal(f.state.jumpError, ''); assert.equal(f.state.globalPage, 1);
    f.state.onlyProblems = true; f.state.search = ''; await f.flush(); assert.equal(f.state.matchingSources.length, 1);
  } finally { f.dispose(); }
});
test('grid can jump to global page while missing source previews are excluded', async () => {
  const sources = [source(0), source(1)], calls = [];
  const f = componentFixture('components/common/PdfPageGrid.vue', { sources, pages: Array.from({ length: 30 }, (_, i) => ({ uid: String(i), source: i % 2, index: 0, rotation: 0 })), unavailableSources: [1], jumpPage: 26 }, { window: { engine: { onNotification: () => () => {}, cancelTask: async () => ({ cancelled: true }), pdfTools: { run: async (action, files) => { calls.push(files); throw Error('stub'); } } } } });
  try { await f.flush(); assert.equal(f.state.batch, 3); assert.ok(f.state.visible.length <= 12); assert.ok(calls.every(files => !files.includes(source(1).path))); }
  finally { f.dispose(); }
});
test('scan review restores manually merged segments and emits updates after marker edit', async () => {
  const f = componentFixture('components/panels/ScanReview.vue', { pdfPath: 'C:/scan.pdf', signature: 'sig', total: 4, initialMarkers: [0,2], initialSegments: [[0,1,2,3]], options: {}, outputDir: '', prefix: '', compression: 'lossless', ocr: false });
  try { assert.equal(f.state.segments.length, 1); assert.equal(f.state.segments[0].length, 4); f.state.toggleMarker(3); await f.flush(); assert.equal(f.state.markers.includes(3), true); }
  finally { f.dispose(); }
});
test('scan review bounds the segment navigation list for a 100000-page scan', async () => {
  const initialSegments = Array.from({ length: 100000 }, (_, i) => [i]);
  const f = componentFixture('components/panels/ScanReview.vue', { pdfPath: 'C:/scan.pdf', signature: 'sig', total: 100000, initialMarkers: [], initialSegments, options: {}, outputDir: '', prefix: '', compression: 'lossless', ocr: false });
  try { assert.equal(f.state.visibleSegments.length, 40); f.state.segmentListPage = 2500; await f.flush(); assert.equal(f.state.visibleSegments[0].index, 99960); }
  finally { f.dispose(); }
});
test('scan workspace restores review without invalidating it through input and option watchers', async () => {
  const scan = { version: 1, sources: [{ ...source(0), page_count: 4, role: 'document' }], pages: [], settings: { options: { detection_mode: 'qrcode', dpi: 260, qrcode_no_decode: true, reference_roi: null }, prefix: '复核', compression: 'lossless', useMaxSegment: true }, review: { source: 0, total: 4, markers: [0,2], segments: [[0,1,2,3]], options: { detection_mode: 'qrcode', dpi: 260 }, expanded: true } };
  const saves = [];
  const f = componentFixture('components/panels/ScanSplitPanel.vue', {}, { window: { addEventListener() {}, removeEventListener() {}, electronAPI: { workspace: api({ load: async () => load(scan), save: async (_, value) => { saves.push(value); return { savedAt: 'now' }; } }) } } });
  try { await new Promise(r => setTimeout(r, 5)); await f.flush(); assert.ok(f.state.review); assert.equal(f.state.review.segments.length, 1); assert.equal(f.state.opts.dpi, 260); assert.equal(f.state.opts.qrcode_no_decode, true); assert.equal(saves.length, 0);
    f.state.saveReviewEdits({ markers: [0,3], segments: [[0,1,2],[3]] }); await f.flush(); await f.state.workspace.flush(); assert.deepEqual(Array.from(saves.at(-1).review.markers), [0,3]);
    f.state.opts.dpi = 250; await f.flush(); assert.equal(f.state.review, null);
  } finally { f.dispose(); }
});
test('an immediate flush captures an edit before the autosave debounce expires', async () => {
  const saves = [];
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: { workspace: api({ save: async (_, value) => { saves.push(value); return { savedAt: 'now' }; } }) } } });
  try { await new Promise(r => setTimeout(r, 5)); f.state.filename = '关闭前最后编辑'; await f.state.workspace.flush(); assert.equal(saves.at(-1).settings.filename, '关闭前最后编辑'); }
  finally { f.dispose(); }
});
test('close flush drains edits made while an earlier native save is pending', async () => {
  const saves = []; let release;
  const firstWrite = new Promise(resolve => { release = resolve; });
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: { workspace: api({ save: async (_, value) => { saves.push(value); if (saves.length === 1) await firstWrite; return { savedAt: 'now' }; } }) } } });
  try {
    await new Promise(r => setTimeout(r, 5)); f.state.filename = 'FIRST'; const closing = f.state.workspace.flush();
    while (!saves.length) await new Promise(r => setTimeout(r, 0));
    f.state.filename = 'SECOND'; await f.flush(); release(); await closing;
    assert.equal(saves.at(-1).settings.filename, 'SECOND'); assert.equal(saves.length, 2);
  } finally { release(); f.dispose(); }
});
test('restored workspace refreshes OCR capability from a ready source', async () => {
  const listeners = new Set(); const engine = { onNotification(fn) { listeners.add(fn); return () => listeners.delete(fn); }, cancelTask: async () => ({ cancelled: true }), pdfTools: { async run(action, files, options, task_id) { setTimeout(() => listeners.forEach(fn => fn({ method: 'task.complete', params: { task_id, ok: true, result: { action, sources: [source(0)], output_files: [], errors: [], ocr: { available: true } } } })), 0); return { task_id }; } } };
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine, electronAPI: { workspace: api() } } });
  try { await new Promise(r => setTimeout(r, 25)); assert.equal(f.state.ocrAvailable, true); } finally { f.dispose(); }
});
test('native reselection of changed scan input drops stale review and permits a fresh scan', async () => {
  const scan = { version: 1, sources: [{ ...source(0), page_count: 4, role: 'document' }], pages: [], settings: {}, review: { source: 0, total: 4, markers: [0,2], segments: [[0,1],[2,3]], options: {}, expanded: true } };
  const f = componentFixture('components/panels/ScanSplitPanel.vue', {}, { window: { addEventListener() {}, removeEventListener() {}, electronAPI: { openFileDialog: async () => [source(0).path], workspace: api({ load: async () => ({ ...load(scan), sources: [{ index: 0, path: source(0).path, status: 'changed' }] }) }) } } });
  try { await new Promise(r => setTimeout(r, 5)); assert.equal(f.state.sourceProblems.length, 1); await f.state.pickPdf(); await f.flush(); assert.equal(f.state.review, null); assert.equal(f.state.sourceProblems.length, 0); assert.equal(f.state.snapshotScanWorkspace().sources[0].signature, ''); }
  finally { f.dispose(); }
});
