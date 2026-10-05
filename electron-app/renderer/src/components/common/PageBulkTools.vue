<script setup lang="ts">
import { computed, ref } from 'vue';
import { selectPageRange, type WorkPage } from '../../pdf-workbench';
const props = defineProps<{ pages: WorkPage[]; selected: string[]; disabled?: boolean }>();
const emit = defineEmits<{ select: [ids: string[]]; move: [position: number] }>();
const expression = ref(''), position = ref(1), error = ref('');
const maximum = computed(() => Math.max(1, props.pages.length - props.selected.length + 1));
function choose(mode: 'range' | 'odd' | 'even' | 'invert') {
  if (props.disabled) return;
  error.value = '';
  try {
    const selected = new Set(props.selected);
    emit('select', mode === 'range' ? selectPageRange(props.pages, expression.value) : props.pages.filter((p, i) => mode === 'invert' ? !selected.has(p.uid) : i % 2 === (mode === 'odd' ? 0 : 1)).map(p => p.uid));
  } catch (caught) { error.value = String(caught).replace(/^Error: /, ''); }
}
function move() {
  if (props.disabled || !props.selected.length) return;
  if (!Number.isInteger(position.value) || position.value < 1 || position.value > maximum.value) { error.value = `移动后起始位置须为 1–${maximum.value}。`; return; }
  error.value = ''; emit('move', position.value);
}
</script>
<template>
  <details class="bulk-tools">
    <summary>批量选页与移动 <span>页码按当前排序计算</span></summary>
    <div class="bulk-body">
      <form class="bulk-row" @submit.prevent="choose('range')"><input v-model="expression" aria-label="页码范围" placeholder="例如 1-5,8,12-20" :disabled="disabled" maxlength="20000" /><button class="btn" :disabled="disabled || !pages.length">按页码选中</button></form>
      <div class="bulk-row"><button v-for="item in ([['odd', '奇数页'], ['even', '偶数页'], ['invert', '反选']] as const)" :key="item[0]" class="btn" :disabled="disabled || !pages.length" @click="choose(item[0])">{{ item[1] }}</button><span>范围和奇偶页会替换当前选择</span></div>
      <form class="bulk-row move-row" @submit.prevent="move"><label for="bulk-position">移动后起始位置</label><input id="bulk-position" v-model.number="position" type="number" min="1" :max="maximum" :disabled="disabled || !selected.length" /><button class="btn" :disabled="disabled || !selected.length">移动所选</button><span>保持所选页顺序 · 可撤销</span></form>
      <p v-if="error" class="bulk-error" role="alert">{{ error }}</p>
    </div>
  </details>
</template>
<style scoped>
.bulk-tools { padding: 10px 14px; border-bottom: 1px solid var(--color-border); font-size: 11px; }
summary { cursor: pointer; font-weight: 600; }
summary span { margin-left: 10px; font-weight: 400; color: var(--color-text-secondary); }
.bulk-body { display: grid; gap: 9px; padding-top: 12px; }
.bulk-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.bulk-row>span { font-size: 10px; color: var(--color-text-secondary); }
input { border: 1px solid var(--color-border); border-radius: 6px; padding: 6px 8px; color: inherit; background: var(--color-surface); min-width: 0; }
input:not([type=number]) { flex: 1; }
input[type=number] { width: 70px; }
.btn { font-size: 11px; padding: 5px 9px; }
.move-row { border-top: 1px solid var(--color-border); padding-top: 9px; }
.bulk-error { margin: 0; color: #b45309; }
</style>
