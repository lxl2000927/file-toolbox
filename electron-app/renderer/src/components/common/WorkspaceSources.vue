<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { WorkspacePage, WorkspaceSource, WorkspaceSourceStatus } from '../../../../shared/workspace-types';
const props = defineProps<{ sources: WorkspaceSource[]; pages: WorkspacePage[]; statuses?: WorkspaceSourceStatus[]; disabled?: boolean }>();
const emit = defineEmits<{ jump: [page: number]; filter: [source: number]; relocate: [source: number] }>();
const search = ref(''), onlyProblems = ref(false), listPage = ref(1), sourceIndex = ref(-1), originalPage = ref(1), globalPage = ref(1), jumpError = ref('');
const statusMap = computed(() => new Map((props.statuses || []).map(s => [s.index, s])));
const pageCounts = computed(() => { const map = new Map<number, number>(); for (const p of props.pages) map.set(p.source, (map.get(p.source) || 0) + 1); return map; });
const matchingSources = computed(() => {
  const term = search.value.trim().toLocaleLowerCase();
  return props.sources.map((source, index) => ({ source, index, status: statusMap.value.get(index)?.status || 'ready' }))
    .filter(item => (!term || `${item.source.name} ${item.source.path}`.toLocaleLowerCase().includes(term)) && (!onlyProblems.value || item.status !== 'ready'));
});
const listPages = computed(() => Math.max(1, Math.ceil(matchingSources.value.length / 40)));
const visibleSources = computed(() => matchingSources.value.slice((listPage.value - 1) * 40, listPage.value * 40));
const groups = computed(() => {
  const map = new Map<string, typeof visibleSources.value>();
  for (const item of visibleSources.value) { const folder = item.source.path.replace(/[\\/][^\\/]+$/, '') || '来源'; const group = map.get(folder) || []; group.push(item); map.set(folder, group); }
  return [...map].map(([folder, items]) => ({ folder, items }));
});
watch([search, onlyProblems], () => { listPage.value = 1; });
watch(listPages, count => { listPage.value = Math.min(listPage.value, count); });
function choose(index: number) { sourceIndex.value = index; originalPage.value = 1; emit('filter', index); jumpError.value = ''; }
function jumpGlobal() {
  if (!Number.isInteger(globalPage.value) || globalPage.value < 1 || globalPage.value > props.pages.length) { jumpError.value = `工作区页码需在 1–${props.pages.length} 内`; return; }
  jumpError.value = ''; emit('filter', -1); emit('jump', globalPage.value);
}
function jumpOriginal() {
  if (sourceIndex.value < 0 || !Number.isInteger(originalPage.value)) { jumpError.value = '请先点选来源，再输入原文件页码'; return; }
  const found = props.pages.findIndex(page => page.source === sourceIndex.value && page.index === originalPage.value - 1);
  if (found < 0) { jumpError.value = '该来源页不在当前工作区中，可能已被删除'; return; }
  globalPage.value = found + 1; jumpGlobal();
}
</script>
<template>
  <details class="source-navigator">
    <summary>来源与页码导航 <span>{{ sources.length }} 份来源 · 按文件夹分组</span></summary>
    <div class="source-controls"><input v-model="search" class="input" placeholder="搜索来源名称或路径" aria-label="搜索来源" /><label><input v-model="onlyProblems" type="checkbox" /> 仅缺失 / 已变化</label><button class="btn small" @click="sourceIndex = -1; emit('filter', -1)">全部页面</button></div>
    <div class="source-list"><section v-for="group in groups" :key="group.folder"><h4 :title="group.folder">{{ group.folder }}</h4><div v-for="item in group.items" :key="item.index" class="source-row"><button class="source-name" :class="{ active: sourceIndex === item.index }" :title="item.source.path" @click="choose(item.index)"><span>{{ item.source.name }}</span><small>{{ pageCounts.get(item.index) || 0 }} / {{ item.source.page_count }} 页</small></button><span v-if="item.status !== 'ready'" class="source-problem">{{ item.status === 'missing' ? '来源缺失' : '来源已变化' }}</span><button v-if="item.status !== 'ready'" class="btn small" :disabled="disabled" @click="emit('relocate', item.index)">重新定位</button></div></section><p v-if="!visibleSources.length">没有符合条件的来源。</p></div>
    <div class="source-pagination"><button class="btn small" :disabled="listPage <= 1" @click="listPage--">上一组来源</button><span>{{ listPage }} / {{ listPages }} · {{ matchingSources.length }} 份匹配</span><button class="btn small" :disabled="listPage >= listPages" @click="listPage++">下一组来源</button></div>
    <div class="source-jumps"><label>工作区第 <input v-model.number="globalPage" type="number" min="1" :max="pages.length" aria-label="工作区页码" @keyup.enter="jumpGlobal" /> 页</label><button class="btn small" @click="jumpGlobal">跳转</button><label :title="sources[sourceIndex]?.path">{{ sources[sourceIndex]?.name || '先点选来源' }} · 原第 <input v-model.number="originalPage" type="number" min="1" :max="sources[sourceIndex]?.page_count" aria-label="来源原页码" @keyup.enter="jumpOriginal" /> 页</label><button class="btn small" @click="jumpOriginal">定位原页</button></div>
    <p v-if="jumpError" role="status" class="source-problem">{{ jumpError }}</p>
  </details>
</template>
<style scoped>
.source-navigator { margin: 10px 16px; border: 1px solid var(--color-border); border-radius: 10px; padding: 9px 12px; font-size: 12px; }
summary { cursor: pointer; font-weight: 600; } summary span { font-weight: 400; color: var(--color-text-secondary); margin-left: 9px; }
.source-controls,.source-pagination,.source-jumps,.source-row { display: flex; align-items: center; gap: 8px; }
.source-controls,.source-jumps { flex-wrap: wrap; margin-top: 10px; }.source-controls>.input { flex: 1; min-width: 150px; }.source-controls label { white-space: nowrap; }
.source-list { max-height: 210px; overflow: auto; margin-top: 8px; }.source-list h4 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; margin: 8px 0 4px; color: var(--color-text-secondary); }
.source-row { padding: 2px 0; }.source-name { flex: 1; display: flex; justify-content: space-between; text-align: left; gap: 10px; min-width: 0; padding: 5px 6px; border: 0; border-radius: 5px; color: inherit; background: transparent; cursor: pointer; }.source-name span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.source-name small { white-space: nowrap; color: var(--color-text-secondary); }.source-name.active,.source-name:hover { background: var(--color-primary-bg); }.source-problem { color: #b45309; font-size: 11px; }.source-pagination { justify-content: center; margin-top: 8px; }.source-jumps label { display: inline-flex; align-items: center; gap: 5px; max-width: 100%; }.source-jumps input { width: 65px; padding: 4px; color: inherit; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 5px; }
</style>
