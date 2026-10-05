const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');
const vm = require('node:vm');
const { buildSync } = require('esbuild');

function loadResults() {
  const code = buildSync({
    entryPoints: [path.join(__dirname, '../renderer/src/scan-results.ts')],
    bundle: true, platform: 'node', format: 'cjs', write: false,
  }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports });
  return module.exports;
}

test('zero markers and long segments are presented as warnings', () => {
  const { normalizeScanSplitResult, scanCompletionNotice } = loadResults();
  const empty = normalizeScanSplitResult({ output_files: [], marker_pages: [], total_pages: 3,
    warnings: ['未发现标记页，未发生拆分'] });
  assert.equal(scanCompletionNotice(empty).kind, 'warning');
  assert.match(scanCompletionNotice(empty).message, /未发生拆分/);
  const suspect = normalizeScanSplitResult({ output_files: ['part.pdf'], marker_pages: [0], total_pages: 12,
    suspect_segments: [{ index: 1, start_page: 1, end_page: 12, page_count: 12, max_pages: 3 }] });
  assert.equal(scanCompletionNotice(suspect).kind, 'warning');
  assert.match(scanCompletionNotice(suspect).message, /12/);
});

test('partial write failure retains completed files and unfinished segments', () => {
  const { normalizeScanSplitResult, scanCompletionNotice } = loadResults();
  const result = normalizeScanSplitResult({ output_files: ['part-1.pdf'], marker_pages: [0, 1, 2], total_pages: 3,
    error: 'disk full', failed_segments: [{ index: 2, start_page: 2, end_page: 2, page_count: 1 }],
    pending_segments: [{ index: 3, start_page: 3, end_page: 3, page_count: 1 }] });
  assert.equal(result.output_files[0], 'part-1.pdf');
  assert.equal(result.failed_segments[0].index, 2);
  assert.equal(result.pending_segments[0].index, 3);
  assert.equal(scanCompletionNotice(result).kind, 'danger');
  assert.match(scanCompletionNotice(result).message, /disk full/);
  assert.match(scanCompletionNotice(result).message, /保留.*1/);
});
