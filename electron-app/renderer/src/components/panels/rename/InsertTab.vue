<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule, RenameRuleOf, RenameRulePatch } from "../../../env";
import AppSelect from "../../common/AppSelect.vue";
import { findRenameRule, inputPositiveInt, upsertRenameRule } from "../../../utils";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

type InsertRuleType = "insert_text" | "insert_number";

function getRule<T extends InsertRuleType>(type: T, defaults: RenameRulePatch<T>): RenameRuleOf<T> {
  return findRenameRule(props.rules, type, defaults);
}

function patchRule<T extends InsertRuleType>(type: T, defaults: RenameRulePatch<T>, patch: RenameRulePatch<T>) {
  emit("update:rules", upsertRenameRule(props.rules, type, defaults, patch));
}

const insertText = computed(() =>
  getRule("insert_text", { text: "", position: "后缀" }),
);
const insertNumber = computed(() =>
  getRule("insert_number", { start: 1, step: 1, digits: 1, position: "后缀" }),
);
const insertPositionOptions = [
  { label: "首位", value: "前缀" },
  { label: "末位", value: "后缀" },
  { label: "指定位置", value: "指定位置" },
];
const numberPositionOptions = [
  { label: "首位", value: "前缀" },
  { label: "末位", value: "后缀" },
];

</script>

<template>
  <div class="rules-tab rename-tab-rules">
    <fieldset class="rules-group">
      <legend>规则列表</legend>

      <details open class="rule-block">
        <summary>① 插入字符</summary>
        <div class="rule-grid">
          <label class="label-inline">插入字符</label>
          <input
            class="input"
            placeholder="插入字符"
            :value="insertText.text || ''"
            @input="patchRule('insert_text', { text: '', position: '后缀' }, { text: ($event.target as HTMLInputElement).value })"
          />
          <label class="label-inline">插入位置</label>
          <AppSelect :model-value="insertText.position || '后缀'" :options="insertPositionOptions" @update:model-value="patchRule('insert_text', { text: '', position: '后缀' }, { position: $event as RenameRuleOf<'insert_text'>['position'], index: undefined })" />
          <template v-if="insertText.position === '指定位置'">
            <label class="label-inline">索引</label>
            <input
              class="input input-sm"
              type="number"
              min="1"
              :value="insertText.index ?? 1"
              @input="patchRule('insert_text', { text: '', position: '指定位置', index: 1 }, { index: inputPositiveInt(($event.target as HTMLInputElement).value) })"
            />
          </template>
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
            @input="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { start: inputPositiveInt(($event.target as HTMLInputElement).value) })"
          />
          <label class="label-inline">位数</label>
          <input
            class="input input-sm"
            type="number"
            min="1"
            :value="insertNumber.digits ?? 1"
            @input="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { digits: inputPositiveInt(($event.target as HTMLInputElement).value) })"
          />
          <label class="label-inline">递增量</label>
          <input
            class="input input-sm"
            type="number"
            min="1"
            :value="insertNumber.step ?? 1"
            @input="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { step: inputPositiveInt(($event.target as HTMLInputElement).value) })"
          />
          <label class="label-inline">位置</label>
          <AppSelect :model-value="insertNumber.position || '后缀'" :options="numberPositionOptions" @update:model-value="patchRule('insert_number', { start: 1, step: 1, digits: 1, position: '后缀' }, { position: $event as RenameRuleOf<'insert_number'>['position'] })" />
        </div>
      </details>
    </fieldset>
  </div>
</template>

<style scoped>
</style>
