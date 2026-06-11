/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}

export type RenamePreviewItem = { old: string; new: string };

export type FileOperation = {
  original_path: string;
  new_path: string;
  operation_type: string;
  timestamp?: string;
  success: boolean;
  error_message?: string;
};

export type UndoResult = {
  restored: { from: string; to: string; operation?: string }[];
  failed: { path?: string; error?: string }[];
};

export type RenameRule = {
  type: string;
  [key: string]: any;
};

export type PdfSplitMode =
  | "by_page_count"
  | "by_file_size"
  | "by_page_range"
  | "by_bookmark";

export type PdfSplitConfig = {
  mode: PdfSplitMode;
  page_count?: number;
  max_size?: number;
  size_unit?: "MB" | "KB";
  page_ranges?: string;
  bookmark_level?: number;
  output_dir?: string;
  file_prefix?: string;
};

export type PlannedOutput = {
  filename: string;
  page_range: [number, number] | null;
};

export type PdfSplitPlan = {
  valid: boolean;
  message: string;
  page_count: number | null;
  output_dir: string;
  outputs: PlannedOutput[];
};

export type PdfSplitPreviewMany = {
  lines: string[];
  plans: Record<string, PdfSplitPlan>;
};

export type ExecuteSummary = {
  total: number;
  successful: number;
  failed: number;
  errors: string[];
  operations: FileOperation[];
  undo_token?: string;
};

export type ScanDetectionMode = "qrcode" | "stamp" | "feature" | "auto";

export type ScanSplitOptions = {
  detection_mode?: ScanDetectionMode;
  dpi?: number;
  nfeatures?: number;
  ratio?: number;
  min_matches?: number;
  ransac_reproj_threshold?: number;
  min_inlier_ratio?: number;
  marker_as_first_page?: boolean;
  exclude_marker_page?: boolean;
  reference_roi?: [number, number, number, number] | null;
  qrcode_text_contains?: string;
  qrcode_no_decode?: boolean;
  qrcode_skip_pages?: number;
  use_roi?: boolean;
  qrcode_use_roi?: boolean;
  qrcode_max_attempts?: number;
  max_segment_pages?: number;
  enable_multithread?: boolean;
  enable_gpu?: boolean;
};

export type TaskNotification =
  | {
      method: "task.progress";
      params: {
        task_id: string;
        phase: string;
        current: number;
        total: number;
        file?: string;
      };
    }
  | {
      method: "task.log";
      params: { task_id: string; message: string };
    }
  | {
      method: "task.complete";
      params:
        | { task_id: string; ok: true; result: any; task_type?: string; elapsed_ms?: number; cancelled?: boolean }
        | { task_id: string; ok: false; error: string; trace?: string; result?: any; task_type?: string; elapsed_ms?: number; cancelled?: boolean };
    };

export interface EngineAPI {
  status: () => Promise<{ status: "starting" | "ready" | "error"; error?: string }>;
  ping: () => Promise<{ pong: boolean }>;
  rename: {
    preview: (files: string[], rules: RenameRule[]) => Promise<RenamePreviewItem[]>;
    execute: (
      files: string[],
      rules: RenameRule[],
      saveMethod?: "copy" | "overwrite",
      outputDir?: string,
    ) => Promise<ExecuteSummary>;
    undo: (undoToken: string) => Promise<UndoResult>;
  };
  pdfSplit: {
    validate: (pdfPath: string) => Promise<{
      valid: boolean;
      message: string;
      page_count: number | null;
    }>;
    preview: (pdfPath: string, config: PdfSplitConfig) => Promise<PdfSplitPlan>;
    previewMany: (pdfPaths: string[], config: PdfSplitConfig) => Promise<PdfSplitPreviewMany>;
    execute: (pdfPaths: string[], config: PdfSplitConfig) => Promise<ExecuteSummary>;
    executeAsync: (
      pdfPaths: string[],
      config: PdfSplitConfig,
      taskId?: string,
    ) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
  };
  scanSplit: {
    previewReference: (referenceImagePath: string, opts?: { nfeatures?: number; roi?: [number, number, number, number] | null }) => Promise<{ ok: boolean; data_url?: string; width?: number; height?: number; error?: string; keypoints_total?: number; keypoints_in_roi?: number }>;
    probePage: (params: { pdfPath: string; referenceImagePath: string; options: Record<string, any>; pageIndex: number; taskId?: string }) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
    scanOnly: (params: { pdfPath: string; referenceImagePath: string; options: Record<string, any>; pageLimit: number; taskId?: string }) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
    executeAsync: (params: {
      pdfPath: string;
      referenceImagePath: string;
      outputDir?: string;
      prefix?: string;
      options: ScanSplitOptions;
      taskId?: string;
    }) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
  };
  cancelTask: (taskId: string) => Promise<{ cancelled: boolean; task_id: string }>;
  history: {
    get: (count?: number, options?: { operationType?: string; currentSession?: boolean; sessionId?: string }) => Promise<{ records: any[]; session_id: string }>;
    clear: () => Promise<{ cleared: boolean; session_id: string }>;
  };
  onNotification: (
    callback: (payload: { method: string; params: any }) => void,
  ) => () => void;
}

export type UpdateCheckResult = {
  ok: boolean;
  error?: string;
  hasUpdate?: boolean;
  current?: string;
  latest?: string | null;
  name?: string;
  body?: string;
  url?: string;
};

export interface ElectronAPI {
  openFileDialog: (options?: {
    filters?: { name: string; extensions: string[] }[];
    multi?: boolean;
    title?: string;
  }) => Promise<string[]>;
  openDirectoryDialog: (options?: { title?: string }) => Promise<string>;
  statPaths: (paths: string[]) => Promise<
    { path: string; isFile: boolean; isDirectory: boolean; size: number }[]
  >;
  readDirFiles: (dirPath: string, exts?: string[]) => Promise<string[]>;
  readFileAsDataUrl: (filePath: string) => Promise<string>;
  checkUpdate: () => Promise<UpdateCheckResult>;
  openExternal: (url: string) => Promise<void>;
  openDataDir: () => Promise<string>;
  restartEngine: () => Promise<void>;
  saveFile: (options: { content: string; defaultName?: string; filters?: { name: string; extensions: string[] }[] }) => Promise<{ saved: boolean; path?: string }>;
  getPathForFile: (file: File) => string;
  getPathsForFiles: (files: File[]) => Promise<string[]>;
}

declare global {
  interface Window {
    engine?: EngineAPI;
    electronAPI?: ElectronAPI;
  }
}
