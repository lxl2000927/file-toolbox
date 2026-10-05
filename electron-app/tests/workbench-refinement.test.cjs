const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildSync } = require('esbuild');
const vm = require('node:vm');
const path = require('node:path');
function load(file) {
  const code = buildSync({ entryPoints: [path.join(__dirname, '../renderer/src', file)], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const module = { exports: {} }; vm.runInNewContext(code, { module, exports: module.exports }); return module.exports;
}
const pages = Array.from({ length: 8 }, (_, index) => ({ source: 0, index, uid: String(index), rotation: 0 }));
const { componentFixture } = require('./component-fixture.cjs');
test('queued cancellation without an engine result is not a failure and preserves previous completed output', async () => {
  const listeners = new Set(); let taskId;
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { engine: {
    onNotification(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    pdfTools: { async run(_action, _files, _options, id) { taskId = id; return { task_id: id, queued: true, position: 1 }; } },
    cancelTask: async () => ({ cancelled: true }),
  } } });
  const cancel = () => listeners.forEach(fn => fn({ method: 'task.complete', params: { task_id: taskId, ok: false, cancelled: true, error: '已取消' } }));
  try {
    f.state.edit(pages); f.state.outputDir = 'C:/out';
    let pending = f.state.execute(); await new Promise(r => setTimeout(r, 0)); cancel(); await pending;
    assert.equal(f.state.error, ''); assert.equal(f.state.result.cancelled, true); assert.match(f.state.notice, /取消/);
    f.state.result = { action: 'assemble', output_files: ['C:/out/finished.pdf'], errors: [], cancelled: false, bytes_before: 100, bytes_after: 90 };
    pending = f.state.execute(); await new Promise(r => setTimeout(r, 0)); cancel(); await pending;
    assert.equal(f.state.error, ''); assert.match(f.state.notice, /取消.*保留上次/);
    assert.equal(f.state.result.output_files[0], 'C:/out/finished.pdf'); assert.equal(f.state.resultIsPrevious, true);
  } finally { f.dispose(); }
});
test('output preparation prevents duplicate exports and retains partial results with an honest notice', async () => {
  let choose, calls = 0, options;
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: { electronAPI: {
    openDirectoryDialog: () => new Promise(resolve => { choose = resolve; }),
  } } });
  try {
    f.state.edit(pages); f.state.ocr = true;
    f.state.task.run = async (_action, _files, opts) => {
      calls++; options = opts;
      return { action: 'assemble', output_files: ['C:/out/finished.pdf'], errors: ['TXT denied'], cancelled: false, bytes_before: 100, bytes_after: 90 };
    };
    const pending = f.state.execute(); assert.equal(f.state.busy, true);
    await f.state.execute(); assert.equal(calls, 0);
    choose('C:/out'); await pending;
    assert.equal(calls, 1); assert.equal(options.ocr_text, true); assert.equal(f.state.busy, false);
    assert.match(f.state.notice, /部分完成/); assert.equal(f.state.result.output_files.length, 1);
    f.state.task.run = async () => { throw Error('Output denied'); };
    await f.state.execute(); assert.match(f.state.error, /保留上次处理结果/);
    assert.equal(f.state.result.output_files.length, 1);
  } finally { f.dispose(); }
});
test('result component bounds huge output lists and reports native action failure without discarding outputs', async () => {
  const copied = [];
  const result = { action: 'assemble', output_files: Array.from({ length: 100000 }, (_, i) => `C:\\out\\${i}.pdf`), errors: [], cancelled: false, bytes_before: 100, bytes_after: 90 };
  const f = componentFixture('components/common/PdfResults.vue', { result }, { window: { electronAPI: {
    copyText: async text => copied.push(text), openDocument: async () => { throw Error('File missing'); }, saveTextCopy: async () => ({ saved: false }),
  } } });
  try {
    assert.equal(f.state.files.length, 50); assert.equal(f.state.pageCount, 2000);
    f.state.page = 2000; assert.equal(f.state.files.at(-1), result.output_files.at(-1));
    await f.state.act('copy', '完整文字'); assert.equal(copied[0], '完整文字');
    await f.state.act('open', result.output_files[0]); assert.equal(f.state.failed, true); assert.match(f.state.feedback, /File missing/);
    assert.equal(f.props.result.output_files.length, 100000);
    await f.state.act('save', 'C:\\out\\all.txt'); assert.equal(f.state.feedback, ''); assert.equal(f.state.failed, false);
    f.props.result = { ...result, output_files: ['C:\\out\\new.pdf'] }; await f.flush(); assert.equal(f.state.page, 1);
  } finally { f.dispose(); }
});
test('bulk controls replace selection atomically, invert positions, and reject invalid moves', async () => {
  const events = [];
  const f = componentFixture('components/common/PageBulkTools.vue', { pages, selected: ['0', '2'], onSelect: ids => events.push(Array.from(ids)), onMove: pos => events.push(pos) });
  try {
    f.state.expression = '1-2,8'; f.state.choose('range'); assert.deepEqual(events.pop(), ['0', '1', '7']);
    f.state.expression = '1-900'; f.state.choose('range'); assert.equal(events.length, 0); assert.ok(f.state.error);
    f.state.choose('odd'); assert.deepEqual(events.pop(), ['0', '2', '4', '6']);
    f.state.choose('even'); assert.deepEqual(events.pop(), ['1', '3', '5', '7']);
    f.state.choose('invert'); assert.deepEqual(events.pop(), ['1', '3', '4', '5', '6', '7']);
    f.state.position = 8; f.state.move(); assert.equal(events.length, 0);
    f.state.position = 6; f.state.move(); assert.equal(events.pop(), 6);
    f.props.disabled = true; await f.flush(); f.state.choose('odd'); f.state.move(); assert.equal(events.length, 0);
  } finally { f.dispose(); }
});
test('result summaries distinguish partial failure and cancellation and compare only complete matching outputs', () => {
  const { resultSummary, sizeComparison, compressionPreset } = load('pdf-results.ts');
  const r = { output_files: ['a.pdf'], errors: [], cancelled: false, bytes_before: 1000, bytes_after: 700, comparison_eligible: true };
  assert.equal(resultSummary(r).kind, 'success'); assert.match(sizeComparison(r), /减少 30/);
  assert.match(sizeComparison({ ...r, bytes_after: 1200 }), /增加 20/);
  assert.equal(sizeComparison({ ...r, comparison_eligible: false }), '');
  assert.equal(resultSummary({ ...r, errors: ['failed'] }).kind, 'partial');
  assert.equal(resultSummary({ ...r, output_files: [], errors: ['failed'] }).kind, 'error');
  assert.equal(resultSummary({ ...r, cancelled: true }).kind, 'cancelled');
  assert.equal(sizeComparison({ ...r, cancelled: true }), '');
  assert.equal(sizeComparison({ ...r, errors: ['failed'] }), '');
  assert.equal(compressionPreset(150, 78), 'balanced'); assert.equal(compressionPreset(151, 78), 'custom');
});
test('page expressions select unique current positions and reject invalid input atomically', () => {
  const { selectPageRange } = load('pdf-workbench.ts');
  assert.deepEqual(Array.from(selectPageRange(pages, '1-3， 3, 8'), p => p), ['0', '1', '2', '7']);
  for (const text of ['0', '9', '3-1', '1,,2', '1.5', '1-9999999999', '1e2', '']) assert.throws(() => selectPageRange(pages, text));
});
test('selected pages move to a final position as a stable block and can be undone', () => {
  const { movePagesTo, PageHistory } = load('pdf-workbench.ts');
  const h = new PageHistory(pages);
  h.commit(movePagesTo(h.current, ['3', '1'], 6));
  assert.deepEqual(Array.from(h.current, p => p.uid), ['0', '2', '4', '5', '6', '1', '3', '7']);
  assert.deepEqual(Array.from(h.undo(), p => p.uid), Array.from(pages, p => p.uid));
  assert.throws(() => movePagesTo(pages, ['1', '3'], 8));
  assert.throws(() => movePagesTo(pages, ['1'], 0));
});
test('moving all 100000 pages does not exceed the JavaScript argument limit', () => {
  const { movePages } = load('pdf-workbench.ts');
  const many = Array.from({ length: 100000 }, (_, i) => ({ uid: String(i), source: 0, index: i, rotation: 0 }));
  const moved = movePages(many, many.map(p => p.uid), '');
  assert.equal(moved.length, 100000); assert.equal(moved[99999].uid, '99999');
});
test('overlapping bulk ranges remain responsive at 100000 pages', () => {
  const { selectPageRange } = load('pdf-workbench.ts');
  const many = Array.from({ length: 100000 }, (_, i) => ({ uid: String(i), source: 0, index: i, rotation: 0 }));
  const started = performance.now();
  assert.equal(selectPageRange(many, Array(1000).fill('1-100000').join(',')).length, 100000);
  assert.ok(performance.now() - started < 500, 'range selection blocked the UI for more than 500 ms');
});
