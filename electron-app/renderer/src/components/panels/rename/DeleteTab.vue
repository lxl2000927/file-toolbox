<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule, RenameRuleOf, RenameRulePatch } from "../../../env";
import AppSelect from "../../common/AppSelect.vue";
import { findRenameRule, inputPositiveInt, upsertRenameRule } from "../../../utils";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

type DeleteRule = RenameRuleOf<"delete_chars">;
type KeepRule = RenameRuleOf<"keep_chars">;

const rule = computed<DeleteRule>(() => {
  return findRenameRule(props.rules, "delete_chars", { delete_type: "删除指定字符", chars: "", count: 1 });
});
const keepRule = computed<KeepRule>(() => {
  return findRenameRule(props.rules, "keep_chars", { mode: "range", range: "", direction: "从右往左" });
});

function patch(p: RenameRulePatch<"delete_chars">) {
  emit("update:rules", upsertRenameRule(props.rules, "delete_chars", { delete_type: "删除指定字符", chars: "", count: 1 }, p));
}

function patchKeep(p: RenameRulePatch<"keep_chars">) {
  const next = [...props.rules];
  const idx = next.findIndex((r) => r.type === "keep_chars");
  const current = idx >= 0 ? next[idx] as KeepRule : keepRule.value;
  const mode = p.mode || current.mode;
  const base: KeepRule = mode === "specified"
    ? { type: "keep_chars", mode: "specified", chars: current.mode === "specified" ? current.chars || "" : "" }
    : { type: "keep_chars", mode: "range", range: current.mode === "range" ? current.range || "" : "", direction: current.mode === "range" ? current.direction || "从右往左" : "从右往左" };
  const updated: KeepRule = { ...base, ...p, type: "keep_chars", mode };
  if (idx >= 0) next[idx] = updated;
  else next.push(updated);
  emit("update:rules", next);
}

const targets = computed<string[]>(() => Array.isArray(rule.value.targets) ? rule.value.targets : []);
const deleteTypeOptions = [
  { label: "删除指定字符", value: "删除指定字符" },
  { label: "删除前 N 个字符", value: "删除前N个字符" },
  { label: "删除后 N 个字符", value: "删除后N个字符" },
  { label: "按类型删除", value: "delete_patterns" },
];
const directionOptions = [
  { label: "从右往左", value: "从右往左" },
  { label: "从左往右", value: "从左往右" },
];

function togglePattern(key: string, on: boolean) {
  const set = new Set(targets.value);
  if (on) set.add(key); else set.delete(key);
  patch({ delete_type: "delete_patterns", targets: Array.from(set) });
}
</script>

<template>
  <div class="rules-tab rename-tab-rules">
  <fieldset class="rules-group">
    <legend>规则列表</legend>
    <details open class="rule-block">
      <summary>① 删除字符</summary>
    <div class="grid">
      <label class="label-inline">方式</label>
      <AppSelect :model-value="rule.delete_type || '删除指定字符'" :options="deleteTypeOptions" @update:model-value="patch({ delete_type: $event as DeleteRule['delete_type'] })" />

      <template v-if="rule.delete_type === '删除指定字符'">
        <label class="label-inline">字符</label>
        <input
          class="input"
          placeholder="要删除的字符（如 _-）"
          :value="rule.chars || ''"
          @input="patch({ chars: ($event.target as HTMLInputElement).value })"
        />
      </template>

      <template v-else-if="rule.delete_type === '删除前N个字符' || rule.delete_type === '删除后N个字符'">
        <label class="label-inline">数量</label>
        <input
          class="input input-sm"
          type="number"
          min="1"
          :value="rule.count ?? 1"
          @input="patch({ count: inputPositiveInt(($event.target as HTMLInputElement).value) })"
        />
      </template>

      <template v-else-if="rule.delete_type === 'delete_patterns'">
        <label class="label-inline">类型</label>
        <div class="checks">
          <label class="checkbox">
            <input type="checkbox" :checked="targets.includes('letters')" @change="togglePattern('letters', ($event.target as HTMLInputElement).checked)" />
            英文字母
          </label>
          <label class="checkbox">
            <input type="checkbox" :checked="targets.includes('digits')" @change="togglePattern('digits', ($event.target as HTMLInputElement).checked)" />
            数字
          </label>
          <label class="checkbox">
            <input type="checkbox" :checked="targets.includes('chinese')" @change="togglePattern('chinese', ($event.target as HTMLInputElement).checked)" />
            中文
          </label>
          <label class="checkbox">
            <input type="checkbox" :checked="targets.includes('symbols')" @change="togglePattern('symbols', ($event.target as HTMLInputElement).checked)" />
            符号
          </label>
        </div>
        <label class="label-inline">自定义字符</label>
        <input
          class="input"
          placeholder="额外要删除的字符"
          :value="rule.custom_chars || ''"
          @input="patch({ custom_chars: ($event.target as HTMLInputElement).value })"
        />
      </template>
    </div>
    </details>

    <details open class="rule-block">
      <summary>② 保留字符</summary>
      <div class="grid">
        <label class="checkbox span-2">
          <input type="radio" name="keep-mode" :checked="keepRule.mode !== 'specified'" @change="patchKeep({ mode: 'range' })" />
          保留范围
        </label>
        <label class="label-inline">范围</label>
        <input class="input" placeholder="例如：1-5" :value="keepRule.range || ''" @input="patchKeep({ mode: 'range', range: ($event.target as HTMLInputElement).value })" />
        <label class="label-inline">方向</label>
        <AppSelect :model-value="keepRule.direction || '从右往左'" :options="directionOptions" @update:model-value="patchKeep({ mode: 'range', direction: $event as NonNullable<KeepRule['direction']> })" />
        <label class="checkbox span-2">
          <input type="radio" name="keep-mode" :checked="keepRule.mode === 'specified'" @change="patchKeep({ mode: 'specified' })" />
          保留指定字符
        </label>
        <label class="label-inline">字符</label>
        <input class="input" placeholder="要保留的字符" :value="keepRule.chars || ''" @input="patchKeep({ mode: 'specified', chars: ($event.target as HTMLInputElement).value })" />
      </div>
    </details>
  </fieldset>
  </div>
</template>

<style scoped>
.checks {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
}
.span-2 { grid-column: 1 / -1; }
</style>
