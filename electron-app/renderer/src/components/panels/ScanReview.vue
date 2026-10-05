<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { PdfSource, PdfToolResult, ScanSplitOptions } from "../../../../shared/api-types";
import { reviewSegments, type WorkPage, formatBytes } from "../../pdf-workbench";
import { usePdfTools } from "../../composables/usePdfTools";
import PdfPageGrid from "../common/PdfPageGrid.vue";
import { pinLeavingItem, clearPinnedItem } from "../../motion";

const props = defineProps<{ pdfPath: string; signature: string; total: number; initialMarkers: number[]; options: ScanSplitOptions; outputDir: string; prefix: string; compression: 'none' | 'lossless'; ocr: boolean }>();
const emit = defineEmits<{ back: []; busy: [busy: boolean]; complete: [result: PdfToolResult]; 'update:compression': [value: 'none' | 'lossless']; 'update:ocr': [value: boolean] }>();
const markers = ref([...props.initialMarkers]);
const segments = ref(reviewSegments(props.total, markers.value, props.options.marker_as_first_page !== false, Boolean(props.options.exclude_marker_page)));
const past = ref<{ markers: number[]; segments: number[][] }[]>([]);
const focusSegment = ref(-1);
const message = ref('');
const outcome = ref<PdfToolResult | null>(null);
const task = usePdfTools();
const { busy, state } = task;
watch(busy, value => emit('busy', value));
const source = computed<PdfSource[]>(() => [{ path: props.pdfPath, name: props.pdfPath.split(/[\\/]/).pop() || '扫描文件', kind: 'pdf', page_count: props.total, size: 0, signature: props.signature, pages: [] }]);
const pages = computed<WorkPage[]>(() => (focusSegment.value >= 0 ? segments.value[focusSegment.value] || [] : Array.from({ length: props.total }, (_, i) => i)).map(index => ({ uid: String(index), source: 0, index, rotation: 0 })));
const outputPages = computed(() => segments.value.reduce((sum, group) => sum + group.length, 0));
const badges = computed(() => Object.fromEntries(markers.value.map(index => [String(index), props.options.exclude_marker_page ? '分隔页 · 不输出' : '分隔标记'])));
function remember() { past.value.push({ markers: [...markers.value], segments: segments.value.map(group => [...group]) }); if (past.value.length > 40) past.value.shift(); outcome.value = null; }
function toggleMarker(index: number) {
  if (busy.value) return;
  remember();
  markers.value = markers.value.includes(index) ? markers.value.filter(p => p !== index) : [...markers.value, index].sort((a, b) => a - b);
  segments.value = reviewSegments(props.total, markers.value, props.options.marker_as_first_page !== false, Boolean(props.options.exclude_marker_page));
  focusSegment.value = -1; message.value = '已按新标记重新分段；可撤销本次修改。';
}
function merge(index: number) { if (busy.value || !segments.value[index + 1]) return; remember(); segments.value.splice(index, 2, [...segments.value[index], ...segments.value[index + 1]]); focusSegment.value = -1; }
function undo() { const previous = past.value.pop(); if (previous) { markers.value = previous.markers; segments.value = previous.segments; focusSegment.value = -1; outcome.value = null; } }
async function exportSegments() {
  if (busy.value || !segments.value.length) return;
  message.value = '';
  try {
    let directory = props.outputDir;
    if (!directory) directory = await window.electronAPI?.openDirectoryDialog({ title: '选择复核结果输出文件夹' }) || '';
    if (!directory) return;
    const result = await task.run('segments', [props.pdfPath], { signatures: [props.signature], segments: segments.value.map(group => [...group]), output_dir: directory,
      filename: props.prefix || '扫描拆分', compression: props.compression, ocr: props.ocr, language: 'chi_sim+eng', dpi: 200 });
    outcome.value = result;
    message.value = result.cancelled ? `已取消，保留 ${result.output_files.length} 个完整文件。` : result.errors.join('\n') || `已输出 ${result.output_files.length} 份，共 ${formatBytes(result.bytes_after)}。`;
    emit('complete', result);
  } catch (error) { message.value = String(error); }
}
</script>

<template>
  <section class="scan-review">
    <header><div><span class="review-eyebrow">REVIEW BEFORE EXPORT</span><h3>检查分段，再保存</h3></div><div class="review-header-actions"><button class="btn" :disabled="busy || !past.length" @click="undo">撤销修改</button><button class="btn" :disabled="busy" @click="emit('back')">返回参数</button></div></header>
    <p class="review-help">点击页面放大查看，用下方按钮补充或移除标记；相邻分段可以合并。{{ options.marker_as_first_page !== false ? '标记归下一份。' : '标记归上一份。' }}</p>
    <div class="review-summary"><strong><Transition name="count" mode="out-in"><span :key="segments.length" class="review-count">{{ segments.length }}</span></Transition> 份</strong><span>输出 {{ outputPages }} / {{ total }} 页</span><span v-if="total > outputPages" class="excluded">排除 {{ total - outputPages }} 个标记页</span><button class="btn" :aria-pressed="focusSegment === -1" @click="focusSegment = -1">查看所有页</button></div>
    <div class="review-scroll">
    <TransitionGroup name="segment" tag="div" class="segment-list" @before-leave="pinLeavingItem" @before-enter="clearPinnedItem" @leave-cancelled="clearPinnedItem"><div v-for="(group, index) in segments" :key="group[0]" class="segment-item"><button class="segment-label" :class="{ active: focusSegment === index }" @click="focusSegment = index"><b>{{ String(index + 1).padStart(2, '0') }}</b><span>第 {{ group[0] + 1 }}–{{ group[group.length - 1] + 1 }} 页<small>{{ group.length }} 页{{ options.max_segment_pages && group.length > options.max_segment_pages ? ' · 疑似漏检' : '' }}</small></span></button><button v-if="index < segments.length - 1" class="merge-segments" :disabled="busy" :aria-label="`合并第 ${index + 1} 份和下一份`" @click="merge(index)">合并下一份</button></div></TransitionGroup>
    <PdfPageGrid :sources="source" :pages="pages" :badges="badges" :selectable="false" :disabled="busy"><template #action="{ page }"><button class="marker-toggle" :disabled="busy" :aria-pressed="markers.includes(page.index)" @click="toggleMarker(page.index)">{{ markers.includes(page.index) ? '移除分隔标记' : '设为分隔标记' }}</button></template></PdfPageGrid>
    <ol v-if="outcome?.output_files.length" class="review-files"><li v-for="file in outcome.output_files" :key="file">{{ file }}</li></ol>
    </div>
    <div class="review-export"><label><input type="checkbox" :checked="compression === 'lossless'" :disabled="busy" @change="emit('update:compression', ($event.target as HTMLInputElement).checked ? 'lossless' : 'none')" /> 结构优化</label><label><input type="checkbox" :checked="ocr" :disabled="busy" @change="emit('update:ocr', ($event.target as HTMLInputElement).checked)" /> 中英文离线 OCR</label><span class="export-spacer" /><button v-if="busy" class="btn" @click="task.cancel">取消输出</button><button v-else class="btn btn-primary" :disabled="!segments.length || !signature" @click="exportSegments">确认导出 {{ segments.length }} 份</button></div>
    <p v-if="busy" class="review-message" role="status">{{ state.phase }} · {{ state.current }} / {{ state.total }}（OCR 在当前页识别结束后响应取消）</p>
    <p v-if="message" class="review-message" role="status">{{ message }}</p>

  </section>
</template>

<style scoped>
.scan-review { padding: 0; height: 100%; min-height: 0; display: flex; flex-direction: column; }
.review-scroll { flex: 1; min-height: 0; overflow: auto; padding: 0 4px 8px 0; }
.review-header-actions { display: flex; gap: 8px; }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.review-eyebrow { font-size: 9px; letter-spacing: 1.5px; color: var(--color-text-secondary); }
h3 { font-size: 19px; margin: 5px 0 0; }
.review-help { font-size: 12px; line-height: 1.8; color: var(--color-text-secondary); margin: 12px 0; }
.review-summary { display: flex; align-items: center; gap: 14px; padding: 12px; border: 1px solid var(--color-border); border-radius: 10px; font-size: 12px; flex-wrap: wrap; }
.review-summary strong { font-size: 20px; }.review-summary .btn { margin-left: auto; font-size: 11px; }
.excluded { color: #b45309; }
.segment-list { position: relative; display: grid; grid-template-columns: repeat(auto-fill,minmax(170px,1fr)); gap: 8px; margin: 14px 0 18px; max-height: 180px; overflow: auto; }
.segment-item { border: 1px solid var(--color-border); border-radius: 8px; overflow: hidden; background: var(--color-surface); }
.segment-enter-active, .segment-move { transition: transform 300ms var(--motion-spring), opacity 180ms ease; }
.segment-leave-active { position: absolute; pointer-events: none; transition: transform 180ms var(--motion-out), opacity 140ms ease; }
.segment-enter-from { opacity: 0; transform: translateX(10px) scale(.97); }
.segment-leave-to { opacity: 0; transform: translateX(-12px) scale(.97); }
.review-count { display: inline-block; min-width: 1ch; font-variant-numeric: tabular-nums; }
.count-enter-active { transition: transform 220ms var(--motion-spring), opacity 160ms ease; }
.count-leave-active { transition: transform 90ms ease, opacity 90ms ease; }
.count-enter-from { opacity: 0; transform: translateY(7px); }
.count-leave-to { opacity: 0; transform: translateY(-5px); }
.segment-label { display: flex; width: 100%; gap: 10px; align-items: center; text-align: left; padding: 10px; border: 0; background: transparent; color: inherit; cursor: pointer; }
.segment-label.active { background: var(--color-primary-light); color: var(--color-primary); }
.segment-label, .merge-segments, .marker-toggle { transition: background-color var(--transition-fast), color var(--transition-fast), box-shadow var(--transition-fast); }
.segment-label:hover, .merge-segments:hover:not(:disabled), .marker-toggle:hover:not(:disabled) { background: var(--color-primary-bg); color: var(--color-primary); }
.segment-label b { font-size: 15px; color: var(--color-text-tertiary); }.segment-label span { font-size: 11px; }.segment-label small { display: block; font-size: 10px; margin-top: 5px; color: var(--color-text-secondary); }
.merge-segments { border: 0; border-top: 1px solid var(--color-border); background: var(--color-surface-2); padding: 5px; width: 100%; cursor: pointer; font-size: 10px; color: var(--color-text-secondary); }
.marker-toggle { width: 100%; margin-top: 9px; padding: 6px 3px; border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text-secondary); background: var(--color-surface); cursor: pointer; font-size: 10px; }
.marker-toggle[aria-pressed=true] { color: #a16207; border-color: #e8cfa6; }
.review-export { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 0; padding: 12px 0 0; border-top: 1px solid var(--color-border); }
.review-export label { display: flex; align-items: center; gap: 5px; font-size: 11px; }.export-spacer { flex: 1; }
.review-message { max-height: 60px; overflow: auto; margin: 8px 0 0; font-size: 12px; line-height: 1.7; color: var(--color-text-secondary); overflow-wrap: anywhere; }
.review-files { font-size: 11px; line-height: 1.8; overflow-wrap: anywhere; padding-left: 20px; user-select: text; }
.review-files { animation: surfaceReveal 260ms var(--motion-out); }
</style>
