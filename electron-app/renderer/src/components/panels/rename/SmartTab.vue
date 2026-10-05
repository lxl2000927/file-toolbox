<script setup lang="ts">
import { computed } from "vue";
import type { RenameRule, RenameRuleOf, RenameRulePatch } from "../../../env";
import AppSelect from "../../common/AppSelect.vue";
import { findRenameRule, inputPositiveInt, upsertRenameRule } from "../../../utils";

const props = defineProps<{ rules: RenameRule[] }>();
const emit = defineEmits<{ "update:rules": [rules: RenameRule[]] }>();

type SmartRule = RenameRuleOf<"smart_recognize">;

const rule = computed<SmartRule>(() => {
  return findRenameRule(props.rules, "smart_recognize", { mode: "content_title", position: "覆盖原名" });
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

function patch(p: RenameRulePatch<"smart_recognize">) {
  emit("update:rules", upsertRenameRule(props.rules, "smart_recognize", { mode: "content_title", position: "覆盖原名" }, p));
}
</script>

<template>
  <fieldset class="rules-group rename-tab-rules">
    <legend>智能识别</legend>
    <p class="text-muted text-sm">从文件内容中提取信息作为新文件名（PDF / 文本）。</p>
    <div class="grid">
      <label class="label-inline">识别模式</label>
      <AppSelect :model-value="rule.mode || 'content_title'" :options="modeOptions" @update:model-value="patch({ mode: $event as SmartRule['mode'] })" />
      <label class="label-inline">写入位置</label>
      <AppSelect :model-value="rule.position || '覆盖原名'" :options="positionOptions" @update:model-value="patch({ position: $event as SmartRule['position'] })" />
      <template v-if="rule.position === '指定位置'">
        <label class="label-inline">索引</label>
        <input
          class="input input-sm"
          type="number"
          min="1"
          :value="rule.index ?? 1"
          @input="patch({ index: inputPositiveInt(($event.target as HTMLInputElement).value) })"
        />
      </template>
    </div>
    <div class="rule-guide">
      <span class="rule-guide-label">{{ rule.mode === 'invoice_info' ? '发票信息' : '标题提取' }}</span>
      <p v-if="rule.mode === 'invoice_info'">从 PDF 中提取发票号码、代码或日期，组合为文件名。</p>
      <p v-else>PDF 优先使用元数据标题，再尝试正文首行；文本文件读取首行内容。</p>
      <div class="example-flow"><span>文件内容</span><span class="example-arrow">→</span><strong>{{ rule.mode === 'invoice_info' ? '发票字段' : '识别标题' }}</strong><span class="example-arrow">→</span><span>新文件名</span></div>
      <p>未提取到有效内容时保留原名。添加文件后，先核对左侧预览再执行。</p>
    </div>
  </fieldset>
</template>

<style scoped>
.text-sm { font-size: var(--font-sm); margin-bottom: 6px; }
</style>
