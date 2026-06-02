import { app, BrowserWindow, ipcMain, dialog, session } from "electron";
import { join, resolve, extname } from "path";
import { existsSync, promises as fsp } from "fs";
import https from "https";
import { PythonBridge } from "./python-bridge";

const GITHUB_REPO = "LXL2000927/file-toolbox";
const GITHUB_API = `https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=1`;
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
const MAX_UPDATE_RESPONSE_SIZE = 1024 * 1024;
const authorizedPaths = new Set<string>();
const ENGINE_METHODS = new Set([
  "ping",
  "rename.preview",
  "rename.execute",
  "rename.undo",
  "pdf_split.validate",
  "pdf_split.preview",
  "pdf_split.preview_many",
  "pdf_split.execute",
  "pdf_split.execute_async",
  "scan_split.execute_async",
  "scan_split.preview_reference",
  "scan_split.probe_page",
  "scan_split.scan_only",
  "task.cancel",
  "history.get",
  "history.clear",
]);

function isMainSender(event: Electron.IpcMainInvokeEvent): boolean {
  return Boolean(mainWindow && event.sender === mainWindow.webContents && !mainWindow.isDestroyed());
}

async function canonicalPath(pathValue: string): Promise<string> {
  return resolve(await fsp.realpath(pathValue));
}

async function authorizePath(pathValue: string): Promise<void> {
  try {
    authorizedPaths.add(await canonicalPath(pathValue));
  } catch {
    authorizedPaths.add(resolve(pathValue));
  }
}

async function isAuthorizedPath(pathValue: string): Promise<boolean> {
  try {
    const actual = await canonicalPath(pathValue);
    for (const root of authorizedPaths) {
      const relative = actual.toLowerCase().startsWith(root.toLowerCase())
        ? actual.slice(root.length)
        : "";
      if (actual === root || relative.startsWith("\\") || relative.startsWith("/")) return true;
    }
  } catch {
    return false;
  }
  return false;
}

async function collectParamPaths(value: any): Promise<string[]> {
  const paths: string[] = [];

  // 显式枚举已知路径字段名（API 契约，非隐式正则约定）
  // 新增路径参数时必须同时更新此列表，以确保路径授权检查覆盖
  const KNOWN_PATH_KEYS = new Set([
    "path", "file", "files", "dir", "directory",
    "pdf_path", "pdf_path_from", "pdf_path_to",
    "reference_image", "reference_image_path",
    "input_path", "input_file", "input_files",
    "output_dir", "output_directory",
    "filepath", "filepath_from", "filepath_to",
  ]);
  const KNOWN_PATH_SUFFIXES = ["_path", "_paths", "_file", "_files", "_dir"];

  const visit = (item: any, key = "") => {
    if (typeof item === "string") {
      const lower = key.toLowerCase();
      // 显式匹配：key 本身在已知列表中，或以已知后缀结尾
      if (KNOWN_PATH_KEYS.has(lower) || KNOWN_PATH_SUFFIXES.some((s) => lower.endsWith(s))) {
        if (item) paths.push(item);
      }
      return;
    }
    if (Array.isArray(item)) {
      for (const child of item) visit(child, key);
      return;
    }
    if (item && typeof item === "object") {
      for (const [childKey, child] of Object.entries(item)) visit(child, childKey);
    }
  };
  visit(value);
  return paths;
}

async function validateEngineParamPaths(method: string, params: any): Promise<void> {
  if (method === "history.get" || method === "history.clear" || method === "task.cancel" || method === "ping" || method === "rename.undo") return;
  const paths = await collectParamPaths(params);
  for (const pathValue of paths) {
    if (!(await isAuthorizedPath(pathValue))) throw new Error(`未授权的文件路径: ${pathValue}`);
  }
}

function isAllowedAppUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    if (isDev) return url.origin === new URL(DEV_RENDERER_URL).origin;
    return url.protocol === "file:";
  } catch {
    return false;
  }
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

function findPython(): string {
  if (!isDev) return "python";
  const candidates = [
    join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),
    join(PROJECT_ROOT, "venv", "Scripts", "python.exe"),
  ];
  for (const c of candidates) {
    if (existsSync(c)) {
      console.log(`[main] 检测到 venv: ${c}`);
      return c;
    }
  }
  console.log("[main] 未找到 venv，使用系统 python");
  return "python";
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
      import("electron").then(({ shell }) => shell.openExternal(safeUrl));
    } catch {
      // Deny by default.
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isAllowedAppUrl(url)) event.preventDefault();
  });

  (mainWindow.webContents as any).on("will-frame-navigate", (event: Electron.Event, url: string) => {
    if (!isAllowedAppUrl(url)) event.preventDefault();
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
    if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
    if (!ENGINE_METHODS.has(String(method || ""))) throw new Error("引擎方法不在允许列表中");
    if (!bridge || engineStatus !== "ready") {
      throw new Error(engineStatus === "error" ? `Python 引擎启动失败：${engineError || "未知错误"}` : "Python 引擎启动中，请稍候");
    }
    await validateEngineParamPaths(String(method || ""), params);
    return bridge.call(method, params);
  });

  ipcMain.handle("engine:status", async (event) => {
    if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
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
      properties: ["openDirectory", "createDirectory"],
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

function attachBridgeNotifications() {
  if (!bridge) return;
  bridge.addNotificationHandler((method, params) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.webContents.send("engine:notification", { method, params });
  });
}

async function startEngine() {
  engineStatus = "starting";
  engineError = "";
  mainWindow?.webContents.send("engine:notification", { method: "engine.status", params: { status: engineStatus } });
  bridge = new PythonBridge();
  const enginePath = isDev
    ? join(PROJECT_ROOT, "engine", "server.py")
    : join(process.resourcesPath, "engine", "engine.exe");
  const pythonExe = findPython();
  await bridge.start(enginePath, isDev, pythonExe);
  engineStatus = "ready";
  attachBridgeNotifications();
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
        let raw = "";
        let tooLarge = false;
        res.on("data", (chunk: Buffer) => {
          raw += chunk.toString();
          if (raw.length > MAX_UPDATE_RESPONSE_SIZE && !tooLarge) {
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
            const data = JSON.parse(raw);
            const release = Array.isArray(data) ? data[0] : data;
            const currentVersion = app.getVersion();
            if (!release?.tag_name) {
              resolve({ ok: true, hasUpdate: false, current: currentVersion, latest: null });
              return;
            }
            const latest = normalizeVersionTag(release.tag_name);
            if (!latest) {
              resolve({ ok: false, error: `Release 版本号格式异常: ${String(release.tag_name)}` });
              return;
            }
            const hasUpdate = compareVersions(latest, currentVersion);
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
  const { shell } = await import("electron");
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
  const { shell } = await import("electron");
  const failure = await shell.openPath(dataDir);
  if (failure) throw new Error(failure);
  return dataDir;
});

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
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

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
