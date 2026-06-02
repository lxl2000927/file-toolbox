<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule } from "../../../env";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

const rule = computed<RenameRule>(() => {
  const found = props.rules.find((r) => r.type === "replace_text");
  return found || { type: "replace_text", find: "", replace: "", case_sensitive: false };
});

function patch(p: Partial<RenameRule>) {
  const next = [...props.rules];
  const idx = next.findIndex((r) => r.type === "replace_text");
  if (idx >= 0) next[idx] = { ...next[idx], ...p };
  else next.push({ type: "replace_text", find: "", replace: "", case_sensitive: false, ...p });
  emit("update:rules", next);
}
</script>

<template>
  <fieldset class="rules-group">
    <legend>替换文字</legend>
    <div class="grid">
      <label class="label-inline">查找</label>
      <input
        class="input"
        placeholder="要查找的文字"
        :value="rule.find || ''"
        @input="patch({ find: ($event.target as HTMLInputElement).value })"
      />
      <label class="label-inline">替换为</label>
      <input
        class="input"
        placeholder="替换后的文字（留空表示删除）"
        :value="rule.replace || ''"
        @input="patch({ replace: ($event.target as HTMLInputElement).value })"
      />
      <span />
      <label class="checkbox">
        <input
          type="checkbox"
          :checked="rule.case_sensitive === true"
          @change="patch({ case_sensitive: ($event.target as HTMLInputElement).checked })"
        />
        区分大小写
      </label>
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
</style>
