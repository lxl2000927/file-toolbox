<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule } from "../../../env";
import AppSelect from "../../common/AppSelect.vue";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

function getRule(type: string, defaults: Partial<RenameRule>): RenameRule {
  const found = props.rules.find((r) => r.type === type);
  if (found) return found;
  return { type, ...defaults } as RenameRule;
}

function patchRule(type: string, defaults: Partial<RenameRule>, patch: Partial<RenameRule>) {
  const next = [...props.rules];
  const idx = next.findIndex((r) => r.type === type);
  if (idx >= 0) {
    next[idx] = { ...next[idx], ...patch } as RenameRule;
  } else {
    next.push({ type, ...defaults, ...patch } as RenameRule);
  }
  emit("update:rules", next);
}

const uniformName = computed(() => getRule("uniform_name", { base_name: "" }));
const insertNumber = computed(() =>
  getRule("insert_number", { start: 1, step: 1, digits: 1, position: "后缀" }),
);
const numberPositionOptions = [
  { label: "末位", value: "后缀" },
  { label: "首位", value: "前缀" },
];

import { positiveInt } from "../../../utils";
</script>

<template>
  <div class="rules-tab">
    <fieldset class="rules-group">
      <legend>规则列表</legend>

      <details open class="rule-block">
        <summary>① 统一名称</summary>
        <div class="rule-grid uniform-grid">
          <input
            class="input"
            placeholder="请输入公共文件名"
            :value="uniformName.base_name || ''"
            @input="patchRule('uniform_name', { base_name: '' }, { base_name: ($event.target as HTMLInputElement).value })"
          />
        </div>
      </details>

      <details open class="rule-block">
        <summary>② 插入编号</summary>
        <div class="rule-grid four-col">
          <label class="label-inline">初始值</label>
          <input
            class="input input-sm"
            type="number"
            min="1"
            :value="insertNumber.start ?? 1"
            @input="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { start: positiveInt(($event.target as HTMLInputElement).value) })"
          />
          <label class="label-inline">位数</label>
          <input
            class="input input-sm"
            type="number"
            min="1"
            max="6"
            :value="insertNumber.digits ?? 1"
            @input="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { digits: positiveInt(($event.target as HTMLInputElement).value) })"
          />
          <label class="label-inline">递增量</label>
          <input
            class="input input-sm"
            type="number"
            min="1"
            :value="insertNumber.step ?? 1"
            @input="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { step: positiveInt(($event.target as HTMLInputElement).value) })"
          />
          <label class="label-inline">位置</label>
          <AppSelect :model-value="insertNumber.position || '后缀'" :options="numberPositionOptions" @update:model-value="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { position: $event as string })" />
        </div>
      </details>
    </fieldset>
  </div>
</template>

<style scoped>
.rules-tab {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
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
.rule-block {
  margin-top: 10px;
}
.rule-block summary {
  list-style: none;
  cursor: pointer;
  font-weight: 600;
  color: var(--color-gray-700);
  padding: 2px 4px;
  user-select: none;
}
.rule-block summary::-webkit-details-marker { display: none; }
.rule-block summary::before {
  content: "▾";
  display: inline-block;
  margin-right: 6px;
  color: var(--color-gray-500);
  transition: transform var(--transition-fast);
}
.rule-block:not([open]) summary::before { transform: rotate(-90deg); }
.rule-grid {
  display: grid;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  padding-left: 8px;
}
.uniform-grid {
  grid-template-columns: 1fr 22px;
}
.rule-grid.four-col {
  grid-template-columns: 70px 1fr 70px 1fr;
}
.label-inline {
  font-size: var(--font-md);
  color: var(--color-gray-700);
  font-weight: 500;
}
</style>
