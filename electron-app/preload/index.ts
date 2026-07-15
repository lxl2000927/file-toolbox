import { contextBridge, ipcRenderer, IpcRendererEvent, webUtils } from "electron";
import type {
  AppUpdateStatus,
  ElectronAPI,
  EngineAPI,
  EngineNotificationPayload,
  PdfSplitConfig,
  RenameRule,
  ScanSplitOptions,
} from "../shared/api-types";

type DialogOpenFilesOptions = {
  filters?: { name: string; extensions: string[] }[];
  multi?: boolean;
  title?: string;
};

const engineApi: EngineAPI = {
  status: () => ipcRenderer.invoke("engine:status"),
  ping: () => ipcRenderer.invoke("engine:call", "ping", {}),

  rename: {
    preview: (files: string[], rules: RenameRule[]) =>
      ipcRenderer.invoke("engine:call", "rename.preview", { files, rules }),
    execute: (
      files: string[],
      rules: RenameRule[],
      saveMethod: "copy" | "overwrite" = "copy",
      outputDir = "",
    ) =>
      ipcRenderer.invoke("engine:call", "rename.execute", {
        files,
        rules,
        save_method: saveMethod,
        output_dir: outputDir,
      }),
    undo: (undoToken: string) =>
      ipcRenderer.invoke("engine:call", "rename.undo", { undo_token: undoToken }),
  },

  pdfSplit: {
    validate: (pdfPath: string) =>
      ipcRenderer.invoke("engine:call", "pdf_split.validate", { pdf_path: pdfPath }),
    preview: (pdfPath: string, config: PdfSplitConfig) =>
      ipcRenderer.invoke("engine:call", "pdf_split.preview", { pdf_path: pdfPath, config }),
    previewMany: (pdfPaths: string[], config: PdfSplitConfig) =>
      ipcRenderer.invoke("engine:call", "pdf_split.preview_many", { pdf_paths: pdfPaths, config }),
    // [Bug #32] pdf_split.execute 同步路由已下线（server.py ROUTES 已移除），
    // 仅保留 executeAsync 走后台线程 + 取消令牌。
    executeAsync: (pdfPaths: string[], config: PdfSplitConfig, taskId?: string) =>
      ipcRenderer.invoke("engine:call", "pdf_split.execute_async", {
        pdf_paths: pdfPaths,
        config,
        task_id: taskId,
      }),
  },

  scanSplit: {
    previewReference: (referenceImagePath: string, opts?: { nfeatures?: number; roi?: [number, number, number, number] | null }): Promise<{ ok: boolean; data_url?: string; width?: number; height?: number; error?: string; keypoints_total?: number; keypoints_in_roi?: number }> =>
      ipcRenderer.invoke("engine:call", "scan_split.preview_reference", { reference_image_path: referenceImagePath, nfeatures: opts?.nfeatures, roi: opts?.roi }),
    probePage: (params: { pdfPath: string; referenceImagePath: string; options: ScanSplitOptions; pageIndex: number; taskId?: string }) =>
      ipcRenderer.invoke("engine:call", "scan_split.probe_page", { pdf_path: params.pdfPath, reference_image_path: params.referenceImagePath, options: params.options, page_index: params.pageIndex, task_id: params.taskId }),
    scanOnly: (params: { pdfPath: string; referenceImagePath: string; options: ScanSplitOptions; pageLimit: number; taskId?: string }) =>
      ipcRenderer.invoke("engine:call", "scan_split.scan_only", { pdf_path: params.pdfPath, reference_image_path: params.referenceImagePath, options: params.options, page_limit: params.pageLimit, task_id: params.taskId }),
    executeAsync: (params: {
      pdfPath: string;
      referenceImagePath: string;
      outputDir?: string;
      prefix?: string;
      options: ScanSplitOptions;
      taskId?: string;
    }) =>
      ipcRenderer.invoke("engine:call", "scan_split.execute_async", {
        pdf_path: params.pdfPath,
        reference_image_path: params.referenceImagePath,
        output_dir: params.outputDir ?? "",
        prefix: params.prefix ?? "",
        options: params.options,
        task_id: params.taskId,
      }),
  },

  cancelTask: (taskId: string) =>
    ipcRenderer.invoke("engine:call", "task.cancel", { task_id: taskId }),

  history: {
    get: (count?: number, options: { operationType?: string; currentSession?: boolean; sessionId?: string } = {}) =>
      ipcRenderer.invoke("engine:call", "history.get", {
        count: count ?? 100,
        operation_type: options.operationType,
        current_session: options.currentSession ?? true,
        session_id: options.sessionId,
      }),
    clear: () => ipcRenderer.invoke("engine:call", "history.clear", {}),
  },

  onNotification: (
    callback: (payload: EngineNotificationPayload) => void,
  ): (() => void) => {
    const listener = (_event: IpcRendererEvent, payload: EngineNotificationPayload) => callback(payload);
    ipcRenderer.on("engine:notification", listener);
    return () => ipcRenderer.removeListener("engine:notification", listener);
  },
};

contextBridge.exposeInMainWorld("engine", engineApi);

const electronApi: ElectronAPI = {
  openFileDialog: (options?: DialogOpenFilesOptions) =>
    ipcRenderer.invoke("dialog:openFiles", options),
  openDirectoryDialog: (options?: { title?: string }) =>
    ipcRenderer.invoke("dialog:openDirectory", options),
  statPaths: (paths: string[]) => ipcRenderer.invoke("fs:statPaths", paths),
  readDirFiles: (dirPath: string, exts?: string[]) =>
    ipcRenderer.invoke("fs:readDirFiles", dirPath, exts),
  getFilePreviewUrl: (filePath: string) =>
    ipcRenderer.invoke("fs:getFilePreviewUrl", filePath),
  update: {
    getStatus: () => ipcRenderer.invoke("app:update:getStatus"),
    check: () => ipcRenderer.invoke("app:update:check"),
    download: () => ipcRenderer.invoke("app:update:download"),
    install: () => ipcRenderer.invoke("app:update:install"),
    onStatus: (callback: (status: AppUpdateStatus) => void): (() => void) => {
      const listener = (_event: IpcRendererEvent, status: AppUpdateStatus) => callback(status);
      ipcRenderer.on("app:update-status", listener);
      return () => ipcRenderer.removeListener("app:update-status", listener);
    },
  },
  openExternal: (url: string) => ipcRenderer.invoke("app:openExternal", url),
  openDataDir: () => ipcRenderer.invoke("app:openDataDir"),
  restartEngine: () => ipcRenderer.invoke("engine:restart"),
  saveFile: (options: { content: string; defaultName?: string; filters?: { name: string; extensions: string[] }[] }) =>
    ipcRenderer.invoke("dialog:saveFile", options),
  getPathForFile: (file: File) => webUtils.getPathForFile(file),
  getPathsForFiles: async (files: File[]) => {
    const paths = Array.from(files, (file) => webUtils.getPathForFile(file));
    const validPaths = paths.filter((path): path is string => Boolean(path));
    const authorizedPaths: string[] = await ipcRenderer.invoke("fs:authorizePaths", validPaths);
    const authorizedSet = new Set(authorizedPaths);
    return paths.map((path) => (path && authorizedSet.has(path) ? path : ""));
  },
};

contextBridge.exposeInMainWorld("electronAPI", electronApi);
