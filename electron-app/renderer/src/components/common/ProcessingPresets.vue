<script setup lang="ts">
import { onMounted, ref } from "vue";
import type { PresetScope, SavedPreset } from "../../../../shared/api-types";
import { useAppDialog } from "../../composables/useAppDialog";

const props = defineProps<{ scope: PresetScope; settings: Record<string, unknown>; disabled?: boolean }>();
const emit = defineEmits<{ apply: [settings: Record<string, unknown>] }>();
const presets = ref<SavedPreset[]>([]);
const selected = ref("");
const name = ref("");
const editing = ref(false);
const busy = ref(false);
const message = ref("");
const dialog = useAppDialog();
async function refresh() {
  try { presets.value = await window.engine?.presets.list(props.scope) || []; }
  catch (error) { message.value = String(error); }
}
onMounted(refresh);
async function save() {
  if (!name.value.trim() || busy.value || props.disabled) return;
  busy.value = true; message.value = "";
  try {
    const record = await window.engine?.presets.save(props.scope, name.value.trim(), JSON.parse(JSON.stringify(props.settings)));
    if (!record) throw new Error("引擎尚未连接");
    await refresh(); selected.value = record.id; editing.value = false; message.value = "方案已保存，重启后仍可使用。";
  } catch (error) { message.value = String(error); }
  finally { busy.value = false; }
}
function apply() {
  const record = presets.value.find(item => item.id === selected.value);
  if (record) { emit('apply', JSON.parse(JSON.stringify(record.settings))); name.value = record.name; message.value = "已应用方案。"; }
}
async function remove() {
  const record = presets.value.find(item => item.id === selected.value);
  if (!record || !await dialog.confirm({ title: "删除方案", message: `删除“${record.name}”？`, kind: "warning" })) return;
  busy.value = true;
  try { await window.engine?.presets.delete(props.scope, record.id); selected.value = ""; await refresh(); }
  catch (error) { message.value = String(error); }
  finally { busy.value = false; }
}
</script>

<template>
  <details class="processing-presets" @toggle="refresh">
    <summary>我的处理方案 <span>{{ presets.length ? `${presets.length} 个已保存` : '保存常用参数' }}</span></summary>
    <div class="preset-content">
      <div class="preset-actions">
        <select v-model="selected" aria-label="已保存方案" :disabled="disabled || busy"><option value="">选择方案</option><option v-for="item in presets" :key="item.id" :value="item.id">{{ item.name }}</option></select>
        <button class="btn" :disabled="disabled || busy || !selected" @click="apply">应用</button>
        <button class="btn" :disabled="disabled || busy || !selected" @click="remove">删除</button>
      </div>
      <button v-if="!editing" class="btn preset-save" :disabled="disabled || busy" @click="editing = true">保存当前参数为方案</button>
      <form v-else class="preset-actions" @submit.prevent="save"><input v-model="name" maxlength="50" placeholder="例如：发票归档" aria-label="方案名称" :disabled="disabled || busy" /><button class="btn" :disabled="disabled || busy || !name.trim()">保存</button></form>
      <p v-if="message" role="status">{{ message }}</p>
    </div>
  </details>
</template>

<style scoped>
.processing-presets { border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); }
summary { padding: 12px; cursor: pointer; font-weight: 600; font-size: 12px; }
summary span { float: right; font-size: 11px; color: var(--color-text-secondary); font-weight: 400; }
.preset-content { padding: 0 12px 12px; display: grid; gap: 8px; }
.preset-actions { display: flex; gap: 6px; }
select, input { flex: 1; min-width: 0; padding: 7px; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-surface); color: inherit; }
.btn { padding: 6px 10px; font-size: 12px; }
.preset-save { width: 100%; }
p { margin: 0; font-size: 12px; color: var(--color-text-secondary); overflow-wrap: anywhere; }
</style>
