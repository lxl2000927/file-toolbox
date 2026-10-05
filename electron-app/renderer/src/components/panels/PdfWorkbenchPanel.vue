<script setup lang="ts">
import { computed, ref, shallowRef, triggerRef, watch, onBeforeUnmount } from "vue";
import type { PdfSource, PdfToolAction, PdfToolOptions, PdfToolResult } from "../../../../shared/api-types";
import { formatBytes, interleavePages, movePages, movePagesTo, PageHistory, type WorkPage } from "../../pdf-workbench";
import { compressionPreset, compressionPresets, resultSummary } from '../../pdf-results';
import { IMPORT_BATCH_SIZE, MAX_WORKSPACE_PAGES, newImportPaths, inspectImportBatch } from "../../pdf-import";
import { usePdfTools } from "../../composables/usePdfTools";
import { useAppDialog } from "../../composables/useAppDialog";
import PdfPageGrid from "../common/PdfPageGrid.vue";
import PageBulkTools from '../common/PageBulkTools.vue';
import PdfResults from '../common/PdfResults.vue';
import ProcessingPresets from "../common/ProcessingPresets.vue";
import AppTabs from "../common/AppTabs.vue";
import AppIcon from "../common/AppIcon.vue";

const sources = shallowRef<PdfSource[]>([]);
const history = shallowRef(new PageHistory());
const pages = computed(() => history.value.current);
const selected = ref<string[]>([]);
const blankIds = ref<string[]>([]);
const action = ref<PdfToolAction>('assemble');
const outputDir = ref('');
const filename = ref('整理结果');
const compression = ref<'none' | 'lossless' | 'raster'>('lossless');
const ocr = ref(false);
const ocrText = ref(true);
const language = ref<'chi_sim+eng' | 'chi_sim' | 'eng'>('chi_sim+eng');
const dpi = ref(150);
const quality = ref(80);
const imageFormat = ref<'png' | 'jpeg'>('png');
const reverseBack = ref(true);
const ocrAvailable = ref(false);
const error = ref('');
const notice = ref('');
const result = shallowRef<PdfToolResult | null>(null);
const resultIsPrevious = ref(false);
const activeTab = ref('pages');
const task = usePdfTools();
const { state } = task;
const importing = ref(false);
const importDone = ref(0), importTotal = ref(0);
const importIssues = shallowRef<string[]>([]);
const preparing = ref(false);
const busy = computed(() => importing.value || preparing.value || task.busy.value);
let importStopped = false, disposed = false;
onBeforeUnmount(() => { disposed = true; importStopped = true; });
const dialog = useAppDialog();
let lastSelected = '';
const badges = computed(() => Object.fromEntries(blankIds.value.map(id => [id, '疑似空白'])));
const totalBytes = computed(() => sources.value.reduce((sum, source) => sum + source.size, 0));
const selectedSet = computed(() => new Set(selected.value));
const chosenPages = computed(() => pages.value.filter(page => selectedSet.value.has(page.uid)));
const isImageExport = computed(() => ['export_images', 'extract_images'].includes(action.value));
const wantsOcr = computed(() => action.value === 'ocr' || ocr.value);
watch([action, compression], () => {
  if (action.value === 'compress' && compression.value === 'none') compression.value = 'lossless';
});
const presetSettings = computed(() => ({ action: action.value, filename: filename.value, compression: compression.value, ocr: ocr.value,
  ocr_text: ocrText.value, language: language.value, dpi: dpi.value, quality: quality.value, image_format: imageFormat.value, reverse_back: reverseBack.value }));
const qualityPreset = computed(() => compressionPreset(dpi.value, quality.value));
function setQualityPreset(event: Event) {
  const preset = compressionPresets.find(p => p.id === (event.target as HTMLSelectElement).value);
  if (preset) { dpi.value = preset.dpi; quality.value = preset.quality; }
}
function moveTo(position: number) {
  if (busy.value) return;
  try { edit(movePagesTo(pages.value, selected.value, position)); }
  catch (caught) { error.value = String(caught); }
}
const modes: { value: PdfToolAction; title: string; description: string }[] = [
  { value: 'assemble', title: '整理 / 合并', description: '按工作区页序导出 PDF' },
  { value: 'compress', title: '压缩 PDF', description: '减小文件体积' },
  { value: 'ocr', title: '离线 OCR', description: '让扫描件可以搜索' },
  { value: 'export_images', title: '导出页面图片', description: '每页保存 PNG / JPEG' },
  { value: 'extract_images', title: '提取原始图片', description: '提取 PDF 内嵌图像' },
];

function edit(next: WorkPage[]) { history.value.commit(next); triggerRef(history); const ids = new Set(next.map(p => p.uid)); selected.value = selected.value.filter(id => ids.has(id)); notice.value = ''; }
function select(uid: string, range: boolean) {
  if (busy.value) return;
  const a = pages.value.findIndex(page => page.uid === lastSelected), b = pages.value.findIndex(page => page.uid === uid);
  if (range && a >= 0) selected.value = [...new Set([...selected.value, ...pages.value.slice(Math.min(a, b), Math.max(a, b) + 1).map(p => p.uid)])];
  else selected.value = selected.value.includes(uid) ? selected.value.filter(id => id !== uid) : [...selected.value, uid];
  lastSelected = uid;
}
function rotate() { edit(pages.value.map(page => selectedSet.value.has(page.uid) ? { ...page, rotation: (page.rotation + 90) % 360 } : page)); }
function removeSelected() { edit(pages.value.filter(page => !selectedSet.value.has(page.uid))); }
function move(dragged: string, before: string) { edit(movePages(pages.value, selected.value.includes(dragged) ? selected.value : [dragged], before)); }
function moveSelected(direction: -1 | 1) {
  const indexes = pages.value.map((p, i) => selectedSet.value.has(p.uid) ? i : -1).filter(i => i >= 0);
  if (!indexes.length) return;
  const edge = direction < 0 ? indexes[0] - 1 : indexes[indexes.length - 1] + 2;
  if (direction < 0 && edge < 0) return;
  edit(movePages(pages.value, selected.value, pages.value[edge]?.uid || ''));
}
function undo() { history.value.undo(); triggerRef(history); selected.value = []; }
function redo() { history.value.redo(); triggerRef(history); selected.value = []; }

async function addFiles(paths?: string[]) {
  if (busy.value) return;
  error.value = '';
  importing.value = true; importStopped = false; importIssues.value = [];
  importDone.value = 0; importTotal.value = 0;
  try {
    const picked = paths || await window.electronAPI?.openFileDialog({ title: '添加 PDF 或图片', multi: true, filters: [{ name: 'PDF 和图片', extensions: ['pdf', 'png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'webp', 'gif'] }] });
    if (!picked?.length) return;
    const pending = newImportPaths(sources.value, picked);
    importTotal.value = pending.length;
    if (!pending.length) { notice.value = '所选文件已在工作区中。'; return; }
    let recordedUndo = false, imported = 0;
    let sourcePages = sources.value.reduce((sum, source) => sum + source.page_count, 0);
    activeTab.value = 'pages';
    for (let offset = 0; offset < pending.length && !importStopped && !disposed; offset += IMPORT_BATCH_SIZE) {
      const batch = pending.slice(offset, offset + IMPORT_BATCH_SIZE);
      const inspected = await inspectImportBatch(batch, files => task.run('inspect', files, { compact_inspect: true, partial_inspect: true }), () => importStopped || disposed);
      if (disposed) return;
      const accepted: PdfSource[] = [], issues = [...inspected.errors];
      for (const source of inspected.sources || []) {
        if (sourcePages + source.page_count > MAX_WORKSPACE_PAGES) { issues.push(`${source.name}：工作区最多 100000 页，未加入此文件。`); continue; }
        sourcePages += source.page_count; accepted.push(source);
      }
      const start = sources.value.length;
      const appended = accepted.flatMap((source, i) => Array.from({ length: source.page_count }, (_, index) => ({ uid: `${start + i}:${index}`, source: start + i, index, rotation: 0 })));
      if (accepted.length) {
        sources.value = [...sources.value, ...accepted];
        const next = [...pages.value, ...appended];
        if (!recordedUndo) { history.value.commit(next); recordedUndo = true; }
        else history.value.replaceCurrent(next);
        triggerRef(history); imported += accepted.length;
      }
      importIssues.value = [...importIssues.value, ...issues];
      ocrAvailable.value = Boolean(inspected.ocr?.available ?? ocrAvailable.value);
      importDone.value = Math.min(offset + batch.length, pending.length);
      if (inspected.cancelled) importStopped = true;
      // Let Vue publish this batch and visible thumbnails enter the queue.
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    notice.value = `${importStopped ? '导入已取消' : '导入完成'}，已加入 ${imported} 份文件${importIssues.value.length ? `，${importIssues.value.length} 项未导入` : ''}。已加入的页面可继续使用。`;
  } catch (caught) { error.value = String(caught); }
  finally { importing.value = false; }
}
async function cancelWork() { if (importing.value) importStopped = true; if (task.busy.value) await task.cancel(); }
async function dropFiles(event: DragEvent) {
  if (busy.value || !event.dataTransfer?.files.length) return;
  try { const paths = await window.electronAPI?.getPathsForFiles(Array.from(event.dataTransfer.files)); if (paths?.length) await addFiles(paths.filter(Boolean)); }
  catch (caught) { error.value = String(caught); }
}
async function clear() {
  if (busy.value) return;
  if (pages.value.length && !await dialog.confirm({ title: '清空工作区', message: '清空当前页面和编辑记录？原文件和已导出文件不受影响。' })) return;
  sources.value = []; history.value = new PageHistory(); selected.value = []; blankIds.value = []; notice.value = ''; result.value = null; resultIsPrevious.value = false; importIssues.value = []; error.value = '';
}
async function detectBlank() {
  error.value = '';
  try {
    const data = await task.run('detect_blank', sources.value.map(s => s.path), { pages: pages.value, signatures: sources.value.map(s => s.signature) });
    if (data.cancelled) { notice.value = '检查已取消，页面未修改。'; return; }
    const found = new Set(data.candidates?.map(p => `${p.source}:${p.index}`));
    blankIds.value = pages.value.filter(p => found.has(`${p.source}:${p.index}`)).map(p => p.uid);
    notice.value = blankIds.value.length ? `找到 ${blankIds.value.length} 个疑似空白页。请放大检查，选择后删除；可撤销。` : '未发现符合保守阈值的空白页，页面均已保留。';
    activeTab.value = 'pages';
  } catch (caught) { error.value = String(caught); }
}
function interleave() {
  if (sources.value.length !== 2 || busy.value) return;
  edit(interleavePages(pages.value, reverseBack.value));
  const front = pages.value.filter(p => p.source === 0).length, back = pages.value.filter(p => p.source === 1).length;
  notice.value = front === back ? '已交错排列，请检查页序后导出。' : `正面 ${front} 页，背面 ${back} 页；多出的页面已保留在末尾，请检查。`;
}
async function chooseOutput() {
  try { const dir = await window.electronAPI?.openDirectoryDialog({ title: '选择输出文件夹' }); if (dir) outputDir.value = dir; }
  catch (caught) { error.value = String(caught); }
}
async function execute(onlySelected = false) {
  if (busy.value || !pages.value.length) return;
  error.value = ''; notice.value = '';
  preparing.value = true;
  const snapshot = onlySelected ? chosenPages.value : pages.value;
  const exportsImages = !onlySelected && isImageExport.value;
  try {
    if (!snapshot.length) return;
    if (!outputDir.value) { await chooseOutput(); if (!outputDir.value) return; }
    if (!exportsImages && compression.value === 'raster' && !await dialog.confirm({ title: '扫描图片压缩', message: '页面会转换为图片，表单和链接将丢失。需要保留可搜索文字时，请同时开启离线 OCR。', kind: 'warning', confirmText: '继续输出' })) return;
    const completed = await task.run(onlySelected ? 'assemble' : action.value, sources.value.map(s => s.path), {
      pages: snapshot, signatures: sources.value.map(s => s.signature), output_dir: outputDir.value, filename: filename.value || '整理结果',
      compression: compression.value === 'none' && action.value === 'compress' ? 'lossless' : compression.value,
      ocr: !exportsImages && wantsOcr.value, ocr_text: !exportsImages && wantsOcr.value && ocrText.value, language: language.value, dpi: Number(dpi.value), quality: Number(quality.value), image_format: imageFormat.value,
    });
    activeTab.value = 'outputs';
    if (completed.cancelled && !completed.output_files.length && result.value) {
      resultIsPrevious.value = true;
      notice.value = '任务已取消，本次没有生成新输出。下方保留上次处理结果。';
      return;
    }
    result.value = completed; resultIsPrevious.value = false;
    const status = resultSummary(result.value);
    notice.value = `${status.title}。${status.message}`;
  } catch (caught) { resultIsPrevious.value = Boolean(result.value); error.value = `${String(caught)}${result.value ? '\n本次处理失败，下方保留上次处理结果。' : ''}`; }
  finally { preparing.value = false; }
}
function applyPreset(settings: Record<string, unknown>) {
  if (modes.some(mode => mode.value === settings.action)) action.value = settings.action as PdfToolAction;
  if (typeof settings.filename === 'string') filename.value = settings.filename.slice(0, 100);
  if (['none', 'lossless', 'raster'].includes(String(settings.compression))) compression.value = settings.compression as typeof compression.value;
  if (['chi_sim+eng', 'chi_sim', 'eng'].includes(String(settings.language))) language.value = settings.language as typeof language.value;
  ocr.value = settings.ocr === true; reverseBack.value = settings.reverse_back !== false;
  ocrText.value = settings.ocr_text !== false;
  dpi.value = Math.max(72, Math.min(400, Number(settings.dpi) || 150)); quality.value = Math.max(30, Math.min(100, Number(settings.quality) || 80));
  imageFormat.value = settings.image_format === 'jpeg' ? 'jpeg' : 'png';
}
</script>

<template>
  <section class="workbench" @dragover.prevent @drop.prevent="dropFiles">
    <header class="workbench-heading"><div><div class="eyebrow">DOCUMENT STUDIO</div><h1>PDF 工作台</h1><p>整理页面，让每一份文档井然有序。</p></div><button class="btn btn-primary" :disabled="busy" @click="addFiles()"><AppIcon name="insert" :size="15" /> 添加 PDF / 图片</button></header>
    <div class="workbench-body">
      <section class="document-space">
        <div class="workspace-header"><AppTabs v-model="activeTab" label="工作台内容" id="pdf-workbench" :options="[{ value: 'pages', label: `页面整理${pages.length ? ` · ${pages.length}` : ''}` }, { value: 'outputs', label: `输出结果${result?.output_files.length ? ` · ${result.output_files.length}` : ''}` }]" /><button v-if="sources.length" class="btn small" :disabled="busy" @click="clear">清空</button></div>
        <div v-if="error" class="wb-message error" role="alert">{{ error }}</div>
        <div v-if="notice && (activeTab === 'pages' || !result || resultIsPrevious)" class="wb-message" role="status">{{ notice }}</div>
        <div v-if="importing" class="wb-message" role="status">正在导入 {{ importDone }} / {{ importTotal }} 份 · 已载入 {{ sources.length }} 份，可翻页预览</div>
        <details v-if="importIssues.length" class="import-issues"><summary>{{ importIssues.length }} 项未导入 · 查看原因</summary><ul><li v-for="(issue, i) in importIssues.slice(0, 100)" :key="i">{{ issue }}</li></ul><p v-if="importIssues.length > 100">仅显示前 100 项，请减少选择后分批重试。</p></details>
        <template v-if="activeTab === 'pages'">
          <div v-if="sources.length" class="selection-toolbar"><span>已选 {{ selected.length }} 页</span><button class="btn small" :disabled="busy || !pages.length" @click="selected = selected.length === pages.length ? [] : pages.map(p => p.uid)">{{ selected.length === pages.length ? '取消全选' : '全选' }}</button><button class="btn small" :disabled="busy || !selected.length" @click="rotate">旋转 90°</button><button class="btn small" :disabled="busy || !selected.length" @click="moveSelected(-1)">前移</button><button class="btn small" :disabled="busy || !selected.length" @click="moveSelected(1)">后移</button><button class="btn small" :disabled="busy || !selected.length" @click="removeSelected">删除</button><span class="toolbar-spacer" /><button class="btn small" :disabled="busy || !history.canUndo" @click="undo">撤销</button><button class="btn small" :disabled="busy || !history.canRedo" @click="redo">重做</button></div>
          <PageBulkTools v-if="sources.length" :pages="pages" :selected="selected" :disabled="busy" @select="selected = $event" @move="moveTo" />
          <div v-if="sources.length" class="document-scroll">
            <PdfPageGrid :sources="sources" :pages="pages" :selected="selected" :badges="badges" :disabled="busy" movable @select="select" @move="move" />
            <div v-if="!pages.length" class="empty-pages">页面已全部移除。可以撤销，或继续添加文件。</div>
          </div>
          <div v-else class="workbench-empty"><div class="empty-document"><AppIcon name="pdf" :size="38" /></div><h2>把文件放进工作台</h2><p>添加 PDF 或图片，拖动排好页序。<br />合并、去空白、识别文字，一处完成。</p><button class="btn btn-primary" :disabled="busy" @click="addFiles()">选择文件</button><span>支持 PDF · PNG · JPEG · TIFF · WebP · 最多 3000 份 / 100000 页</span></div>
        </template>
        <div v-else class="document-scroll output-space">
          <PdfResults v-if="result" :result="result" />
          <div v-else class="empty-pages">输出文件、体积对比和 OCR 文字将在这里显示。</div>
        </div>
        <footer class="workspace-status"><span>{{ sources.length }} 个来源 · {{ formatBytes(totalBytes) }}</span><span>本地处理 · 原文件只读</span></footer>
      </section>

      <aside class="output-settings">
        <div class="settings-scroll">
          <div class="settings-heading"><span class="eyebrow">EXPORT</span><h2>处理与输出</h2></div>
          <div class="mode-options" role="group" aria-label="输出方式"><button v-for="mode in modes" :key="mode.value" :class="{ active: action === mode.value }" :aria-pressed="action === mode.value" :disabled="busy" @click="action = mode.value"><strong>{{ mode.title }}</strong><small>{{ mode.description }}</small></button></div>
          <section class="option-section"><h3>页面辅助</h3><div class="inline-controls"><button class="btn" :disabled="busy || !pages.length" @click="detectBlank">查找空白页</button><button v-if="blankIds.length" class="btn" :disabled="busy" @click="selected = blankIds.filter(id => pages.some(p => p.uid === id))">选中候选 {{ blankIds.length }} 页</button></div><details class="interleave-settings"><summary>正反面交错合并</summary><p>依次导入正面和背面两个文件；交错后可继续调整。</p><label class="check-row"><input v-model="reverseBack" type="checkbox" :disabled="busy" /> 背面逆序（从最后一页开始）</label><button class="btn" :disabled="busy || sources.length !== 2" @click="interleave">应用交错页序</button></details></section>
          <section v-if="!isImageExport" class="option-section"><h3>文档优化</h3><label class="field-label" for="wb-compression">压缩方式</label><select id="wb-compression" v-model="compression" :disabled="busy"><option v-if="action !== 'compress'" value="none">不压缩</option><option value="lossless">结构优化 · 保留文字</option><option value="raster">扫描图片压缩 · 可调画质</option></select><p v-if="compression === 'raster'" class="hint caution">页面会转换为图片，链接和表单将丢失。</p><label v-if="action !== 'ocr'" class="check-row"><input v-model="ocr" type="checkbox" :disabled="busy || !ocrAvailable" /> 添加可搜索文字（离线 OCR）</label><template v-if="wantsOcr"><label class="check-row"><input v-model="ocrText" type="checkbox" :disabled="busy" /> 同时导出完整文字 TXT</label><label class="field-label" for="wb-language">识别语言</label><select id="wb-language" v-model="language" :disabled="busy"><option value="chi_sim+eng">简体中文 + English</option><option value="chi_sim">简体中文</option><option value="eng">English</option></select><p class="hint">{{ ocrAvailable ? '语言数据已就绪，文件无需上传。' : '导入文件后检查语言数据状态。' }} OCR 结果请复核。</p></template></section>
          <section v-if="action === 'export_images' || compression === 'raster' || wantsOcr" class="option-section"><h3>画质设置</h3><label v-if="compression === 'raster' && !isImageExport" class="field-label" for="wb-quality-preset">画质预设<select id="wb-quality-preset" :value="qualityPreset" :disabled="busy" @change="setQualityPreset"><option v-for="preset in compressionPresets" :key="preset.id" :value="preset.id">{{ preset.label }} · {{ preset.dpi }} DPI / {{ preset.quality }}</option><option value="custom" disabled>自定义</option></select></label><label v-if="action === 'export_images'" class="field-label">图片格式<select v-model="imageFormat" :disabled="busy"><option value="png">PNG · 无损</option><option value="jpeg">JPEG · 较小体积</option></select></label><div class="field-pair"><label>DPI<input v-model.number="dpi" type="number" min="72" max="400" :disabled="busy" /></label><label v-if="compression === 'raster' || imageFormat === 'jpeg'">JPEG 质量<input v-model.number="quality" type="number" min="30" max="100" :disabled="busy" /></label></div><p class="hint">DPI 越高，细节越多；大尺寸页面会限制像素以控制内存。</p></section>
          <section class="option-section"><h3>保存位置</h3><label class="field-label" for="wb-filename">{{ isImageExport ? '文件名前缀' : '输出名称' }}</label><input id="wb-filename" v-model="filename" maxlength="100" :disabled="busy" placeholder="整理结果" /><button class="folder-picker" :disabled="busy" :title="outputDir" @click="chooseOutput"><AppIcon name="folder" :size="17" /><span>{{ outputDir || '选择输出文件夹' }}</span><span>…</span></button></section>
          <ProcessingPresets scope="workbench" :settings="presetSettings" :disabled="busy" @apply="applyPreset" />
        </div>
        <footer class="export-footer"><div v-if="busy" class="task-progress" role="status"><span>{{ importing ? '分批导入文件' : state.phase || '准备处理' }} <small v-if="importing || state.total">{{ importing ? importDone : state.current }} / {{ importing ? importTotal : state.total }}</small></span><progress :value="importing ? importDone : state.current" :max="(importing ? importTotal : state.total) || 1" /></div><div v-else class="pipeline">整理 / 复核 <span>→</span> {{ wantsOcr && !isImageExport ? 'OCR → ' : '' }}{{ !isImageExport && compression !== 'none' ? '压缩 → ' : '' }}输出</div><button v-if="busy" class="btn" :disabled="preparing && !task.busy.value" @click="cancelWork">{{ importing ? '停止导入' : '取消任务' }}</button><template v-else><button class="btn btn-primary" :disabled="!pages.length || (wantsOcr && !isImageExport && !ocrAvailable)" @click="execute()">{{ isImageExport ? '导出图片' : '导出 PDF' }} · {{ pages.length }} 页</button><button v-if="selected.length" class="btn extract-button" @click="execute(true)">仅提取所选 {{ selected.length }} 页为 PDF</button></template></footer>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.import-issues { margin: 0 14px 8px; font-size: 12px; color: var(--color-text-secondary); }
.import-issues ul { max-height: 150px; overflow: auto; padding-left: 20px; overflow-wrap: anywhere; }
.workbench { display: flex; flex-direction: column; height: 100%; min-height: 0; padding: 24px 26px 18px; gap: 20px; container-type: inline-size; }
.workbench-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.eyebrow { font-size: 10px; color: var(--color-text-secondary); letter-spacing: 1.5px; font-weight: 700; }
h1 { font-size: 25px; margin: 5px 0; letter-spacing: -.7px; }
.workbench-heading p { margin: 0; font-size: 12px; color: var(--color-text-secondary); }
.workbench-heading>.btn { display: flex; gap: 7px; align-items: center; }
.workbench-body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; }
.document-space { display: flex; flex-direction: column; min-height: 0; min-width: 0; border: 1px solid var(--color-border); border-radius: 16px; background: var(--glass-bg); overflow: hidden; }
.workspace-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--color-border); }
.selection-toolbar { padding: 10px 14px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; border-bottom: 1px solid var(--color-border); font-size: 11px; }
.selection-toolbar>span:first-child { color: var(--color-text-secondary); margin-right: 4px; }
.toolbar-spacer { flex: 1; }
.small { min-height: 27px; padding: 3px 8px; font-size: 11px; }
.document-scroll { flex: 1; min-height: 0; overflow: auto; padding: 18px; }
.workspace-status { display: flex; justify-content: space-between; padding: 12px 16px; border-top: 1px solid var(--color-border); font-size: 10px; color: var(--color-text-secondary); }
.workbench-empty { flex: 1; min-height: 230px; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 28px; text-align: center; }
.empty-document { width: 85px; height: 94px; display: grid; place-items: center; color: var(--color-primary); background: var(--color-primary-light); border: 1px solid #cadcf3; border-radius: 12px; transform: rotate(-6deg); box-shadow: 8px 7px 0 #e4edf8; margin-bottom: 18px; }
.workbench-empty h2 { font-size: 20px; margin: 8px 0; }
.workbench-empty p { color: var(--color-text-secondary); font-size: 13px; line-height: 1.9; margin: 3px 0 20px; }
.workbench-empty>span { font-size: 10px; color: var(--color-text-tertiary); margin-top: 15px; }
.empty-pages { text-align: center; padding: 45px 10px; color: var(--color-text-secondary); font-size: 13px; }
.output-settings { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 16px; overflow: hidden; }
.settings-scroll { flex: 1; min-height: 0; overflow: auto; padding: 20px 18px; }
.settings-heading h2 { margin: 5px 0 16px; font-size: 19px; }
.mode-options { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.mode-options button { position: relative; text-align: left; border: 1px solid var(--color-border); border-radius: 9px; padding: 10px 9px; color: inherit; background: var(--color-surface); cursor: pointer; transition: border-color var(--transition-fast), background-color var(--transition-fast), box-shadow var(--transition), transform 240ms var(--motion-spring); }
.mode-options button:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 5px 12px rgba(15,23,42,.06); }
.mode-options button:active:not(:disabled) { transform: translateY(0) scale(.98); }
.mode-options button.active::after { content: ''; position: absolute; right: 8px; top: 9px; width: 5px; height: 5px; border-radius: 50%; background: var(--color-primary); animation: imageReveal 160ms ease; }
.mode-options button:first-child { grid-column: 1 / -1; }
.mode-options button.active { border-color: var(--color-primary); background: var(--color-primary-light); }
.mode-options strong { display: block; font-size: 12px; font-weight: 600; }
.mode-options small { display: block; font-size: 10px; margin-top: 5px; color: var(--color-text-secondary); line-height: 1.5; }
.option-section { padding: 17px 0; border-bottom: 1px solid var(--color-border); }
.option-section:last-of-type { border-bottom: 0; }
h3 { font-size: 12px; margin: 0 0 11px; }
.field-label { display: block; font-size: 11px; color: var(--color-text-secondary); margin: 8px 0; }
.option-section select, .option-section input:not([type=checkbox]) { box-sizing: border-box; width: 100%; padding: 9px 10px; border: 1px solid var(--color-border); border-radius: 7px; background: var(--color-surface); color: inherit; font-size: 12px; }
.check-row { display: flex; align-items: center; gap: 7px; font-size: 12px; line-height: 1.5; margin: 12px 0 0; }
.check-row input { accent-color: var(--color-primary); }
.hint, .interleave-settings p { font-size: 11px; line-height: 1.7; color: var(--color-text-secondary); margin: 8px 0 0; }
.caution { color: #b45309; }
.field-pair { display: flex; gap: 10px; }
.field-pair label { flex: 1; min-width: 0; font-size: 11px; color: var(--color-text-secondary); }
.field-pair input { margin-top: 6px; }
.inline-controls { display: flex; gap: 6px; flex-wrap: wrap; }
.inline-controls .btn, .interleave-settings .btn { font-size: 11px; padding: 6px 9px; }
.interleave-settings { margin-top: 13px; font-size: 12px; }
.interleave-settings summary { cursor: pointer; }
.interleave-settings .btn { margin-top: 10px; }
.folder-picker { margin-top: 10px; display: flex; align-items: center; gap: 7px; padding: 10px; width: 100%; border: 1px dashed var(--color-border-strong); border-radius: 8px; background: var(--color-surface-2); color: var(--color-text-secondary); cursor: pointer; }
.folder-picker span:first-of-type { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; font-size: 11px; }
.export-footer { padding: 15px 18px; border-top: 1px solid var(--color-border); display: grid; gap: 10px; background: var(--color-surface); }
.pipeline { font-size: 10px; text-align: center; color: var(--color-text-secondary); }
.pipeline span { margin: 0 5px; }
.export-footer>.btn { width: 100%; justify-content: center; }
.extract-button { font-size: 11px; padding: 5px; }
.task-progress { display: grid; gap: 7px; font-size: 11px; }
.task-progress small { float: right; }
progress { width: 100%; height: 5px; accent-color: var(--color-primary); }
.wb-message { font-size: 12px; line-height: 1.6; padding: 10px 14px; margin: 0; background: var(--color-primary-light); color: var(--color-text-secondary); overflow-wrap: anywhere; }
.wb-message.error { color: #b45309; background: #fff7e8; }
.output-space { animation: surfaceReveal 260ms var(--motion-out); }
@container (max-width: 1050px) { .workbench-body { grid-template-columns: minmax(0,1fr) 280px; gap: 14px; } .document-scroll { padding: 13px; } .settings-scroll { padding: 16px 14px; } }
@media (max-height: 780px) { .workbench { padding: 16px 20px; gap: 14px; } h1 { font-size: 22px; } .workbench-empty { padding: 18px; } }
@media (prefers-reduced-motion: reduce) { .mode-options button { transition: none; } }
</style>
