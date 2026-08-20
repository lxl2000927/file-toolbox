import { app, BrowserWindow, dialog, ipcMain, net, protocol, session, shell } from "electron";
import { autoUpdater, type ProgressInfo, type UpdateInfo } from "electron-updater";
import { dirname, extname, isAbsolute, join, relative, resolve } from "path";
import { existsSync, promises as fsp } from "fs";
import https from "https";
import { randomBytes } from "crypto";
import { execFileSync } from "child_process";
import { pathToFileURL } from "url";
import { PythonBridge } from "./python-bridge";
import type { FileAccessError, FileAccessErrorCode, FileAccessResult, FilePathStat } from "../shared/ipc-types";

// 进程级未捕获异常处理，避免崩溃时无日志
process.on("unhandledRejection", (reason) => {
  console.error("[main] Unhandled Rejection:", reason);
});
process.on("uncaughtException", (err) => {
  console.error("[main] Uncaught Exception:", err);
  app.exit(1);
});

// 单实例锁：第二个实例启动时聚焦已有窗口
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const windows = BrowserWindow.getAllWindows();
    if (windows.length > 0) {
      const win = windows[0];
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
}

const GITHUB_REPO = "LXL2000927/file-toolbox";
const GITHUB_RELEASES = `https://github.com/${GITHUB_REPO}/releases`;
const GITHUB_LATEST_RELEASE = `${GITHUB_RELEASES}/latest`;
const NSIS_INSTALL_MARKER = ".file-toolbox-installed";
const FILE_PREVIEW_SCHEME = "file-toolbox-preview";
const UPDATE_CHECK_TIMEOUT_MS = 15_000;

protocol.registerSchemesAsPrivileged([
  {
    scheme: FILE_PREVIEW_SCHEME,
    privileges: { secure: true, standard: true, supportFetchAPI: true, stream: true },
  },
]);

let mainWindow: BrowserWindow | null = null;
let bridge: PythonBridge | null = null;
let engineStatus: "starting" | "ready" | "error" = "starting";
let engineError = "";
let ipcReady = false;
let startEnginePromise: Promise<void> | null = null;

const isDev = !app.isPackaged;
const PROJECT_ROOT = isDev ? join(__dirname, "../../..") : process.resourcesPath;
const MIN_WINDOW_WIDTH = 1120;
const MIN_WINDOW_HEIGHT = 720;
const DEV_RENDERER_URL = "http://localhost:5173";
const MAX_REFERENCE_IMAGE_FILE_SIZE = 15 * 1024 * 1024;  // 原始参考图片文件大小上限（≈15 MiB）
const MAX_INPUT_PDF_FILE_SIZE = 1 * 1024 * 1024 * 1024;
const MAX_GENERIC_INPUT_FILE_SIZE = 500 * 1024 * 1024;
type AuthorizedPath = { kind: "file" | "directory" };
const authorizedPaths = new Map<string, AuthorizedPath>();
const MAX_AUTHORIZED_PATHS = 12000;
const ENGINE_AUTH_TOKEN = randomBytes(32).toString("hex");
const ENGINE_METHODS = new Set(["ping", "rename.preview", "rename.execute", "rename.undo", "pdf_split.validate", "pdf_split.preview", "pdf_split.preview_many", "pdf_split.execute_async", "scan_split.execute_async", "scan_split.preview_reference", "scan_split.probe_page", "scan_split.scan_only", "task.cancel", "history.get", "history.clear"]);
const MAX_SAVE_FILE_CONTENT_SIZE = 20 * 1024 * 1024;
const REFERENCE_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "bmp", "tiff", "tif", "webp", "gif"]);

class FileAccessFailure extends Error {
  constructor(
    readonly code: FileAccessErrorCode,
    message: string,
    readonly pathValue?: string,
  ) {
    super(message);
  }
}

function fileAccessError(error: unknown, pathValue?: string): FileAccessError {
  if (error instanceof FileAccessFailure) {
    return { code: error.code, message: error.message, path: error.pathValue || pathValue };
  }
  const code = error && typeof error === "object" && "code" in error ? String(error.code || "") : "";
  if (code === "ENOENT") return { code: "not_found", message: "路径不存在", path: pathValue };
  if (code === "EACCES" || code === "EPERM") return { code: "permission_denied", message: "没有权限访问该路径", path: pathValue };
  if (code === "ENOTDIR") return { code: "not_directory", message: "路径不是目录", path: pathValue };
  return {
    code: "io_error",
    message: error instanceof Error ? error.message : "文件访问失败",
    path: pathValue,
  };
}

function failedFileAccess<T>(error: unknown, pathValue?: string): FileAccessResult<T> {
  return { ok: false, error: fileAccessError(error, pathValue) };
}

type AppUpdateState = "idle" | "checking" | "available" | "downloading" | "downloaded" | "installing" | "up-to-date" | "unsupported" | "error";
type AppPackageType = "development" | "installer" | "portable" | "archive";

type AppUpdateStatus = {
  state: AppUpdateState;
  supported: boolean;
  packageType: AppPackageType;
  portable: boolean;
  current: string;
  latest?: string | null;
  name?: string;
  body?: string;
  url?: string;
  error?: string;
  percent?: number;
  transferred?: number;
  total?: number;
  bytesPerSecond?: number;
};

let updaterConfigured = false;
let updateCheckPromise: Promise<AppUpdateStatus> | null = null;
let updateDownloadPromise: Promise<AppUpdateStatus> | null = null;
let updateStatus: AppUpdateStatus = {
  state: "idle",
  supported: false,
  packageType: "development",
  portable: false,
  current: app.getVersion(),
};

function isAllowedAppUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    if (isDev) return url.origin === new URL(DEV_RENDERER_URL).origin;
    return url.protocol === "file:";
  } catch {
    return false;
  }
}

function isMainSender(event: Electron.IpcMainInvokeEvent): boolean {
  return Boolean(mainWindow && event.sender === mainWindow.webContents && !mainWindow.isDestroyed());
}

async function canonicalPath(pathValue: string): Promise<string> {
  return resolve(await fsp.realpath(pathValue));
}

function rememberAuthorizedPath(pathValue: string, entry: AuthorizedPath): void {
  authorizedPaths.delete(pathValue);
  authorizedPaths.set(pathValue, entry);
  while (authorizedPaths.size > MAX_AUTHORIZED_PATHS) {
    const oldest = authorizedPaths.keys().next().value;
    if (oldest === undefined) break;
    authorizedPaths.delete(oldest);
  }
}

async function authorizePath(pathValue: string): Promise<void> {
  const resolved = resolve(pathValue);
  try {
    const canonical = await canonicalPath(pathValue);
    const stat = await fsp.stat(canonical);
    rememberAuthorizedPath(canonical, { kind: stat.isDirectory() ? "directory" : "file" });
  } catch {
    rememberAuthorizedPath(resolved, { kind: "file" });
  }
}

async function isAuthorizedPath(pathValue: string): Promise<boolean> {
  let actual: string;
  try {
    actual = await canonicalPath(pathValue);
  } catch {
    actual = resolve(pathValue);
  }
  for (const [root, entry] of authorizedPaths) {
    if (actual.toLowerCase() === root.toLowerCase()) return true;
    if (entry.kind !== "directory") continue;
    const childPath = relative(root, actual);
    if (childPath && !childPath.startsWith("..") && !isAbsolute(childPath)) return true;
  }
  return false;
}

async function validateInputFile(pathValue: string, allowedExts: Set<string>, maxSize: number): Promise<void> {
  const stat = await fsp.stat(pathValue);
  if (!stat.isFile()) throw new FileAccessFailure("not_file", "路径不是文件", pathValue);
  if (stat.size > maxSize) throw new FileAccessFailure("too_large", "文件超过允许的大小上限", pathValue);
  const ext = extname(pathValue).slice(1).toLowerCase();
  if (allowedExts.size && !allowedExts.has(ext)) throw new FileAccessFailure("unsupported_type", "不支持的文件类型", pathValue);
}

async function validateEngineParamPaths(method: string, params: any): Promise<void> {
  const pathParams: Record<string, (value: any) => unknown[]> = {
    "rename.preview": (value) => [value?.files],
    "rename.execute": (value) => [value?.files, value?.output_dir],
    "pdf_split.validate": (value) => [value?.pdf_path],
    "pdf_split.preview": (value) => [value?.pdf_path, value?.config?.output_dir],
    "pdf_split.preview_many": (value) => [value?.pdf_paths, value?.config?.output_dir],
    "pdf_split.execute_async": (value) => [value?.pdf_paths, value?.config?.output_dir],
    "scan_split.preview_reference": (value) => [value?.reference_image_path],
    "scan_split.probe_page": (value) => [value?.pdf_path, value?.reference_image_path],
    "scan_split.scan_only": (value) => [value?.pdf_path, value?.reference_image_path],
    "scan_split.execute_async": (value) => [value?.pdf_path, value?.reference_image_path, value?.output_dir],
  };
  const extract = pathParams[method];
  const paths = (extract ? extract(params) : [])
    .flat(Infinity)
    .filter((value): value is string => typeof value === "string" && Boolean(value.trim()));
  for (const pathValue of paths) if (!(await isAuthorizedPath(pathValue))) throw new Error(`Unauthorized path: ${pathValue}`);
  if (method.startsWith("pdf_split.")) {
    const pdfPaths = [params?.pdf_path, params?.pdf_path_from, params?.pdf_path_to, ...(Array.isArray(params?.pdf_paths) ? params.pdf_paths : [])].filter(Boolean);
    for (const pathValue of pdfPaths) await validateInputFile(String(pathValue), new Set(["pdf"]), MAX_INPUT_PDF_FILE_SIZE);
  }
  if (method.startsWith("scan_split.")) {
    const imageExts = new Set(["png", "jpg", "jpeg", "bmp", "tiff", "tif", "webp", "gif"]);
    for (const pathValue of [params?.pdf_path, params?.input_path, params?.reference_image, params?.reference_image_path].filter(Boolean)) {
      const ext = extname(String(pathValue)).slice(1).toLowerCase();
      const isImage = imageExts.has(ext);
      await validateInputFile(String(pathValue), isImage ? imageExts : new Set(["pdf"]), isImage ? MAX_REFERENCE_IMAGE_FILE_SIZE : MAX_INPUT_PDF_FILE_SIZE);
    }
  }
  if (method.startsWith("rename.")) for (const pathValue of (Array.isArray(params?.files) ? params.files : [])) await validateInputFile(String(pathValue), new Set(), MAX_GENERIC_INPUT_FILE_SIZE);
}

function validateExternalUrl(rawUrl: string): string {
  const url = new URL(String(rawUrl || ""));
  if (url.protocol !== "https:") throw new Error("仅允许打开 HTTPS 链接");
  if (!new Set(["github.com", "www.github.com"]).has(url.hostname.toLowerCase())) {
    throw new Error("不允许打开该外部链接");
  }
  return url.toString();
}

function normalizeVersionTag(tagName: unknown): string | null {
  const latest = String(tagName || "").replace(/^v/i, "").trim();
  if (!latest) return null;
  if (!/^[0-9A-Za-z.+-]+$/.test(latest)) return null;
  return latest;
}

function releasePageUrl(version: unknown): string {
  const normalized = normalizeVersionTag(version);
  return normalized ? `${GITHUB_RELEASES}/tag/v${encodeURIComponent(normalized)}` : GITHUB_RELEASES;
}

function compareVersions(candidate: string, current: string): number {
  const parse = (value: string): number[] => {
    const normalized = normalizeVersionTag(value);
    if (!normalized || !/^\d+(?:\.\d+){1,3}$/.test(normalized)) {
      throw new Error(`版本号格式异常: ${value}`);
    }
    return normalized.split(".").map(Number);
  };
  const left = parse(candidate);
  const right = parse(current);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference !== 0) return Math.sign(difference);
  }
  return 0;
}

function latestReleaseVersionFromUrl(rawUrl: string): string {
  const url = new URL(rawUrl);
  if (!new Set(["github.com", "www.github.com"]).has(url.hostname.toLowerCase())) {
    throw new Error("最新版本地址不在允许范围内");
  }
  const parts = url.pathname.split("/").filter(Boolean);
  const repository = `${parts[0] || ""}/${parts[1] || ""}`;
  if (repository.toLowerCase() !== GITHUB_REPO.toLowerCase() || parts[2] !== "releases" || parts[3] !== "tag") {
    throw new Error("无法从发布地址识别最新版本");
  }
  const latest = normalizeVersionTag(decodeURIComponent(parts[4] || ""));
  if (!latest) throw new Error("发布页版本号格式异常");
  return latest;
}

async function checkLatestReleasePage(): Promise<{ latest: string; url: string }> {
  return new Promise((resolvePromise, rejectPromise) => {
    let settled = false;
    const finish = (error?: Error, value?: { latest: string; url: string }) => {
      if (settled) return;
      settled = true;
      if (error) rejectPromise(error);
      else if (value) resolvePromise(value);
    };
    const request = https.request(GITHUB_LATEST_RELEASE, {
      method: "HEAD",
      headers: {
        Accept: "text/html",
        "User-Agent": "FileToolbox-UpdateChecker",
      },
    }, (response) => {
      const status = response.statusCode || 0;
      const locationHeader = response.headers.location;
      const location = Array.isArray(locationHeader) ? locationHeader[0] : locationHeader;
      response.resume();
      if (status < 300 || status >= 400 || !location) {
        finish(new Error(`GitHub 最新版本响应异常: HTTP ${status}`));
        return;
      }
      try {
        const releaseUrl = new URL(location, GITHUB_LATEST_RELEASE).toString();
        const latest = latestReleaseVersionFromUrl(releaseUrl);
        finish(undefined, { latest, url: releasePageUrl(latest) });
      } catch (error) {
        finish(error instanceof Error ? error : new Error(String(error)));
      }
    });
    request.setTimeout(UPDATE_CHECK_TIMEOUT_MS, () => {
      request.destroy(new Error("检查更新超时，请检查网络后重试"));
    });
    request.on("error", (error) => finish(error));
    request.end();
  });
}

function findPython(): string {
  if (!isDev) return "python";
  const candidates = [
    join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),
    join(PROJECT_ROOT, "venv", "Scripts", "python.exe"),
  ];
  for (const c of candidates) {
    if (existsSync(c) && canRunPython(c)) {
      console.log(`[main] 检测到 venv: ${c}`);
      return c;
    }
  }
  console.log("[main] 未找到 venv，使用系统 python");
  return "python";
}

function canRunPython(exePath: string): boolean {
  try {
    execFileSync(exePath, ["--version"], { stdio: "ignore", timeout: 3000 });
    return true;
  } catch {
    console.warn(`[main] 跳过不可用 Python: ${exePath}`);
    return false;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    show: false,
    backgroundColor: "#f7f8fa",
  });

  mainWindow.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT);
  mainWindow.on("closed", () => {
    mainWindow = null;
    authorizedPaths.clear();
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const safeUrl = validateExternalUrl(url);
      shell.openExternal(safeUrl);
    } catch {
      // Deny by default.
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isAllowedAppUrl(url)) event.preventDefault();
  });

  mainWindow.webContents.on("will-frame-navigate", (event) => {
    if (!isAllowedAppUrl(event.url)) event.preventDefault();
  });

  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription) => {
    console.error(`[main] 页面加载失败: code=${errorCode}, ${errorDescription}`);
  });

  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    console.error(`[main] 渲染进程崩溃: ${details.reason}`);
  });

  if (isDev) {
    mainWindow.loadURL(DEV_RENDERER_URL).catch((err) => {
      console.error("[main] loadURL 失败:", err);
    });
  } else {
    mainWindow.loadFile(join(__dirname, "../renderer/index.html")).catch((err) => {
      console.error("[main] loadFile 失败:", err);
    });
  }
}

function setupIPC() {
  if (ipcReady) return;
  ipcReady = true;

  ipcMain.handle("engine:call", async (event, method: string, params: any) => {
    if (!isMainSender(event)) throw new Error("Invalid IPC sender");
    if (!ENGINE_METHODS.has(String(method || ""))) throw new Error("Engine method is not allowed");
    if (!bridge || engineStatus !== "ready") {
      throw new Error(engineStatus === "error" ? `Python 引擎启动失败：${engineError || "未知错误"}` : "Python 引擎启动中，请稍候");
    }
    await validateEngineParamPaths(String(method || ""), params);
    return bridge.call(method, params);
  });

  ipcMain.handle("engine:status", async (event) => {
    if (!isMainSender(event)) throw new Error("Invalid IPC sender");
    return { status: engineStatus, error: engineError };
  });

  ipcMain.handle("fs:authorizePaths", async (event, paths: string[]) => {
    if (!isMainSender(event) || !Array.isArray(paths)) return [];
    const authorized: string[] = [];
    for (const pathValue of paths) {
      if (typeof pathValue !== "string" || !pathValue) continue;
      await authorizePath(pathValue);
      authorized.push(pathValue);
    }
    return authorized;
  });

  ipcMain.handle(
    "dialog:openFiles",
    async (event, options?: { filters?: Electron.FileFilter[]; multi?: boolean; title?: string }) => {
      if (!isMainSender(event)) return [];
      if (!mainWindow) return [];
      const properties: Array<"openFile" | "multiSelections"> = ["openFile"];
      if (options?.multi !== false) properties.push("multiSelections");
      const result = await dialog.showOpenDialog(mainWindow, {
        properties,
        title: options?.title,
        filters: options?.filters ?? [{ name: "所有文件", extensions: ["*"] }],
      });
      if (result.canceled) return [];
      for (const pathValue of result.filePaths) await authorizePath(pathValue);
      return result.filePaths;
    },
  );

  ipcMain.handle("dialog:openDirectory", async (event, options?: { title?: string }) => {
    if (!isMainSender(event)) return "";
    if (!mainWindow) return "";
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"],
      title: options?.title,
    });
    if (result.canceled || !result.filePaths[0]) return "";
    await authorizePath(result.filePaths[0]);
    return result.filePaths[0];
  });

  ipcMain.handle("fs:statPaths", async (event, paths: string[]) => {
    if (!isMainSender(event)) return [failedFileAccess<FilePathStat>(new FileAccessFailure("invalid_sender", "IPC 调用来源无效"))];
    if (!Array.isArray(paths)) return [failedFileAccess<FilePathStat>(new FileAccessFailure("invalid_argument", "路径列表格式无效"))];
    const out: FileAccessResult<FilePathStat>[] = [];
    for (const p of paths || []) {
      try {
        if (typeof p !== "string" || !p) throw new FileAccessFailure("invalid_argument", "路径不能为空");
        if (!(await isAuthorizedPath(p))) throw new FileAccessFailure("unauthorized", "路径尚未授权", p);
        const s = await fsp.stat(p);
        out.push({ ok: true, value: { path: p, isFile: s.isFile(), isDirectory: s.isDirectory(), size: s.size } });
      } catch (error) {
        out.push(failedFileAccess<FilePathStat>(error, typeof p === "string" ? p : undefined));
      }
    }
    return out;
  });

  ipcMain.handle("fs:readDirFiles", async (event, dirPath: string, exts?: string[]) => {
    if (!isMainSender(event)) return failedFileAccess<string[]>(new FileAccessFailure("invalid_sender", "IPC 调用来源无效"));
    try {
      if (typeof dirPath !== "string" || !dirPath) throw new FileAccessFailure("invalid_argument", "目录路径不能为空");
      if (!(await isAuthorizedPath(dirPath))) throw new FileAccessFailure("unauthorized", "目录尚未授权", dirPath);
      const stat = await fsp.stat(dirPath);
      if (!stat.isDirectory()) throw new FileAccessFailure("not_directory", "路径不是目录", dirPath);
      const items = await fsp.readdir(dirPath, { withFileTypes: true });
      const results: string[] = [];
      for (const it of items) {
        if (!it.isFile()) continue;
        if (exts && exts.length) {
          const lower = it.name.toLowerCase();
          if (!exts.some((e) => lower.endsWith(e.toLowerCase()))) continue;
        }
        results.push(join(dirPath, it.name));
      }
      return { ok: true, value: results } satisfies FileAccessResult<string[]>;
    } catch (error) {
      return failedFileAccess<string[]>(error, dirPath);
    }
  });

  ipcMain.handle("fs:getFilePreviewUrl", async (event, filePath: string) => {
    if (!isMainSender(event)) return failedFileAccess<string>(new FileAccessFailure("invalid_sender", "IPC 调用来源无效"));
    try {
      if (typeof filePath !== "string" || !filePath) throw new FileAccessFailure("invalid_argument", "文件路径不能为空");
      const canonical = await validatedReferenceImagePath(filePath);
      const previewUrl = new URL(`${FILE_PREVIEW_SCHEME}://local/file`);
      previewUrl.searchParams.set("path", canonical);
      return { ok: true, value: previewUrl.toString() } satisfies FileAccessResult<string>;
    } catch (error) {
      return failedFileAccess<string>(error, filePath);
    }
  });

}

function attachBridgeNotifications(targetBridge: PythonBridge) {
  targetBridge.addNotificationHandler((method, params) => {
    if (targetBridge !== bridge) return;
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.webContents.send("engine:notification", { method, params });
  });
  targetBridge.addExitHandler((err) => {
    if (targetBridge !== bridge) return;
    if (engineStatus !== "ready") return;
    engineStatus = "error";
    engineError = err.message;
    publishEngineStatus();
  });
}

async function validatedReferenceImagePath(pathValue: string): Promise<string> {
  if (!(await isAuthorizedPath(pathValue))) throw new FileAccessFailure("unauthorized", "路径尚未授权", pathValue);
  const canonical = await canonicalPath(pathValue);
  await validateInputFile(canonical, REFERENCE_IMAGE_EXTENSIONS, MAX_REFERENCE_IMAGE_FILE_SIZE);
  return canonical;
}

function setupFilePreviewProtocol(): void {
  protocol.handle(FILE_PREVIEW_SCHEME, async (request) => {
    try {
      const requestUrl = new URL(request.url);
      const filePath = requestUrl.searchParams.get("path") || "";
      const canonical = await validatedReferenceImagePath(filePath);
      return net.fetch(pathToFileURL(canonical).toString());
    } catch {
      return new Response(null, { status: 404 });
    }
  });
}

function publishEngineStatus(): void {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send("engine:notification", {
    method: "engine.status",
    params: { status: engineStatus, error: engineError },
  });
}

async function startEngine(): Promise<void> {
  if (startEnginePromise) return startEnginePromise;

  const attempt = (async () => {
    engineStatus = "starting";
    engineError = "";
    publishEngineStatus();

    const previousBridge = bridge;
    bridge = null;
    if (previousBridge) {
      try {
        await previousBridge.shutdown();
      } catch (error) {
        engineStatus = "error";
        engineError = error instanceof Error ? error.message : String(error);
        publishEngineStatus();
        throw error;
      }
    }

    const nextBridge = new PythonBridge();
    bridge = nextBridge;
    const enginePath = isDev
      ? join(PROJECT_ROOT, "engine", "server.py")
      : join(process.resourcesPath, "engine", "engine.exe");
    const pythonExe = isDev ? findPython() : "python";
    // 先注册 exit handler，再启动引擎，避免 ready 后瞬间崩溃的状态不一致
    attachBridgeNotifications(nextBridge);

    try {
      await nextBridge.start(enginePath, isDev, pythonExe, ENGINE_AUTH_TOKEN);
    } catch (error) {
      if (bridge === nextBridge) bridge = null;
      await nextBridge.shutdown().catch(() => {});
      engineStatus = "error";
      engineError = error instanceof Error ? error.message : String(error);
      publishEngineStatus();
      throw error;
    }

    if (bridge !== nextBridge) {
      await nextBridge.shutdown();
      return;
    }
    engineStatus = "ready";
    publishEngineStatus();
  })();

  startEnginePromise = attempt;
  try {
    await attempt;
  } finally {
    if (startEnginePromise === attempt) startEnginePromise = null;
  }
}

function isPortableBuild(): boolean {
  return Boolean(process.env.PORTABLE_EXECUTABLE_FILE || process.env.PORTABLE_EXECUTABLE_DIR);
}

function isNsisInstalledBuild(): boolean {
  if (!app.isPackaged) return false;
  const appDir = dirname(process.execPath);
  return existsSync(join(appDir, NSIS_INSTALL_MARKER))
    || existsSync(join(appDir, "Uninstall File Toolbox.exe"))
    || existsSync(join(appDir, `Uninstall ${app.getName()}.exe`));
}

function getPackageType(): AppPackageType {
  if (!app.isPackaged) return "development";
  if (isPortableBuild()) return "portable";
  if (isNsisInstalledBuild()) return "installer";
  return "archive";
}

function isUpdateSupported(): boolean {
  return process.platform === "win32" && getPackageType() === "installer";
}

function releaseNotesText(value: unknown): string {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return "";
  return value
    .map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && "note" in item) return String((item as { note?: unknown }).note || "");
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

function updateInfoFields(info: UpdateInfo): Partial<AppUpdateStatus> {
  return {
    latest: normalizeVersionTag(info.version) || info.version,
    name: String(info.releaseName || `File Toolbox ${info.version}`),
    body: releaseNotesText(info.releaseNotes),
    url: releasePageUrl(info.version),
  };
}

function publishUpdateStatus(next: Partial<AppUpdateStatus>): AppUpdateStatus {
  updateStatus = {
    ...updateStatus,
    ...next,
    current: app.getVersion(),
    packageType: getPackageType(),
    portable: getPackageType() === "portable",
    supported: isUpdateSupported(),
  };
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("app:update-status", updateStatus);
  }
  return updateStatus;
}

function unsupportedUpdateStatus(): AppUpdateStatus {
  const packageType = getPackageType();
  return publishUpdateStatus({
    state: "unsupported",
    supported: false,
    packageType,
    portable: packageType === "portable",
    latest: null,
    url: GITHUB_RELEASES,
    error: packageType === "portable"
      ? "便携单文件版通过发布页手动更新，避免覆盖正在运行的程序文件。"
      : packageType === "archive"
        ? "压缩包版通过发布页手动更新，下载后关闭程序并解压替换。"
        : "开发环境不执行真实软件更新，请使用打包后的安装版测试。",
  });
}

function configureAutoUpdater(): void {
  if (updaterConfigured) return;
  updaterConfigured = true;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;

  autoUpdater.on("checking-for-update", () => {
    publishUpdateStatus({ state: "checking", error: undefined, percent: 0, transferred: 0, total: 0, bytesPerSecond: 0 });
  });
  autoUpdater.on("update-available", (info) => {
    publishUpdateStatus({ state: "available", error: undefined, percent: 0, transferred: 0, total: 0, bytesPerSecond: 0, ...updateInfoFields(info) });
  });
  autoUpdater.on("update-not-available", (info) => {
    publishUpdateStatus({ state: "up-to-date", error: undefined, ...updateInfoFields(info) });
  });
  autoUpdater.on("download-progress", (progress: ProgressInfo) => {
    publishUpdateStatus({
      state: "downloading",
      percent: Math.max(0, Math.min(100, Number(progress.percent) || 0)),
      transferred: Math.max(0, Number(progress.transferred) || 0),
      total: Math.max(0, Number(progress.total) || 0),
      bytesPerSecond: Math.max(0, Number(progress.bytesPerSecond) || 0),
      error: undefined,
    });
  });
  autoUpdater.on("update-downloaded", (info) => {
    publishUpdateStatus({ state: "downloaded", percent: 100, error: undefined, ...updateInfoFields(info) });
  });
  autoUpdater.on("error", (error) => {
    publishUpdateStatus({ state: "error", error: error.message || String(error) });
  });
}

async function checkForAppUpdate(): Promise<AppUpdateStatus> {
  if (updateCheckPromise) return updateCheckPromise;
  const supportsAutomaticUpdate = isUpdateSupported();
  updateCheckPromise = (async () => {
    publishUpdateStatus({
      state: "checking",
      latest: null,
      name: "",
      body: "",
      url: GITHUB_RELEASES,
      error: undefined,
      percent: 0,
      transferred: 0,
      total: 0,
      bytesPerSecond: 0,
    });
    try {
      const release = await checkLatestReleasePage();
      const hasUpdate = compareVersions(release.latest, app.getVersion()) > 0;
      if (!hasUpdate) {
        return publishUpdateStatus({
          state: "up-to-date",
          latest: release.latest,
          name: `File Toolbox ${release.latest}`,
          body: "",
          url: release.url,
          error: undefined,
        });
      }
      if (supportsAutomaticUpdate) {
        configureAutoUpdater();
        await autoUpdater.checkForUpdates();
      } else {
        return publishUpdateStatus({
          state: "available",
          latest: release.latest,
          name: `File Toolbox ${release.latest}`,
          body: "",
          url: release.url,
          error: undefined,
        });
      }
      return updateStatus;
    } catch (error) {
      return publishUpdateStatus({ state: "error", error: error instanceof Error ? error.message : String(error) });
    } finally {
      updateCheckPromise = null;
    }
  })();
  return updateCheckPromise;
}

async function downloadAppUpdate(): Promise<AppUpdateStatus> {
  if (!isUpdateSupported()) return unsupportedUpdateStatus();
  if (updateDownloadPromise) return updateDownloadPromise;
  if (updateStatus.state === "downloaded") return updateStatus;
  if (updateStatus.state !== "available" && !(updateStatus.state === "error" && updateStatus.latest)) {
    throw new Error("请先检查更新并确认存在新版本");
  }
  configureAutoUpdater();
  updateDownloadPromise = (async () => {
    publishUpdateStatus({ state: "downloading", error: undefined, percent: 0, transferred: 0, total: 0, bytesPerSecond: 0 });
    try {
      await autoUpdater.downloadUpdate();
      return updateStatus;
    } catch (error) {
      return publishUpdateStatus({ state: "error", error: error instanceof Error ? error.message : String(error) });
    } finally {
      updateDownloadPromise = null;
    }
  })();
  return updateDownloadPromise;
}

ipcMain.handle("app:update:getStatus", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  if (!isUpdateSupported()) return unsupportedUpdateStatus();
  return publishUpdateStatus({});
});

ipcMain.handle("app:update:check", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  return checkForAppUpdate();
});

ipcMain.handle("app:update:download", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  return downloadAppUpdate();
});

ipcMain.handle("app:update:install", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  if (!isUpdateSupported()) return { accepted: false, status: unsupportedUpdateStatus() };
  if (updateStatus.state !== "downloaded") throw new Error("更新尚未下载完成");
  publishUpdateStatus({ state: "installing", error: undefined });
  const currentBridge = bridge;
  bridge = null;
  if (currentBridge) {
    try {
      await currentBridge.shutdown();
    } catch (error) {
      console.error("[main] 更新安装前关闭 Python 引擎失败:", error);
      bridge = currentBridge;
      publishUpdateStatus({ state: "error", error: "安装更新前无法安全关闭 Python 引擎" });
      throw new Error("安装更新前无法安全关闭 Python 引擎");
    }
  }
  setTimeout(() => autoUpdater.quitAndInstall(false, true), 0);
  return { accepted: true, status: updateStatus };
});

ipcMain.handle("app:openExternal", async (event, url: string) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  return shell.openExternal(validateExternalUrl(url));
});

ipcMain.handle("app:openDataDir", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  const baseDir = process.env.APPDATA || app.getPath("appData");
  const dataDir = join(baseDir, "FileToolbox");
  try {
    await fsp.mkdir(dataDir, { recursive: true });
  } catch {
    // ignore creation errors; openPath will surface a readable message
  }
  const failure = await shell.openPath(dataDir);
  if (failure) throw new Error(failure);
  return dataDir;
});

ipcMain.handle("dialog:saveFile", async (event, options: {
  content: string;
  defaultName?: string;
  filters?: { name: string; extensions: string[] }[];
}) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  if (!mainWindow) throw new Error("Window not available");
  const content = String(options?.content ?? "");
  if (Buffer.byteLength(content, "utf-8") > MAX_SAVE_FILE_CONTENT_SIZE) {
    throw new Error("导出内容过大，请缩小筛选范围后重试");
  }
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: options.defaultName || "export.txt",
    filters: options.filters || [{ name: "所有文件", extensions: ["*"] }],
  });
  if (result.canceled || !result.filePath) return { saved: false };
  await fsp.writeFile(result.filePath, content, "utf-8");
  return { saved: true, path: result.filePath };
});

ipcMain.handle("engine:restart", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  await startEngine();
});

if (gotLock) {
  app.whenReady().then(async () => {
    setupFilePreviewProtocol();
    session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
      callback(false);
    });
    session.defaultSession.setPermissionCheckHandler(() => false);
    setupIPC();
    createWindow();
    startEngine().catch((e) => {
      console.error("引擎启动失败:", e);
    });
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  } else if (mainWindow && mainWindow.isDestroyed()) {
    createWindow();
  }
});

let shutdownBeforeQuitStarted = false;
app.on("before-quit", (event) => {
  if (shutdownBeforeQuitStarted || !bridge) return;
  event.preventDefault();
  shutdownBeforeQuitStarted = true;
  const currentBridge = bridge;
  bridge = null;
  currentBridge.shutdown()
    .catch((error) => console.error("[main] 引擎关闭失败:", error))
    .finally(() => app.quit());
});
