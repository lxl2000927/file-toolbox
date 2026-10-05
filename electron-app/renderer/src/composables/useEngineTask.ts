import { computed, onBeforeUnmount, ref } from "vue";

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
    | { ok: true; result: unknown; taskType?: string; elapsedMs?: number; cancelled?: boolean }
    | { ok: false; error: string; trace?: string; result?: unknown; taskType?: string; elapsedMs?: number; cancelled?: boolean },
) => void;

export type TaskStartResult = { task_id: string; queued?: boolean; position?: number };

const MAX_LOG_LINES = 500;

export function useEngineTask(opts: {
  onLog?: (line: string) => void;
  onComplete?: TaskCompleteHandler;
  onQueued?: (position: number) => void;
}) {
  // 保留最新的 onComplete 引用，给 engine.status 监听器使用
  const onCompleteRef = { current: opts.onComplete };

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
  const busy = computed(() => pending.value || state.value.running || state.value.queued);
  const cancellable = computed(() => Boolean(state.value.taskId) && (pending.value || state.value.running || state.value.queued));

  let unsubscribe: (() => void) | null = null;
  let cancellingTaskId: string | null = null;
  let submitted = false;
  let cancelRequested = false;

  function start(taskId: string) {
    reset();
    pending.value = true;
    state.value.taskId = taskId;
    state.value.running = false;
    state.value.queued = false;

    unsubscribe = window.engine?.onNotification(({ method, params }) => {
      if (method === "engine.status") {
        const status = String(params?.status || "");
        if (status === "error" && busy.value) {
          const prevTaskId = state.value.taskId;
          state.value.running = false;
          state.value.queued = false;
          state.value.taskId = null;
          cancellingTaskId = null;
          pending.value = false;
          cleanup();
          if (prevTaskId) {
            onCompleteRef.current?.({
              ok: false,
              error: "引擎异常断开",
              cancelled: false,
              result: null,
            });
          }
        }
        return;
      }
      if (!params || params.task_id !== state.value.taskId) return;
      if (method === "task.progress") {
        pending.value = false;
        state.value.running = true;
        state.value.queued = false;
        state.value.phase = String(params.phase || "");
        state.value.current = Number.isFinite(Number(params.current)) ? Number(params.current) : 0;
        state.value.total = Number.isFinite(Number(params.total)) ? Number(params.total) : 0;
        if (params.file) state.value.file = String(params.file);
      } else if (method === 'task.state') {
        const labels: Record<string, string> = { pausing: '等待当前处理结束后暂停', paused: '已暂停，可在任务中心继续', running: '已继续处理', queued: '排队中', cancelling: '正在取消' };
        if (params.state && labels[params.state]) state.value.phase = labels[params.state];
      } else if (method === "task.log" || method === 'task.warning') {
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
          state.value.running = true;
          state.value.phase = "准备处理";
        } else {
          markQueued(Number(params.position || 1), params.task_id);
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
        state.value.taskId = null;
        cancellingTaskId = null;
        cleanup();
        if (params.ok) {
          onCompleteRef.current?.({
            ok: true,
            result: params.result,
            taskType: params.task_type,
            elapsedMs: params.elapsed_ms,
            cancelled: Boolean(params.cancelled),
          });
        } else {
          onCompleteRef.current?.({
            ok: false,
            error: params.error || "任务执行失败",
            trace: params.trace,
            result: params.result,
            taskType: params.task_type,
            elapsedMs: params.elapsed_ms,
            cancelled: Boolean(params.cancelled),
          });
        }
      }
      if (method !== "task.complete") markSubmitted(taskId);
    }) ?? null;
  }

  function markSubmitted(taskId: string) {
    if (state.value.taskId !== taskId || submitted) return;
    submitted = true;
    if (cancelRequested) {
      cancelRequested = false;
      void cancel();
    }
  }

  async function cancel() {
    const taskId = state.value.taskId;
    if (!taskId || cancellingTaskId === taskId) return;
    // File authorization in the main process can outlast an immediate Stop
    // click. Wait for registration so cancellation cannot miss the new job.
    if (!submitted) {
      cancelRequested = true;
      state.value.phase = "等待任务提交后取消";
      return;
    }
    cancellingTaskId = taskId;
    try {
      if (!window.engine) throw new Error("引擎不可用");
      const result = await window.engine.cancelTask(taskId);
      if (state.value.taskId !== taskId) return;
      if (!result.cancelled) {
        reset();
        onCompleteRef.current?.({ ok: false, error: "取消失败：任务不存在或已结束" });
      } else {
        state.value.phase = "正在取消";
      }
    } catch (error) {
      if (state.value.taskId !== taskId) return;
      const message = `取消请求失败，可重试：${error instanceof Error ? error.message : String(error)}`;
      state.value.phase = message;
      opts.onLog?.(message);
    } finally {
      if (cancellingTaskId === taskId) cancellingTaskId = null;
    }
  }

  function markQueued(position: number, taskId = state.value.taskId) {
    if (!taskId || taskId !== state.value.taskId || !pending.value || state.value.running) return;
    pending.value = true;
    state.value.running = false;
    state.value.queued = true;
    state.value.phase = `排队中（第 ${Math.max(1, Number(position) || 1)} 位）`;
    opts.onQueued?.(Math.max(1, Number(position) || 1));
  }

  function reset() {
    cleanup();
    cancellingTaskId = null;
    submitted = false;
    cancelRequested = false;
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

  return { state, logs, pending, busy, cancellable, start, markSubmitted, markQueued, cancel, reset };
}

export function generateTaskId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}
