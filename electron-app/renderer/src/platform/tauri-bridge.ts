import type {
  AppUpdateStatus,
  ElectronAPI,
  EngineAPI,
  EngineNotificationPayload,
  ExecuteSummary,
  RenamePreviewItem,
  RenameRule,
  UndoResult,
} from "../../../shared/api-types";
import type { FileAccessResult, FilePathStat } from "../../../shared/ipc-types";
import {
  electronCapabilities,
  tauriPhaseOneCapabilities,
} from "./runtime";

type InvokeFn = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type ListenFn = <T>(
  event: string,
  handler: (event: { payload: T }) => void,
) => Promise<() => void>;

type EngineStatusResponse = {
  status: "starting" | "ready" | "error";
  error?: string;
};

const notMigrated = {
  code: "NOT_MIGRATED",
  message: "该功能尚未迁移到 Tauri",
} as const;

const unsupportedUpdateStatus: AppUpdateStatus = {
  state: "unsupported",
  supported: false,
  packageType: "development",
  portable: false,
  current: "2.5.0",
};

export function createTauriBridge(deps: {
  invoke: InvokeFn;
  listen: ListenFn;
}): { engine: EngineAPI; electron: ElectronAPI } {
  const { invoke, listen } = deps;

  const engineCall = <T>(method: string, params: Record<string, unknown>): Promise<T> =>
    invoke<T>("engine_call", { method, params });

  const notMigratedError = (): Promise<never> =>
    Promise.reject({ code: notMigrated.code, message: notMigrated.message });

  function subscribe<T>(event: string, callback: (payload: T) => void): () => void {
    let active = true;
    let unlisten: (() => void) | undefined;
    void listen<T>(event, (event) => {
      if (active) {
        callback(event.payload);
      }
    })
      .then((dispose) => {
        if (active) {
          unlisten = dispose;
        } else {
          dispose();
        }
      })
      .catch(() => {
        // A failed subscription leaves the callback inactive; the bridge still works.
      });

    return () => {
      if (!active) {
        return;
      }
      active = false;
      unlisten?.();
    };
  }

  const engine: EngineAPI = {
    status: () => invoke<EngineStatusResponse>("engine_status"),
    ping: () => engineCall<{ pong: boolean }>("ping", {}),
    rename: {
      preview: (files: string[], rules: RenameRule[]) =>
        engineCall<RenamePreviewItem[]>("rename.preview", { files, rules }),
      execute: (files, rules, saveMethod = "copy", outputDir = "") =>
        engineCall<ExecuteSummary>("rename.execute", {
          files,
          rules,
          save_method: saveMethod,
          output_dir: outputDir,
        }),
      undo: (undoToken: string) =>
        engineCall<UndoResult>("rename.undo", { undo_token: undoToken }),
    },
    pdfSplit: {
      validate: () => notMigratedError(),
      preview: () => notMigratedError(),
      previewMany: () => notMigratedError(),
      executeAsync: () => notMigratedError(),
    },
    scanSplit: {
      previewReference: () => notMigratedError(),
      probePage: () => notMigratedError(),
      scanOnly: () => notMigratedError(),
      executeAsync: () => notMigratedError(),
    },
    cancelTask: () => notMigratedError(),
    history: {
      get: async () => ({ records: [], session_id: "" }),
      clear: async () => ({
        cleared: false,
        session_id: "",
        error: notMigrated.message,
      }),
    },
    onNotification: (callback: (payload: EngineNotificationPayload) => void) =>
      subscribe<EngineNotificationPayload>("engine-notification", callback),
  };

  const electron: ElectronAPI = {
    openFileDialog: (options) => invoke<string[]>("open_files", { options }),
    openDirectoryDialog: (options) =>
      invoke<string>("open_directory", { title: options?.title }),
    statPaths: (paths) =>
      invoke<FileAccessResult<FilePathStat>[]>("stat_paths", { paths }),
    readDirFiles: async (dirPath) => ({
      ok: false,
      error: {
        code: "unsupported_type",
        message: notMigrated.message,
        path: dirPath,
      },
    }),
    getFilePreviewUrl: async (filePath) => ({
      ok: false,
      error: {
        code: "unsupported_type",
        message: notMigrated.message,
        path: filePath,
      },
    }),
    update: {
      getStatus: async () => unsupportedUpdateStatus,
      check: async () => unsupportedUpdateStatus,
      download: async () => unsupportedUpdateStatus,
      install: async () => ({ accepted: false, status: unsupportedUpdateStatus }),
      onStatus: () => () => {},
    },
    openExternal: () => notMigratedError(),
    openDataDir: () => notMigratedError(),
    restartEngine: () => invoke<void>("engine_restart"),
    saveFile: () => notMigratedError(),
    getPathForFile: () => "",
    getPathsForFiles: async () => [],
    onFileDrop: (callback: (paths: string[]) => void) =>
      subscribe<string[]>("desktop-file-drop", callback),
  };

  return { engine, electron };
}

export async function installDesktopBridge(): Promise<void> {
  const { isTauri, invoke } = await import("@tauri-apps/api/core");
  if (!isTauri()) {
    window.desktopRuntime = { kind: "electron", capabilities: electronCapabilities };
    return;
  }
  const { listen } = await import("@tauri-apps/api/event");
  const bridge = createTauriBridge({
    invoke: invoke as InvokeFn,
    listen: listen as ListenFn,
  });
  window.engine = bridge.engine;
  window.electronAPI = bridge.electron;
  window.desktopRuntime = { kind: "tauri", capabilities: tauriPhaseOneCapabilities };
}
