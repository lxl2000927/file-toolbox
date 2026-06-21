import { app, BrowserWindow, ipcMain, dialog, session, shell } from "electron";
import { join, resolve, extname } from "path";
import { existsSync, promises as fsp } from "fs";
import https from "https";
import { randomBytes } from "crypto";
import { execFileSync } from "child_process";
import { PythonBridge } from "./python-bridge";

// 进程级未捕获异常处理，避免崩溃时无日志
process.on("unhandledRejection", (reason) => {
  console.error("[main] Unhandled Rejection:", reason);
});
process.on("uncaughtException", (err) => {
  console.error("[main] Uncaught Exception:", err);
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
const GITHUB_API = `https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=20`;
const GITHUB_RELEASES = `https://github.com/${GITHUB_REPO}/releases`;

let mainWindow: BrowserWindow | null = null;
let bridge: PythonBridge | null = null;
let engineStatus: "starting" | "ready" | "error" = "starting";
let engineError = "";
let ipcReady = false;

const isDev = !app.isPackaged;
const PROJECT_ROOT = isDev ? join(__dirname, "../../..") : process.resourcesPath;
const MIN_WINDOW_WIDTH = 1120;
const MIN_WINDOW_HEIGHT = 720;
const DEV_RENDERER_URL = "http://localhost:5173";
const MAX_REFERENCE_IMAGE_FILE_SIZE = 15 * 1024 * 1024;  // 原始参考图片文件大小上限（≈15 MiB）
const MAX_INPUT_PDF_FILE_SIZE = 200 * 1024 * 1024;
const MAX_GENERIC_INPUT_FILE_SIZE = 500 * 1024 * 1024;
const authorizedPaths = new Set<string>();
const ENGINE_AUTH_TOKEN = randomBytes(32).toString("hex");
const ENGINE_METHODS = new Set(["ping", "rename.preview", "rename.execute", "rename.undo", "pdf_split.validate", "pdf_split.preview", "pdf_split.preview_many", "pdf_split.execute_async", "scan_split.execute_async", "scan_split.preview_reference", "scan_split.probe_page", "scan_split.scan_only", "task.cancel", "history.get", "history.clear"]);
const MAX_UPDATE_RESPONSE_SIZE = 1024 * 1024;
const MAX_SAVE_FILE_CONTENT_SIZE = 20 * 1024 * 1024;

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

async function authorizePath(pathValue: string): Promise<void> {
  try { authorizedPaths.add(await canonicalPath(pathValue)); } catch { authorizedPaths.add(resolve(pathValue)); }
}

async function isAuthorizedPath(pathValue: string): Promise<boolean> {
  try {
    const actual = await canonicalPath(pathValue);
    for (const root of authorizedPaths) {
      const relative = actual.toLowerCase().startsWith(root.toLowerCase()) ? actual.slice(root.length) : "";
      if (actual === root || relative.startsWith("\\") || relative.startsWith("/")) return true;
    }
  } catch {
    // realpath 失败（例如路径尚未创建），退化为 resolve 后比对
    const fallback = resolve(pathValue);
    for (const root of authorizedPaths) {
      const relative = fallback.toLowerCase().startsWith(root.toLowerCase()) ? fallback.slice(root.length) : "";
      if (fallback === root || relative.startsWith("\\") || relative.startsWith("/")) return true;
    }
    return false;
  }
  return false;
}

async function validateInputFile(pathValue: string, allowedExts: Set<string>, maxSize: number): Promise<void> {
  const stat = await fsp.stat(pathValue);
  if (!stat.isFile()) throw new Error(`Invalid file: ${pathValue}`);
  if (stat.size > maxSize) throw new Error(`File too large: ${pathValue}`);
  const ext = extname(pathValue).slice(1).toLowerCase();
  if (allowedExts.size && !allowedExts.has(ext)) throw new Error(`Unsupported file type: ${pathValue}`);
}

async function validateEngineParamPaths(method: string, params: any): Promise<void> {
  if (["history.get", "history.clear", "task.cancel", "ping", "rename.undo"].includes(method)) return;
  const paths: string[] = [];
  const pathKeys = new Set(["path", "file", "files", "dir", "directory", "pdf_path", "pdf_path_from", "pdf_path_to", "reference_image", "reference_image_path", "input_path", "input_file", "input_files", "output_dir", "output_directory", "filepath", "filepath_from", "filepath_to"]);
  const visit = (item: any, key = "") => {
    if (typeof item === "string") {
      if (!item.trim()) return;
      const lower = key.toLowerCase();
      if (pathKeys.has(lower) || ["_path", "_paths", "_file", "_files", "_dir"].some((suffix) => lower.endsWith(suffix))) paths.push(item);
      return;
    }
    if (Array.isArray(item)) for (const child of item) visit(child, key);
    else if (item && typeof item === "object") for (const [childKey, child] of Object.entries(item)) visit(child, childKey);
  };
  visit(params);
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

function validateReleaseUrl(rawUrl: string): string {
  const safeUrl = validateExternalUrl(rawUrl);
  const url = new URL(safeUrl);
  if (!url.pathname.startsWith(`/${GITHUB_REPO}/releases`)) {
    throw new Error("Release 链接不在允许范围内");
  }
  return safeUrl;
}

function safeReleaseUrl(rawUrl: string | undefined): string {
  try {
    return validateReleaseUrl(rawUrl || GITHUB_RELEASES);
  } catch {
    return GITHUB_RELEASES;
  }
}

function normalizeVersionTag(tagName: unknown): string | null {
  const latest = String(tagName || "").replace(/^v/i, "").trim();
  if (!latest) return null;
  if (!/^[0-9A-Za-z.+-]+$/.test(latest)) return null;
  return latest;
}

function getReleaseVersion(release: any): string | null {
  if (!release || release.draft || release.prerelease) return null;
  return normalizeVersionTag(release.tag_name);
}

function newestStableRelease(data: unknown): any | null {
  const releases = Array.isArray(data) ? data : [data];
  let selected: any | null = null;
  let selectedVersion: string | null = null;
  for (const release of releases) {
    const version = getReleaseVersion(release);
    if (!version) continue;
    if (!selected || !selectedVersion || compareVersions(version, selectedVersion)) {
      selected = release;
      selectedVersion = version;
    }
  }
  return selected;
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
    if (!isMainSender(event)) return [];
    const out: { path: string; isFile: boolean; isDirectory: boolean; size: number }[] = [];
    for (const p of paths || []) {
      try {
        if (!(await isAuthorizedPath(p))) throw new Error("未授权路径");
        const s = await fsp.stat(p);
        out.push({ path: p, isFile: s.isFile(), isDirectory: s.isDirectory(), size: s.size });
      } catch {
        out.push({ path: p, isFile: false, isDirectory: false, size: 0 });
      }
    }
    return out;
  });

  ipcMain.handle("fs:readDirFiles", async (event, dirPath: string, exts?: string[]) => {
    if (!isMainSender(event)) return [];
    try {
      if (!(await isAuthorizedPath(dirPath))) return [];
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
      return results;
    } catch {
      return [];
    }
  });

  ipcMain.handle("fs:readFileAsDataUrl", async (event, filePath: string) => {
    if (!isMainSender(event)) return "";
    try {
      if (!(await isAuthorizedPath(filePath))) return "";
      const stat = await fsp.stat(filePath);
      if (!stat.isFile() || stat.size > MAX_REFERENCE_IMAGE_FILE_SIZE) return "";
      const ext = extname(filePath).slice(1).toLowerCase();
      const mimeMap: Record<string, string> = {
        png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
        bmp: "image/bmp", tiff: "image/tiff", tif: "image/tiff",
        webp: "image/webp", gif: "image/gif",
      };
      const mime = mimeMap[ext];
      if (!mime) return "";
      const buf = await fsp.readFile(filePath);
      return `data:${mime};base64,${buf.toString("base64")}`;
    } catch {
      return "";
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
    mainWindow?.webContents.send("engine:notification", { method: "engine.status", params: { status: engineStatus, error: engineError } });
  });
}

async function startEngine() {
  engineStatus = "starting";
  engineError = "";
  mainWindow?.webContents.send("engine:notification", { method: "engine.status", params: { status: engineStatus } });
  bridge?.shutdown();
  const nextBridge = new PythonBridge();
  bridge = nextBridge;
  const enginePath = isDev
    ? join(PROJECT_ROOT, "engine", "server.py")
    : join(process.resourcesPath, "engine", "engine.exe");
  const pythonExe = findPython();
  // 先注册 exit handler，再启动引擎，避免 ready 后瞬间崩溃的状态不一致
  attachBridgeNotifications(nextBridge);
  await nextBridge.start(enginePath, isDev, pythonExe, ENGINE_AUTH_TOKEN);
  if (bridge !== nextBridge) {
    nextBridge.shutdown();
    return;
  }
  engineStatus = "ready";
  mainWindow?.webContents.send("engine:notification", { method: "engine.status", params: { status: engineStatus } });
}

ipcMain.handle("app:checkUpdate", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  return new Promise((resolve) => {
    const req = https.get(
      GITHUB_API,
      {
        headers: {
          Accept: "application/vnd.github+json",
          "User-Agent": "FileToolbox-UpdateChecker",
        },
        timeout: 10000,
      },
      (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          resolve({ ok: false, error: `GitHub 响应异常: HTTP ${res.statusCode || 0}` });
          return;
        }
        const contentType = String(res.headers["content-type"] || "").toLowerCase();
        if (contentType && !/json/i.test(contentType)) {
          res.resume();
          resolve({ ok: false, error: `GitHub 响应类型异常: ${contentType}` });
          return;
        }
        const chunks: Buffer[] = [];
        let receivedBytes = 0;
        let tooLarge = false;
        res.on("data", (chunk: Buffer) => {
          chunks.push(chunk);
          receivedBytes += chunk.length;
          if (receivedBytes > MAX_UPDATE_RESPONSE_SIZE && !tooLarge) {
            tooLarge = true;
            req.destroy(new Error("响应过大"));
          }
        });
        res.on("end", () => {
          if (tooLarge) {
            resolve({ ok: false, error: "GitHub 响应过大" });
            return;
          }
          try {
            const raw = Buffer.concat(chunks, receivedBytes).toString("utf8");
            const data = JSON.parse(raw);
            const release = newestStableRelease(data);
            const currentVersion = app.getVersion();
            if (!release) {
              resolve({ ok: true, hasUpdate: false, current: currentVersion, latest: null });
              return;
            }
            const latest = normalizeVersionTag(release.tag_name);
            if (!latest) {
              resolve({ ok: false, error: `Release 版本号格式异常: ${String(release.tag_name)}` });
              return;
            }
            const hasUpdate = compareVersions(latest, currentVersion);
            if (!hasUpdate) {
              resolve({
                ok: true,
                hasUpdate: false,
                current: currentVersion,
                latest,
              });
              return;
            }
            const releaseUrl = safeReleaseUrl(release.html_url);
            resolve({
              ok: true,
              hasUpdate,
              current: currentVersion,
              latest,
              name: release.name || "",
              body: release.body || "",
              url: releaseUrl,
            });
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            resolve({ ok: false, error: `解析响应失败: ${message}` });
          }
        });
      },
    );
    req.on("error", (err) => resolve({ ok: false, error: err.message }));
    req.on("timeout", () => {
      req.destroy();
      resolve({ ok: false, error: "请求超时" });
    });
  });
});

function compareVersions(a: string, b: string): boolean {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] || 0;
    const vb = pb[i] || 0;
    if (va > vb) return true;
    if (va < vb) return false;
  }
  return false;
}

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
  // Stop current engine process then restart
  try { bridge?.shutdown(); } catch { /* ignore */ }
  engineStatus = "starting";
  engineError = "";
  mainWindow?.webContents.send("engine:notification", {
    method: "engine.status",
    params: { status: "starting", error: "" },
  });
  await startEngine();
});

if (gotLock) {
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
      callback(false);
    });
    session.defaultSession.setPermissionCheckHandler(() => false);
    setupIPC();
    createWindow();
    startEngine().catch((e) => {
      engineStatus = "error";
      engineError = e instanceof Error ? e.message : String(e);
      console.error("引擎启动失败:", e);
      mainWindow?.webContents.send("engine:notification", { method: "engine.status", params: { status: engineStatus, error: engineError } });
    });
  });
}

app.on("window-all-closed", () => {
  bridge?.shutdown();
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

app.on("before-quit", () => {
  bridge?.shutdown();
});
