import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import type { WorkspaceLoadResult, WorkspaceScope, WorkspaceSourceStatus, WorkspaceState } from '../../../shared/workspace-types';

const flushers = new Set<() => Promise<void>>();
// App calls this before acknowledging the main-process close request. Waiting
// for nextTick includes changes made immediately before clicking Close.
export async function flushWorkspaces(): Promise<void> {
  await nextTick();
  await Promise.all([...flushers].map(flush => flush()));
  if (typeof window !== 'undefined') await window.electronAPI?.workspace?.flush();
}
export function useWorkspacePersistence(scope: WorkspaceScope, snapshot: () => WorkspaceState, apply: (state: WorkspaceState) => void | Promise<void>) {
  const api = typeof window !== 'undefined' ? window.electronAPI?.workspace : undefined;
  const ready = ref(!api), restoring = ref(Boolean(api)), saving = ref(false), error = ref(''), savedAt = ref('');
  const statuses = shallowRef<WorkspaceSourceStatus[]>([]);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let dirty = false, disposed = false, applied = false;
  let writes: Promise<void> = Promise.resolve();
  let draining: Promise<void> | null = null;
  let initialLoad: Promise<void> = Promise.resolve();
  async function accept(result: WorkspaceLoadResult) {
    restoring.value = true;
    try {
      // Publish unavailable sources before mounting restored page grids. An
      // awaited apply can otherwise render one tick with every source ready.
      statuses.value = result.sources;
      if (result.state) await apply(result.state);
      savedAt.value = result.savedAt || '';
      error.value = '';
      await nextTick();
      applied = true; dirty = false;
    } finally { restoring.value = false; ready.value = true; }
  }
  function schedule() {
    if (!api || !ready.value || restoring.value || disposed || !applied) return;
    dirty = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = undefined; void flush().catch(() => {}); }, 600);
  }
  const stop = watch(snapshot, schedule);
  function flush(): Promise<void> {
    if (!api) return Promise.resolve();
    if (draining) return draining;
    const attempt = (async () => {
      await initialLoad;
      for (;;) {
        await nextTick();
        if (timer) { clearTimeout(timer); timer = undefined; }
        if (!dirty || !applied) {
          await writes;
          // Watchers can mark another edit while a native write is pending.
          // A close acknowledgement is valid only after this stable tick.
          await nextTick();
          if (!dirty || !applied) return;
          continue;
        }
        dirty = false;
        const captured = JSON.parse(JSON.stringify(snapshot())) as WorkspaceState;
        writes = writes.catch(() => {}).then(async () => {
          saving.value = true;
          try { const result = await api.save(scope, captured); savedAt.value = result.savedAt; error.value = ''; }
          catch (caught) { dirty = true; error.value = `工作区保存失败：${String(caught)}`; throw caught; }
          finally { saving.value = false; }
        });
        await writes;
      }
    })();
    draining = attempt;
    void attempt.finally(() => { if (draining === attempt) draining = null; }).catch(() => {});
    return attempt;
  }
  async function restore() {
    if (!api) return;
    restoring.value = true; error.value = '';
    try { await accept(await api.load(scope)); }
    catch (caught) { error.value = `无法恢复工作区：${String(caught)}`; restoring.value = false; ready.value = true; }
  }
  async function relocate(index: number) {
    if (!api || restoring.value) return;
    try {
      await flush(); restoring.value = true;
      const result = await api.relocate(scope, index);
      if (result) await accept(result);
    } catch (caught) { error.value = `重新定位失败：${String(caught)}`; }
    finally { restoring.value = false; }
  }
  const label = computed(() => restoring.value ? '正在恢复工作区…' : saving.value ? '正在保存…' : error.value ? '工作区未保存' : savedAt.value ? '工作区已自动保存' : '工作区自动保存');
  onMounted(() => { initialLoad = restore(); });
  flushers.add(flush);
  onBeforeUnmount(() => {
    stop(); disposed = true;
    // Keep this flusher registered until its last write settles; closing during
    // unmount must not lose the last debounced edit.
    void flush().catch(() => {}).finally(() => flushers.delete(flush));
  });
  return { ready, restoring, saving, error, savedAt, statuses, label, flush, restore, relocate };
}
