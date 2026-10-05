import { app, BrowserWindow, clipboard, dialog, ipcMain, net, protocol, session, shell } from "electron";
import { autoUpdater, type ProgressInfo, type UpdateInfo } from "electron-updater";
import { basename, dirname, extname, isAbsolute, join, relative, resolve } from "path";
import { existsSync, promises as fsp } from "fs";
import https from "https";
import { randomBytes } from "crypto";
import { execFileSync } from "child_process";
import { pathToFileURL } from "url";
import { PythonBridge } from "./python-bridge";
import { PreviewEngine } from './preview-engine';
import { WorkspaceStore } from './workspace-store';
import { captureDocument, captureIdentity, verifyIdentity, relocateDocument, type SavedIdentity } from './restored-access';
import type { WorkspaceScope, WorkspaceSource } from '../shared/workspace-types';
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
let workspaceStore: WorkspaceStore | null = null;
let allowWindowClose = false, closePending = false;
let rendererUnavailable = false;
const workspaceFlushes = new Map<string, { resolve: () => void; reject: (error: Error) => void }>();
const previewEngine = new PreviewEngine(async () => {
  const worker = new PythonBridge();
  const enginePath = isDev ? join(PROJECT_ROOT, 'engine', 'server.py') : join(process.resourcesPath, 'engine', 'engine.exe');
  try { await worker.start(enginePath, isDev, isDev ? findPython() : 'python', ENGINE_AUTH_TOKEN, { FILE_TOOLBOX_PREVIEW_ENGINE: '1' }); }
  catch (error) { await worker.shutdown(); throw error; }
  return worker;
}, (method, params) => {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('engine:notification', { method, params });
});

const isDev = !app.isPackaged;
const PROJECT_ROOT = isDev ? join(__dirname, "../../..") : process.resourcesPath;
const MIN_WINDOW_WIDTH = 1120;
const MIN_WINDOW_HEIGHT = 720;
const DEV_RENDERER_URL = "http://localhost:5173";
const MAX_REFERENCE_IMAGE_FILE_SIZE = 15 * 1024 * 1024;  // 原始参考图片文件大小上限（≈15 MiB）
const MAX_INPUT_PDF_FILE_SIZE = 200 * 1024 * 1024;
const MAX_GENERIC_INPUT_FILE_SIZE = 500 * 1024 * 1024;
type FileIdentity = { dev: string; ino: string; size: string; mtime_ns: string; canonical?: string };
type AuthorizedPath = { kind: "file" | "directory"; canonical: string; identity: FileIdentity };
const authorizedPaths = new Map<string, AuthorizedPath>();
const authorizedDirectories = new Map<string, AuthorizedPath>();
const selectionGenerations = new Map<string, number>();
let selectionGeneration = 0;
const MAX_AUTHORIZED_PATHS = 12000;
const ENGINE_AUTH_TOKEN = randomBytes(32).toString("hex");
const ENGINE_METHODS = new Set(["ping", "rename.preview", "rename.execute", "rename.undo", "pdf_split.validate", "pdf_split.preview", "pdf_split.preview_many", "pdf_split.execute_async", "scan_split.execute_async", "scan_split.preview_reference", "scan_split.probe_page", "scan_split.scan_only", "task.cancel", "history.get", "history.clear"]);
const MAX_SAVE_FILE_CONTENT_SIZE = 20 * 1024 * 1024;
for (const method of ["pdf_tools.run", "presets.list", "presets.save", "presets.delete"]) ENGINE_METHODS.add(method);
for (const method of ['tasks.list', 'tasks.retry', 'tasks.reveal', 'task.pause', 'task.resume']) ENGINE_METHODS.add(method);
const REFERENCE_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "bmp", "tiff", "tif", "webp", "gif"]);
// MuPDF can preserve JPEG2000/JBIG2/portable-map encodings when extracting
// embedded images; these are valid outputs even though they aren't import types.
const RESULT_DOCUMENT_EXTENSIONS = new Set(['pdf', 'txt', ...REFERENCE_IMAGE_EXTENSIONS,
  'jpx', 'jp2', 'j2k', 'jbig2', 'jb2', 'pnm', 'pam', 'pbm', 'pgm', 'ppm']);

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

function sourceByteLimit(source: WorkspaceSource) { return source.kind === 'pdf' ? MAX_INPUT_PDF_FILE_SIZE : MAX_REFERENCE_IMAGE_FILE_SIZE; }
function grantSaved(path: string, saved: SavedIdentity, kind: 'file' | 'directory') {
  rememberAuthorizedPath(resolve(path), { kind, canonical: saved.canonical, identity: saved });
  rememberAuthorizedPath(saved.canonical, { kind, canonical: saved.canonical, identity: saved });
}
function getWorkspaceStore(): WorkspaceStore {
  if (workspaceStore) return workspaceStore;
  workspaceStore = new WorkspaceStore(join(process.env.APPDATA || app.getPath('appData'), 'FileToolbox', 'workspaces'), {
    getSelectionGeneration: source => selectionGenerations.get(grantKey(source.path)),
    async capture(source) {
      if (!await isAuthorizedPath(source.path)) throw new Error('工作区来源尚未授权');
      const identity = await captureDocument(source.path, sourceByteLimit(source));
      if (!await isAuthorizedPath(source.path) || source.signature && identity.sha256 !== source.signature) throw new Error('源文件已变化，请重新导入');
      return identity;
    },
    async verify(source, identity) {
      try {
        await verifyIdentity(source.path, identity, 'file');
        if (source.signature && source.signature !== identity.sha256) throw new Error('来源签名与工作区不一致');
        grantSaved(source.path, identity, 'file');
        return { status: 'ready' };
      } catch (error) {
        return { status: (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'changed', message: String(error) };
      }
    },
    async relocate(source, identity, path) {
      if (!await isAuthorizedPath(path)) throw new Error('请通过文件选择框重新定位');
      await validateInputFile(path, source.kind === 'pdf' ? new Set(['pdf']) : REFERENCE_IMAGE_EXTENSIONS, sourceByteLimit(source));
      const replacement = await relocateDocument(path, identity, sourceByteLimit(source));
      grantSaved(path, replacement, 'file');
      return { source: { ...source, path, name: basename(path), signature: replacement.sha256, size: Number(replacement.size) }, identity: replacement };
    },
  });
  return workspaceStore;
}

async function flushWorkspaceBeforeClose(): Promise<void> {
  if (!rendererUnavailable && mainWindow && !mainWindow.isDestroyed() && typeof mainWindow.webContents.isLoadingMainFrame === 'function' && !mainWindow.webContents.isLoadingMainFrame()) {
    const token = randomBytes(16).toString('hex');
    await new Promise<void>((resolvePromise, reject) => {
      const timer = setTimeout(() => { workspaceFlushes.delete(token); reject(new Error('等待工作区保存超时，请稍后再试')); }, 30000);
      workspaceFlushes.set(token, { resolve: () => { clearTimeout(timer); resolvePromise(); }, reject: error => { clearTimeout(timer); reject(error); } });
      try { mainWindow!.webContents.send('workspace:flush-requested', token); }
      catch (error) { const pending = workspaceFlushes.get(token); workspaceFlushes.delete(token); pending?.reject(error instanceof Error ? error : new Error(String(error))); }
    });
  }
  await workspaceStore?.flush();
}

async function restoreTaskAccess(event: Electron.IpcMainInvokeEvent, method: string, params: any) {
  const id = params?.task_id;
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(id)) throw new Error('任务标识无效');
  const currentBridge = bridge!;
  if (method === 'tasks.reveal') {
    const spec = await currentBridge.call('tasks.output_spec', { task_id: id });
    const output = spec.output_files?.[0];
    assertPathString(output);
    await validateInputFile(output, RESULT_DOCUMENT_EXTENSIONS, Infinity);
    if (!isMainSender(event)) throw new Error('IPC 调用来源无效');
    await authorizePath(output, false);
    shell.showItemInFolder(await documentPath(event, output));
    return;
  }
  const spec = await currentBridge.call('tasks.retry_spec', { task_id: id });
  if (!['pdf_tools.run', 'pdf_split.execute_async', 'scan_split.execute_async', 'scan_split.scan_only', 'scan_split.probe_page'].includes(spec.method)) throw new Error('该任务不支持续做');
  const regrant = async (entries: Record<string, SavedIdentity>, kind: 'file' | 'directory') => {
    if (!entries || typeof entries !== 'object' || Array.isArray(entries) || Object.keys(entries).length > 5000) throw new Error('任务文件身份无效');
    for (const [path, identity] of Object.entries(entries)) {
      assertPathString(path); await verifyIdentity(path, identity, kind); grantSaved(path, identity, kind);
    }
  };
  await regrant(spec.input_identities, 'file');
  await regrant(spec.output_identities || {}, 'directory');
  const checked = await validateEngineParamPaths(spec.method, spec.params);
  for (const path of Object.keys(checked._input_identities)) {
    if (!spec.input_identities[path]) throw new Error('任务来源身份缺失');
    await verifyIdentity(path, spec.input_identities[path], 'file');
  }
  // A formerly missing child may now exist. Keep the original verified ancestor
  // proof instead of changing the identity set used by the task journal.
  const savedOutputs = spec.output_identities || {};
  for (const identity of Object.values(checked._output_identities) as FileIdentity[]) {
    if (!Object.values(savedOutputs).some(saved => containsPath((saved as SavedIdentity).canonical, identity.canonical!))) {
      throw new Error('任务输出目录与原授权范围不一致');
    }
  }
  checked._output_identities = savedOutputs;
  if (!isMainSender(event) || bridge !== currentBridge) throw new Error('引擎已重启，请重试');
  return currentBridge.call(spec.method, { ...checked, task_id: `retry_${randomBytes(12).toString('hex')}`, _resume_task_id: id });
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
    url.hash = "";
    const entry = isDev ? DEV_RENDERER_URL + "/" : pathToFileURL(join(__dirname, "../renderer/index.html")).href;
    return url.href === entry;
  } catch {
    return false;
  }
}

function isMainSender(event: Electron.IpcMainInvokeEvent): boolean {
  return Boolean(mainWindow && !mainWindow.isDestroyed() && event.sender === mainWindow.webContents
    && event.senderFrame === mainWindow.webContents.mainFrame
    && event.senderFrame && isAllowedAppUrl(event.senderFrame.url));
}

async function canonicalPath(pathValue: string): Promise<string> {
  return resolve(await fsp.realpath(pathValue));
}

function rememberAuthorizedPath(pathValue: string, entry: AuthorizedPath): void {
  const key = grantKey(pathValue);
  authorizedPaths.delete(key);
  authorizedPaths.set(key, entry);
  authorizedDirectories.delete(key);
  if (entry.kind === "directory") authorizedDirectories.set(key, entry);
  while (authorizedPaths.size > MAX_AUTHORIZED_PATHS) {
    const oldest = authorizedPaths.keys().next().value;
    if (oldest === undefined) break;
    authorizedPaths.delete(oldest);
    authorizedDirectories.delete(oldest);
  }
}

function grantKey(pathValue: string): string {
  const path = resolve(pathValue);
  return process.platform === "win32" ? path.toLowerCase() : path;
}

function authorizationCandidates(pathValue: string): AuthorizedPath[] {
  const lexical = resolve(pathValue);
  const direct = authorizedPaths.get(grantKey(lexical));
  const candidates = direct ? [direct] : [];
  for (const [root, entry] of authorizedDirectories) {
    if (entry !== direct && containsPath(root, lexical)) candidates.push(entry);
  }
  return candidates;
}

async function authorizePath(pathValue: string, nativeSelection = true): Promise<void> {
  assertPathString(pathValue);
  const canonical = await canonicalPath(pathValue);
  const stat = await fsp.stat(canonical, { bigint: true });
  if (!stat.isDirectory() && !stat.isFile()) throw new Error("仅支持普通文件和目录");
  const entry: AuthorizedPath = { kind: stat.isDirectory() ? "directory" : "file", canonical, identity: fileIdentity(stat) };
  rememberAuthorizedPath(resolve(pathValue), entry);
  rememberAuthorizedPath(canonical, entry);
  if (nativeSelection) {
    const generation = ++selectionGeneration;
    selectionGenerations.set(grantKey(pathValue), generation);
    selectionGenerations.set(grantKey(canonical), generation);
    while (selectionGenerations.size > MAX_AUTHORIZED_PATHS) selectionGenerations.delete(selectionGenerations.keys().next().value!);
  }
}

async function transferRenameGrants(method: string, checked: any, result: any): Promise<void> {
  const operations = method === 'rename.execute' ? result?.operations : result?.restored;
  if (!Array.isArray(operations)) return;
  for (const operation of operations) {
    if (method === 'rename.execute' && (!operation?.success || operation.operation_type !== 'overwrite')) continue;
    if (method === 'rename.undo' && operation?.operation === 'copy') continue;
    const from = method === 'rename.execute' ? operation.original_path : operation?.from;
    const to = method === 'rename.execute' ? operation.new_path : operation?.to;
    if (typeof from !== 'string' || typeof to !== 'string' || !samePath(dirname(resolve(from)), dirname(resolve(to)))) continue;
    const selected = authorizedPaths.get(grantKey(from));
    const identity = method === 'rename.execute' ? checked._input_identities[from] : selected?.kind === 'file' ? selected.identity : undefined;
    if (!identity) continue;
    try {
      const canonical = await canonicalPath(to);
      if (identity.canonical && !samePath(dirname(canonical), dirname(identity.canonical))) continue;
      const stat = await fsp.stat(canonical, { bigint: true });
      if (!stat.isFile() || !['dev', 'ino', 'size', 'mtime_ns'].every(key => fileIdentity(stat)[key as keyof FileIdentity] === identity[key])) continue;
      const entry: AuthorizedPath = { kind: 'file', canonical, identity: fileIdentity(stat) };
      rememberAuthorizedPath(to, entry); rememberAuthorizedPath(canonical, entry);
    } catch { /* A moved or externally replaced result must be selected again. */ }
  }
}

function fileIdentity(stat: import("fs").BigIntStats): FileIdentity {
  return { dev: String(stat.dev), ino: String(stat.ino), size: String(stat.size), mtime_ns: String(stat.mtimeNs) };
}

function samePath(left: string, right: string): boolean {
  return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right;
}

function containsPath(root: string, value: string): boolean {
  const child = relative(root, value);
  return !child || (child !== ".." && !child.startsWith("..\\") && !child.startsWith("../") && !isAbsolute(child));
}

function assertPathString(value: unknown, allowEmpty = false): asserts value is string {
  if (typeof value !== "string" || /[\x00-\x1f]/.test(value) || (!value && !allowEmpty)
    || (value !== "" && (!value.trim() || !isAbsolute(value)))) throw new Error("路径必须是绝对路径字符串");
}

// Resolve missing descendants through their existing ancestor, never fall back
// to a lexical path when realpath fails for permission or other I/O errors.
async function canonicalOutputPath(pathValue: string): Promise<string> {
  let ancestor = resolve(pathValue);
  const suffix: string[] = [];
  for (;;) {
    try { return resolve(await fsp.realpath(ancestor), ...suffix); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      const parent = dirname(ancestor);
      if (parent === ancestor) throw error;
      // A dangling link is not a missing directory we may safely create.
      try { await fsp.lstat(ancestor); throw new Error("路径包含失效的链接"); }
      catch (statError) { if ((statError as NodeJS.ErrnoException).code !== "ENOENT") throw statError; }
      suffix.unshift(relative(parent, ancestor));
      ancestor = parent;
    }
  }
}

async function isAuthorizedPath(pathValue: string): Promise<boolean> {
  try {
    assertPathString(pathValue);
    const lexical = resolve(pathValue);
    const candidates = authorizationCandidates(lexical);
    if (!candidates.length) return false; // Includes unselected UNC paths: no filesystem I/O.
    const actual = await canonicalOutputPath(lexical);
    for (const entry of candidates) {
      if (entry.kind === "directory" && containsPath(entry.canonical, actual)) return true;
      if (entry.kind === "file" && samePath(entry.canonical, actual)) {
        const stat = await fsp.stat(actual, { bigint: true });
        if (String(stat.dev) === entry.identity.dev && String(stat.ino) === entry.identity.ino) return true;
      }
    }
    return false;
  } catch { return false; }
}

async function validateInputFile(pathValue: string, allowedExts: Set<string>, maxSize: number): Promise<void> {
  const stat = await fsp.stat(pathValue);
  if (!stat.isFile()) throw new FileAccessFailure("not_file", "路径不是文件", pathValue);
  if (stat.size > maxSize) throw new FileAccessFailure("too_large", "文件超过允许的大小上限", pathValue);
  const ext = extname(pathValue).slice(1).toLowerCase();
  if (allowedExts.size && !allowedExts.has(ext)) throw new FileAccessFailure("unsupported_type", "不支持的文件类型", pathValue);
}

async function validateEngineParamPaths(method: string, params: any): Promise<any> {
  if (!params || typeof params !== "object" || Array.isArray(params)) throw new Error("参数必须是对象");
  const checked = { ...params, _input_identities: {} as Record<string, FileIdentity>, _output_identities: {} as Record<string, FileIdentity> };
  const inputs: Array<{ path: string; kind: "pdf" | "reference" | "file" }> = [];
  const outputs: string[] = [];
  const input = (value: unknown, kind: "pdf" | "reference" | "file", optional = false) => {
    if (value === undefined && optional) return;
    assertPathString(value, optional);
    if (value) inputs.push({ path: value, kind });
  };
  const batch = (value: unknown, kind: "pdf" | "file") => {
    if (!Array.isArray(value) || value.length > 5000) throw new Error("文件列表必须是一维数组，最多 5000 个文件");
    for (const item of value) input(item, kind);
  };
  const output = (value: unknown) => {
    if (value === undefined) return;
    assertPathString(value, true);
    if (value) outputs.push(value);
  };
  if (method === "rename.preview" || method === "rename.execute") {
    batch(checked.files, "file");
    output(checked.output_dir);
  } else if (method === "pdf_tools.run") {
    if (!Array.isArray(checked.files) || !checked.files.length || checked.files.length > 3000) throw new Error("请选择 1–3000 个文件");
    for (const file of checked.files) input(file, "reference");
    if (!checked.options || typeof checked.options !== "object" || Array.isArray(checked.options)) throw new Error("PDF 选项必须是对象");
    checked.options = { ...checked.options };
    output(checked.options.output_dir);
  } else if (method.startsWith("pdf_split.")) {
    if (method === "pdf_split.preview_many" || method === "pdf_split.execute_async") batch(checked.pdf_paths, "pdf");
    else input(checked.pdf_path, "pdf");
    if (checked.config !== undefined) {
      if (!checked.config || typeof checked.config !== "object" || Array.isArray(checked.config)) throw new Error("PDF 配置必须是对象");
      checked.config = { ...checked.config };
      output(checked.config.output_dir);
    }
  } else if (method.startsWith("scan_split.")) {
    if (method !== "scan_split.preview_reference") input(checked.pdf_path, "pdf");
    input(checked.reference_image_path, "reference", method !== "scan_split.preview_reference");
    output(checked.output_dir);
  }
  for (const pathValue of outputs) {
    if (!(await isAuthorizedPath(pathValue))) throw new Error(`Unauthorized path: ${pathValue}`);
    // Remember the selected existing ancestor for outputs that will be created.
    const canonical = await canonicalOutputPath(pathValue);
    let ancestor = canonical;
    while (!existsSync(ancestor) && dirname(ancestor) !== ancestor) ancestor = dirname(ancestor);
    if (!(await isAuthorizedPath(ancestor))) throw new Error('输出目录尚未授权');
    checked._output_identities[ancestor] = await captureIdentity(ancestor, 'directory');
  }
  for (const { path: pathValue, kind } of inputs) {
    if (!(await isAuthorizedPath(pathValue))) throw new Error(`Unauthorized path: ${pathValue}`);
    const canonical = await canonicalPath(pathValue);
    const isImage = kind === "reference" && REFERENCE_IMAGE_EXTENSIONS.has(extname(pathValue).slice(1).toLowerCase());
    await validateInputFile(pathValue, kind === "file" ? new Set() : isImage ? REFERENCE_IMAGE_EXTENSIONS : new Set(["pdf"]),
      kind === "file" ? MAX_GENERIC_INPUT_FILE_SIZE : isImage ? MAX_REFERENCE_IMAGE_FILE_SIZE : MAX_INPUT_PDF_FILE_SIZE);
    const identity = fileIdentity(await fsp.stat(pathValue, { bigint: true }));
    // Recheck selection after stat so a replacement cannot become a new trusted input.
    if (!(await isAuthorizedPath(pathValue))) throw new Error(`文件已发生变化，请重新选择: ${pathValue}`);
    const identityAllowed = authorizationCandidates(pathValue).some((entry) => entry.kind === "directory"
      ? containsPath(entry.canonical, canonical)
      : samePath(entry.canonical, canonical) && identity.dev === entry.identity.dev && identity.ino === entry.identity.ino);
    if (!identityAllowed) throw new Error(`文件已发生变化，请重新选择: ${pathValue}`);
    checked._input_identities[pathValue] = { ...identity, canonical };
  }
  return checked;
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
  allowWindowClose = false;
  rendererUnavailable = false;
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
  mainWindow.on('close', event => {
    if (allowWindowClose) return;
    event.preventDefault();
    if (closePending) return;
    closePending = true;
    flushWorkspaceBeforeClose().then(() => { allowWindowClose = true; mainWindow?.close(); })
      .catch(error => { void dialog.showMessageBox(mainWindow!, { type: 'error', title: '工作区未保存', message: '关闭已取消，请检查保存状态后重试。', detail: String(error) }); })
      .finally(() => { closePending = false; });
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
    authorizedPaths.clear();
    authorizedDirectories.clear();
    selectionGenerations.clear();
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
    rendererUnavailable = true;
    // Renderer memory has already been lost. Drain writes accepted by the main
    // process, rather than waiting for an acknowledgement that cannot arrive.
    for (const pending of workspaceFlushes.values()) pending.resolve();
    workspaceFlushes.clear();
    console.error(`[main] 渲染进程崩溃: ${details.reason}`);
  });
  mainWindow.webContents.on('did-finish-load', () => { rendererUnavailable = false; });

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

  for (const operation of ['load', 'save', 'clear'] as const) {
    ipcMain.handle(`workspace:${operation}`, async (event, scope: WorkspaceScope, state: unknown) => {
      if (!isMainSender(event)) throw new Error('IPC 调用来源无效');
      const store = getWorkspaceStore();
      if (operation === 'save') return store.save(scope, state);
      return store[operation](scope);
    });
  }
  ipcMain.handle('workspace:flush', async event => { if (!isMainSender(event)) throw new Error('IPC 调用来源无效'); await getWorkspaceStore().flush(); });
  ipcMain.handle('workspace:flushed', async (event, token: string, error?: string) => {
    if (!isMainSender(event)) throw new Error('IPC 调用来源无效');
    const pending = workspaceFlushes.get(token);
    if (pending) { workspaceFlushes.delete(token); error ? pending.reject(new Error(String(error))) : pending.resolve(); }
  });
  ipcMain.handle('workspace:relocate', async (event, scope: WorkspaceScope, sourceIndex: number) => {
    if (!isMainSender(event)) throw new Error('IPC 调用来源无效');
    const saved = await getWorkspaceStore().load(scope), source = saved.state?.sources[sourceIndex];
    if (!source || !Number.isSafeInteger(sourceIndex)) throw new Error('请选择有效的来源文件');
    const picked = await dialog.showOpenDialog(mainWindow!, { title: `重新定位 ${source.name}`, properties: ['openFile'], filters: [{ name: '原始文档', extensions: source.kind === 'pdf' ? ['pdf'] : [...REFERENCE_IMAGE_EXTENSIONS] }] });
    if (picked.canceled || !picked.filePaths[0]) return null;
    if (!isMainSender(event)) throw new Error('IPC 调用来源无效');
    await authorizePath(picked.filePaths[0]);
    return getWorkspaceStore().relocate(scope, sourceIndex, picked.filePaths[0]);
  });

  ipcMain.handle("engine:call", async (event, method: string, params: any) => {
    if (!isMainSender(event)) throw new Error("Invalid IPC sender");
    if (!ENGINE_METHODS.has(String(method || ""))) throw new Error("Engine method is not allowed");
    if (!bridge || engineStatus !== "ready") {
      throw new Error(engineStatus === "error" ? `Python 引擎启动失败：${engineError || "未知错误"}` : "Python 引擎启动中，请稍候");
    }
    if (method === 'tasks.retry' || method === 'tasks.reveal') return restoreTaskAccess(event, method, params);
    if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('参数必须是对象');
    // Resume metadata is fetched from our own journal, never supplied by UI.
    const publicParams = Object.fromEntries(Object.entries(params).filter(([key]) => !key.startsWith('_')));
    const checked = await validateEngineParamPaths(method, publicParams);
    if (!isMainSender(event)) throw new Error("Invalid IPC sender");
    if (method === 'task.cancel' && previewEngine.owns(checked.task_id)) return previewEngine.cancel(checked.task_id);
    if (method === 'scan_split.preview_reference' || method === 'pdf_tools.run' && ['inspect', 'thumbnails'].includes(checked.action)) return previewEngine.call(method, checked);
    const result = await bridge.call(method, checked);
    if (method === 'rename.execute' || method === 'rename.undo') await transferRenameGrants(method, checked, result);
    return result;
  });

  ipcMain.handle("engine:status", async (event) => {
    if (!isMainSender(event)) throw new Error("Invalid IPC sender");
    return { status: engineStatus, error: engineError };
  });

  ipcMain.handle("fs:authorizePaths", async (event, paths: string[]) => {
    if (!isMainSender(event) || !mainWindow || !Array.isArray(paths) || !paths.length || paths.length > 5000) return [];
    try { paths.forEach((value) => assertPathString(value)); } catch { return []; }
    const requested = [...new Set(paths)];
    // Raw IPC strings cannot prove a native drag/drop. Confirm in the main
    // process before touching even a network path supplied by the renderer.
    const consent = await dialog.showMessageBox(mainWindow, {
      type: "question", title: "允许访问拖入的文件", message: `是否允许处理这 ${requested.length} 个项目？`,
      detail: "文件夹授权包含其内容。请核对刚刚拖入的路径：\n\n" + requested.join("\n"),
      buttons: ["允许", "取消"], defaultId: 1, cancelId: 1, noLink: true,
    });
    if (consent.response !== 0 || !isMainSender(event)) return [];
    const authorized: string[] = [];
    for (const pathValue of requested) {
      try { await authorizePath(pathValue); authorized.push(pathValue); } catch { /* fail closed */ }
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
    await previewEngine.shutdown();
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

function handleUpdaterError(error: Error): void {
  const restoreEngine = updateStatus.state === 'installing' && !bridge;
  publishUpdateStatus({ state: 'error', error: error.message || String(error) });
  if (restoreEngine) {
    void startEngine().catch(failure => console.error('[main] 安装失败后恢复引擎失败:', failure));
  }
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
  autoUpdater.on("error", handleUpdaterError);
}

async function checkForAppUpdate(): Promise<AppUpdateStatus> {
  if (updateDownloadPromise || ["downloading", "downloaded", "installing"].includes(updateStatus.state)) return updateStatus;
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
  const attempt = (async () => {
    publishUpdateStatus({ state: "downloading", error: undefined, percent: 0, transferred: 0, total: 0, bytesPerSecond: 0 });
    try {
      await autoUpdater.downloadUpdate();
      return updateStatus;
    } catch (error) {
      return publishUpdateStatus({ state: "error", error: error instanceof Error ? error.message : String(error) });
    }
  })();
  updateDownloadPromise = attempt;
  try { return await attempt; }
  finally { if (updateDownloadPromise === attempt) updateDownloadPromise = null; }
}

ipcMain.handle("app:update:getStatus", async (event) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  if (updateStatus.state === "idle" && !isUpdateSupported()) return unsupportedUpdateStatus();
  return { ...updateStatus, current: app.getVersion(), packageType: getPackageType(),
    portable: isPortableBuild(), supported: isUpdateSupported() };
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
  await flushWorkspaceBeforeClose();
  await previewEngine.shutdown();
  publishUpdateStatus({ state: "installing", error: undefined });
  const currentBridge = bridge;
  bridge = null;
  if (currentBridge) {
    try {
      await currentBridge.shutdown();
    } catch (error) {
      console.error("[main] 更新安装前关闭 Python 引擎失败:", error);
      bridge = currentBridge;
      engineStatus = 'error';
      engineError = '更新安装前关闭引擎失败，请重启引擎后重试';
      publishEngineStatus();
      publishUpdateStatus({ state: "error", error: "安装更新前无法安全关闭 Python 引擎" });
      throw new Error("安装更新前无法安全关闭 Python 引擎");
    }
  }
  setTimeout(() => {
    try { autoUpdater.quitAndInstall(false, true); }
    catch (error) { handleUpdaterError(error instanceof Error ? error : new Error(String(error))); }
  }, 0);
  return { accepted: true, status: updateStatus };
});

ipcMain.handle("app:openExternal", async (event, url: string) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  return shell.openExternal(validateExternalUrl(url));
});

async function documentPath(event: Electron.IpcMainInvokeEvent, value: string, textOnly = false): Promise<string> {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  assertPathString(value);
  if (!await isAuthorizedPath(value)) throw new Error("文件未授权或已发生变化，请重新选择");
  const canonical = await canonicalPath(value);
  const extensions = textOnly ? new Set(['txt']) : RESULT_DOCUMENT_EXTENSIONS;
  await validateInputFile(canonical, extensions, Infinity);
  if (!await isAuthorizedPath(canonical) || !isMainSender(event)) throw new Error("文件已发生变化，请重新选择");
  return canonical;
}

ipcMain.handle("document:open", async (event, value: string) => {
  const path = await documentPath(event, value);
  const failure = await shell.openPath(path);
  if (failure) throw new Error(failure);
});
ipcMain.handle("document:reveal", async (event, value: string) => {
  shell.showItemInFolder(await documentPath(event, value));
});
ipcMain.handle("clipboard:writeText", async (event, text: string) => {
  if (!isMainSender(event)) throw new Error("IPC 调用来源无效");
  if (typeof text !== 'string' || Buffer.byteLength(text, 'utf8') > MAX_SAVE_FILE_CONTENT_SIZE) throw new Error("复制内容无效或过大");
  clipboard.writeText(text);
});
ipcMain.handle("document:saveTextCopy", async (event, value: string) => {
  let source = await documentPath(event, value, true);
  const selected = await dialog.showSaveDialog(mainWindow!, {
    title: '导出完整 OCR 文字', defaultPath: basename(source), filters: [{ name: '文字文件', extensions: ['txt'] }],
  });
  if (selected.canceled || !selected.filePath) return { saved: false };
  source = await documentPath(event, value, true);
  const target = selected.filePath;
  if (extname(target).toLowerCase() !== '.txt') throw new Error('请保存为 .txt 文件');
  if (!samePath(source, resolve(target))) {
    const temporary = join(dirname(target), `.ocr-export-${randomBytes(12).toString('hex')}.tmp`);
    try { await fsp.copyFile(source, temporary); await fsp.rename(temporary, target); }
    finally { await fsp.unlink(temporary).catch(() => {}); }
  }
  return { saved: true, path: target };
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
  if (shutdownBeforeQuitStarted) return;
  event.preventDefault();
  shutdownBeforeQuitStarted = true;
  (allowWindowClose ? Promise.resolve() : flushWorkspaceBeforeClose()).then(async () => {
    const currentBridge = bridge; bridge = null;
    await Promise.allSettled([currentBridge?.shutdown(), previewEngine.shutdown()]);
    allowWindowClose = true; app.quit();
  }).catch(error => {
    shutdownBeforeQuitStarted = false;
    if (mainWindow) void dialog.showMessageBox(mainWindow, { type: 'error', title: '工作区未保存', message: '退出已取消，请检查保存状态后重试。', detail: String(error) });
  });
});
