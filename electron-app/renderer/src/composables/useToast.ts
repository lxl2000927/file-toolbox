import { reactive } from "vue";

type ToastKind = "success" | "error" | "info";

type ToastItem = {
  id: number;
  message: string;
  kind: ToastKind;
};

let _nextId = 0;
const _activeTimers = new Map<number, ReturnType<typeof setTimeout>>();

export const toastState = reactive<{ items: ToastItem[] }>({ items: [] });

export function useToast() {
  function show(message: string, kind: ToastKind = "info") {
    const id = ++_nextId;
    if (toastState.items.length >= 5) {
      const oldest = toastState.items[0];
      if (oldest) {
        const t = _activeTimers.get(oldest.id);
        if (t !== undefined) { clearTimeout(t); _activeTimers.delete(oldest.id); }
      }
      toastState.items.shift();
    }
    toastState.items.push({ id, message, kind });
    const timer = setTimeout(() => {
      const idx = toastState.items.findIndex((t) => t.id === id);
      if (idx >= 0) toastState.items.splice(idx, 1);
      _activeTimers.delete(id);
    }, 3500);
    _activeTimers.set(id, timer);
  }

  function success(message: string) {
    show(message, "success");
  }
  function error(message: string) {
    show(message, "error");
  }
  function info(message: string) {
    show(message, "info");
  }

  return { show, success, error, info };
}
