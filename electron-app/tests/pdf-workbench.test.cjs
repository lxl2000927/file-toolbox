const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildSync } = require('esbuild');
const path = require('node:path');
const vm = require('node:vm');
const { componentFixture } = require('./component-fixture.cjs');
function load() {
  const code = buildSync({ entryPoints: [path.join(__dirname, '../renderer/src/pdf-workbench.ts')], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const module = { exports: {} }; vm.runInNewContext(code, { module, exports: module.exports }); return module.exports;
}
const plain = value => JSON.parse(JSON.stringify(value));
test('selected pages move as a stable group and can be undone/redone', () => {
  const { PageHistory, movePages } = load();
  const pages = [0, 1, 2, 3].map(i => ({ uid: String(i), source: 0, index: i, rotation: 0 }));
  const history = new PageHistory(pages);
  history.commit(movePages(history.current, ['1', '3'], '0'));
  assert.deepEqual(plain(history.current.map(p => p.index)), [1, 3, 0, 2]);
  assert.deepEqual(plain(history.undo().map(p => p.index)), [0, 1, 2, 3]);
  assert.deepEqual(plain(history.redo().map(p => p.index)), [1, 3, 0, 2]);
  history.undo(); history.commit(history.current.slice(0, 2));
  assert.equal(history.canRedo, false);
});
test('review segments preserve ownership and exclude only explicit marker pages', () => {
  const { reviewSegments } = load();
  assert.deepEqual(plain(reviewSegments(5, [0, 3], true, false)), [[0, 1, 2], [3, 4]]);
  assert.deepEqual(plain(reviewSegments(5, [0, 3], true, true)), [[1, 2], [4]]);
  assert.deepEqual(plain(reviewSegments(5, [1, 3], false, false)), [[0, 1], [2, 3], [4]]);
  assert.deepEqual(plain(reviewSegments(5, [], true, false)), [[0, 1, 2, 3, 4]]);
});
test('interleave preview matches reversed back ordering and retains unequal tail', () => {
  const { interleavePages } = load();
  const pages = [0, 1, 2].map(i => ({ source: 0, index: i, uid: `a${i}` })).concat([0, 1].map(i => ({ source: 1, index: i, uid: `b${i}` })));
  assert.deepEqual(plain(interleavePages(pages, true).map(p => p.uid)), ['a0', 'b1', 'a1', 'b0', 'a2']);
});

test('reactive page objects are snapshotted before crossing the Electron bridge', async () => {
  const { reactive } = require('vue');
  const subscribers = new Set();
  let parameters;
  const f = componentFixture('components/panels/PdfWorkbenchPanel.vue', {}, { window: {
    engine: {
      onNotification(fn) { subscribers.add(fn); return () => subscribers.delete(fn); },
      pdfTools: { async run(action, files, options, taskId) {
        parameters = structuredClone(options);
        queueMicrotask(() => subscribers.forEach(fn => fn({ method: 'task.complete', params: { task_id: taskId, ok: true,
          result: { action, output_files: [], errors: [], cancelled: false, bytes_before: 0, bytes_after: 0 } } })));
        return { task_id: taskId };
      } },
    },
  } });
  try {
    const pages = reactive([{ source: 0, index: 2, rotation: 90 }]);
    const completed = f.state.task.run('thumbnails', ['C:/selected.pdf'], { pages });
    pages[0].index = 8;
    await completed;
    assert.equal(parameters.pages[0].index, 2);
    assert.equal(f.state.busy, false);
  } finally { f.dispose(); }
});

test('review is invalidated after an in-flight export if its input changed', async () => {
  const f = componentFixture('components/panels/ScanSplitPanel.vue');
  try {
    f.state.review = { pdfPath: 'C:/selected.pdf', signature: 'old', total: 3, markers: [0], options: {} };
    f.state.reviewBusy = true;
    f.state.referenceImage = 'C:/different-reference.png';
    await f.flush();
    assert.ok(f.state.review, 'in-flight output retains its own snapshot');
    f.state.reviewBusy = false;
    await f.flush();
    assert.equal(f.state.review, null);
    assert.match(f.state.error, /重新扫描/);
  } finally { f.dispose(); }
});
