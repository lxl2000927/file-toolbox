import { reactive } from "vue";

type DialogKind = "info" | "success" | "warning" | "danger";

type DialogOptions = {
  title: string;
  message: string;
  kind?: DialogKind;
  confirmText?: string;
  cancelText?: string;
  showCancel?: boolean;
};

type DialogState = DialogOptions & {
  open: boolean;
  resolver: ((value: boolean) => void) | null;
};

export const dialogState = reactive<DialogState>({
  open: false,
  title: "",
  message: "",
  kind: "info",
  confirmText: "确定",
  cancelText: "取消",
  showCancel: false,
  resolver: null,
});

function openDialog(options: DialogOptions) {
  return new Promise<boolean>((resolve) => {
    if (dialogState.resolver) {
      dialogState.resolver(false);
    }
    dialogState.open = true;
    dialogState.title = options.title;
    dialogState.message = options.message;
    dialogState.kind = options.kind || "info";
    dialogState.confirmText = options.confirmText || "确定";
    dialogState.cancelText = options.cancelText || "取消";
    dialogState.showCancel = Boolean(options.showCancel);
    dialogState.resolver = resolve;
  });
}

export function resolveDialog(value: boolean) {
  const resolver = dialogState.resolver;
  dialogState.open = false;
  dialogState.resolver = null;
  resolver?.(value);
}

export function useAppDialog() {
  return {
    alert: (options: DialogOptions) => openDialog({ ...options, showCancel: false }),
    confirm: (options: DialogOptions) => openDialog({ ...options, showCancel: true }),
  };
}
