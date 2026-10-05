<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue';
import type { TaskRecord } from '../../../../shared/task-types';

const records = ref<TaskRecord[]>([]);
const error = ref(''), message = ref(''), filter = ref('all'), pending = ref('');
const page = ref(1);
const states: Record<string, string> = { queued: '等待处理', running: '正在处理', pausing: '等待安全暂停', paused: '已暂停',
  completed: '已完成', failed: '处理失败', cancelled: '已取消', interrupted: '上次运行中断', cancelling: '正在取消' };
const activeStates = new Set(['queued', 'running', 'pausing', 'paused', 'cancelling']);
const activeCount = computed(() => records.value.filter(r => activeStates.has(r.state)).length);
const retryCount = computed(() => records.value.filter(r => r.can_retry).length);
const filtered = computed(() => records.value.filter(r => filter.value === 'all' ||
  (filter.value === 'active' ? activeStates.has(r.state) : filter.value === 'retry' ? r.can_retry : r.state === 'completed')));
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 30)));
const visible = computed(() => filtered.value.slice((Math.min(page.value, totalPages.value) - 1) * 30, Math.min(page.value, totalPages.value) * 30));
let revision = 0, disposed = false, visiblePanel = true;
let refreshTimer: ReturnType<typeof setTimeout> | undefined, unsubscribe: (() => void) | undefined;
async function refresh() {
  const current = ++revision;
  if (!window.engine?.tasks) { error.value = '任务引擎尚未连接'; return; }
  try {
    const result = await window.engine.tasks.list();
    if (disposed || current !== revision) return;
    records.value = result.tasks; error.value = result.storage_error || '';
  } catch (caught) { if (!disposed && current === revision) error.value = String(caught); }
}
function schedule() {
  if (!visiblePanel || refreshTimer) return;
  refreshTimer = setTimeout(() => { refreshTimer = undefined; void refresh(); }, 350);
}
function title(record: TaskRecord) {
  if (record.method?.startsWith('scan_split')) return '扫描拆分';
  if (record.method?.startsWith('pdf_split')) return '普通拆分';
  return 'PDF 工作台';
}
function timestamp(value: string | number) {
  const date = new Date(typeof value === 'number' ? (value < 1e12 ? value * 1000 : value) : value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString('zh-CN', { hour12: false });
}
async function act(action: 'pause' | 'resume' | 'retry' | 'cancel', record: TaskRecord) {
  if (pending.value) return;
  if (action === 'retry' && !record.can_retry) return;
  pending.value = record.task_id; error.value = ''; message.value = '';
  try {
    if (action === 'cancel') await window.engine!.cancelTask(record.task_id);
    else await window.engine!.tasks[action](record.task_id);
    message.value = action === 'retry' ? '已提交续做任务；已验证的完整输出会保留。' : action === 'pause' ? '暂停将在当前处理单元的安全边界生效。' : action === 'resume' ? '已继续任务。' : '已提交取消请求。';
    void refresh();
  } catch (caught) { error.value = String(caught); }
  finally { pending.value = ''; }
}
async function reveal(id: string) {
  try { await window.engine?.tasks.reveal(id); }
  catch (caught) { error.value = String(caught); }
}
onMounted(() => {
  void refresh();
  unsubscribe = window.engine?.onNotification(({ method }) => { if (method.startsWith('task.') || method === 'engine.status') schedule(); });
});
onActivated(() => { visiblePanel = true; schedule(); });
onDeactivated(() => { visiblePanel = false; if (refreshTimer) clearTimeout(refreshTimer); refreshTimer = undefined; });
onBeforeUnmount(() => { disposed = true; revision++; unsubscribe?.(); if (refreshTimer) clearTimeout(refreshTimer); });
</script>

<template>
  <section class="task-center">
    <header class="task-heading"><div><p class="eyebrow">YOUR PROCESSING DESK</p><h1>任务中心</h1><p class="subtitle">查看处理进度，暂停、继续，或从中断处续做。</p></div><button class="btn" @click="refresh">刷新列表</button></header>
    <div class="task-overview"><div><strong>{{ activeCount }}</strong><span>进行中与等待</span></div><div><strong>{{ retryCount }}</strong><span>可重试或续做</span></div><div><strong>{{ records.length }}</strong><span>最近任务</span></div></div>
    <div class="task-toolbar"><label>显示<select v-model="filter" @change="page = 1"><option value="all">全部任务</option><option value="active">进行中与等待</option><option value="retry">可重试或续做</option><option value="completed">已完成</option></select></label><p>中断后需手动续做。暂停与取消会等待当前原生处理返回。</p></div>
    <p v-if="error" class="task-error" role="alert">{{ error }}</p><p v-if="message" class="task-message" role="status">{{ message }}</p>
    <div class="task-list">
      <div v-if="!visible.length && !error" class="task-empty"><h2>这里会保留你的处理任务</h2><p>从工作台、普通拆分或扫描拆分开始，进度和完成文件会集中显示在这里。</p></div>
      <article v-for="record in visible" :key="record.task_id" class="task-card" :data-state="record.state">
        <div class="task-card-top"><div><h2>{{ title(record) }}</h2><time>{{ timestamp(record.created_at) }}</time></div><span class="state-badge">{{ states[record.state] || record.state }}</span></div>
        <p class="task-phase">{{ record.phase || '等待任务进度' }}<span v-if="record.total > 0">{{ record.current }} / {{ record.total }}</span></p>
        <progress v-if="activeStates.has(record.state) && record.total > 0" :value="Math.min(record.current, record.total)" :max="record.total" aria-label="当前阶段进度" />
        <p v-if="record.file" class="task-file" :title="record.file">{{ record.file }}</p><p v-if="record.error" class="task-detail-error">{{ record.error }}</p>
        <div class="task-actions"><span>{{ record.output_count ?? record.output_files?.length ?? 0 }} 个已完成文件</span><button v-if="record.output_files?.length" class="btn" @click="reveal(record.task_id)">定位结果</button><button v-if="['running', 'queued'].includes(record.state)" class="btn" :disabled="!!pending" @click="act('pause', record)">暂停</button><button v-if="['paused', 'pausing'].includes(record.state)" class="btn btn-primary" :disabled="!!pending" @click="act('resume', record)">继续</button><button v-if="activeStates.has(record.state)" class="btn" :disabled="!!pending" @click="act('cancel', record)">取消</button><button v-if="record.can_retry" class="btn btn-primary" :disabled="!!pending" @click="act('retry', record)">重试 / 续做</button></div>
      </article>
    </div>
    <footer class="task-pagination"><span>保留最近 {{ records.length }} 项记录</span><button class="btn" :disabled="page <= 1" @click="page--">上一页</button><span>{{ Math.min(page, totalPages) }} / {{ totalPages }}</span><button class="btn" :disabled="page >= totalPages" @click="page++">下一页</button></footer>
  </section>
</template>

<style scoped>
.task-center { height: 100%; display: flex; flex-direction: column; min-height: 0; padding: 28px 32px 16px; gap: 16px; }
.task-heading { display: flex; align-items: center; justify-content: space-between; }
.eyebrow { letter-spacing: .14em; font-size: 10px; color: var(--color-primary); margin-bottom: 6px; }
h1 { font-size: 25px; font-weight: 650; margin: 0; } .subtitle { margin-top: 8px; color: var(--color-text-secondary); font-size: 13px; }
.task-overview { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; } .task-overview > div { background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: 14px; padding: 16px 20px; display: flex; gap: 12px; align-items: baseline; } .task-overview strong { font-size: 27px; font-weight: 600; color: var(--color-primary-dark); } .task-overview span { font-size: 12px; color: var(--color-text-secondary); }
.task-toolbar { display: flex; gap: 20px; align-items: center; } .task-toolbar label { display: flex; gap: 10px; align-items: center; white-space: nowrap; font-size: 12px; } .task-toolbar select { padding: 7px 10px; border-radius: 7px; border: 1px solid var(--glass-border); background: var(--glass-bg); } .task-toolbar p { font-size: 12px; color: var(--color-text-secondary); }
.task-list { flex: 1; min-height: 0; overflow: auto; padding-right: 4px; } .task-card { background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: 14px; padding: 18px 20px; margin-bottom: 12px; animation: surfaceReveal 200ms var(--motion-out); } .task-card-top { display: flex; align-items: center; justify-content: space-between; gap: 20px; } .task-card h2 { font-size: 15px; margin: 0 0 5px; } time { font-size: 11px; color: var(--color-text-secondary); } .state-badge { font-size: 11px; padding: 5px 9px; border-radius: 20px; background: #edf2fa; color: #3a547c; } [data-state=completed] .state-badge { background: #e8f5ed; color: #267846; } [data-state=failed] .state-badge, [data-state=interrupted] .state-badge { background: #fff0e5; color: #a35420; }
.task-phase { display: flex; justify-content: space-between; gap: 16px; font-size: 12px; margin: 13px 0 7px; } progress { height: 5px; width: 100%; accent-color: var(--color-primary); } .task-file { font-size: 11px; color: var(--color-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 9px; } .task-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 14px; } .task-actions > span { margin-right: auto; font-size: 12px; color: var(--color-text-secondary); }
.task-error, .task-detail-error { color: #aa362d; font-size: 12px; overflow-wrap: anywhere; } .task-error { padding: 10px; background: #fff0ed; border-radius: 8px; } .task-message { font-size: 12px; color: var(--color-primary-dark); } .task-empty { padding: 50px 24px; text-align: center; color: var(--color-text-secondary); } .task-empty h2 { font-size: 17px; } .task-empty p { font-size: 13px; margin-top: 12px; } .task-pagination { display: flex; align-items: center; gap: 12px; font-size: 12px; } .task-pagination > span:first-child { margin-right: auto; color: var(--color-text-secondary); }
</style>
