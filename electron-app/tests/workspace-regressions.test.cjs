const { test } = require('node:test');
const assert = require('node:assert/strict');
const { componentFixture } = require('./component-fixture.cjs');

const source = i => ({ path: `C:/workspace-regression/${i}.pdf`, name: `${i}.pdf`, kind: 'pdf', signature: `sig-${i}`, size: 100, page_count: 2, pages: [] });
const page = (uid, source, index = 0) => ({ uid, source, index, rotation: 0 });
const empty = () => ({ version: 1, sources: [], pages: [], settings: {} });
const loaded = (state, problems = []) => ({ state, savedAt: 'saved', sources: state.sources.map((s, index) => ({ index, path: s.path, status: problems.includes(index) ? 'missing' : 'ready' })) });
const api = overrides => ({ load: async () => loaded(empty()), save: async () => ({ savedAt: 'saved' }), clear: async () => {}, flush: async () => {}, relocate: async () => null, ...overrides });
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
function engine(inspect = path => ({ ...source(0), path })) {
  const listeners = new Set(), calls = [];
  return { calls, onNotification(fn) { listeners.add(fn); return () => listeners.delete(fn); }, cancelTask: async () => ({ cancelled: true }),
    pdfTools: { async run(action, files, options, task_id) {
      calls.push({ action, files, options });
      setTimeout(() => listeners.forEach(fn => fn({ method: 'task.complete', params: { task_id, ok: true,
        result: { action, sources: files.map(inspect), output_files: [], errors: [], bytes_before: 0, bytes_after: 0, ocr: { available: false } } } })), 0);
      return { task_id };
    } } };
}

test('failed initial restore rejects close flush, protects unread state, and can retry recovery before editing', async () => {
  const persisted = { ...empty(), sources: [source(0)], pages: [page('old', 0)], settings: { filename: 'previous work' } };
  let unavailable = true; const saves = [];
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: { workspace: api({
    load: async () => { if (unavailable) throw Error('temporary read failure'); return loaded(persisted); },
    save: async (_, value) => { saves.push(value); return { savedAt: 'saved' }; },
  }) } } });
  try {
    await settle();
    await assert.rejects(f.state.workspace.flush(), /恢复|read failure/);
    assert.equal(f.state.busy, true, 'editor must stay locked while previous workspace is unread');
    assert.equal(saves.length, 0);
    unavailable = false; await f.state.workspace.restore(); await f.flush();
    assert.equal(f.state.busy, false); assert.equal(f.state.filename, 'previous work');
    assert.deepEqual(Array.from(f.state.pages, p => p.uid), ['old']);
    f.state.filename = 'recovered edit'; await f.state.workspace.flush();
    assert.equal(saves.at(-1).settings.filename, 'recovered edit');
    assert.deepEqual(Array.from(saves.at(-1).pages, p => p.uid), ['old']);
  } finally { f.dispose(); }
});

test('filtered Shift selection and select all affect only visible pages', async () => {
  const state = { ...empty(), sources: [source(0), source(1)], pages: [page('a1', 0), page('b1', 1), page('a2', 0, 1)] };
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: { workspace: api({ load: async () => loaded(state) }) } } });
  try {
    await settle(); f.state.select('b1', false); f.state.filterSource(0); await f.flush();
    f.state.select('a1', false); f.state.select('a2', true);
    assert.deepEqual(Array.from(f.state.selected), ['a1', 'a2']);
    f.state.selectAll(); assert.deepEqual(Array.from(f.state.selected), []);
    f.state.selectAll(); assert.deepEqual(Array.from(f.state.selected), ['a1', 'a2']);
    f.state.rotate(); assert.equal(f.state.pages.find(p => p.uid === 'b1').rotation, 0);
    f.state.removeSelected(); assert.deepEqual(Array.from(f.state.pages, p => p.uid), ['b1']);
  } finally { f.dispose(); }
});

test('global bulk selection reveals all selected pages before a filtered workspace can edit them', async () => {
  const state = { ...empty(), sources: [source(0), source(1)], pages: [page('a1', 0), page('b1', 1), page('a2', 0, 1)] };
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: { workspace: api({ load: async () => loaded(state) }) } } });
  let bulk;
  try {
    await settle(); f.state.filterSource(0);
    bulk = componentFixture('components/common/PageBulkTools.vue', { pages: f.state.pages, selected: [], onSelect: ids => f.state.selectBulk(ids) });
    assert.equal(typeof f.state.selectBulk, 'function', 'global selection needs a handler that reveals hidden selected pages');
    bulk.state.expression = '1-2'; bulk.state.choose('range'); await f.flush();
    assert.equal(f.state.sourceFilter, -1);
    assert.deepEqual(Array.from(f.state.shownPages, p => p.uid), ['a1', 'b1', 'a2']);
    assert.deepEqual(Array.from(f.state.selected), ['a1', 'b1']);
  } finally { bulk?.dispose(); f.dispose(); }
});

test('deleted missing sources do not enter export or blank detection, and undo restores their problem', async () => {
  const state = { ...empty(), sources: [source(0), source(1)], pages: [page('missing', 0), page('good', 1, 1)] };
  const bridge = engine();
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine: bridge, electronAPI: { workspace: api({ load: async () => loaded(state, [0]) }) } } });
  try {
    await settle(); f.state.select('missing', false); f.state.removeSelected(); await f.flush();
    assert.equal(f.state.sourceProblems.length, 0);
    f.state.outputDir = 'C:/output'; await f.state.execute();
    const output = bridge.calls.find(call => call.action === 'assemble');
    assert.ok(output); assert.deepEqual(Array.from(output.files), [source(1).path]);
    assert.deepEqual(Array.from(output.options.signatures), ['sig-1']);
    assert.equal(output.options.pages[0].source, 0); assert.equal(output.options.pages[0].index, 1);
    await f.state.detectBlank(); const blank = bridge.calls.find(call => call.action === 'detect_blank');
    assert.deepEqual(Array.from(blank.files), [source(1).path]); assert.equal(blank.options.pages[0].source, 0);
    f.state.undo(); assert.equal(f.state.sourceProblems.length, 1);
    await f.state.execute(); assert.match(f.state.error, /重新定位/);
    assert.equal(bridge.calls.filter(call => call.action === 'assemble').length, 1);
  } finally { f.dispose(); }
});

test('selected-page export ignores unavailable sources outside the requested pages', async () => {
  const state = { ...empty(), sources: [source(0), source(1)], pages: [page('missing', 0), page('good', 1)] };
  const bridge = engine();
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine: bridge, electronAPI: { workspace: api({ load: async () => loaded(state, [0]) }) } } });
  try {
    await settle(); f.state.select('good', false); f.state.outputDir = 'C:/output'; await f.state.execute(true);
    const output = bridge.calls.find(call => call.action === 'assemble');
    assert.ok(output); assert.deepEqual(Array.from(output.files), [source(1).path]); assert.equal(output.options.pages[0].source, 0);
  } finally { f.dispose(); }
});

test('restored OCR capability probes a referenced ready source instead of a deleted missing source', async () => {
  const state = { ...empty(), sources: [source(0), source(1)], pages: [page('good', 1)] };
  const bridge = engine();
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine: bridge, electronAPI: { workspace: api({ load: async () => loaded(state, [0]) }) } } });
  try {
    await settle();
    assert.deepEqual(Array.from(bridge.calls.find(call => call.action === 'inspect').files), [source(1).path]);
  } finally { f.dispose(); }
});

test('clear resets source problems and filter before importing the same native path', async () => {
  const state = { ...empty(), sources: [source(0), source(1)], pages: [page('a', 0), page('b', 1)] };
  const bridge = engine(path => ({ ...source(0), path, signature: 'fresh-native-selection' }));
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine: bridge, electronAPI: {
    openFileDialog: async () => [source(0).path], workspace: api({ load: async () => loaded(state, [0]) }),
  } } });
  try {
    await settle(); f.state.filterSource(1); f.state.selected = ['a', 'b']; f.state.removeSelected();
    await f.state.clear(); await f.state.addFiles(); await f.flush();
    assert.equal(f.state.sourceProblems.length, 0); assert.equal(f.state.sourceFilter, -1);
    assert.equal(f.state.pages.length, 2); assert.equal(f.state.shownPages.length, 2);
    assert.equal(f.state.sources[0].signature, 'fresh-native-selection');
  } finally { f.dispose(); }
});

test('scan reference relocation follows its current index after adding a document', async () => {
  const reference = { ...source(1), role: 'reference', kind: 'image', path: 'C:/workspace-regression/marker.png', page_count: 0 };
  const state = { ...empty(), sources: [reference] }; let persisted = state, relocated;
  const f = componentFixture('components/panels/ScanSplitPanel.vue', {}, { window: { addEventListener() {}, removeEventListener() {}, electronAPI: {
    openFileDialog: async () => [source(0).path], workspace: api({ load: async () => loaded(state, [0]),
      save: async (_, value) => { persisted = value; return { savedAt: 'saved' }; },
      relocate: async (_, index) => { relocated = persisted.sources[index].path; return null; },
    }),
  } } });
  try {
    await settle(); await f.state.pickPdf(); await f.flush();
    const missing = f.state.sourceProblems[0]; await f.state.workspace.relocate(missing.index);
    assert.equal(relocated, reference.path); assert.equal(missing.index, 1);
  } finally { f.dispose(); }
});

test('native scan reselection requests persistence even when blank metadata and path stay identical', async () => {
  const document = { ...source(0), role: 'document', size: 0, signature: '', page_count: 0 };
  const state = { ...empty(), sources: [document] }; const saves = [];
  const f = componentFixture('components/panels/ScanSplitPanel.vue', {}, { window: { addEventListener() {}, removeEventListener() {}, electronAPI: {
    openFileDialog: async () => [document.path], workspace: api({ load: async () => loaded(state),
      save: async (_, value) => { saves.push(value); return { savedAt: 'saved' }; },
    }),
  } } });
  try {
    await settle(); await f.state.workspace.flush(); saves.length = 0;
    await f.state.pickPdf(); await f.state.workspace.flush();
    assert.equal(saves.length, 1); assert.equal(saves[0].sources[0].path, document.path);
  } finally { f.dispose(); }
});

test('scan reference relocation distinguishes a PDF used for both document and reference', async () => {
  const state = { ...empty(), sources: [{ ...source(0), role: 'document' }, { ...source(0), role: 'reference', page_count: 0 }] };
  let relocated;
  const f = componentFixture('components/panels/ScanSplitPanel.vue', {}, { window: { addEventListener() {}, removeEventListener() {}, electronAPI: {
    workspace: api({ load: async () => loaded(state, [1]), relocate: async (_, index) => { relocated = index; return null; } }),
  } } });
  try {
    await settle(); await f.state.workspace.relocate(f.state.sourceProblems[0].index);
    assert.equal(relocated, 1);
  } finally { f.dispose(); }
});
