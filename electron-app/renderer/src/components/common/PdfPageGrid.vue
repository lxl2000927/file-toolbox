<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onActivated, onDeactivated, ref, shallowRef, watch } from "vue";
import type { PdfSource } from "../../../../shared/api-types";
import type { WorkPage } from "../../pdf-workbench";
import { usePdfTools } from "../../composables/usePdfTools";
import { pinLeavingItem, clearPinnedItem } from "../../motion";
import { previewRequest, ThumbnailCache } from "../../pdf-preview";

const props = withDefaults(defineProps<{ sources: PdfSource[]; pages: WorkPage[]; allPages?: WorkPage[]; selected?: string[]; badges?: Record<string, string>; disabled?: boolean; selectable?: boolean; movable?: boolean; jumpPage?: number; jumpToken?: number; unavailableSources?: number[] }>(), { selectable: true });
const emit = defineEmits<{ select: [uid: string, range: boolean]; move: [dragged: string, before: string] }>();
const batch = ref(1);
const pageSize = 12;
const totalBatches = computed(() => Math.max(1, Math.ceil(props.pages.length / pageSize)));
const visible = computed(() => props.pages.slice((batch.value - 1) * pageSize, batch.value * pageSize));
const unavailable = computed(() => new Set(props.unavailableSources || []));
const globalPositions = computed(() => new Map((props.allPages || props.pages).map((page, index) => [page.uid, index + 1])));
const thumbnails = shallowRef<Record<string, string>>({});
const cache = new ThumbnailCache();
const loadError = ref("");
const loading = ref(false);
const previewTask = usePdfTools();
const detailTask = usePdfTools();
const previewDialog = ref<HTMLDialogElement | null>(null);
const detail = ref<WorkPage | null>(null);
const detailImage = ref("");
const detailError = ref("");
let generation = 0, disposed = false, active = true;
let detailGeneration = 0;
const dragUid = ref('');
const dragOverUid = ref('');
const key = (page: WorkPage) => `${props.sources[page.source]?.path}:${props.sources[page.source]?.signature}:${page.index}`;

watch(totalBatches, count => { batch.value = Math.min(batch.value, count); });
watch(() => [props.jumpPage, props.jumpToken], () => { if (props.jumpPage) batch.value = Math.max(1, Math.min(totalBatches.value, Math.ceil(props.jumpPage / pageSize))); }, { immediate: true });
watch(() => visible.value.map(page => `${key(page)}:${unavailable.value.has(page.source)}`).join('|'), () => {
  if (previewTask.busy.value) void previewTask.cancel();
  generation++; void load();
}, { immediate: true });

async function load() {
  if (loading.value || disposed || !active) return;
  const version = generation;
  loadError.value = "";
  const shown: Record<string, string> = {};
  visible.value.forEach(page => { const value = cache.get(key(page)); if (value) shown[key(page)] = value; });
  thumbnails.value = shown;
  const missing = visible.value.filter(page => !shown[key(page)] && !unavailable.value.has(page.source));
  if (!missing.length || !props.sources.length) return;
  loading.value = true;
  const keys = missing.map(key);
  try {
    const request = previewRequest(props.sources, missing);
    const result = await previewTask.run('thumbnails', request.files, request.options);
    if (disposed || !active || version !== generation) return;
    if (result.cancelled) return;
    const next = { ...shown };
    result.thumbnails?.forEach((item, i) => { cache.set(keys[i], item.data_url); next[keys[i]] = item.data_url; });
    thumbnails.value = next;
  } catch (error) { if (!disposed && version === generation) loadError.value = String(error); }
  finally { loading.value = false; if (!disposed && active && version !== generation) void load(); }
}

async function enlarge(page: WorkPage) {
  if (detailTask.busy.value || unavailable.value.has(page.source)) return;
  const version = ++detailGeneration;
  detail.value = page; detailImage.value = ''; detailError.value = '';
  await nextTick(); previewDialog.value?.showModal();
  try {
    const request = previewRequest(props.sources, [page]);
    const result = await detailTask.run('thumbnails', request.files, { ...request.options, thumbnail_size: 1400 });
    if (version === detailGeneration && detail.value?.uid === page.uid) detailImage.value = result.thumbnails?.[0]?.data_url || '';
  } catch (error) { if (version === detailGeneration) detailError.value = String(error); }
}
function closePreview() { detailGeneration++; previewDialog.value?.close(); detail.value = null; detailImage.value = ''; if (detailTask.busy.value) void detailTask.cancel(); }
function startDrag(event: DragEvent, page: WorkPage) {
  if (!props.movable || props.disabled) { event.preventDefault(); return; }
  dragUid.value = page.uid; event.dataTransfer?.setData('text/plain', page.uid);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}
function endDrag() { dragUid.value = ''; dragOverUid.value = ''; }
function drop(event: DragEvent, before: string) {
  if (!dragUid.value) return; // File drops still belong to the parent workbench.
  event.preventDefault(); event.stopPropagation();
  if (props.movable && !props.disabled) emit('move', dragUid.value, before);
  endDrag();
}
onActivated(() => { active = true; void load(); });
onDeactivated(() => { active = false; generation++; closePreview(); if (previewTask.busy.value) void previewTask.cancel(); });
onBeforeUnmount(() => { disposed = true; cache.clear(); });
</script>

<template>
  <div class="pdf-page-workspace">
    <div class="page-pagination">
      <span>{{ pages.length }} 页 <small v-if="loading">· 正在生成预览</small></span>
      <div><button class="btn" :disabled="batch <= 1" aria-label="上一组页面" @click="batch--">←</button><label>第 <input v-model.number="batch" type="number" min="1" :max="totalBatches" aria-label="页面组" @change="batch = Math.max(1, Math.min(totalBatches, Number(batch) || 1))" /> / {{ totalBatches }} 组</label><button class="btn" :disabled="batch >= totalBatches" aria-label="下一组页面" @click="batch++">→</button></div>
    </div>
    <p v-if="loadError" class="grid-error" role="alert">{{ loadError }} <button class="btn" @click="load">重试预览</button></p>
    <TransitionGroup name="page-sort" tag="div" class="page-grid" appear @before-leave="pinLeavingItem" @before-enter="clearPinnedItem" @leave-cancelled="clearPinnedItem">
      <article v-for="(page, index) in visible" :key="page.uid" class="page-card" :style="{ '--motion-order': Math.min(index, 7) }" :class="{ selected: selected?.includes(page.uid), marked: badges?.[page.uid], dragging: dragUid === page.uid, 'drop-target': dragOverUid === page.uid }" :draggable="Boolean(movable && !disabled)" @dragstart="startDrag($event, page)" @dragover.prevent="dragOverUid = dragUid && dragUid !== page.uid ? page.uid : ''" @dragleave="dragOverUid = dragOverUid === page.uid ? '' : dragOverUid" @drop="drop($event, page.uid)" @dragend="endDrag">
        <div class="page-top"><label v-if="selectable !== false"><input type="checkbox" :checked="selected?.includes(page.uid)" :disabled="disabled" :aria-label="`选择第 ${globalPositions.get(page.uid)} 页`" @click="emit('select', page.uid, $event.shiftKey)" /><span>{{ globalPositions.get(page.uid) }}</span></label><span v-else>第 {{ page.index + 1 }} 页</span><span v-if="badges?.[page.uid]" class="page-badge">{{ badges[page.uid] }}</span></div>
        <button class="thumbnail" :disabled="unavailable.has(page.source)" :aria-label="`放大预览第 ${page.index + 1} 页`" @click="enlarge(page)"><img v-if="thumbnails[key(page)] && !unavailable.has(page.source)" :src="thumbnails[key(page)]" :style="{ transform: `rotate(${page.rotation}deg)` }" alt="" /><span v-else class="placeholder">{{ unavailable.has(page.source) ? '来源不可用 · 请重新定位' : loadError ? '预览失败' : '加载预览…' }}</span></button>
        <div class="page-caption"><span :title="sources[page.source]?.name">{{ sources[page.source]?.name }}</span><small>原第 {{ page.index + 1 }} 页<span v-if="page.rotation"> · 旋转 {{ page.rotation }}°</span></small></div>
        <slot name="action" :page="page" />
      </article>
    </TransitionGroup>
    <dialog ref="previewDialog" class="page-preview-dialog" @cancel.prevent="closePreview" @click="$event.target === previewDialog && closePreview()">
      <header><strong>{{ detail ? sources[detail.source]?.name : '' }} · 第 {{ (detail?.index || 0) + 1 }} 页</strong><button class="btn" @click="closePreview">关闭</button></header>
      <div class="large-page"><img v-if="detailImage" :src="detailImage" :style="{ transform: `rotate(${detail?.rotation || 0}deg)` }" alt="页面预览" /><p v-else role="status">{{ detailError || '正在生成高清预览…' }}</p></div>
    </dialog>
  </div>
</template>

<style scoped>
.pdf-page-workspace { min-width: 0; }
.page-pagination { display: flex; align-items: center; justify-content: space-between; padding: 0 0 14px; font-size: 12px; color: var(--color-text-secondary); gap: 10px; }
.page-pagination>div { display: flex; align-items: center; gap: 7px; }
.page-pagination input { width: 45px; text-align: center; padding: 4px; border: 1px solid var(--color-border); border-radius: 6px; color: inherit; background: var(--color-surface); }
.page-pagination .btn { min-height: 27px; padding: 2px 10px; }
.page-grid { position: relative; display: grid; grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); gap: 14px; }
.page-card { position: relative; min-width: 0; padding: 10px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; translate: 0 0; transition: border-color var(--transition-fast), box-shadow var(--transition), translate var(--motion-settle) var(--motion-spring), opacity var(--transition-fast); }
.page-card:hover:not(.dragging) { translate: 0 -3px; border-color: var(--color-border-strong); box-shadow: 0 7px 17px rgba(15,23,42,.08); }
.page-card.selected { border-color: var(--color-primary); box-shadow: 0 0 0 2px var(--color-primary-light), 0 5px 14px rgba(37,99,235,.09); }
.page-card.selected:hover { border-color: var(--color-primary); }
.page-card.selected .thumbnail { background: var(--color-primary-bg); }
.page-card.dragging { opacity: .4; }
.page-card.drop-target::after { content: ''; position: absolute; left: -9px; top: 8px; bottom: 8px; width: 3px; border-radius: 3px; background: var(--color-primary); }
.page-sort-enter-active { transition: transform 260ms var(--motion-spring), opacity 180ms ease; transition-delay: calc(var(--motion-order, 0) * 18ms); }
.page-sort-leave-active { position: absolute; pointer-events: none; transition: transform 160ms var(--motion-out), opacity 140ms ease; }
.page-sort-enter-from { opacity: 0; transform: translateY(14px) scale(.96); }
.page-sort-leave-to { opacity: 0; transform: translateY(-5px) scale(.95); }
.page-sort-move { transition: transform 300ms var(--motion-spring); }
.page-card.marked { border-top: 3px solid #d69a33; padding-top: 8px; }
.page-top { display: flex; justify-content: space-between; align-items: center; height: 20px; margin-bottom: 8px; font-size: 12px; }
.page-top label { display: flex; gap: 6px; align-items: center; }
.page-top input { accent-color: var(--color-primary); }
.page-badge { color: #996315; font-size: 10px; background: #fff5d6; border-radius: 4px; padding: 2px 4px; animation: surfaceReveal 220ms var(--motion-spring); }
.thumbnail { width: 100%; height: 165px; border: 0; border-radius: 6px; background: var(--color-surface-2); overflow: hidden; cursor: zoom-in; display: flex; align-items: center; justify-content: center; }
.thumbnail { transition: background-color var(--transition); }
.thumbnail img { width: 83%; height: 83%; object-fit: contain; filter: drop-shadow(0 2px 3px #0002); transition: transform 320ms var(--motion-spring); animation: imageReveal 180ms ease; }
.placeholder { font-size: 11px; color: var(--color-text-tertiary); }
.page-caption { margin-top: 9px; display: grid; gap: 3px; font-size: 11px; }
.page-caption>span { white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.page-caption small { color: var(--color-text-secondary); }
.grid-error { color: #b45309; font-size: 12px; overflow-wrap: anywhere; }
.page-preview-dialog { padding: 18px; width: min(900px, 90vw); height: min(850px, 88vh); border: 1px solid var(--color-border); border-radius: 16px; color: var(--color-text-primary); background: var(--color-surface); box-shadow: var(--shadow-lg); }
.page-preview-dialog::backdrop { background: #0f172a88; backdrop-filter: blur(4px); }
.page-preview-dialog[open] { animation: dialogReveal 300ms var(--motion-spring); }
.page-preview-dialog[open]::backdrop { animation: imageReveal 200ms ease; }
.page-preview-dialog header { display: flex; align-items: center; justify-content: space-between; gap: 15px; font-size: 13px; }
.page-preview-dialog strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.large-page { display: flex; align-items: center; justify-content: center; height: calc(100% - 45px); overflow: auto; }
.large-page img { object-fit: contain; max-width: 88%; max-height: 88%; animation: imageReveal 180ms ease; }
@media (prefers-reduced-motion: reduce) { .thumbnail img, .page-card { transition: none; } }
</style>
