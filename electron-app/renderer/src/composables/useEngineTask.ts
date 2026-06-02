import { onBeforeUnmount, ref, watch } from "vue";

export type TaskState = {
  taskId: string | null;
  phase: string;
  current: number;
  total: number;
  file: string;
  running: boolean;
};

export type TaskCompleteHandler = (
  payload:
    | { ok: true; result: any; taskType?: string; elapsedMs?: number; cancelled?: boolean }
    | { ok: false; error: string; trace?: string; result?: any; taskType?: string; elapsedMs?: number; cancelled?: boolean },
) => void;

const MAX_LOG_LINES = 500;

export function useEngineTask(opts: {
  onLog?: (line: string) => void;
  onComplete?: TaskCompleteHandler;
}) {
  const state = ref<TaskState>({
    taskId: null,
    phase: "",
    current: 0,
    total: 0,
    file: "",
    running: false,
  });
  const logs = ref<string[]>([]);

  let unsubscribe: (() => void) | null = null;

  function start(taskId: string) {
    reset();
    state.value.taskId = taskId;
    state.value.running = true;

    unsubscribe = window.engine?.onNotification(({ method, params }) => {
      if (!params || params.task_id !== state.value.taskId) return;
      if (method === "task.progress") {
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
      } else if (method === "task.complete") {
        state.value.running = false;
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
    await window.engine?.cancelTask(state.value.taskId);
  }

  function reset() {
    cleanup();
    state.value = {
      taskId: null,
      phase: "",
      current: 0,
      total: 0,
      file: "",
      running: false,
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

  return { state, logs, start, cancel, reset };
}

export function generateTaskId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

