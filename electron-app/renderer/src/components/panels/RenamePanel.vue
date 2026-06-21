<script setup lang="ts">
import { ref, computed, watch, toRaw, onBeforeUnmount, onMounted } from "vue";
import InsertTab from "./rename/InsertTab.vue";
import ReplaceTab from "./rename/ReplaceTab.vue";
import DeleteTab from "./rename/DeleteTab.vue";
import SmartTab from "./rename/SmartTab.vue";
import CustomTab from "./rename/CustomTab.vue";
import type { FileOperation, RenameRule, RenamePreviewItem, ExecuteSummary } from "../../env";
import { useAppDialog } from "../../composables/useAppDialog";
import { useToast } from "../../composables/useToast";
import { formatEngineError } from "../../utils";
import AppIcon from "../common/AppIcon.vue";

type TabKey = "insert" | "replace" | "delete" | "smart" | "custom";
type NaturalSortPart = { type: "number"; value: number } | { type: "text"; value: string };

const tabs: { key: TabKey; label: string }[] = [
  { key: "insert", label: "插入" },
  { key: "replace", label: "替换" },
  { key: "delete", label: "删除" },
  { key: "smart", label: "智能识别" },
  { key: "custom", label: "自定义" },
];

const activeTab = ref<TabKey>("insert");
const dialog = useAppDialog();
const toast = useToast();
const files = ref<string[]>([]);
const selected = ref<Set<string>>(new Set());
const previews = ref<RenamePreviewItem[]>([]);
const outputDir = ref("");
const executing = ref(false);
const error = ref("");
const summary = ref<ExecuteSummary | null>(null);
const lastOperations = ref<FileOperation[]>([]);
const undoToken = ref("");
const insertTabRules = ref<RenameRule[]>([]);
const replaceTabRules = ref<RenameRule[]>([]);
const deleteTabRules = ref<RenameRule[]>([]);
const smartTabRules = ref<RenameRule[]>([]);
const customTabRules = ref<RenameRule[]>([]);

const fileBasename = (p: string) => p.split(/[\\/]/).pop() || p;

const sortDir = ref<"asc" | "desc" | null>(null);
const sortMode = ref<"name" | "size">("name");
const sortMenuOpen = ref(false);
const sortMenuStyle = ref<Record<string, string>>({});
const dragOver = ref(false);
const draggingFile = ref<string | null>(null);
const dragTargetFile = ref<string | null>(null);
// #12 拖拽插入位置指示线：before / after / null
const dragTargetPos = ref<"before" | "after" | null>(null);
const rowDragPointerId = ref<number | null>(null);
const rowDragColumnX = ref<number | null>(null);
const rowDragLastMoveAt = ref(0);
const fileSizes = ref<Map<string, number>>(new Map());

const activeRules = computed({
  get: () => {
    if (activeTab.value === "insert") return insertTabRules.value;
    if (activeTab.value === "replace") return replaceTabRules.value;
    if (activeTab.value === "delete") return deleteTabRules.value;
    if (activeTab.value === "smart") return smartTabRules.value;
    return customTabRules.value;
  },
  set: (value: RenameRule[]) => {
    if (activeTab.value === "insert") insertTabRules.value = value;
    else if (activeTab.value === "replace") replaceTabRules.value = value;
    else if (activeTab.value === "delete") deleteTabRules.value = value;
    else if (activeTab.value === "smart") smartTabRules.value = value;
    else customTabRules.value = value;
  },
});

function naturalSortKey(name: string): NaturalSortPart[] {
  const stem = name.replace(/\.[^/.]*$/, "");
  return (stem.match(/\d+|\D+/g) || [stem]).map((part) => (
    /^\d+$/.test(part)
      ? { type: "number", value: Number(part) }
      : { type: "text", value: part.toLocaleLowerCase() }
  ));
}

function compareNaturalNames(a: string, b: string) {
  const ka = naturalSortKey(a);
  const kb = naturalSortKey(b);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const va = ka[i];
    const vb = kb[i];
    if (!va) return -1;
    if (!vb) return 1;
    if (va.type === vb.type) {
      if (va.value < vb.value) return -1;
      if (va.value > vb.value) return 1;
    } else {
      return va.type === "number" ? -1 : 1;
    }
  }
  return 0;
}

const sortedFiles = computed(() => {
  if (!sortDir.value) return files.value;
  const arr = [...files.value];
  const insertIndex = new Map(files.value.map((file, index) => [file, index]));
  arr.sort((a, b) => {
    if (sortMode.value === "size") {
      const sa = fileSizes.value.get(a) || 0;
      const sb = fileSizes.value.get(b) || 0;
      if (sa !== sb) return sortDir.value === "asc" ? sa - sb : sb - sa;
    } else {
      const result = compareNaturalNames(fileBasename(a), fileBasename(b));
      if (result !== 0) return sortDir.value === "asc" ? result : -result;
    }
    return (insertIndex.get(a) || 0) - (insertIndex.get(b) || 0);
  });
  return arr;
});

const targetFiles = computed(() =>
  sortedFiles.value.filter((p) => selected.value.has(p)),
);

function isEffectiveRule(rule: RenameRule) {
  if (rule.type === "insert_text") return Boolean(String(rule.text || "").trim());
  if (rule.type === "replace_text") return Boolean(String(rule.find || ""));
  if (rule.type === "delete_chars") {
    if (rule.delete_type === "删除指定字符") return Boolean(String(rule.chars || "").trim());
    if (rule.delete_type === "删除前N个字符" || rule.delete_type === "删除后N个字符") return Number(rule.count || 0) > 0;
    return Boolean((rule.targets || []).length || String(rule.custom_chars || "").trim());
  }
  if (rule.type === "keep_chars") {
    return rule.mode === "range"
      ? Boolean(String(rule.range || "").trim())
      : Boolean(String(rule.chars || "").trim());
  }
  if (rule.type === "uniform_name") return Boolean(String(rule.base_name || "").trim());
  if (rule.type === "insert_number") {
    // 即使未配置也返回 true（引擎侧会忽略无意义的编号规则），但可给出 UI 提示
    return true;
  }
  if (rule.type === "change_extension") {
    return Boolean(String(rule.new_ext || "").trim());
  }
  if (rule.type === "smart_recognize") {
    return Boolean(rule.mode && typeof rule.mode === "string");
  }
  return true;
}

const currentRules = computed(() => {
  const source = [
    ...insertTabRules.value,
    ...replaceTabRules.value,
    ...deleteTabRules.value,
    ...smartTabRules.value,
    ...customTabRules.value,
  ];
  // 所有规则类型统一去重：每种 type 只保留最后出现的那条（由 Tab 优先级决定）
  // 注意：insert_number 类型在 InsertTab 和 CustomTab 都可能被添加，
  // 两者都会管理编号规则，后修改者生效（覆盖前者）。
  // 若需独立控制，可在 UI 上对 CustomTab 的 insert_number 添加做禁用提示。
  const result: RenameRule[] = [];
  for (const rule of source) {
    if (!isEffectiveRule(rule)) continue;
    const idx = result.findIndex((r) => r.type === rule.type);
    if (idx >= 0) {
      result[idx] = rule;
    } else {
      result.push(rule);
    }
  }
  return result;
});

const bridgeFiles = computed(() => [...targetFiles.value]);
const bridgeRules = computed(() => currentRules.value.map((rule) => structuredClone(toRaw(rule))));

function toggleNameSort() {
  const wasNameSort = sortMode.value === "name";
  sortMode.value = "name";
  sortDir.value = !wasNameSort || !sortDir.value || sortDir.value === "desc" ? "asc" : "desc";
}

function setSort(mode: "name" | "size", dir: "asc" | "desc") {
  sortMode.value = mode;
  sortDir.value = dir;
  sortMenuOpen.value = false;
}

function beginRowDrag(path: string, e: PointerEvent) {
  e.preventDefault();
  e.stopPropagation();
  if (sortDir.value) {
    files.value = [...sortedFiles.value];
    sortDir.value = null;
  }
  draggingFile.value = path;
  dragTargetFile.value = null;
  rowDragPointerId.value = e.pointerId;
  const opsCell = (e.currentTarget as HTMLElement).closest("td.col-ops");
  const rect = opsCell?.getBoundingClientRect();
  rowDragColumnX.value = rect ? rect.left + rect.width / 2 : e.clientX;
  document.body.style.cursor = "grabbing";
  document.body.style.userSelect = "none";
  window.addEventListener("pointermove", onRowDragMove);
  window.addEventListener("pointerup", endRowDrag);
  window.addEventListener("pointercancel", cancelRowDrag);
}

function onFileAreaDragOver(e: DragEvent) {
  if (draggingFile.value) {
    e.preventDefault();
    dragOver.value = false;
    return;
  }
  e.preventDefault();
  dragOver.value = true;
}

function onRowDragMove(e: PointerEvent) {
  if (!draggingFile.value || (rowDragPointerId.value !== null && e.pointerId !== rowDragPointerId.value)) return;
  const now = performance.now();
  if (now - rowDragLastMoveAt.value < 18) return;
  const element = document.elementFromPoint(rowDragColumnX.value ?? e.clientX, e.clientY) as HTMLElement | null;
  const opsCell = element?.closest("td.col-ops");
  const row = opsCell?.closest<HTMLTableRowElement>("tr[data-file-path]");
  const targetPath = row?.dataset.filePath || "";
  if (!row || !targetPath || targetPath === draggingFile.value) {
    dragTargetFile.value = null;
    dragTargetPos.value = null;
    return;
  }
  dragTargetFile.value = targetPath;
  // #12 根据指针 Y 与目标行中点判断插入位置（before / after）
  const rowRect = row.getBoundingClientRect();
  const midY = rowRect.top + rowRect.height / 2;
  const insertAfter = e.clientY >= midY;
  dragTargetPos.value = insertAfter ? "after" : "before";
  const sourceIndex = files.value.indexOf(draggingFile.value);
  const targetIndex = files.value.indexOf(targetPath);
  if (sourceIndex < 0 || targetIndex < 0) return;
  // 实时重排：让用户直观看到拖拽效果；指示线由 dragTargetPos 驱动
  if (moveFileNearTarget(draggingFile.value, targetPath, insertAfter)) rowDragLastMoveAt.value = now;
}

function moveFileNearTarget(sourcePath: string, targetPath: string, insertAfter: boolean) {
  const next = [...files.value];
  const sourceIndex = next.indexOf(sourcePath);
  if (sourceIndex < 0) return false;
  next.splice(sourceIndex, 1);
  const targetIndex = next.indexOf(targetPath);
  if (targetIndex < 0) return false;
  const insertIndex = targetIndex + (insertAfter ? 1 : 0);
  next.splice(insertIndex, 0, sourcePath);
  const changed = next.some((file, index) => file !== files.value[index]);
  if (changed) files.value = next;
  return changed;
}

function cleanupRowDrag() {
  window.removeEventListener("pointermove", onRowDragMove);
  window.removeEventListener("pointerup", endRowDrag);
  window.removeEventListener("pointercancel", cancelRowDrag);
  rowDragPointerId.value = null;
  rowDragColumnX.value = null;
  rowDragLastMoveAt.value = 0;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
}

function finishRowDrag() {
  cleanupRowDrag();
  draggingFile.value = null;
  dragTargetFile.value = null;
  dragTargetPos.value = null;
}

function cancelRowDrag() {
  finishRowDrag();
}

function endRowDrag(e: PointerEvent) {
  if (rowDragPointerId.value !== null && e.pointerId !== rowDragPointerId.value) return;
  finishRowDrag();
  summary.value = null;
}

function openSortMenu(e: MouseEvent) {
  e.stopPropagation();
  const btn = e.currentTarget as HTMLElement;
  const rect = btn.getBoundingClientRect();
  const menuWidth = 150;
  const left = Math.min(rect.left, window.innerWidth - menuWidth - 8);
  sortMenuStyle.value = {
    top: `${rect.bottom + 4}px`,
    left: `${Math.max(8, left)}px`,
  };
  sortMenuOpen.value = !sortMenuOpen.value;
}

function closeSortMenu() {
  sortMenuOpen.value = false;
}

onBeforeUnmount(() => {
  if (previewTimer.value !== null) {
    clearTimeout(previewTimer.value);
    previewTimer.value = null;
  }
  document.removeEventListener("click", closeSortMenu);
  cleanupRowDrag();
  // 防御性移除列宽拖拽监听器，避免组件销毁时残留
  document.removeEventListener("mousemove", onColMove);
  document.removeEventListener("mouseup", onColUp);
});

onMounted(() => {
  document.addEventListener("click", closeSortMenu);
});

const previewTimer = ref<number | null>(null);
const previewToken = ref(0);

watch(
  [files, insertTabRules, replaceTabRules, deleteTabRules, smartTabRules, customTabRules, selected, activeTab, sortDir, sortMode],
  () => {
    schedulePreview();
  },
  { deep: true },
);

function schedulePreview() {
  if (previewTimer.value !== null) window.clearTimeout(previewTimer.value);
  previewTimer.value = window.setTimeout(() => doPreview(), 220);
}

async function doPreview() {
  if (executing.value || !bridgeFiles.value.length || !window.engine || !bridgeRules.value.length) {
    previewToken.value++;
    previews.value = [];
    return;
  }
  const token = ++previewToken.value;
  const filesSnapshot = bridgeFiles.value;
  const rulesSnapshot = bridgeRules.value;
  try {
    error.value = "";
    const result = await window.engine.rename.preview(filesSnapshot, rulesSnapshot);
    if (token === previewToken.value) previews.value = result;
  } catch (e: any) {
    if (token === previewToken.value) error.value = formatEngineError(e);
  }
}

const previewMap = computed(() => {
  const m = new Map<string, string>();
  for (const it of previews.value) m.set(it.old, it.new);
  return m;
});
const renameBannerKind = computed<"info" | "success" | "warning" | "danger">(() => {
  if (executing.value) return "info";
  if (error.value) return "danger";
  if (summary.value) return summary.value.failed === 0 ? "success" : "warning";
  return "info";
});
const renameBannerIcon = computed<"rename" | "alert" | "check">(() => {
  if (error.value) return "alert";
  if (summary.value && summary.value.failed === 0) return "check";
  return "rename";
});
const renameBannerMessage = computed(() => {
  if (executing.value) return `正在重命名 ${targetFiles.value.length} 个文件…`;
  if (error.value) return error.value;
  if (summary.value) return `执行完成：成功 ${summary.value.successful} / ${summary.value.total}，失败 ${summary.value.failed}`;
  if (!files.value.length) return "添加文件后选择规则，左侧实时查看原文件名与新文件名预览。";
  if (!currentRules.value.length) return `已添加 ${files.value.length} 个文件，选择或填写规则后开始生成预览。`;
  if (previews.value.length) return `实时预览已更新：已选择 ${targetFiles.value.length} / ${files.value.length} 个文件。`;
  return `已选择 ${targetFiles.value.length} / ${files.value.length} 个文件，规则准备就绪。`;
});

async function addFiles() {
  const result = await window.electronAPI?.openFileDialog({ multi: true });
  if (result?.length) appendFiles(result);
}

function appendFiles(paths: string[]) {
  const set = new Set(files.value);
  const selectedSet = new Set(selected.value);
  const addedPaths: string[] = [];
  for (const p of paths) {
    if (!p) continue;
    if (!set.has(p)) addedPaths.push(p);
  }
  const MAX_FILES = 5000;
  if (addedPaths.length + files.value.length > MAX_FILES) {
    const allowed = Math.max(0, MAX_FILES - files.value.length);
    if (allowed <= 0) {
      toast.info(`文件数量已达上限 ${MAX_FILES}，无法继续添加`);
      return;
    }
    addedPaths.splice(allowed);
    toast.info(`文件数量超过上限 ${MAX_FILES}，仅添加前 ${allowed} 个`);
  }
  for (const p of addedPaths) set.add(p);
  const prevCount = files.value.length;
  files.value = Array.from(set);
  const addedCount = files.value.length - prevCount;
  if (addedCount > 0) toast.success(`已添加 ${addedCount} 个文件`);
  for (const p of addedPaths) selectedSet.add(p);
  selected.value = new Set(files.value.filter((p) => selectedSet.has(p)));
  summary.value = null;
  updateFileSizes(addedPaths);
}

function updateFileSizes(paths: string[], replace = false) {
  if (replace) fileSizes.value = new Map();
  const targetPaths = Array.from(new Set(paths)).filter(Boolean);
  if (!targetPaths.length) return;
  if (window.electronAPI?.statPaths) {
    window.electronAPI.statPaths(targetPaths).then((stats) => {
      for (const s of stats) {
        if (s.isFile && s.size >= 0) {
          fileSizes.value.set(s.path, s.size);
        }
      }
      fileSizes.value = new Map(fileSizes.value);
    });
  }
}

function clearFiles() {
  files.value = [];
  previews.value = [];
  selected.value = new Set();
  fileSizes.value = new Map();
  summary.value = null;
  lastOperations.value = [];
}


function removeFile(path: string) {
  files.value = files.value.filter((p) => p !== path);
  selected.value.delete(path);
  selected.value = new Set(selected.value);
  fileSizes.value.delete(path);
  fileSizes.value = new Map(fileSizes.value);
  summary.value = null;
}

function toggleAll(checked: boolean) {
  if (checked) selected.value = new Set(files.value);
  else selected.value = new Set();
}

function invertSelection() {
  const next = new Set<string>();
  for (const file of files.value) {
    if (!selected.value.has(file)) next.add(file);
  }
  selected.value = next;
}

function toggleOne(path: string, checked: boolean) {
  const next = new Set(selected.value);
  if (checked) next.add(path);
  else next.delete(path);
  selected.value = next;
}

const allChecked = computed(
  () => files.value.length > 0 && selected.value.size === files.value.length,
);
const indeterminate = computed(
  () => selected.value.size > 0 && selected.value.size < files.value.length,
);
const counterText = computed(() => `${selected.value.size}/${files.value.length}`);

const canRun = computed(() => targetFiles.value.length > 0 && currentRules.value.length > 0 && !executing.value);
const canUndo = computed(
  () => !executing.value && Boolean(undoToken.value) && lastOperations.value.some((o) => o.success),
);

function effectiveSaveMethod() {
  return outputDir.value ? "copy" : "overwrite";
}

async function execute() {
  if (!canRun.value || !window.engine) return;
  executing.value = true;
  error.value = "";
  summary.value = null;
  try {
    const result = await window.engine.rename.execute(
      bridgeFiles.value,
      bridgeRules.value,
      effectiveSaveMethod(),
      outputDir.value,
    );
    summary.value = result;
    if (result.failed === 0) toast.success(`重命名完成：成功 ${result.successful} 个文件`);
    else toast.error(`重命名：成功 ${result.successful}，失败 ${result.failed}`);
    // #20 copy 与 overwrite 都生成 undo_token，都支持撤销，不再按 saveMethod 丢弃
    lastOperations.value = result.operations || [];
    undoToken.value = result.undo_token || "";
    // #21 overwrite 模式下部分成功也要按 operations 更新列表，避免显示已被 rename 走的原路径
    if (effectiveSaveMethod() === "overwrite" && result.operations) {
      const opMap = new Map<string, string>();
      const reverseMap = new Map<string, string>();
      for (const op of result.operations) {
        if (op.success && op.original_path && op.new_path) {
          opMap.set(op.original_path, op.new_path);
          reverseMap.set(op.new_path, op.original_path);
        }
      }
      if (opMap.size) {
        files.value = files.value.map((p) => opMap.get(p) || p);
        // 同步 selected：把旧路径上的选中状态迁移到新路径
        const newSelected = new Set<string>();
        for (const p of files.value) {
          const oldP = reverseMap.get(p) || p;
          if (selected.value.has(oldP)) newSelected.add(p);
        }
        selected.value = newSelected;
        // 刷新 fileSizes：路径已变更
        updateFileSizes(files.value, true);
      }
    }
  } catch (e: any) {
    error.value = formatEngineError(e);
  } finally {
    executing.value = false;
  }
  await doPreview();
}

async function undo() {
  if (!canUndo.value || !window.engine) return;
  const confirmed = await dialog.confirm({
    title: "撤销重命名",
    message: "确定要撤销上次的重命名操作吗？此操作会将已覆盖重命名的文件恢复为原路径。",
    kind: "warning",
    confirmText: "确认撤销",
  });
  if (!confirmed) return;
  try {
    const r = await window.engine.rename.undo(undoToken.value);
    // #25 只对 restored 中的 from→to 做映射，failed 的 path 保留当前路径
    if (r?.restored?.length) {
      const restoreMap = new Map<string, string>();
      for (const item of r.restored) {
        restoreMap.set(item.from, item.to);
      }
      files.value = files.value.map((p) => restoreMap.get(p) || p);
      selected.value = new Set(files.value);
    }
    // failed 的文件保持当前路径不变
    lastOperations.value = [];
    undoToken.value = "";
    summary.value = null;
    if (r?.failed?.length) error.value = `部分撤销失败：${r.failed.map((it: any) => it.error || it.path).join("；")}`;
    else error.value = "";
    await doPreview();
  } catch (e: any) {
    error.value = formatEngineError(e);
  }
}

async function pickOutputDir() {
  const dir = await window.electronAPI?.openDirectoryDialog({ title: "选择重命名输出目录" });
  if (dir) {
    outputDir.value = dir;
  }
}

async function onDrop(e: DragEvent) {
  e.preventDefault();
  if (draggingFile.value) {
    dragOver.value = false;
    return;
  }
  dragOver.value = false;
  const dt = e.dataTransfer;
  if (!dt) return;
  const paths = await window.electronAPI?.getPathsForFiles(Array.from(dt.files || []));
  if (paths?.length) appendFiles(paths);
}

const colCheckWidth = ref(300);
const isResizingCol = ref(false);
const renameTableRef = ref<HTMLTableElement | null>(null);

const colCheckMinWidth = 190;
const colNameMinWidth = 220;
const colOpsWidth = 52;

function startColResize(e: MouseEvent) {
  e.preventDefault();
  isResizingCol.value = true;
  colResizeStartX = e.clientX;
  colResizeStartW = colCheckWidth.value;
  colResizeTable = renameTableRef.value;
  document.addEventListener("mousemove", onColMove);
  document.addEventListener("mouseup", onColUp);
}

// 列宽拖拽处理器提升为组件级命名函数，便于 onBeforeUnmount 防御性移除
let colResizeStartX = 0;
let colResizeStartW = 0;
let colResizeTable: HTMLTableElement | null = null;
function onColMove(ev: MouseEvent) {
  const delta = ev.clientX - colResizeStartX;
  const tableWidth = colResizeTable?.clientWidth || 0;
  const maxW = tableWidth
    ? tableWidth - colNameMinWidth - colOpsWidth
    : colResizeStartW;
  const newW = Math.max(colCheckMinWidth, Math.min(colResizeStartW + delta, Math.max(colCheckMinWidth, maxW)));
  colCheckWidth.value = newW;
}
function onColUp() {
  isResizingCol.value = false;
  document.removeEventListener("mousemove", onColMove);
  document.removeEventListener("mouseup", onColUp);
}
</script>

<template>
  <div class="rename-shell panel-shell panel-shell-responsive">
    <div class="banner panel-header rename-banner" :class="`banner-${renameBannerKind}`">
      <span class="banner-icon"><AppIcon :name="renameBannerIcon" /></span>
      <span class="banner-title">批量重命名</span>
      <span class="banner-text">{{ renameBannerMessage }}</span>
      <span class="banner-kbd">支持拖拽</span>
    </div>

    <!-- 主体：左大表 + 右 Tab 规则 -->
    <div class="rename-grid panel-grid">
      <!-- 左：文件名表格 -->
      <fieldset class="files-section glass-card section-card">
        <div class="toolbar section-toolbar">
          <button class="btn btn-primary btn-pill" @click="addFiles">添加文件</button>
          <button class="btn btn-secondary btn-pill" :disabled="!files.length" @click="clearFiles">
            清空列表
          </button>
          <span class="flex-1" />
          <button class="btn btn-secondary btn-pill" :disabled="!files.length" @click="invertSelection">反选</button>
        </div>
        <div class="table-wrap drop-area" :class="{ dragging: dragOver }" @dragover="onFileAreaDragOver" @dragleave="dragOver = false" @drop="onDrop($event)">
          <table
            v-if="files.length"
            ref="renameTableRef"
            class="data-table rename-table"
            :class="{ 'row-sorting': draggingFile }"
            :style="{ '--col-check-width': colCheckWidth + 'px' }"
          >
            <colgroup>
              <col class="col-check-def" />
              <col class="col-name-def" />
              <col class="col-ops-def" />
            </colgroup>
            <thead>
              <tr>
                <th class="col-check" scope="col">
                  <div class="header-sort-cell">
                    <label class="checkbox header-checkbox" @click.stop>
                    <input
                      type="checkbox"
                      :checked="allChecked"
                      :indeterminate.prop="indeterminate"
                      @change="toggleAll(($event.target as HTMLInputElement).checked)"
                    />
                    </label>
                    <button class="header-label" type="button" @click="toggleNameSort">
                      原文件名({{ counterText }})
                    </button>
                    <button
                      class="sort-btn"
                      type="button"
                      title="排序选项"
                      aria-label="打开排序选项"
                      @click="openSortMenu"
                    >
                      <span class="sort-indicator" :class="{ unsorted: !sortDir, asc: sortDir === 'asc', desc: sortDir === 'desc' }">
                        <span class="sort-triangle sort-triangle-up"></span>
                        <span class="sort-triangle sort-triangle-down"></span>
                      </span>
                    </button>
                  </div>
                  <span
                    class="col-resize-handle"
                    :class="{ active: isResizingCol }"
                    @mousedown="startColResize"
                  ></span>
                </th>
                <th class="col-name" scope="col">
                  新文件名
                </th>
                <th class="col-ops" scope="col"></th>
              </tr>
            </thead>
            <!-- TODO: 引入虚拟滚动（vue-virtual-scroller）支持大量文件 -->
            <tbody>
              <tr
                v-for="f in sortedFiles"
                :key="f"
                :data-file-path="f"
                :class="{
                  'diff-row': previewMap.get(f) && previewMap.get(f) !== fileBasename(f),
                  'row-dragging': draggingFile === f,
                  'row-drop-target': dragTargetFile === f,
                  'row-drop-target-before': dragTargetFile === f && dragTargetPos === 'before',
                  'row-drop-target-after': dragTargetFile === f && dragTargetPos === 'after',
                }"
              >
                <td class="col-check">
                  <label class="checkbox">
                    <input
                      type="checkbox"
                      :checked="selected.has(f)"
                      @change="toggleOne(f, ($event.target as HTMLInputElement).checked)"
                    />
                    <span class="truncate selectable" :title="f" :class="{ 'diff-old': previewMap.get(f) && previewMap.get(f) !== fileBasename(f) }">{{ fileBasename(f) }}</span>
                  </label>
                </td>
                <td class="col-name truncate selectable" :title="previewMap.get(f) || ''">
                  <span v-if="previewMap.get(f)" :class="{ 'diff-new': previewMap.get(f) !== fileBasename(f) }">{{ previewMap.get(f) }}</span>
                  <span v-else class="text-muted">—</span>
                </td>
                <td class="col-ops">
                  <div class="row-actions">
                    <button
                      class="row-drag-handle"
                      type="button"
                      title="拖动调整顺序"
                      aria-label="拖动调整文件顺序"
                      @pointerdown="beginRowDrag(f, $event)"
                    >
                      <span class="drag-handle-icon" aria-hidden="true"></span>
                    </button>
                    <button class="row-remove-btn" type="button" title="移除" aria-label="从列表移除文件" @click="removeFile(f)">✕</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-state empty-drop-state">
            <div class="empty-icon">📂</div>
            <div class="empty-title">还没有添加文件</div>
            <div class="empty-hint">点击左上角「添加文件」或拖拽文件到此处</div>
          </div>
        </div>
      </fieldset>

      <!-- 右：Tab 规则面板 -->
      <section class="rules-section">
        <div
          class="tab-bar segmented-control segmented-animated"
          :style="{ '--active-index': Math.max(0, tabs.findIndex((t) => t.key === activeTab)), '--segment-count': tabs.length }"
        >
          <button
            v-for="t in tabs"
            :key="t.key"
            class="tab segmented-item"
            :class="{ active: activeTab === t.key }"
            @click="activeTab = t.key"
          >{{ t.label }}</button>
        </div>
        <div class="tab-body glass-card section-card">
          <InsertTab v-if="activeTab === 'insert'" :rules="insertTabRules" @update:rules="insertTabRules = $event" />
          <ReplaceTab v-else-if="activeTab === 'replace'" :rules="replaceTabRules" @update:rules="replaceTabRules = $event" />
          <DeleteTab v-else-if="activeTab === 'delete'" :rules="deleteTabRules" @update:rules="deleteTabRules = $event" />
          <SmartTab v-else-if="activeTab === 'smart'" :rules="smartTabRules" @update:rules="smartTabRules = $event" />
          <CustomTab v-else :rules="customTabRules" @update:rules="customTabRules = $event" />
        </div>

        <!-- 底部：选项与操作 -->
        <fieldset class="action-bar glass-card section-card action-footer">
          <!-- 选项与操作：一行内平铺 -->
          <div class="action-row action-main">
            <div class="option-group output-group">
              <input class="input flex-1" :value="outputDir" placeholder="输出目录（留空则覆盖原文件）" readonly :title="outputDir" />
              <button class="btn btn-outline btn-sm" @click="pickOutputDir">选择目录</button>
              <button v-if="outputDir" class="btn btn-secondary btn-sm" @click="outputDir = ''">清除</button>
            </div>
            <span class="flex-1" />
            <div class="action-controls">
              <button class="btn btn-outline" :disabled="!canUndo" @click="undo">撤销</button>
              <button
                class="btn btn-primary btn-action"
                :disabled="!canRun"
                :aria-busy="executing"
                @click="execute"
              >
                <span v-if="executing" class="btn-spinner" aria-hidden="true" />
                {{ executing ? "执行中…" : "开始重命名" }}
              </button>
            </div>
          </div>
          <div v-if="error" class="error-line">{{ error }}</div>
          <div v-else-if="summary" class="summary-line" :class="summary.failed === 0 ? 'ok' : 'warn'">
            执行完成 · 成功 {{ summary.successful }} / {{ summary.total }}，失败 {{ summary.failed }}
          </div>
        </fieldset>
      </section>
    </div>

    <Teleport to="body">
      <div v-if="sortMenuOpen" class="sort-menu" :style="sortMenuStyle" @click.stop>
        <button class="sort-menu-item" :class="{ active: sortMode === 'name' && sortDir === 'asc' }" @click="setSort('name', 'asc')">按文件名升序</button>
        <button class="sort-menu-item" :class="{ active: sortMode === 'name' && sortDir === 'desc' }" @click="setSort('name', 'desc')">按文件名降序</button>
        <div class="sort-menu-sep"></div>
        <button class="sort-menu-item" :class="{ active: sortMode === 'size' && sortDir === 'asc' }" @click="setSort('size', 'asc')">按文件大小升序</button>
        <button class="sort-menu-item" :class="{ active: sortMode === 'size' && sortDir === 'desc' }" @click="setSort('size', 'desc')">按文件大小降序</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.rename-grid {
  --panel-grid-columns: minmax(470px, 1.18fr) minmax(320px, 1fr);
}
.table-wrap {
  flex: 1 1 0;
  min-height: 0;
  overflow: auto;
  border: 0.5px solid var(--glass-border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.3);
}
.empty-drop-state {
  width: 100%;
  height: 100%;
  min-height: 240px;
  box-sizing: border-box;
}
.rename-table {
  --col-check-min-width: 190px;
  --col-name-min-width: 220px;
  --col-ops-width: 70px;
  font-size: var(--font-md);
  table-layout: fixed;
  width: 100%;
  min-width: calc(var(--col-check-min-width) + var(--col-name-min-width) + var(--col-ops-width));
}
.rename-table .col-check-def {
  width: var(--col-check-width, 300px);
}
.rename-table .col-name-def {
  width: auto;
}
.rename-table .col-ops-def {
  width: var(--col-ops-width);
}
.rename-table th {
  background: var(--color-gray-50);
  color: var(--color-gray-700);
  font-weight: 600;
  white-space: nowrap;
  height: 28px;
  position: relative;
}
.rename-table th,
.rename-table td {
  overflow: hidden;
}
.rename-table .col-name {
  min-width: var(--col-name-min-width);
}
.rename-table .col-ops {
  width: var(--col-ops-width);
  min-width: var(--col-ops-width);
  max-width: var(--col-ops-width);
  padding-left: 8px;
  padding-right: 12px;
  text-align: center;
}
.rename-table th.col-check span { font-weight: 700; color: var(--color-gray-800); }
.rename-table td.col-check .checkbox span { color: var(--color-gray-800); }
.table-wrap .empty-state {
  padding: 48px 16px;
  color: var(--color-gray-500);
}
.empty-title { font-size: var(--font-md); font-weight: 600; color: var(--color-gray-700); }

/* 文件行悬浮微浮 */
.rename-table tbody tr {
  position: relative;
  transition: background-color var(--transition-fast), opacity 120ms ease, transform 170ms cubic-bezier(0.2, 0.8, 0.2, 1), filter 140ms ease, box-shadow 140ms ease;
}
.rename-row-move {
  transition: transform 170ms cubic-bezier(0.2, 0.8, 0.2, 1);
}
.rename-table.row-sorting {
  cursor: grabbing;
}
.rename-table.row-sorting tbody tr:not(.row-dragging) {
  opacity: 0.72;
  filter: saturate(0.86);
}
.rename-table.row-sorting tbody tr:hover {
  background: transparent;
}
.rename-table tbody tr:hover {
  background: rgba(37, 99, 235, 0.055);
}
.rename-table tbody tr.row-dragging {
  opacity: 1;
  filter: none;
  background: rgba(255, 255, 255, 0.96);
  transform: translateY(-2px) scale(1.012);
  z-index: 4;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.18), 0 0 0 1px rgba(37, 99, 235, 0.16);
}
.rename-table tbody tr.row-drop-target {
  background: rgba(37, 99, 235, 0.035);
}
.rename-table.row-sorting tbody tr.row-drop-target:not(.row-dragging) {
  opacity: 0.86;
}
/* #12 拖拽插入位置指示线 */
.rename-table tbody tr.row-drop-target-before::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--color-primary);
  z-index: 10;
  pointer-events: none;
}
.rename-table tbody tr.row-drop-target-after::after {
  content: "";
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--color-primary);
  z-index: 10;
  pointer-events: none;
}
/* #11 执行按钮 spinner */
.btn-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: rename-spin 0.6s linear infinite;
  margin-right: 6px;
  vertical-align: middle;
}
@keyframes rename-spin {
  to { transform: rotate(360deg); }
}
.row-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  opacity: 0;
  transform: translateX(2px);
  width: 100%;
  transition: opacity var(--transition-fast), transform var(--transition-fast);
}
.rename-table tbody tr:hover .row-actions,
.rename-table tbody tr.row-dragging .row-actions,
.rename-table tbody tr.row-drop-target .row-actions {
  opacity: 1;
  transform: translateX(0);
}
.row-drag-handle,
.row-remove-btn {
  width: 22px;
  height: 22px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--color-gray-600);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
}
.row-drag-handle {
  cursor: grab;
}
.row-drag-handle:active {
  cursor: grabbing;
}
.rename-table.row-sorting .row-drag-handle {
  cursor: grabbing;
}
.drag-handle-icon {
  width: 12px;
  height: 12px;
  display: block;
  background-image: linear-gradient(currentColor, currentColor), linear-gradient(currentColor, currentColor), linear-gradient(currentColor, currentColor);
  background-size: 12px 1px, 12px 1px, 12px 1px;
  background-position: center 3px, center 6px, center 9px;
  background-repeat: no-repeat;
}
.row-drag-handle:hover {
  color: var(--color-primary);
  background: rgba(37, 99, 235, 0.08);
  border-color: rgba(37, 99, 235, 0.18);
}
.row-remove-btn:hover {
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.08);
  border-color: rgba(239, 68, 68, 0.18);
}
.sort-btn {
  width: 18px;
  height: 18px;
  padding: 0;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background-color var(--transition-fast), border-color var(--transition-fast);
}
.sort-btn:hover {
  background: var(--color-hover);
  border-color: var(--color-border);
}
.sort-indicator {
  width: 8px;
  height: 12px;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.sort-triangle {
  width: 0;
  height: 0;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  transition: border-color var(--transition-fast), opacity var(--transition-fast);
}
.sort-triangle-up {
  border-bottom: 5px solid var(--color-gray-600);
}
.sort-triangle-down {
  border-top: 5px solid var(--color-gray-600);
}
.sort-indicator.asc .sort-triangle-up {
  border-bottom-color: var(--color-primary);
}
.sort-indicator.asc .sort-triangle-down {
  opacity: 0.25;
}
.sort-indicator.desc .sort-triangle-up {
  opacity: 0.25;
}
.sort-indicator.desc .sort-triangle-down {
  border-top-color: var(--color-primary);
}
.sort-indicator.unsorted .sort-triangle {
  opacity: 0.78;
}
.header-sort-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  min-width: 0;
}
.header-checkbox {
  flex: 0 0 auto;
  padding: 0;
}
.header-label {
  display: inline-block;
  font-weight: 700;
  color: var(--color-gray-800);
  font-size: var(--font-md);
  padding: 0;
  border: 0;
  background: none;
  vertical-align: middle;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
  min-width: 0;
}
.header-label:hover { color: var(--color-primary-dark); }
.col-check .checkbox {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  max-width: 100%;
  min-width: 0;
}
.col-check .checkbox input[type="checkbox"] {
  flex-shrink: 0;
}
.col-check .checkbox .truncate,
.col-check .checkbox .header-label {
  min-width: 0;
}
.sort-menu {
  position: fixed;
  z-index: 9999;
  min-width: 150px;
  padding: 5px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-white);
  box-shadow: var(--shadow-lg);
}
.sort-menu-item {
  display: block;
  width: 100%;
  padding: 7px 12px;
  border: none;
  border-radius: var(--radius);
  background: none;
  text-align: left;
  font-size: var(--font-sm);
  color: var(--color-gray-700);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}
.sort-menu-item:hover { background: var(--color-hover); }
.sort-menu-item.active { color: var(--color-primary); font-weight: 600; }
.sort-menu-sep { height: 1px; background: var(--color-border); margin: 4px 0; }
.col-resize-handle {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  cursor: col-resize;
  z-index: 1;
}
.col-resize-handle::after {
  content: "";
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 50%;
  background: var(--color-gray-300);
  border-radius: 1px;
  transition: background-color var(--transition-fast);
}
.col-resize-handle:hover::after,
.col-resize-handle.active::after {
  background: var(--color-primary);
}
.rules-section {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.tab-bar {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  margin-bottom: 10px;
  padding-top: 4px;
  flex-shrink: 0;
  min-width: 0;
}
.tab {
  height: 32px;
  padding: 0 8px;
  font-size: var(--font-md);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.tab-body {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 10px;
}
.action-bar {
  padding: 6px 10px 10px;
}
.action-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.action-main {
  justify-content: space-between;
  gap: var(--space-3);
}
.action-controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.output-group {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1 1 260px;
  min-width: 0;
}
.action-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  color: var(--color-gray-600);
  font-size: var(--font-sm);
}
.action-copy strong {
  color: var(--color-gray-900);
  font-size: var(--font-md);
}
.action-copy span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.error-line {
  color: var(--color-danger);
  font-size: var(--font-sm);
  margin-top: 8px;
}
.summary-line {
  font-size: var(--font-sm);
  margin-top: 8px;
}
.summary-line.ok { color: var(--color-success); }
.summary-line.warn { color: var(--color-warning); }

/* 预览差异高亮 */
.diff-row {
  background: rgba(22, 163, 74, 0.06);
}
.diff-row:hover {
  background: rgba(22, 163, 74, 0.1);
}
.diff-old {
  color: var(--color-gray-400);
  text-decoration: line-through;
}
.diff-new {
  color: var(--color-success);
  font-weight: 600;
}
</style>
