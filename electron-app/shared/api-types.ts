import type { FileAccessResult, FilePathStat } from "./ipc-types";

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

export type InsertTextRule = {
  type: "insert_text";
  text: string;
  position: "前缀" | "后缀" | "指定位置";
  index?: number | "";
};

export type InsertNumberRule = {
  type: "insert_number";
  prefix?: string;
  start: number | "";
  step: number | "";
  digits: number | "";
  position: "前缀" | "后缀";
};

export type DeleteCharsRule = {
  type: "delete_chars";
  delete_type: "删除指定字符" | "删除前N个字符" | "删除后N个字符" | "delete_patterns";
  chars?: string;
  count?: number | "";
  targets?: string[];
  custom_chars?: string;
};

export type ReplaceTextRule = {
  type: "replace_text";
  find: string;
  replace: string;
  case_sensitive: boolean;
};

export type ChangeExtensionRule = { type: "change_extension"; new_ext: string };
export type UniformNameRule = { type: "uniform_name"; base_name: string };

export type SmartRecognizeRule = {
  type: "smart_recognize";
  mode: "content_title" | "invoice_info";
  position: "覆盖原名" | "首位" | "末位" | "指定位置";
  index?: number | "";
};

export type KeepCharsRule = {
  type: "keep_chars";
  mode: "range" | "specified";
  range?: string;
  direction?: "从右往左" | "从左往右";
  chars?: string;
};

export type RenameRule =
  | InsertTextRule
  | InsertNumberRule
  | DeleteCharsRule
  | ReplaceTextRule
  | ChangeExtensionRule
  | UniformNameRule
  | SmartRecognizeRule
  | KeepCharsRule;

export type RenameRuleType = RenameRule["type"];
export type RenameRuleOf<T extends RenameRuleType> = Extract<RenameRule, { type: T }>;
export type RenameRulePatch<T extends RenameRuleType> = Partial<Omit<RenameRuleOf<T>, "type">>;

export type PdfSplitMode = "by_page_count" | "by_file_size" | "by_page_range" | "by_bookmark";

export type PdfSplitConfig = {
  mode: PdfSplitMode;
  page_count?: number | "";
  max_size?: number | "";
  size_unit?: "MB" | "KB";
  page_ranges?: string;
  bookmark_level?: number | "";
  output_dir?: string;
  file_prefix?: string;
};

export type PlannedOutput = { filename: string; page_range: [number, number] | null };

export type PdfSplitPlan = {
  valid: boolean;
  message: string;
  page_count: number | null;
  output_dir: string;
  outputs: PlannedOutput[];
};

export type PdfSplitPreviewMany = { lines: string[]; plans: Record<string, PdfSplitPlan> };

export type ExecuteSummary = {
  total: number;
  successful: number;
  failed: number;
  errors: string[];
  operations: FileOperation[];
  output_files?: string[];
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

export type EngineNotificationParams = {
  task_id?: string;
  status?: string;
  phase?: string;
  current?: number;
  total?: number;
  file?: string;
  message?: string;
  queued?: boolean;
  position?: number;
  ok?: boolean;
  result?: unknown;
  task_type?: string;
  elapsed_ms?: number;
  cancelled?: boolean;
  error?: string;
  trace?: string;
  remaining_queued?: number;
};

export type EngineNotificationPayload = {
  method: string;
  params: EngineNotificationParams;
};

export interface EngineAPI {
  status: () => Promise<{ status: "starting" | "ready" | "error"; error?: string }>;
  ping: () => Promise<{ pong: boolean }>;
  rename: {
    preview: (files: string[], rules: RenameRule[]) => Promise<RenamePreviewItem[]>;
    execute: (files: string[], rules: RenameRule[], saveMethod?: "copy" | "overwrite", outputDir?: string) => Promise<ExecuteSummary>;
    undo: (undoToken: string) => Promise<UndoResult>;
  };
  pdfSplit: {
    validate: (pdfPath: string) => Promise<{ valid: boolean; message: string; page_count: number | null }>;
    preview: (pdfPath: string, config: PdfSplitConfig) => Promise<PdfSplitPlan>;
    previewMany: (pdfPaths: string[], config: PdfSplitConfig) => Promise<PdfSplitPreviewMany>;
    executeAsync: (pdfPaths: string[], config: PdfSplitConfig, taskId?: string) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
  };
  scanSplit: {
    previewReference: (referenceImagePath: string, opts?: { nfeatures?: number; roi?: [number, number, number, number] | null }) => Promise<{ ok: boolean; data_url?: string; width?: number; height?: number; error?: string; keypoints_total?: number; keypoints_in_roi?: number }>;
    probePage: (params: { pdfPath: string; referenceImagePath: string; options: ScanSplitOptions; pageIndex: number; taskId?: string }) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
    scanOnly: (params: { pdfPath: string; referenceImagePath: string; options: ScanSplitOptions; pageLimit: number; taskId?: string }) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
    executeAsync: (params: { pdfPath: string; referenceImagePath: string; outputDir?: string; prefix?: string; options: ScanSplitOptions; taskId?: string }) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
  };
  cancelTask: (taskId: string) => Promise<{ cancelled: boolean; task_id: string }>;
  history: {
    get: (count?: number, options?: { operationType?: string; currentSession?: boolean; sessionId?: string }) => Promise<{ records: unknown[]; session_id: string }>;
    clear: () => Promise<{ cleared: boolean; session_id: string; error?: string }>;
  };
  onNotification: (callback: (payload: EngineNotificationPayload) => void) => () => void;
}

export type AppUpdateState = "idle" | "checking" | "available" | "downloading" | "downloaded" | "installing" | "up-to-date" | "unsupported" | "error";
export type AppPackageType = "development" | "installer" | "portable" | "archive";

export type AppUpdateStatus = {
  state: AppUpdateState;
  supported: boolean;
  packageType: AppPackageType;
  portable: boolean;
  current: string;
  error?: string;
  latest?: string | null;
  name?: string;
  body?: string;
  url?: string;
  percent?: number;
  transferred?: number;
  total?: number;
  bytesPerSecond?: number;
};

export interface ElectronAPI {
  openFileDialog: (options?: { filters?: { name: string; extensions: string[] }[]; multi?: boolean; title?: string }) => Promise<string[]>;
  openDirectoryDialog: (options?: { title?: string }) => Promise<string>;
  statPaths: (paths: string[]) => Promise<FileAccessResult<FilePathStat>[]>;
  readDirFiles: (dirPath: string, exts?: string[]) => Promise<FileAccessResult<string[]>>;
  getFilePreviewUrl: (filePath: string) => Promise<FileAccessResult<string>>;
  update: {
    getStatus: () => Promise<AppUpdateStatus>;
    check: () => Promise<AppUpdateStatus>;
    download: () => Promise<AppUpdateStatus>;
    install: () => Promise<{ accepted: boolean; status: AppUpdateStatus }>;
    onStatus: (callback: (status: AppUpdateStatus) => void) => () => void;
  };
  openExternal: (url: string) => Promise<void>;
  openDataDir: () => Promise<string>;
  restartEngine: () => Promise<void>;
  saveFile: (options: { content: string; defaultName?: string; filters?: { name: string; extensions: string[] }[] }) => Promise<{ saved: boolean; path?: string }>;
  getPathForFile: (file: File) => string;
  getPathsForFiles: (files: File[]) => Promise<string[]>;
  onFileDrop?: (callback: (paths: string[]) => void) => () => void;
}
