import { reactive } from "vue";

type ToastKind = "success" | "error" | "info";
type PauseReason = "pointer" | "focus";
type ToastItem = { id: number; message: string; kind: ToastKind; duration: number; paused: boolean };
type ToastTimer = { handle?: ReturnType<typeof setTimeout>; startedAt: number; remaining: number; reasons: Set<PauseReason> };
const durations: Record<ToastKind, number> = { success: 4000, info: 3500, error: 5000 };
let nextId = 0;
const activeTimers = new Map<number, ToastTimer>();
export const toastState = reactive<{ items: ToastItem[] }>({ items: [] });

function dismiss(id: number) {
  const timer = activeTimers.get(id);
  if (timer?.handle !== undefined) clearTimeout(timer.handle);
  activeTimers.delete(id);
  const index = toastState.items.findIndex((item) => item.id === id);
  if (index >= 0) toastState.items.splice(index, 1);
}
function schedule(id: number, timer: ToastTimer) {
  timer.startedAt = Date.now();
  timer.handle = setTimeout(() => dismiss(id), timer.remaining);
}
function pause(id: number, reason: PauseReason) {
  const timer = activeTimers.get(id);
  const item = toastState.items.find((item) => item.id === id);
  if (!timer || !item || timer.reasons.has(reason)) return;
  if (!timer.reasons.size) {
    if (timer.handle !== undefined) clearTimeout(timer.handle);
    timer.handle = undefined;
    timer.remaining = Math.max(0, timer.remaining - (Date.now() - timer.startedAt));
  }
  timer.reasons.add(reason);
  item.paused = true;
}
function resume(id: number, reason: PauseReason) {
  const timer = activeTimers.get(id);
  const item = toastState.items.find((item) => item.id === id);
  if (!timer || !item || !timer.reasons.delete(reason) || timer.reasons.size) return;
  item.paused = false;
  schedule(id, timer);
}
export function useToast() {
  function show(message: string, kind: ToastKind = "info") {
    if (toastState.items.length >= 5) dismiss(toastState.items[0].id);
    const id = ++nextId;
    const duration = durations[kind];
    toastState.items.push({ id, message, kind, duration, paused: false });
    const timer: ToastTimer = { startedAt: 0, remaining: duration, reasons: new Set() };
    activeTimers.set(id, timer);
    schedule(id, timer);
    return id;
  }
  return { show, dismiss, pause, resume,
    success: (message: string) => show(message, "success"),
    error: (message: string) => show(message, "error"),
    info: (message: string) => show(message, "info"),
  };
}
