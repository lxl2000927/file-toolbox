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
  <fieldset class="rules-group rename-tab-rules">
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
</style>
