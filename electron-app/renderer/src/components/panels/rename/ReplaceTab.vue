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
        aria-label="查找文字" class="input"
        placeholder="要查找的文字"
        :value="rule.find || ''"
        @input="patch({ find: ($event.target as HTMLInputElement).value })"
      />
      <label class="label-inline">替换为</label>
      <input
        aria-label="替换后的文字" class="input"
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
    <div class="rule-guide">
      <span class="rule-guide-label">使用示例</span>
      <div class="example-flow"><span>合同_旧版.pdf</span><span class="example-arrow">→</span><strong>合同_新版.pdf</strong></div>
      <p>查找“旧版”，替换为“新版”。替换为留空时删除匹配的文字；文件扩展名保持不变。</p>
      <p>添加文件后，左侧会按当前输入实时预览新名称。</p>
    </div>
  </fieldset>
</template>

<style scoped>
</style>
