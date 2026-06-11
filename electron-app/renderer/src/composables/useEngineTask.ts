import { onBeforeUnmount, ref } from "vue";

export type TaskState = {
  taskId: string | null;
  phase: string;
  current: number;
  total: number;
  file: string;
  running: boolean;
  queued: boolean;
};

export type TaskCompleteHandler = (
  payload:
    | { ok: true; result: any; taskType?: string; elapsedMs?: number; cancelled?: boolean }
    | { ok: false; error: string; trace?: string; result?: any; taskType?: string; elapsedMs?: number; cancelled?: boolean },
) => void;

export type TaskStartResult = { task_id: string; queued?: boolean; position?: number };

const MAX_LOG_LINES = 500;

export function useEngineTask(opts: {
  onLog?: (line: string) => void;
  onComplete?: TaskCompleteHandler;
  onQueued?: (position: number) => void;
}) {
  const state = ref<TaskState>({
    taskId: null,
    phase: "",
    current: 0,
    total: 0,
    file: "",
    running: false,
    queued: false,
  });
  const logs = ref<string[]>([]);
  const pending = ref(false);

  let unsubscribe: (() => void) | null = null;

  function start(taskId: string) {
    reset();
    pending.value = true;
    state.value.taskId = taskId;
    state.value.running = false;
    state.value.queued = false;

    unsubscribe = window.engine?.onNotification(({ method, params }) => {
      if (!params || params.task_id !== state.value.taskId) return;
      if (method === "task.progress") {
        pending.value = false;
        state.value.running = true;
        state.value.queued = false;
        state.value.phase = String(params.phase || "");
        state.value.current = Number.isFinite(Number(params.current)) ? Number(params.current) : 0;
        state.value.total = Number.isFinite(Number(params.total)) ? Number(params.total) : 0;
        if (params.file) state.value.file = String(params.file);
      } else if (method === "task.log") {
        const msg = String(params.message || "");
        logs.value.push(msg);
        if (logs.value.length > MAX_LOG_LINES) {
          logs.value.splice(0, logs.value.length - MAX_LOG_LINES);
        }
        opts.onLog?.(msg);
      } else if (method === "task.queued") {
        if (!params.queued) {
          pending.value = false;
          state.value.queued = false;
        } else {
          pending.value = true;
          state.value.running = false;
          state.value.queued = true;
          state.value.phase = `排队中（第 ${Math.max(1, Number(params.position) || 1)} 位）`;
          opts.onQueued?.(Number(params.position || 0));
        }
        const msg = String(params.message || "");
        if (msg) {
          logs.value.push(msg);
          if (logs.value.length > MAX_LOG_LINES) {
            logs.value.splice(0, logs.value.length - MAX_LOG_LINES);
          }
          opts.onLog?.(msg);
        }
      } else if (method === "task.complete") {
        pending.value = false;
        state.value.running = false;
        state.value.queued = false;
        cleanup();
        if (params.ok) {
          opts.onComplete?.({
            ok: true,
            result: params.result,
            taskType: params.task_type,
            elapsedMs: params.elapsed_ms,
            cancelled: Boolean(params.cancelled),
          });
        } else {
          opts.onComplete?.({
            ok: false,
            error: params.error,
            trace: params.trace,
            result: params.result,
            taskType: params.task_type,
            elapsedMs: params.elapsed_ms,
            cancelled: Boolean(params.cancelled),
          });
        }
      }
    }) ?? null;
  }

  async function cancel() {
    if (!state.value.taskId) return;
    pending.value = false;
    const result = await window.engine?.cancelTask(state.value.taskId);
    if (result && !result.cancelled) {
      state.value.running = false;
      state.value.queued = false;
      state.value.phase = "取消失败：任务不存在或已结束";
    }
  }

  function markQueued(position: number) {
    pending.value = true;
    state.value.running = false;
    state.value.queued = true;
    state.value.phase = `排队中（第 ${Math.max(1, Number(position) || 1)} 位）`;
    opts.onQueued?.(Math.max(1, Number(position) || 1));
  }

  function reset() {
    cleanup();
    pending.value = false;
    state.value = {
      taskId: null,
      phase: "",
      current: 0,
      total: 0,
      file: "",
      running: false,
      queued: false,
    };
    logs.value = [];
  }

  function cleanup() {
    if (unsubscribe) {
      unsubscribe();
      unsubscribe = null;
    }
  }

  onBeforeUnmount(() => cleanup());

  return { state, logs, pending, start, markQueued, cancel, reset };
}

export function generateTaskId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}
