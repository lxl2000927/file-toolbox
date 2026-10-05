const assert = require('node:assert/strict');
const { test } = require('node:test');
const { componentFixture } = require('./component-fixture.cjs');

function key(key) { return { key, preventDefault() {}, stopPropagation() {} }; }
function select() {
  return componentFixture('components/common/AppSelect.vue', {
    modelValue: '', options: [{ value: '', label: 'Select', disabled: true },
      { value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }],
  });
}

test('select closes on Tab and starts keyboard navigation at an enabled option', async () => {
  const f = select();
  try {
    f.state.onKeydown(key('Enter')); await f.flush();
    assert.equal(f.state.activeIndex, 1);
    f.state.onKeydown(key('End')); await f.flush();
    assert.equal(f.state.activeIndex, 2);
    f.state.onKeydown(key('Home')); await f.flush();
    assert.equal(f.state.activeIndex, 1);
    f.state.onKeydown(key('Tab')); await f.flush();
    assert.equal(f.state.open, false);
  } finally { f.dispose(); }
});

test('select closes when focus leaves, it becomes disabled, or its page is cached', async () => {
  const f = select();
  try {
    f.state.toggle(); await f.flush();
    f.state.onDocumentFocusIn({ target: {} }); await f.flush();
    assert.equal(f.state.open, false);
    f.state.toggle(); await f.flush(); f.props.disabled = true; await f.flush();
    assert.equal(f.state.open, false);
    f.props.disabled = false; await f.flush(); f.state.toggle(); await f.flush();
    await f.deactivate(); assert.equal(f.state.open, false);
  } finally { f.dispose(); }
});

function scan() {
  const f = componentFixture('components/panels/ScanSplitPanel.vue');
  f.state.previewDataUrl = 'data:image/png;base64,test';
  f.state.previewNaturalSize = { width: 1000, height: 1200 };
  f.state.roiNaturalSize = { width: 1000, height: 1200 };
  f.state.previewZoom = 0.5;
  f.state.previewImgRef = { naturalWidth: 1000, naturalHeight: 1200,
    getBoundingClientRect: () => ({ left: 100, top: 100 }) };
  f.state.previewStageRef = { clientWidth: 516, clientHeight: 376, scrollLeft: 0, scrollTop: 0,
    style: {}, setPointerCapture() {}, releasePointerCapture() {} };
  return f;
}
function pointer(f, x, y) { return { button: 0, pointerId: 1, clientX: x, clientY: y, currentTarget: f.state.previewStageRef }; }

test('inline preview draws an ROI in image pixels and pan mode keeps it intact', () => {
  const f = scan();
  try {
    f.state.roiDrawMode = true;
    f.state.onStagePointerDown(pointer(f, 120, 130));
    f.state.onStagePointerMove(pointer(f, 220, 230));
    f.state.onStagePointerUp(pointer(f, 220, 230));
    assert.deepEqual(Array.from(f.state.opts.reference_roi || []), [40, 60, 200, 200]);
    f.state.roiDrawMode = false;
    f.state.onStagePointerDown(pointer(f, 200, 200));
    f.state.onStagePointerMove(pointer(f, 180, 160));
    f.state.onStagePointerUp(pointer(f, 180, 160));
    assert.equal(f.state.previewStageRef.scrollLeft, 20);
    assert.equal(f.state.previewStageRef.scrollTop, 40);
    assert.deepEqual(Array.from(f.state.opts.reference_roi), [40, 60, 200, 200]);
  } finally { f.dispose(); }
});

test('cancelled drawing preserves the committed ROI and releases capture', () => {
  const f = scan();
  try {
    f.state.opts.reference_roi = [10, 20, 30, 40]; f.state.roiDrawMode = true;
    f.state.onStagePointerDown(pointer(f, 120, 130));
    f.state.onStagePointerMove(pointer(f, 220, 230));
    f.state.onRoiPointerCancel(pointer(f, 220, 230));
    assert.deepEqual(Array.from(f.state.opts.reference_roi), [10, 20, 30, 40]);
    assert.equal(f.state.selectionDraft, null);
    assert.equal(f.state.selectionStart, null);
  } finally { f.dispose(); }
});

test('an image loaded in a hidden reference tab fits when that tab becomes visible', async () => {
  const f = scan();
  try {
    f.state.activityTab = 'logs'; await f.flush();
    f.state.previewStageRef.clientWidth = 0; f.state.previewStageRef.clientHeight = 0;
    f.state.onPreviewLoaded();
    f.state.previewStageRef.clientWidth = 516; f.state.previewStageRef.clientHeight = 376;
    f.state.activityTab = 'reference'; await f.flush();
    assert.equal(f.state.previewZoom, 0.3);
    f.state.setPreviewZoom(0.6);
    f.state.activityTab = 'logs'; await f.flush();
    f.state.activityTab = 'reference'; await f.flush();
    assert.equal(f.state.previewZoom, 0.6, 'tab changes preserve manual zoom after fitting');
  } finally { f.dispose(); }
});

test('expanded preview uses its own fit as the zoom lower bound', () => {
  const f = scan();
  try {
    f.state.fitPreviewToStage();
    f.state.roiStageRef = { clientWidth: 224, clientHeight: 264 };
    f.state.roiDialogOpen = true; f.state.fitRoiDialogToStage();
    f.state.setPreviewZoom(0.1);
    assert.equal(f.state.previewZoom, 0.2);
  } finally { f.dispose(); }
});

test('PDF cancellation produces one honest notice and retains completed output', () => {
  let notify;
  const f = componentFixture('components/panels/PdfSplitPanel.vue', {}, { window: {
    engine: { onNotification(fn) { notify = fn; return () => {}; } },
  } });
  const notices = [];
  try {
    for (const kind of ['success', 'info', 'error']) f.state.toast[kind] = (message) => notices.push({ kind, message });
    f.state.startTask('pdf-test');
    notify({ method: 'task.complete', params: { task_id: 'pdf-test', ok: true, cancelled: true,
      result: { total: 2, successful: 1, failed: 0, errors: [], operations: [], output_files: ['completed.pdf'] } } });
    assert.equal(notices.length, 1);
    assert.equal(notices[0].kind, 'info');
    assert.match(notices[0].message, /已取消.*1.*文件/);
    assert.equal(f.state.isCancelledSummary, true);
    assert.equal(f.state.summary.output_files[0], 'completed.pdf');
  } finally { f.dispose(); }
});

test('PDF partial failure is not announced as full success', () => {
  let notify;
  const f = componentFixture('components/panels/PdfSplitPanel.vue', {}, { window: {
    engine: { onNotification(fn) { notify = fn; return () => {}; } },
  } });
  const notices = [];
  try {
    for (const kind of ['success', 'info', 'error']) f.state.toast[kind] = (message) => notices.push({ kind, message });
    f.state.startTask('pdf-test');
    notify({ method: 'task.complete', params: { task_id: 'pdf-test', ok: true,
      result: { total: 2, successful: 1, failed: 1, errors: ['write failed'], operations: [] } } });
    assert.equal(notices.length, 1);
    assert.equal(notices[0].kind, 'info');
    assert.match(notices[0].message, /失败 1/);
  } finally { f.dispose(); }
});

test('ordinary logs do not invent scan settings or zero-valued performance data', () => {
  const f = componentFixture('components/panels/AboutPanel.vue');
  try {
    const text = f.state.formatLogRecord({ timestamp: '2026-10-02 12:30:00', operation_type: 'rename',
      message: '重命名完成', success: true, details: {} });
    assert.doesNotMatch(text, /配置|耗时统计|ROI|标记页/);
    assert.equal(f.state.formatScanOptions(null), '');
    assert.equal(f.state.formatPerformanceStats(undefined), '');
    assert.match(f.state.formatScanOptions({ max_segment_pages: 10 }), /疑似漏检提醒.*10/);
  } finally { f.dispose(); }
});

test('rename sorting menu supports Escape, arrow navigation, and page deactivation', async () => {
  const f = componentFixture('components/panels/RenamePanel.vue');
  try {
    const trigger = { getBoundingClientRect: () => ({ left: 200, bottom: 150 }), isConnected: true,
      focus: () => { f.document.activeElement = trigger; } };
    const items = Array.from({ length: 4 }, () => ({ focus() { f.document.activeElement = this; } }));
    f.state.sortBtnRef = trigger;
    f.state.sortMenuRef = { querySelector: () => items[0], querySelectorAll: () => items };
    const event = { stopPropagation() {}, currentTarget: trigger };
    f.state.openSortMenu(event); await f.flush();
    assert.equal(f.document.activeElement, items[0]);
    f.state.onSortMenuKeydown(key('ArrowDown'));
    assert.equal(f.document.activeElement, items[1]);
    f.state.onSortMenuKeydown(key('Escape')); await f.flush();
    assert.equal(f.state.sortMenuOpen, false);
    assert.equal(f.document.activeElement, trigger);
    f.state.openSortMenu(event); await f.flush();
    await f.deactivate(); assert.equal(f.state.sortMenuOpen, false);
  } finally { f.dispose(); }
});
