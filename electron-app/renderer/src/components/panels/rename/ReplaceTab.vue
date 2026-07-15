<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule, RenameRuleOf, RenameRulePatch } from "../../../env";
import { findRenameRule, upsertRenameRule } from "../../../utils";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

type ReplaceRule = RenameRuleOf<"replace_text">;

const rule = computed<ReplaceRule>(() => {
  return findRenameRule(props.rules, "replace_text", { find: "", replace: "", case_sensitive: false });
});

function patch(p: RenameRulePatch<"replace_text">) {
  emit("update:rules", upsertRenameRule(props.rules, "replace_text", { find: "", replace: "", case_sensitive: false }, p));
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
