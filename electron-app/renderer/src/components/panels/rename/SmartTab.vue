<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule } from "../../../env";
import AppSelect from "../../common/AppSelect.vue";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

const rule = computed<RenameRule>(() => {
  const found = props.rules.find((r) => r.type === "smart_recognize");
  return found || { type: "smart_recognize", mode: "content_title", position: "覆盖原名" };
});
const modeOptions = [
  { label: "提取标题（PDF 元数据 / 首行）", value: "content_title" },
  { label: "发票信息（号码/代码/日期）", value: "invoice_info" },
];
const positionOptions = [
  { label: "覆盖原名", value: "覆盖原名" },
  { label: "作为前缀", value: "首位" },
  { label: "作为后缀", value: "末位" },
  { label: "自定义位置", value: "指定位置" },
];

function patch(p: Partial<RenameRule>) {
  const next = [...props.rules];
  const idx = next.findIndex((r) => r.type === "smart_recognize");
  if (idx >= 0) next[idx] = { ...next[idx], ...p };
  else next.push({ type: "smart_recognize", mode: "content_title", position: "覆盖原名", ...p });
  emit("update:rules", next);
}
</script>

<template>
  <fieldset class="rules-group">
    <legend>智能识别</legend>
    <p class="text-muted text-sm">从文件内容中提取信息作为新文件名（PDF / 文本）。</p>
    <div class="grid">
      <label class="label-inline">识别模式</label>
      <AppSelect :model-value="rule.mode || 'content_title'" :options="modeOptions" @update:model-value="patch({ mode: $event as string })" />
      <label class="label-inline">写入位置</label>
      <AppSelect :model-value="rule.position || '覆盖原名'" :options="positionOptions" @update:model-value="patch({ position: $event as string })" />
      <template v-if="rule.position === '指定位置'">
        <label class="label-inline">索引</label>
        <input
          class="input input-sm"
          type="number"
          min="1"
          :value="rule.index ?? 1"
          @input="patch({ index: Math.max(1, Math.floor(Number(($event.target as HTMLInputElement).value) || 1)) })"
        />
      </template>
    </div>
  </fieldset>
</template>

<style scoped>
.rules-group {
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: 10px 12px 14px;
  background: var(--color-white);
}
.rules-group legend {
  padding: 0 6px;
  font-weight: 600;
  color: var(--color-gray-800);
  font-size: var(--font-md);
}
.grid {
  display: grid;
  grid-template-columns: 80px 1fr;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.label-inline {
  font-size: var(--font-md);
  color: var(--color-gray-700);
  font-weight: 500;
}
.text-sm { font-size: var(--font-sm); margin-bottom: 6px; }
</style>
