<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { PdfToolResult } from '../../../../shared/api-types';
import { formatBytes } from '../../pdf-workbench';
import { resultSummary, sizeComparison } from '../../pdf-results';
const props = defineProps<{ result: PdfToolResult }>();
const page = ref(1), textPage = ref(0), pending = ref(false), feedback = ref(''), failed = ref(false);
const summary = computed(() => resultSummary(props.result));
const comparison = computed(() => sizeComparison(props.result));
const pageCount = computed(() => Math.max(1, Math.ceil(props.result.output_files.length / 50)));
const files = computed(() => props.result.output_files.slice((page.value - 1) * 50, page.value * 50));
const ocrPage = computed(() => props.result.ocr_pages?.[textPage.value]);
const textFiles = computed(() => props.result.text_files || []);
const textFilePage = ref(1);
const visibleTextFiles = computed(() => textFiles.value.slice((textFilePage.value - 1) * 50, textFilePage.value * 50));
watch(() => props.result, () => { page.value = 1; textPage.value = 0; textFilePage.value = 1; feedback.value = ''; });
function basename(path: string) { return path.split(/[\\/]/).pop() || path; }
async function act(action: 'open' | 'reveal' | 'copy' | 'save', value: string) {
  if (pending.value) return;
  pending.value = true; feedback.value = ''; failed.value = false;
  try {
    const api = window.electronAPI;
    if (!api) throw new Error('请在桌面程序中操作');
    if (action === 'open') { await api.openDocument(value); feedback.value = '已请求系统打开文件。'; }
    else if (action === 'reveal') { await api.revealDocument(value); feedback.value = '已在文件夹中定位。'; }
    else if (action === 'copy') { await api.copyText(value); feedback.value = '已复制。'; }
    else { const saved = await api.saveTextCopy(value); if (saved.saved) feedback.value = `完整 TXT 已导出至 ${saved.path}`; }
  } catch (caught) { failed.value = true; feedback.value = String(caught); }
  finally { pending.value = false; }
}
</script>
<template>
  <section class="pdf-results">
    <div class="result-status" :data-kind="summary.kind"><strong>{{ summary.title }}</strong><span>{{ summary.message }}</span></div>
    <div class="output-stats"><div><span>完整输出</span><strong>{{ result.output_files.length }} <small>个文件</small></strong></div><div><span>涉及来源体积</span><strong>{{ formatBytes(result.bytes_before) }}</strong></div><div><span>输出体积 · 不含 TXT</span><strong>{{ formatBytes(result.bytes_after) }}</strong></div></div>
    <p v-if="comparison" class="size-comparison">{{ comparison }}<small>相同来源的全部页面；具体效果取决于原文件</small></p>
    <p v-else class="output-note">仅在完整导出相同来源的全部页面时比较体积；提取页、图片及未完成任务不计算压缩比例。</p>
    <p v-for="(issue, i) in result.errors" :key="`error-${i}`" class="result-issue" role="alert">{{ issue }}</p>
    <p v-for="(warning, i) in result.warnings" :key="i" class="result-issue">{{ warning }}</p>
    <div v-if="feedback" :class="['action-feedback', { failed }]" :role="failed ? 'alert' : 'status'">{{ feedback }}</div>
    <p class="output-note">输出为新文件，同名自动编号。原文件保持不变。</p>
    <ol class="output-list" :start="(page - 1) * 50 + 1"><li v-for="file in files" :key="file"><span class="output-path" :title="file">{{ file }}</span><div class="file-actions"><button class="btn" :disabled="pending" @click="act('open', file)">打开</button><button class="btn" :disabled="pending" @click="act('reveal', file)">定位</button><button class="btn" :disabled="pending" @click="act('copy', file)">复制路径</button></div></li></ol>
    <nav v-if="pageCount > 1" class="result-pagination" aria-label="输出文件分页"><button class="btn" :disabled="page === 1" @click="page--">上一页</button><span>{{ page }} / {{ pageCount }} · 每页 50 个</span><button class="btn" :disabled="page === pageCount" @click="page++">下一页</button></nav>
    <details v-if="result.ocr_pages?.length || textFiles.length" class="ocr-text" open>
      <summary>识别文字 <span>共 {{ result.ocr_total_pages || 0 }} 页</span></summary>
      <p class="output-note">预览前 {{ result.ocr_preview_limit || 20 }} 页，每页最多 {{ result.ocr_preview_chars || 4000 }} 字。请复核识别内容。</p>
      <template v-if="ocrPage">
        <div class="text-controls"><select v-model.number="textPage" aria-label="文字预览页"><option v-for="(entry, i) in result.ocr_pages" :value="i" :key="i">{{ basename(entry.output_file) }} · 第 {{ entry.page }} 页</option></select><button class="btn" :disabled="pending || !ocrPage.text" @click="act('copy', ocrPage.text)">复制当前预览</button></div>
        <p v-if="ocrPage.truncated" class="result-issue">本页预览已截断，仅显示前 {{ result.ocr_preview_chars || 4000 }} 字。</p>
        <pre>{{ ocrPage.text || '此页未识别到文字。' }}</pre>
      </template>
      <div v-if="textFiles.length" class="text-exports"><strong>完整文字 TXT · {{ textFiles.length }} 份</strong><div v-for="file in visibleTextFiles" :key="file" class="text-export"><span :title="file">{{ basename(file) }}</span><button class="btn" :disabled="pending" @click="act('open', file)">打开 TXT</button><button class="btn" :disabled="pending" @click="act('save', file)">导出完整 TXT</button></div><nav v-if="textFiles.length > 50" class="result-pagination"><button class="btn" :disabled="textFilePage === 1" @click="textFilePage--">上一页</button><span>{{ textFilePage }} / {{ Math.ceil(textFiles.length / 50) }}</span><button class="btn" :disabled="textFilePage * 50 >= textFiles.length" @click="textFilePage++">下一页</button></nav></div>
      <p v-else class="output-note">本次未生成完整 TXT。开启“同时导出完整文字 TXT”后重新输出。</p>
    </details>
  </section>
</template>
<style scoped>
.pdf-results { font-size: 12px; min-width: 0; }
.result-status { display: grid; gap: 6px; margin-bottom: 16px; line-height: 1.6; }
.result-status strong { font-size: 18px; }
.result-status span, .output-note { color: var(--color-text-secondary); }
.result-status[data-kind=partial] strong, .result-status[data-kind=error] strong, .result-status[data-kind=cancelled] strong { color: #b45309; }
.output-stats { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 10px; }
.output-stats>div { border: 1px solid var(--color-border); border-radius: 10px; padding: 13px; animation: surfaceReveal 280ms var(--motion-out) both; }
.output-stats>div:nth-child(2) { animation-delay: 35ms; }.output-stats>div:nth-child(3) { animation-delay: 70ms; }
.output-stats span { display: block; font-size: 10px; color: var(--color-text-secondary); margin-bottom: 9px; }
.output-stats strong { font-size: 18px; }.output-stats small { font-size: 10px; font-weight: 400; }
.size-comparison { font-size: 16px; color: var(--color-primary); margin: 16px 0; }.size-comparison small { display: block; font-size: 10px; margin-top: 6px; color: var(--color-text-secondary); }
.output-note { font-size: 11px; line-height: 1.7; }
.result-issue, .action-feedback { padding: 9px 11px; background: var(--color-primary-light); border-radius: 7px; line-height: 1.7; overflow-wrap: anywhere; white-space: pre-wrap; }
.result-issue, .action-feedback.failed { color: #b45309; background: #fff7e8; }
.output-list { padding-left: 24px; margin: 12px 0; }.output-list li { padding: 11px 0; border-bottom: 1px solid var(--color-border); }
.output-path { display: block; overflow-wrap: anywhere; line-height: 1.7; user-select: text; }
.file-actions { display: flex; gap: 6px; margin-top: 8px; }
.btn { font-size: 11px; padding: 5px 9px; }
.result-pagination { display: flex; align-items: center; justify-content: center; gap: 10px; font-size: 11px; margin: 14px 0; }
.ocr-text { border-top: 1px solid var(--color-border); margin-top: 22px; padding-top: 18px; }.ocr-text summary { font-weight: 600; cursor: pointer; }.ocr-text summary span { float: right; font-weight: 400; color: var(--color-text-secondary); }
.text-controls { display: flex; gap: 8px; }.text-controls select { flex: 1; min-width: 0; padding: 7px; color: inherit; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 6px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; user-select: text; padding: 14px; background: var(--color-surface-2); border-radius: 9px; line-height: 1.8; }
.text-exports { display: grid; gap: 10px; }.text-exports>strong { font-size: 11px; }.text-export { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }.text-export>span { flex: 1; min-width: 100px; overflow-wrap: anywhere; }
</style>
