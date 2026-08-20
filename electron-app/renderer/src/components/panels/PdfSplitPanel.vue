<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount, onMounted } from "vue";
import type { PdfSplitConfig, PdfSplitMode, PdfSplitPlan, ExecuteSummary } from "../../env";
import { useEngineTask, generateTaskId } from "../../composables/useEngineTask";
import { useToast } from "../../composables/useToast";
import { useAppDialog } from "../../composables/useAppDialog";
import { fileBasename, positiveInt, positiveNumber, inputPositiveInt, inputPositiveNumber, formatEngineError } from "../../utils";
import AppSelect from "../common/AppSelect.vue";
import PanelBanner from "../common/PanelBanner.vue";

type FileItem = { path: string; pageCount: number | null; valid: boolean; message: string };

const files = ref<FileItem[]>([]);
const activeIndex = ref(0);
const outputDir = ref("");
const filePrefix = ref("");

const config = ref<PdfSplitConfig>({
  mode: "by_page_count",
  page_count: 1,
  page_ranges: "",
  max_size: 10,
  size_unit: "MB",
  bookmark_level: 1,
  output_dir: "",
  file_prefix: "",
});

const previewPlans = ref<{ file: string; plan: PdfSplitPlan }[]>([]);
const previewTextLines = ref<string[]>([]);
const previewSignature = ref("");
const previewing = ref(false);
let previewToken = 0;
const previewTimer = ref<number | null>(null);
const composing = ref(false);
let unsubscribeNativeDrop: (() => void) | null = null;

onBeforeUnmount(() => {
  unsubscribeNativeDrop?.();
  unsubscribeNativeDrop = null;
  previewToken++;
  if (previewTimer.value !== null) {
    window.clearTimeout(previewTimer.value);
    previewTimer.value = null;
  }
});

onMounted(() => {
  unsubscribeNativeDrop = window.electronAPI?.onFileDrop?.((paths) => {
    const pdfPaths = Array.from(new Set(paths.filter((path) => /\.pdf$/i.test(path))));
    if (pdfPaths.length) void appendFiles(pdfPaths);
  }) ?? null;
});

function schedulePreview() {
  // IME 组字期不触发预览，避免反复 IPC
  if (composing.value) return;
  if (previewTimer.value !== null) window.clearTimeout(previewTimer.value);
  previewTimer.value = window.setTimeout(() => {
    previewTimer.value = null;
    refreshPreview();
  }, 220);
}
const summary = ref<ExecuteSummary | null>(null);
const error = ref("");
const dragOver = ref(false);
const toast = useToast();
const dialog = useAppDialog();

const splitModeOptions = [
  { label: "按页数", value: "by_page_count" },
  { label: "按大小", value: "by_file_size" },
  { label: "按范围", value: "by_page_range" },
  { label: "按书签", value: "by_bookmark" },
];
const sizeUnitOptions = [
  { label: "MB", value: "MB" },
  { label: "KB", value: "KB" },
];

function collectOutputFiles(result: unknown): string[] {
  const value = result && typeof result === "object" ? result as Record<string, unknown> : {};
  const files = Array.isArray(value.output_files) ? value.output_files : [];
  const operations = Array.isArray(value.operations) ? value.operations : [];
  const nested = operations
    .filter((operation): operation is { output_files?: unknown[] } => Boolean(operation && typeof operation === "object"))
    .flatMap((operation) => Array.isArray(operation.output_files) ? operation.output_files : []);
  return Array.from(new Set([...files, ...nested].filter(Boolean).map(String)));
}

const { state: taskState, busy: taskBusy, cancellable: taskCancellable, start: startTask, markQueued, cancel: cancelTask, reset: resetTask } = useEngineTask({
  onComplete: (payload) => {
    const outputFiles = collectOutputFiles(payload.result);
    if (payload.ok) {
      summary.value = payload.result as ExecuteSummary;
      error.value = "";
      const s = summary.value;
      if (s) toast.success(`拆分完成：成功 ${s.successful} / ${s.total}，失败 ${s.failed}`);
      // #27 取消时已生成的文件不删除，提示用户保留了多少个
      if (payload.cancelled && outputFiles.length > 0) {
        toast.info(`已取消，但保留了 ${outputFiles.length} 个已生成的文件`);
      }
    } else {
      error.value = formatEngineError(payload);
      toast.error(error.value);
      // #27 即便失败/取消，也可能有部分输出文件
      if (payload.cancelled && outputFiles.length > 0) {
        toast.info(`已取消，但保留了 ${outputFiles.length} 个已生成的文件`);
      }
    }
  },
});

const activeFile = computed(() => files.value[activeIndex.value]);
const validCount = computed(() => files.value.filter((f) => f.valid).length);
const canPreview = computed(() => validCount.value > 0 && !previewing.value && !taskBusy.value);
const canRun = computed(() => validCount.value > 0 && !previewing.value && !taskBusy.value);
const isCancelledSummary = computed(() => Boolean(summary.value?.errors?.some((msg: string) => String(msg).includes("已取消"))));

const hasPreview = computed(() => previewPlans.value.length > 0 || previewTextLines.value.length > 0);
const previewStale = computed(() => hasPreview.value && previewSignature.value !== currentPreviewSignature());
const previewStatusText = computed(() => {
  if (previewing.value) return "预览中…";
  if (previewStale.value) return "设置已变更，预览已过期";
  if (hasPreview.value) return "预览已生成";
  return "未预览";
});
const previewStatusClass = computed(() => ({
  running: previewing.value,
  stale: previewStale.value,
  ready: hasPreview.value && !previewStale.value,
}));
const splitBannerKind = computed<"info" | "success" | "warning" | "danger">(() => {
  if (taskState.value.running || previewing.value) return "info";
  if (error.value) return "danger";
  if (summary.value) return summary.value.failed === 0 && !isCancelledSummary.value ? "success" : "warning";
  if (previewStale.value) return "warning";
  return "info";
});
const splitBannerIcon = computed<"pdf" | "alert" | "check">(() => {
  if (error.value || previewStale.value) return "alert";
  if (summary.value && summary.value.failed === 0 && !isCancelledSummary.value) return "check";
  return "pdf";
});
const splitBannerMessage = computed(() => {
  if (taskState.value.running) return `正在拆分：${taskState.value.phase || "处理中"} · ${taskState.value.current}/${taskState.value.total}`;
  if (previewing.value) return "正在生成拆分预览…";
  if (error.value) return error.value;
  if (summary.value) return `${isCancelledSummary.value ? "已取消" : "拆分完成"}：成功 ${summary.value.successful} / ${summary.value.total}，失败 ${summary.value.failed}`;
  if (previewStale.value) return "设置已变更，当前预览已过期，请重新生成预览。";
  if (hasPreview.value) return "拆分预览已生成，可核对输出文件名后开始拆分。";
  if (!files.value.length) return "拖入 PDF 或点击「添加 PDF 文件」，选择页数、大小、范围或书签方式后预览拆分结果。";
  return `已添加 ${files.value.length} 个 PDF，其中 ${validCount.value} 个有效。`;
});

const previewLines = computed(() => {
  if (previewTextLines.value.length) return previewTextLines.value;
  if (!previewPlans.value.length) return [];
  const lines: string[] = [];
  for (const { file, plan } of previewPlans.value) {
    const base = fileBasename(file);
    if (!plan.valid) {
      lines.push(`${base}  [失败] ${plan.message || "无法生成预览"}`);
      continue;
    }
    const pageCount = plan.page_count != null ? `${plan.page_count} 页` : "";
    const dir = plan.output_dir || "";
    lines.push(`${base}  (${pageCount})`);
    if (dir) lines.push(`  输出目录: ${dir}`);
    // #26 valid 时如果 message 非 OK 也追加提示（如 size_warning）
    if (plan.valid && plan.message && plan.message !== "OK") {
      lines.push(`  ℹ️ ${plan.message}`);
    }
    for (const o of plan.outputs) {
      const range = o.page_range ? ` (${o.page_range[0]}-${o.page_range[1]})` : "";
      lines.push(`  - ${o.filename}${range}`);
    }
    lines.push("");
  }
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
});

function normalizedConfig() {
  const next: PdfSplitConfig = { ...config.value };
  next.page_count = positiveInt(next.page_count ?? 1);
  next.max_size = positiveNumber(next.max_size ?? 10, 10);
  next.bookmark_level = positiveInt(next.bookmark_level ?? 1);
  next.output_dir = outputDir.value;
  next.file_prefix = filePrefix.value;
  config.value = next;
  return next;
}

function currentPreviewConfig(): PdfSplitConfig {
  return {
    ...config.value,
    page_count: positiveInt(config.value.page_count ?? 1),
    max_size: positiveNumber(config.value.max_size ?? 10, 10),
    bookmark_level: positiveInt(config.value.bookmark_level ?? 1),
    output_dir: outputDir.value,
    file_prefix: filePrefix.value,
  };
}

function currentPreviewSignature() {
  return JSON.stringify({
    files: files.value.filter((f) => f.valid).map((f) => f.path),
    config: currentPreviewConfig(),
  });
}

watch(
  [outputDir, filePrefix],
  () => {
    config.value = {
      ...config.value,
      output_dir: outputDir.value,
      file_prefix: filePrefix.value,
    };
  },
);

async function pickFiles() {
  const paths = await window.electronAPI?.openFileDialog({
    multi: true,
    filters: [{ name: "PDF 文件", extensions: ["pdf"] }],
  });
  if (paths?.length) await appendFiles(paths);
}

async function appendFiles(paths: string[]) {
  const engine = window.engine;
  if (!engine) {
    error.value = "引擎未就绪，无法校验 PDF 文件";
    return;
  }
  const exists = new Set(files.value.map((f) => f.path));
  const newPaths = paths.filter((p) => !exists.has(p) && /\.pdf$/i.test(p));
  if (!newPaths.length) return;
  const validations: FileItem[] = await Promise.all(
    newPaths.map(async (p) => {
      try {
        const r = await engine.pdfSplit.validate(p);
        return { path: p, pageCount: r.page_count, valid: r.valid, message: r.message };
      } catch (caught) {
        return { path: p, pageCount: null, valid: false, message: formatEngineError(caught) };
      }
    }),
  );
  files.value = [...files.value, ...validations];
  toast.success(`已添加 ${validations.length} 个 PDF`);
}

function clearFiles() {
  files.value = [];
  previewPlans.value = [];
  previewTextLines.value = [];
  previewSignature.value = "";
  summary.value = null;
  activeIndex.value = 0;
}

function removeFile(idx: number) {
  files.value = files.value.filter((_, i) => i !== idx);
  if (activeIndex.value >= files.value.length)
    activeIndex.value = Math.max(0, files.value.length - 1);
}

async function pickOutputDir() {
  const dir = await window.electronAPI?.openDirectoryDialog({ title: "选择输出目录" });
  if (dir) outputDir.value = dir;
}

async function refreshPreview() {
  if (!files.value.length || !window.engine || taskState.value.running) {
    previewPlans.value = [];
    previewTextLines.value = [];
    previewSignature.value = "";
    return;
  }
  const token = ++previewToken;
  previewing.value = true;
  error.value = "";
  summary.value = null;
  try {
    const cfg = normalizedConfig();
    const signature = currentPreviewSignature();
    const validFiles = files.value.filter((f) => f.valid);
    const paths = validFiles.map((f) => f.path);
    if (!paths.length) {
      previewPlans.value = [];
      previewTextLines.value = [];
      previewSignature.value = "";
      return;
    }
    const preview = await window.engine.pdfSplit.previewMany(paths, cfg);
    const results = paths.map((file) => ({
      file,
      plan: preview.plans[file] || {
        valid: false,
        message: "预览失败",
        page_count: null,
        output_dir: "",
        outputs: [],
      },
    }));
    if (token === previewToken) {
      previewPlans.value = results;
      previewTextLines.value = Array.isArray(preview.lines) ? preview.lines : [];
      previewSignature.value = signature;
    }
  } catch (caught) {
    if (token === previewToken) {
      previewPlans.value = [];
      previewTextLines.value = [];
      previewSignature.value = "";
      error.value = formatEngineError(caught);
    }
  } finally {
    if (token === previewToken) previewing.value = false;
  }
}

async function copyPreview() {
  if (!previewLines.value.length || previewStale.value) return;
  try {
    await navigator.clipboard.writeText(previewLines.value.join("\n"));
    toast.success("预览已复制");
  } catch {
    toast.error("复制失败，请检查剪贴板权限");
  }
}

async function executeSplit() {
  if (!canRun.value || !window.engine) return;
  // 未预览时弹确认，防止误操作
  if (previewStale.value) {
    const ok = await dialog.confirm({
      title: "预览已过期",
      message: "当前设置已变更，预览结果已过期。是否继续执行拆分？",
      kind: "warning",
      confirmText: "继续执行",
    });
    if (!ok) return;
  } else if (!hasPreview.value) {
    const ok = await dialog.confirm({
      title: "尚未预览",
      message: "建议先点击「预览拆分结果」确认输出文件，再执行拆分。是否继续执行？",
      kind: "warning",
      confirmText: "继续执行",
    });
    if (!ok) return;
  }
  summary.value = null;
  error.value = "";
  previewPlans.value = [];
  previewTextLines.value = [];
  previewSignature.value = "";
  const taskId = generateTaskId("pdf_split");
  previewToken++;
  previewing.value = false;
  const paths = files.value.filter((f) => f.valid).map((f) => f.path);
  if (!paths.length) {
    error.value = "没有有效的 PDF 文件";
    return;
  }
  try {
    startTask(taskId);
    const res = await window.engine.pdfSplit.executeAsync(paths, normalizedConfig(), taskId);
    if (res?.queued) markQueued(res.position || 1);
  } catch (caught) {
    error.value = formatEngineError(caught);
    resetTask();
  }
}

async function onDrop(e: DragEvent) {
  e.preventDefault();
  dragOver.value = false;
  const dt = e.dataTransfer;
  if (!dt) return;
  const paths = await window.electronAPI?.getPathsForFiles(Array.from(dt.files || []));
  if (paths?.length) appendFiles(paths);
}
</script>

<template>
  <div class="split-shell panel-shell panel-shell-responsive">
    <PanelBanner class="split-banner" :kind="splitBannerKind" :icon="splitBannerIcon" title="普通拆分" :message="splitBannerMessage" hint="支持批量" />

    <div class="split-grid panel-grid">
      <!-- 左：文件选择 -->
      <fieldset class="files-section glass-card section-card">
        <div class="toolbar section-toolbar">
          <button class="btn btn-primary btn-pill" @click="pickFiles">添加 PDF 文件</button>
          <button class="btn btn-secondary btn-pill" :disabled="!files.length" @click="clearFiles">
            清空列表
          </button>
        </div>
        <div
          class="files-area drop-area"
          :class="{ dragging: dragOver }"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop="onDrop"
        >
          <div v-if="files.length === 0" class="empty-state">
            <div class="empty-icon">📂</div>
            <div class="empty-title">还没有添加 PDF</div>
            <div class="empty-hint">支持拖拽添加，或点击左上角「添加 PDF 文件」选择文件。</div>
          </div>
          <div v-else class="file-list">
            <div
              v-for="(f, idx) in files"
              :key="f.path"
              class="file-row"
              :class="{ active: idx === activeIndex }"
              @click="activeIndex = idx"
            >
              <span class="file-icon">📄</span>
              <div class="flex-1 truncate">
                <div class="file-name truncate selectable" :title="f.path">{{ fileBasename(f.path) }}</div>
                <div class="file-meta">
                  <span v-if="f.valid" class="tag tag-info">{{ f.pageCount }} 页</span>
                  <span v-else class="tag tag-danger">{{ f.message }}</span>
                </div>
              </div>
              <button class="btn btn-icon btn-icon-danger" title="移除" aria-label="从列表移除 PDF" @click.stop="removeFile(idx)">✕</button>
            </div>
          </div>
        </div>
      </fieldset>

      <!-- 右：模式 + 输出 + 操作 -->
      <section class="right-col">
        <!-- 拆分模式 -->
        <fieldset class="group glass-card section-card">
          <div
            class="segment-group segmented-control segmented-animated"
            :style="{ '--active-index': Math.max(0, splitModeOptions.findIndex((opt) => opt.value === config.mode)), '--segment-count': splitModeOptions.length }"
          >
            <button
              v-for="opt in splitModeOptions"
              :key="opt.value"
              class="segment-btn segmented-item"
              :class="{ active: config.mode === opt.value }"
              @click="config.mode = opt.value as PdfSplitMode; schedulePreview()"
            >{{ opt.label }}</button>
          </div>

          <div v-if="config.mode === 'by_page_count'" class="form-row-layout mt-3">
            <label class="label-inline">每份页数：</label>
            <input
              class="input"
              type="number"
              min="1"
              :value="config.page_count ?? 1"
              @input="config.page_count = inputPositiveInt(($event.target as HTMLInputElement).value, 1); schedulePreview()"
              @compositionstart="composing = true"
              @compositionend="composing = false; schedulePreview()"
            />
          </div>
          <div v-else-if="config.mode === 'by_page_range'" class="form-row-layout mt-3">
            <label class="label-inline">页码范围：</label>
            <input
              class="input"
              placeholder="如：1-3, 5, 10-12"
              :value="config.page_ranges ?? ''"
              @input="config.page_ranges = ($event.target as HTMLInputElement).value; schedulePreview()"
              @compositionstart="composing = true"
              @compositionend="composing = false; schedulePreview()"
            />
          </div>
          <div v-else-if="config.mode === 'by_file_size'" class="form-row-layout mt-3">
            <label class="label-inline">最大文件大小：</label>
            <div class="size-field">
              <input
                class="input"
                type="number"
              min="0.1"
              step="0.1"
                :value="config.max_size ?? 10"
                @input="config.max_size = inputPositiveNumber(($event.target as HTMLInputElement).value, 10); schedulePreview()"
                @compositionstart="composing = true"
                @compositionend="composing = false; schedulePreview()"
              />
              <AppSelect
                class="unit-select"
                :model-value="config.size_unit ?? 'MB'"
                :options="sizeUnitOptions"
                min-width="72px"
                @update:model-value="config.size_unit = $event as 'MB' | 'KB'; schedulePreview()"
              />
            </div>
          </div>
          <div v-else-if="config.mode === 'by_bookmark'" class="form-row-layout mt-3">
            <label class="label-inline">书签级别：</label>
            <input
              class="input"
              type="number"
              min="1"
              :value="config.bookmark_level ?? 1"
              @input="config.bookmark_level = inputPositiveInt(($event.target as HTMLInputElement).value, 1); schedulePreview()"
              @compositionstart="composing = true"
              @compositionend="composing = false; schedulePreview()"
            />
          </div>
        </fieldset>

        <!-- 输出设置 -->
        <fieldset class="group glass-card section-card">
          <div class="row">
            <input class="input flex-1" :value="outputDir" placeholder="输出目录（留空则与 PDF 同目录）" readonly :title="outputDir" />
            <button class="btn btn-primary btn-sm" @click="pickOutputDir">选择输出目录</button>
          </div>
          <div class="row mt-3">
            <input
              class="input flex-1"
              placeholder="输出文件名前缀（可选，例如：split_）"
              v-model="filePrefix"
            />
          </div>
        </fieldset>

        <!-- 操作 -->
        <fieldset class="group group-actions glass-card section-card">
          <div class="row">
            <button
              class="btn btn-primary btn-block"
              :disabled="!canPreview"
              :aria-busy="previewing"
              @click="refreshPreview"
            >
              <span v-if="previewing" class="btn-spinner" aria-hidden="true" />
              {{ previewing ? "预览中…" : "预览拆分结果" }}
            </button>
            <button
              class="btn btn-outline"
              :disabled="!previewLines.length || previewStale || previewing || taskBusy"
              @click="copyPreview"
            >复制预览</button>
          </div>

          <div class="preview-status mt-3" :class="previewStatusClass">
            {{ previewStatusText }}
          </div>
          <div v-if="previewStale" class="preview-stale-line">
            当前预览基于旧设置，请重新生成后再复制或核对输出文件名。
          </div>

          <div v-if="taskBusy" class="progress mt-3" :class="{ indeterminate: !taskState.total }">
            <div class="progress-bar" :style="{ width: taskState.total ? (taskState.current / taskState.total * 100) + '%' : undefined }" />
          </div>
          <div v-if="taskBusy" class="progress-text">
            {{ taskState.phase || (taskState.queued ? "排队中" : "处理中") }} · {{ taskState.current }}/{{ taskState.total }}
            <span v-if="taskState.file"> · {{ taskState.file }}</span>
          </div>
          <div v-if="error" class="error-line">{{ error }}</div>
          <div
            v-else-if="summary"
            class="summary-line"
            :class="summary.failed === 0 && !isCancelledSummary ? 'ok' : 'warn'"
          >
            {{ isCancelledSummary ? '已取消' : '完成' }} · 成功 {{ summary.successful }} / {{ summary.total }}，失败 {{ summary.failed }}
          </div>

          <div class="preview-box mt-3" :class="{ empty: !previewLines.length, stale: previewStale }">
            <div v-if="!previewLines.length" class="empty-state preview-empty-state">
              <div class="empty-title">尚未生成预览</div>
              <div class="empty-hint">点击「预览拆分结果」查看输出计划</div>
            </div>
            <pre v-else class="preview-text selectable">{{ previewLines.join("\n") }}</pre>
          </div>

          <button
            v-if="taskCancellable"
            class="btn btn-danger btn-lg btn-block mt-3"
            @click="cancelTask"
          >取消</button>
          <button
            v-else
            class="btn btn-primary btn-lg btn-block mt-3"
            :disabled="!canRun"
            @click="executeSplit"
          >开始拆分</button>
        </fieldset>
      </section>
    </div>
  </div>
</template>

<style scoped>
.split-grid {
  --panel-grid-columns: minmax(380px, 1.18fr) minmax(320px, 1fr);
}

/* 左侧 */
.files-area {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.empty-state {
  flex: 1;
  color: var(--color-gray-500);
}
.empty-title { font-size: var(--font-md); font-weight: 600; color: var(--color-gray-700); }
.segment-btn {
  min-height: 30px;
}

.file-list {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius);
  border: 1px solid transparent;
  cursor: pointer;
}
.file-row { transition: background-color var(--transition-fast), border-color var(--transition-fast); }
.file-row:hover {
  background: var(--color-gray-50);
  border-color: rgba(37, 99, 235, 0.18);
}
.file-row.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary);
}
.file-icon { font-size: 18px; }
.file-name { font-size: var(--font-md); font-weight: 500; color: var(--color-gray-900); }
.file-meta { display: flex; gap: 4px; margin-top: 2px; }

/* 右侧 */
.right-col {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.group-actions {
  flex: 1 1 0;
}
.row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.row.mt-3 { margin-top: 10px; }
.mt-3 { margin-top: 10px; }
.label-inline {
  font-size: var(--font-md);
  color: var(--color-gray-700);
  font-weight: 500;
  white-space: nowrap;
}
.preview-box {
  flex: 1 1 0;
  min-height: 140px;
  background: rgba(255, 255, 255, 0.35);
  border: 0.5px solid var(--glass-border);
  border-radius: var(--radius);
  overflow: auto;
  padding: 10px;
}
.preview-box.empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(248, 250, 252, 0.56);
}
.preview-box.stale {
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(254, 243, 199, 0.28);
}
.preview-placeholder {
  color: var(--color-gray-400);
  font-size: var(--font-md);
}
/* #16 预览框空状态 */
.preview-empty-state {
  width: min(100%, 280px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 18px 16px;
  color: var(--color-gray-500);
  text-align: center;
}
.preview-empty-state .empty-title {
  font-size: var(--font-md);
  font-weight: 600;
  color: var(--color-gray-700);
}
.preview-empty-state .empty-hint {
  font-size: var(--font-sm);
  line-height: 1.6;
  color: var(--color-gray-500);
}
/* #11 按钮 spinner */
.btn-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: pdfspin 0.6s linear infinite;
  margin-right: 6px;
  vertical-align: middle;
}
@keyframes pdfspin {
  to { transform: rotate(360deg); }
}
.preview-status {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: var(--font-sm);
  color: var(--color-gray-500);
  background: var(--color-gray-100);
}
.preview-status.ready {
  color: var(--color-success);
  background: var(--color-success-bg);
}
.preview-status.running {
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
}
.preview-status.stale {
  color: var(--color-warning);
  background: var(--color-warning-bg);
}
.preview-stale-line {
  margin-top: 6px;
  font-size: var(--font-sm);
  color: var(--color-warning);
}
.preview-text {
  font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Consolas, monospace;
  font-size: var(--font-sm);
  color: var(--color-gray-800);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
.progress-text { margin-top: 6px; font-size: var(--font-sm); color: var(--color-gray-600); }
.error-line { color: var(--color-danger); font-size: var(--font-sm); margin-top: 6px; }
.summary-line { font-size: var(--font-sm); margin-top: 6px; }
.summary-line.ok { color: var(--color-success); }
.summary-line.warn { color: var(--color-warning); }

/* ── 拆分模式表单行（标签列 auto + 字段列 1fr） */
.form-row-layout {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: var(--space-2);
}
.form-row-layout > .label-inline {
  white-space: nowrap;
}
.form-row-layout > .input {
  min-width: 0;
}

/* 按大小拆分：input + unit inline */
.size-field {
  display: flex;
  gap: 6px;
  min-width: 0;
}
.size-field .input {
  flex: 1;
  min-width: 60px;
}
.size-field .unit-select {
  flex-shrink: 0;
  width: 76px;
}

/* 模式下拉：限制宽度避免撑破布局 */
.mode-select {
  max-width: 100%;
}
</style>
