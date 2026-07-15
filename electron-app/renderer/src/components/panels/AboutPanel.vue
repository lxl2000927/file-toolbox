<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, onActivated, onDeactivated, computed, watch } from "vue";
import type { AppUpdateStatus } from "../../env";
import AppIcon from "../common/AppIcon.vue";
import AppSelect from "../common/AppSelect.vue";
import { useAppDialog } from "../../composables/useAppDialog";
import { useToast } from "../../composables/useToast";
import { marked } from "marked";

const dialog = useAppDialog();
const toast = useToast();

type SettingsTab = "logs" | "updates" | "about";
type LogLevelFilter = "all" | "error" | "warning" | "success" | "info" | "debug";
type LogSourceFilter = "all" | "rename" | "pdf_split" | "scan_split" | "system";
type UnknownRecord = Record<string, unknown>;
type LogRecord = UnknownRecord;

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" ? value as UnknownRecord : {};
}

function errorMessage(error: unknown, fallback = "未知错误"): string {
  if (error instanceof Error && error.message) return error.message;
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === "string" && message) return message;
  }
  return fallback;
}

const settingsTabs: { key: SettingsTab; label: string }[] = [
  { key: "logs", label: "日志" },
  { key: "updates", label: "更新" },
  { key: "about", label: "关于" },
];

const aboutFeatures = [
  {
    label: "批量重命名",
    title: "规则组合与实时预览",
    description: "支持插入字符/编号、替换、删除或保留字符、智能识别和自定义规则；执行前可实时预览新文件名，覆盖模式支持撤销上次操作。",
  },
  {
    label: "PDF 普通拆分",
    title: "多模式拆分与批量处理",
    description: "支持按页数、文件大小、页码范围和书签拆分；可批量校验 PDF，生成可复制的拆分预览，并在执行时显示任务进度。",
  },
  {
    label: "扫描拆分",
    title: "标记页识别与调参工具",
    description: "支持二维码、印章和特征点匹配三类识别方式；可框选 ROI 提升稳定性，支持命中后跳过页数、快速扫描、单页测试和高级参数预设。",
  },
];

const aboutArchitecture = [
  "Electron 主进程负责窗口、文件选择、更新检查和本地系统能力。",
  "Vue 3 渲染端负责交互界面、实时预览状态、任务进度和结果展示。",
  "Python 引擎通过 stdio JSON-RPC 提供重命名、PDF 拆分、扫描拆分和历史记录能力。",
  "核心处理逻辑位于 src/core，历史记录与路径处理位于 src/utils。",
];

const aboutTips = [
  "重命名前建议开启实时预览，确认规则顺序和目标文件名后再执行。",
  "PDF 拆分前先生成预览；如果设置变化，过期预览会提醒重新生成。",
  "扫描拆分遇到误检或漏检时，优先尝试 ROI 和检测模式，再调整高级参数。",
  "运行日志可在“日志”分段导出，用于排查任务失败或记录批处理过程。",
];

const updateStatus = ref<AppUpdateStatus>({
  state: "idle",
  supported: false,
  packageType: "development",
  portable: false,
  current: "",
});
let unsubscribeUpdateStatus: (() => void) | null = null;
const ALLOWED_RELEASE_TAGS = new Set([
  "a", "p", "br", "strong", "em", "b", "i", "u", "s", "code", "pre", "blockquote",
  "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td", "img",
]);
const ALLOWED_RELEASE_ATTRS = new Set(["href", "src", "alt", "title", "colspan", "rowspan"]);

function isSafeReleaseUrl(value: string, options: { allowRelative?: boolean; allowMailto?: boolean } = {}) {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith("#")) return true;
  if (options.allowMailto && /^mailto:/i.test(trimmed)) return true;
  if (!options.allowRelative && !/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return false;
  try {
    const url = new URL(trimmed, window.location.origin);
    return ["http:", "https:"].includes(url.protocol);
  } catch {
    return false;
  }
}

const releaseBodyHtmlCache = new Map<string, string>();

// TODO: 后续可换 DOMPurify 降低维护成本（当前白名单清洗逻辑安全，但维护成本较高）
const releaseBodyHtml = computed(() => {
  const release = updateStatus.value;
  const body = release?.body;
  if (!body) return "";
  // 按 release 标识+body 缓存，避免重复 marked 解析与 DOM 清洗
  const cacheKey = body;
  const cached = releaseBodyHtmlCache.get(cacheKey);
  if (cached !== undefined) return cached;
  const html = marked.parse(body, { async: false }) as string;
  const template = document.createElement("template");
  template.innerHTML = html;
  template.content.querySelectorAll("script, style, iframe, object, embed, form, input, button, textarea, select, link, meta, svg, math").forEach((node) => node.remove());
  template.content.querySelectorAll("*").forEach((node) => {
    const tagName = node.tagName.toLowerCase();
    if (!ALLOWED_RELEASE_TAGS.has(tagName)) {
      node.replaceWith(...Array.from(node.childNodes));
      return;
    }
    for (const attr of Array.from(node.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();
      if (name.startsWith("on") || !ALLOWED_RELEASE_ATTRS.has(name)) {
        node.removeAttribute(attr.name);
        continue;
      }
      if (name === "href" && !isSafeReleaseUrl(value, { allowRelative: true, allowMailto: true })) node.removeAttribute(attr.name);
      if (name === "src" && !isSafeReleaseUrl(value, { allowRelative: false })) node.removeAttribute(attr.name);
    }
    if (tagName === "a") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
  const result = template.innerHTML;
  releaseBodyHtmlCache.set(cacheKey, result);
  return result;
});
const updateBusy = computed(() => ["checking", "downloading", "installing"].includes(updateStatus.value.state));
const updateHasRelease = computed(() => Boolean(updateStatus.value.latest) && ["available", "downloading", "downloaded", "installing"].includes(updateStatus.value.state));
const updatePercent = computed(() => Math.max(0, Math.min(100, Math.round(Number(updateStatus.value.percent) || 0))));
const updatePackageLabel = computed(() => ({
  installer: "安装版",
  portable: "便携单文件版",
  archive: "压缩包版",
  development: "开发版",
}[updateStatus.value.packageType]));
const updateCheckButtonLabel = computed(() => {
  if (updateStatus.value.state === "unsupported") return "检查更新";
  switch (updateStatus.value.state) {
    case "checking": return "检查中…";
    case "downloading": return "下载中…";
    case "downloaded": return "已准备安装";
    case "installing": return "正在安装…";
    case "idle": return "检查更新";
    case "available": return "再次检查";
    default: return "重新检查";
  }
});
const updateStatusTone = computed(() => {
  if (["checking", "downloading", "installing"].includes(updateStatus.value.state)) return "loading";
  if (updateStatus.value.state === "error") return "error";
  if (["downloaded", "up-to-date"].includes(updateStatus.value.state)) return "ok";
  return "info";
});
const updateMessage = computed(() => {
  const status = updateStatus.value;
  switch (status.state) {
    case "checking": return "正在连接更新服务检查新版本…";
    case "available": return `发现新版本 ${status.latest || ""}（当前 ${status.current}）`;
    case "downloading": return `正在下载 File Toolbox ${status.latest || "更新"}…`;
    case "downloaded": return "更新已下载并完成校验，可以重启安装。";
    case "installing": return "正在退出应用并启动安装程序…";
    case "up-to-date": return `当前已是最新版本 ${status.current}`;
    case "unsupported": return "当前版本不支持软件内更新。";
    case "error": return status.error || "更新失败，请重试。";
    default: return "";
  }
});
const dataDirMsg = ref("");
const activeSettingsTab = ref<SettingsTab>("logs");
const activeSettingsTabIndex = computed(() => settingsTabs.findIndex((tab) => tab.key === activeSettingsTab.value));

const logRaw = ref<LogRecord[]>([]);
const logLoading = ref(false);
const logClearing = ref(false);
const logError = ref("");
const logLevelFilter = ref<LogLevelFilter>("all");
const logSourceFilter = ref<LogSourceFilter>("all");
const logSearch = ref("");
let logAutoRefreshTimer: number | null = null;
let logRefreshPromise: Promise<void> | null = null;
let logRefreshGeneration = 0;
const LOG_PAGE_SIZE = 50;
const logVisibleCount = ref(LOG_PAGE_SIZE);
const logViewerRef = ref<HTMLElement | null>(null);
let logIo: IntersectionObserver | null = null;

const logLevelOptions: { label: string; value: LogLevelFilter; tone?: Exclude<LogLevelFilter, "all"> }[] = [
  { label: "级别：全部", value: "all" },
  { label: "错误", value: "error", tone: "error" },
  { label: "警告", value: "warning", tone: "warning" },
  { label: "成功", value: "success", tone: "success" },
  { label: "信息", value: "info", tone: "info" },
  { label: "调试", value: "debug", tone: "debug" },
];
const logSourceOptions: { label: string; value: LogSourceFilter; icon: "scan" | "pdf" | "rename" | "settings" | "package" }[] = [
  { label: "来源：全部", value: "all", icon: "package" },
  { label: "重命名", value: "rename", icon: "rename" },
  { label: "PDF拆分", value: "pdf_split", icon: "pdf" },
  { label: "扫描拆分", value: "scan_split", icon: "scan" },
  { label: "系统", value: "system", icon: "settings" },
];

function formatLogRecord(rawRecord: unknown) {
  const record = asRecord(rawRecord);
  const _msg = record?.message || record?.description || "日志记录";
  const _src = normalizeLogSource(String(record?.source || record?.operation_type || ""));
  const details = asRecord(record?.details);
  const _status = details.cancelled ? "已取消" : record?.success === true ? "成功" : record?.success === false ? "失败" : "未知";
  const detailLogs = Array.isArray(details.log_tail) ? details.log_tail.map((line: unknown) => String(line)).filter(Boolean) : [];
  const scanOptionsText = formatScanOptions(details.options);
  const perfText = formatPerformanceStats(details.performance_stats);
  const detailTruncatedText = details.log_tail_truncated
    ? `\n    （详细日志已截断${Number(details.log_tail_original_count) > 0 ? `，原 ${Number(details.log_tail_original_count)} 条` : ""}）`
    : "";
  const detailText = detailLogs.length || detailTruncatedText
    ? `\n  详细日志${detailLogs.length ? `\n${detailLogs.map((line: string) => `    ${line}`).join("\n")}` : ""}${detailTruncatedText}`
    : "";
  const metaText = formatLogMeta(details);
  const _rawText = `[${record.timestamp}] [${_src}] [_LEVEL_] ${_msg} · ${_status}` +
    metaText +
    scanOptionsText +
    (record?.error_message ? `\n  错误：${record.error_message}` : "") +
    perfText +
    detailText;
  const level = normalizeLogLevel(record?.level, _rawText, record);
  const text = _rawText.replace("[_LEVEL_]", `[${level}]`);
  return text;
}

function formatDuration(seconds: unknown) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "0ms";
  if (value < 1) return `${Math.round(value * 1000)}ms`;
  if (value < 60) return `${value.toFixed(2)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m${(value - minutes * 60).toFixed(1)}s`;
}

function formatScanOptions(rawOptions: unknown) {
  const options = asRecord(rawOptions);
  if (!options || typeof options !== "object") return "";
  const modeMap: Record<string, string> = {
    auto: "自动",
    qrcode: "二维码",
    stamp: "印章",
    feature: "特征匹配",
  };
  const parts: string[] = [];
  const mode = String(options.detection_mode || "");
  if (mode) parts.push(`${modeMap[mode] || mode}模式`);
  if (Number.isFinite(Number(options.dpi))) parts.push(`DPI ${Number(options.dpi)}`);
  const roi = Array.isArray(options.reference_roi) ? options.reference_roi.map((item: unknown) => Number(item)) : null;
  const useRoi = options.use_roi === true || options.qrcode_use_roi === true;
  if (useRoi && roi && roi.length === 4 && roi.every(Number.isFinite)) {
    parts.push(`ROI 已启用 [x=${roi[0]}, y=${roi[1]}, w=${roi[2]}, h=${roi[3]}]`);
  } else if (useRoi) {
    parts.push("ROI 已启用但未框选区域");
  } else {
    parts.push("ROI 未启用");
  }
  if (options.qrcode_no_decode === true) parts.push("二维码不解码");
  const qrText = typeof options.qrcode_text_contains === "string" ? options.qrcode_text_contains.trim() : "";
  if (qrText) parts.push(`二维码内容包含“${qrText}”`);
  if (Number(options.qrcode_skip_pages) > 0) parts.push(`命中后跳过 ${Number(options.qrcode_skip_pages)} 页`);
  if (Number.isFinite(Number(options.qrcode_max_attempts))) {
    const value = Number(options.qrcode_max_attempts);
    const label = value < 24 ? "快速" : value < 72 ? "标准" : value < 144 ? "增强" : "极强";
    parts.push(`二维码识别强度 ${label}`);
  }
  const markerMode = options.exclude_marker_page === true
    ? "不保存标记页"
    : options.marker_as_first_page === false ? "标记页放上一份末尾" : "标记页放下一份开头";
  parts.push(markerMode);
  if (Number(options.max_segment_pages) > 0) parts.push(`每份最多 ${Number(options.max_segment_pages)} 页`);
  if (Number.isFinite(Number(options.nfeatures))) parts.push(`特征点 ${Number(options.nfeatures)}`);
  if (Number.isFinite(Number(options.min_matches))) parts.push(`最小匹配 ${Number(options.min_matches)}`);
  if (Number.isFinite(Number(options.ratio))) parts.push(`比例 ${Number(options.ratio)}`);
  if (Number.isFinite(Number(options.min_inlier_ratio))) parts.push(`内点比例 ${Number(options.min_inlier_ratio)}`);
  if (Number.isFinite(Number(options.ransac_reproj_threshold))) parts.push(`RANSAC ${Number(options.ransac_reproj_threshold)}`);
  if (options.enable_multithread === true) parts.push("OpenCV 多线程");
  if (options.enable_gpu === true) parts.push("OpenCL 加速");
  return parts.length ? `\n  配置：${parts.join(" · ")}` : "";
}

function formatLogMeta(rawDetails: unknown) {
  const details = asRecord(rawDetails);
  const parts: string[] = [];
  if (Array.isArray(details.marker_pages)) {
    parts.push(`标记页 ${Number(details.marker_pages_original_count) || details.marker_pages.length}`);
  }
  if (Array.isArray(details.output_files) && details.output_files.length) {
    parts.push(`输出 ${Number(details.output_files_original_count) || details.output_files.length}`);
  }
  if (Number.isFinite(Number(details.elapsed_ms))) parts.push(`耗时 ${formatDuration(Number(details.elapsed_ms) / 1000)}`);
  return parts.length ? `\n  摘要：${parts.join(" · ")}` : "";
}

function formatPerformanceStats(rawStats: unknown) {
  const stats = asRecord(rawStats);
  if (!stats || typeof stats !== "object") return "";
  const pagesScanned = Number(stats.pages_scanned || 0);
  const pagesSkipped = Number(stats.pages_skipped || 0);
  const markerCount = Number(stats.markers_found || 0);
  const pageScanSeconds = Number(stats.page_scan_seconds);
  const lines = [
    `识别阶段：${formatDuration(stats.scan_seconds)}${Number.isFinite(pageScanSeconds) ? ` · 页面扫描 ${formatDuration(pageScanSeconds)}` : ""} · 实际处理 ${pagesScanned} 页 · 跳过 ${pagesSkipped} 页 · 命中 ${markerCount} 页`,
    `阶段：渲染 ${formatDuration(stats.render_seconds)} · 二维码 ${formatDuration(stats.qr_seconds)} · 印章 ${formatDuration(stats.stamp_seconds)} · 特征 ${formatDuration(stats.feature_seconds)} · 其他 ${formatDuration(stats.other_seconds)}`,
    `辅助：DPI兜底 ${formatDuration(stats.dpi_fallback_seconds)}（${Number(stats.dpi_fallback_hits || 0)}/${Number(stats.dpi_fallback_attempts || 0)}） · 分段 ${formatDuration(stats.build_seconds)} · 写入 ${formatDuration(stats.write_seconds)}`,
  ];
  if (Number.isFinite(Number(stats.stamp_hits)) || Number.isFinite(Number(stats.qr_hits)) || Number.isFinite(Number(stats.feature_hits))) {
    lines.push(`命中来源：印章 ${Number(stats.stamp_hits || 0)} 页 · 二维码 ${Number(stats.qr_hits || 0)} 页 · 特征点 ${Number(stats.feature_hits || 0)} 页 · 全部未命中 ${Math.max(0, pagesScanned - markerCount)} 页`);
  }
  if (Number(stats.roi_clip_pages || 0) || Number(stats.roi_clip_fallback_pages || 0)) {
    lines.push(`ROI：局部渲染 ${Number(stats.roi_clip_pages || 0)} 页 · 回退 ${Number(stats.roi_clip_fallback_pages || 0)} 页`);
  }
  if (Number(stats.total_seconds || 0)) {
    lines.push(`总计：${formatDuration(stats.total_seconds)}`);
  }
  return `\n  耗时统计\n${lines.map((line) => `    ${line}`).join("\n")}`;
}

// 日志级别统一由后端 HistoryManager.infer_level() 推断，前端仅在缺失时做兜底映射
function normalizeLogLevel(value: unknown, _fallbackText: string, record?: LogRecord): Exclude<LogLevelFilter, "all"> {
  const level = String(value || "").toLowerCase();
  if (["error", "warning", "success", "info", "debug"].includes(level)) return level as Exclude<LogLevelFilter, "all">;
  // 兜底：后端未提供 level 时按 success 字段推断
  if (asRecord(record?.details).cancelled === true) return "warning";
  if (record?.success === false) return "error";
  if (record?.success === true) return "success";
  return "info";
}

function normalizeLogSource(operationType: string): LogSourceFilter {
  const type = String(operationType || "").toLowerCase();
  if (type.includes("rename")) return "rename";
  if (type.includes("pdf_split")) return "pdf_split";
  if (type.includes("scan_split")) return "scan_split";
  return "system";
}

const logItems = computed(() => logRaw.value.map((record) => {
  const text = formatLogRecord(record);
  return {
    text,
    level: normalizeLogLevel(record?.level, text, record),
    source: normalizeLogSource(String(record?.source || record?.operation_type || "")),
    record,
  };
}));

const filteredLogItems = computed(() => {
  const keyword = logSearch.value.trim().toLowerCase();
  return logItems.value.filter((item) => {
    if (logLevelFilter.value !== "all" && item.level !== logLevelFilter.value) return false;
    if (logSourceFilter.value !== "all" && item.source !== logSourceFilter.value) return false;
    if (keyword && !item.text.toLowerCase().includes(keyword)) return false;
    return true;
  });
});

const filteredLogLines = computed(() => filteredLogItems.value.map((item) => item.text));
const filteredLogRaw = computed(() => filteredLogItems.value.map((item) => item.record));

// 虚拟滚动：仅渲染可见范围内的日志条目
const visibleLogItems = computed(() => filteredLogItems.value.slice(0, logVisibleCount.value));
const hasMoreLogs = computed(() => logVisibleCount.value < filteredLogItems.value.length);

function setupLogSentinel(el: unknown) {
  logIo?.disconnect();
  if (!(el instanceof Element)) return;
  const root = logViewerRef.value || (el as HTMLElement).closest<HTMLElement>(".log-viewer");
  if (!root) return;
  logIo = new IntersectionObserver((entries) => {
    if (entries[0]?.isIntersecting) {
      logVisibleCount.value = Math.min(logVisibleCount.value + LOG_PAGE_SIZE, filteredLogItems.value.length);
    }
  }, { root, rootMargin: "120px" });
  logIo.observe(el);
}

function resetLogVisibleCount() {
  logVisibleCount.value = LOG_PAGE_SIZE;
}

watch(logSearch, () => resetLogVisibleCount());
watch(logLevelFilter, () => resetLogVisibleCount());
watch(logSourceFilter, () => resetLogVisibleCount());

onMounted(() => {
  refreshLogs();
  startLogAutoRefresh();
  const updateApi = window.electronAPI?.update;
  if (updateApi) {
    unsubscribeUpdateStatus = updateApi.onStatus((status) => {
      updateStatus.value = status;
    });
    updateApi.getStatus()
      .then((status) => { updateStatus.value = status; })
      .catch((error: unknown) => {
        updateStatus.value = {
          ...updateStatus.value,
          state: "error",
          error: errorMessage(error, "无法读取更新状态"),
        };
      });
  }
});

onBeforeUnmount(() => {
  stopLogAutoRefresh();
  logRefreshGeneration += 1;
  logIo?.disconnect();
  unsubscribeUpdateStatus?.();
  unsubscribeUpdateStatus = null;
});

// KeepAlive 下 onMounted/onBeforeUnmount 不会在切面板时触发，
// 用 onActivated/onDeactivated 管理定时器避免后台空转
onActivated(() => {
  if (activeSettingsTab.value === "logs") {
    refreshLogs({ silent: true });
    startLogAutoRefresh();
  }
});

onDeactivated(() => {
  stopLogAutoRefresh();
});

watch(activeSettingsTab, (tab) => {
  if (tab === "logs") {
    refreshLogs({ silent: true });
    startLogAutoRefresh();
  } else {
    stopLogAutoRefresh();
  }
});

function startLogAutoRefresh() {
  if (logAutoRefreshTimer || activeSettingsTab.value !== "logs") return;
  logAutoRefreshTimer = window.setInterval(() => {
    if (!logRefreshPromise && !logClearing.value) refreshLogs({ silent: true });
  }, 2000);
}

function stopLogAutoRefresh() {
  if (!logAutoRefreshTimer) return;
  window.clearInterval(logAutoRefreshTimer);
  logAutoRefreshTimer = null;
}

async function refreshLogs(options: { silent?: boolean } = {}) {
  const engine = window.engine;
  if (!engine || logClearing.value) return;
  if (logRefreshPromise) {
    if (options.silent) return;
    logLoading.value = true;
    try {
      await logRefreshPromise;
    } finally {
      logLoading.value = false;
    }
    return;
  }

  const generation = logRefreshGeneration;
  if (!options.silent) logLoading.value = true;
  const request = (async () => {
    try {
      const res = await engine.history.get(100, { currentSession: false });
      if (generation !== logRefreshGeneration) return;
      logRaw.value = Array.isArray(res?.records) ? res.records.map(asRecord) : [];
      logError.value = "";
    } catch (error: unknown) {
      if (generation !== logRefreshGeneration) return;
      logError.value = `无法获取历史记录：${errorMessage(error)}`;
    }
  })();
  logRefreshPromise = request;
  try {
    await request;
  } finally {
    if (logRefreshPromise === request) logRefreshPromise = null;
    if (!options.silent) logLoading.value = false;
  }
}

async function clearLogs() {
  const engine = window.engine;
  if (!engine || !logRaw.value.length) return;
  // #29 清空前必须确认，操作不可撤销
  const confirmed = await dialog.confirm({
    title: "清空历史记录",
    message: "此操作不可撤销，确定要清空全部历史记录吗？",
    kind: "warning",
    confirmText: "清空",
  });
  if (!confirmed) return;
  logRefreshGeneration += 1;
  logClearing.value = true;
  logError.value = "";
  try {
    const res = await engine.history.clear();
    if (!res?.cleared) throw new Error(res?.error || "历史记录未能写入磁盘");
    logRaw.value = [];
    resetLogVisibleCount();
    toast.success("已清空历史记录");
  } catch (e: unknown) {
    const message = errorMessage(e);
    logError.value = `清空历史记录失败：${message}`;
    toast.error("清空失败：" + message);
  } finally {
    logClearing.value = false;
  }
}

async function exportLogsTxt() {
  if (!filteredLogLines.value.length) return;
  // #33 导出加 try/catch，失败时 toast 提示
  try {
    const text = filteredLogLines.value.join("\n");
    await window.electronAPI?.saveFile({
      content: text,
      defaultName: `file-toolbox-logs-${new Date().toISOString().slice(0, 10)}.txt`,
      filters: [{ name: "文本文件", extensions: ["txt"] }],
    });
  } catch (e: unknown) {
    toast.error("导出失败：" + errorMessage(e));
  }
}

async function exportLogsJson() {
  if (!filteredLogRaw.value.length) return;
  // #33 导出加 try/catch，失败时 toast 提示
  try {
    const text = JSON.stringify(filteredLogRaw.value, null, 2);
    await window.electronAPI?.saveFile({
      content: text,
      defaultName: `file-toolbox-logs-${new Date().toISOString().slice(0, 10)}.json`,
      filters: [{ name: "JSON 文件", extensions: ["json"] }],
    });
  } catch (e: unknown) {
    toast.error("导出失败：" + errorMessage(e));
  }
}

async function checkUpdate() {
  const updateApi = window.electronAPI?.update;
  if (!updateApi || updateBusy.value) return;
  activeSettingsTab.value = "updates";
  try {
    updateStatus.value = await updateApi.check();
  } catch (e: unknown) {
    updateStatus.value = { ...updateStatus.value, state: "error", error: errorMessage(e, "检查更新失败") };
  }
}

function handleUpdatePrimaryAction() {
  checkUpdate();
}

async function downloadUpdate() {
  const updateApi = window.electronAPI?.update;
  if (!updateApi || updateBusy.value) return;
  try {
    updateStatus.value = await updateApi.download();
  } catch (e: unknown) {
    updateStatus.value = { ...updateStatus.value, state: "error", error: errorMessage(e, "下载更新失败") };
  }
}

async function openUpdateRelease() {
  const url = updateStatus.value.url || "https://github.com/LXL2000927/file-toolbox/releases";
  try {
    await window.electronAPI?.openExternal(url);
  } catch (e: unknown) {
    updateStatus.value = { ...updateStatus.value, state: "error", error: errorMessage(e, "无法打开发布页") };
  }
}

async function installUpdate() {
  const updateApi = window.electronAPI?.update;
  if (!updateApi || updateStatus.value.state !== "downloaded") return;
  const confirmed = await dialog.confirm({
    title: "重启并安装更新",
    message: "应用将退出并安装新版本。正在运行的文件处理任务会被终止，确定继续吗？",
    kind: "warning",
    confirmText: "重启并安装",
  });
  if (!confirmed) return;
  try {
    const result = await updateApi.install();
    updateStatus.value = result.status;
  } catch (e: unknown) {
    updateStatus.value = { ...updateStatus.value, state: "error", error: errorMessage(e, "无法启动安装程序") };
  }
}

function formatBytes(value: unknown) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function openGithub(e: Event) {
  e.preventDefault();
  window.electronAPI?.openExternal("https://github.com/LXL2000927/file-toolbox");
}

async function openDataDir() {
  if (!window.electronAPI) return;
  dataDirMsg.value = "";
  try {
    const dir = await window.electronAPI.openDataDir();
    dataDirMsg.value = `已打开数据目录：${dir}`;
  } catch (e: unknown) {
    dataDirMsg.value = `无法打开数据目录：${errorMessage(e)}`;
  }
}
</script>

<template>
  <div class="about-shell panel-shell panel-shell-responsive">
    <div
      class="settings-tabs segmented-control segmented-animated"
      role="tablist"
      aria-label="设置分组"
      :style="{ '--segment-count': settingsTabs.length, '--active-index': activeSettingsTabIndex }"
    >
      <button
        v-for="tab in settingsTabs"
        :key="tab.key"
        class="settings-tab segmented-item"
        :class="{ active: activeSettingsTab === tab.key }"
        type="button"
        role="tab"
        :aria-selected="activeSettingsTab === tab.key"
        @click="activeSettingsTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <div class="settings-content">
      <Transition name="settings-view" mode="out-in">
      <div v-if="activeSettingsTab === 'logs'" key="logs" class="settings-section logs-section glass-card section-card">
        <div class="logs-toolbar section-toolbar">
          <button class="log-tool-button log-refresh-button" @click="() => refreshLogs()" :disabled="logLoading || logClearing">
            {{ logLoading ? "刷新中…" : "刷新" }}
          </button>
          <AppSelect class="log-filter-select" v-model="logLevelFilter" :options="logLevelOptions" min-width="128px" />
          <AppSelect class="log-filter-select" v-model="logSourceFilter" :options="logSourceOptions" min-width="136px" />
          <input v-model="logSearch" class="input log-search" placeholder="搜索日志..." />
          <button class="log-tool-button" :disabled="!logRaw.length || logClearing" @click="clearLogs">
            {{ logClearing ? "清空中…" : "清空" }}
          </button>
          <button class="log-tool-button" :disabled="!filteredLogLines.length" @click="exportLogsTxt">导出 TXT</button>
          <button class="log-tool-button" :disabled="!filteredLogRaw.length" @click="exportLogsJson">导出 JSON</button>
          <button class="log-tool-button" @click="openDataDir">数据目录</button>
        </div>
        <div v-if="dataDirMsg" class="settings-message">{{ dataDirMsg }}</div>
        <div v-if="logError" class="settings-message log-error" role="alert">{{ logError }}</div>

        <div ref="logViewerRef" class="log-viewer">
          <div v-if="!filteredLogItems.length && !logLoading" class="log-placeholder">
            {{ logRaw.length ? "没有符合筛选条件的日志" : logError ? "当前无法显示历史记录" : "暂无操作记录" }}
          </div>
          <div v-else class="log-record-list selectable">
            <pre
              v-for="(item, index) in visibleLogItems"
              :key="`${item.record?.timestamp || index}-${index}`"
              class="log-text log-record"
              :class="`log-record-${item.level}`"
            >{{ item.text }}</pre>
            <div
              v-if="hasMoreLogs"
              :ref="setupLogSentinel"
              class="log-sentinel"
            >
              加载更多…
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="activeSettingsTab === 'updates'" key="updates" class="settings-section updates-section glass-card section-card">
        <div class="app-brand compact">
          <span class="app-logo"><AppIcon name="package" :size="28" /></span>
          <div>
            <span class="app-name">File Toolbox</span>
            <div class="version-pills">
              <span class="version-pill">v{{ updateStatus.current || '未知' }}</span>
              <span class="version-pill version-pill-sub">{{ updatePackageLabel }}</span>
            </div>
          </div>
          <span class="flex-1" />
          <button class="btn btn-primary btn-sm" @click="handleUpdatePrimaryAction" :disabled="updateBusy || updateStatus.state === 'downloaded'">
            {{ updateCheckButtonLabel }}
          </button>
        </div>

        <div
          v-if="updateMessage && ['checking', 'downloading', 'downloaded', 'installing', 'error'].includes(updateStatus.state)"
          class="update-status"
          :class="updateStatusTone"
          :role="updateStatus.state === 'error' ? 'alert' : 'status'"
          aria-live="polite"
        >
          <span v-if="['checking', 'downloading', 'installing'].includes(updateStatus.state)" class="spinner"></span>
          <AppIcon v-else-if="updateStatusTone === 'ok'" name="success" :size="14" />
          <AppIcon v-else-if="updateStatusTone === 'error'" name="alert" :size="14" />
          <AppIcon v-else name="info" :size="14" />
          {{ updateMessage }}
        </div>

        <Transition name="update-state" mode="out-in">
        <div v-if="updateHasRelease" class="release-panel has-update">
          <div class="release-topline">
            <span class="release-badge" :class="{ current: updateStatus.state === 'downloaded' }">
              {{ updateStatus.state === 'downloaded' ? '已准备安装' : updateStatus.state === 'downloading' ? '正在下载' : updateStatus.state === 'installing' ? '正在安装' : '发现新版本' }}
            </span>
            <span class="release-versions">当前 {{ updateStatus.current || '未知' }} → 最新 {{ updateStatus.latest || '未知' }}</span>
          </div>
          <h4>{{ updateStatus.name || `File Toolbox ${updateStatus.latest || ''}` }}</h4>

          <div v-if="updateStatus.state === 'downloading'" class="update-download-progress">
            <div class="progress">
              <div class="progress-bar" :style="{ width: `${updatePercent}%` }" />
            </div>
            <div class="update-progress-meta">
              <strong>{{ updatePercent }}%</strong>
              <span>
                {{ formatBytes(updateStatus.transferred) }} / {{ formatBytes(updateStatus.total) }}
                <template v-if="Number(updateStatus.bytesPerSecond) > 0"> · {{ formatBytes(updateStatus.bytesPerSecond) }}/s</template>
              </span>
            </div>
          </div>

          <div v-if="updateStatus.body" class="release-body selectable" v-html="releaseBodyHtml" />
          <p v-else class="settings-hint">该版本暂未提供更新说明。</p>
          <div class="release-actions">
            <button
              v-if="updateStatus.state === 'available' && updateStatus.supported"
              class="btn btn-primary"
              type="button"
              @click="downloadUpdate"
            >立即下载</button>
            <button
              v-else-if="updateStatus.state === 'available'"
              class="btn btn-primary"
              type="button"
              @click="openUpdateRelease"
            >打开发布页</button>
            <button
              v-else-if="updateStatus.state === 'downloaded'"
              class="btn btn-primary"
              type="button"
              @click="installUpdate"
            >重启并安装</button>
          </div>
        </div>

        <div v-else-if="updateStatus.state === 'up-to-date'" class="release-panel current-version">
          <div class="release-topline">
            <span class="release-badge current">当前已是最新版本</span>
            <span class="release-versions">v{{ updateStatus.current || '未知' }}</span>
          </div>
          <p v-if="updateStatus.latest">最新正式版本为 v{{ updateStatus.latest }}，无需更新。</p>
          <p v-else>未发现可用的正式更新。</p>
        </div>

        <div v-else-if="updateStatus.state === 'unsupported'" class="release-panel update-unsupported">
          <div class="release-topline">
            <span class="release-badge manual">{{ updatePackageLabel }}</span>
            <span class="release-versions">v{{ updateStatus.current || '未知' }}</span>
          </div>
          <h4>{{ updatePackageLabel }}更新方式</h4>
          <p>{{ updateStatus.error }}</p>
        </div>

        <div v-else-if="updateStatus.state === 'error'" class="release-panel update-error-panel">
          <div class="release-topline">
            <span class="release-badge error">更新失败</span>
            <span class="release-versions">v{{ updateStatus.current || '未知' }}</span>
          </div>
          <p>{{ updateStatus.error || '无法检查更新，请稍后重试。' }}</p>
          <div class="release-actions">
            <button class="btn btn-primary" type="button" @click="checkUpdate">重新检查</button>
          </div>
        </div>

        <div v-else-if="updateStatus.state === 'idle' || updateStatus.state === 'checking'" class="update-empty-card">
          <span class="update-empty-icon"><AppIcon name="package" :size="28" /></span>
          <div class="update-empty-title">版本检查</div>
          <p>当前版本 v{{ updateStatus.current || '未知' }}</p>
        </div>
        </Transition>
      </div>

      <div v-else key="about" class="settings-section glass-card section-card about-section">
        <div class="about-hero">
          <span class="app-logo"><AppIcon name="package" :size="28" /></span>
          <div class="about-hero-info">
            <span class="app-name">File Toolbox</span>
            <div class="version-pills">
              <span class="version-pill">v{{ updateStatus.current || '未知' }}</span>
              <span class="version-pill version-pill-sub">Electron</span>
              <span class="version-pill version-pill-sub">Python Engine</span>
            </div>
          </div>
          <a href="#" class="about-github-link" @click.prevent="openGithub" title="在 GitHub 上查看源码">
            GitHub · LXL2000927/file-toolbox
          </a>
        </div>

        <p class="about-lead">
          File Toolbox 是一个 Electron + Vue 3 + Python 的本地文件处理工具箱，聚焦批量重命名、PDF 普通拆分和扫描件自动拆分。应用通过桌面界面组织任务，通过 Python 引擎在本机完成实际文件处理。
        </p>

        <div class="about-feature-grid">
          <article v-for="(feature, index) in aboutFeatures" :key="feature.label" class="about-feature-card" :class="`accent-${index + 1}`">
            <span class="about-feature-label">{{ feature.label }}</span>
            <h4>{{ feature.title }}</h4>
            <p>{{ feature.description }}</p>
          </article>
        </div>

        <div class="about-info-grid">
          <section class="about-info-card span-2">
            <h4>处理架构</h4>
            <ul class="arch-list">
              <li v-for="item in aboutArchitecture" :key="item">{{ item }}</li>
            </ul>
          </section>
          <section class="about-info-card">
            <h4>本地与隐私</h4>
            <p>
              文件处理在本机完成，不需要上传到第三方服务器。检查或下载更新时，应用会连接 GitHub Release 获取版本信息和安装包。
            </p>
          </section>
          <section class="about-info-card">
            <h4>使用建议</h4>
            <ul class="tips-list">
              <li v-for="tip in aboutTips" :key="tip">{{ tip }}</li>
            </ul>
          </section>
        </div>
      </div>
      </Transition>
    </div>

  </div>
</template>

<style scoped>
.settings-tabs {
  align-items: center;
  flex-shrink: 0;
  align-self: flex-start;
}
.settings-tab {
  min-width: 74px;
  height: 30px;
  padding: 0 12px;
  font-size: var(--font-md);
  font-weight: 600;
}
.settings-content {
  flex: 1;
  min-height: 0;
  display: flex;
  position: relative;
}
.settings-section {
  flex: 1;
  overflow: auto;
}
.updates-section {
  display: flex;
  flex-direction: column;
  padding: 12px 14px;
}

.settings-view-enter-active,
.settings-view-leave-active {
  transition: opacity 0.14s ease, transform 0.16s cubic-bezier(0.2, 0.8, 0.2, 1);
}
.settings-view-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.settings-view-leave-to {
  opacity: 0;
  transform: translateY(-2px);
}

/* 左：应用介绍 */
.app-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.app-brand.compact {
  margin-bottom: 0;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--color-border);
}
.app-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius);
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
}
.app-name {
  font-size: var(--font-lg);
  font-weight: 700;
  color: var(--color-gray-900);
}
.version-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.version-pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: var(--font-sm);
  font-weight: 600;
  background: var(--color-primary-bg);
  color: var(--color-primary-dark);
}
.version-pill-sub {
  background: var(--color-gray-100);
  color: var(--color-gray-600);
  font-weight: 500;
}
.about-hero {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.about-hero-info {
  flex: 1;
  min-width: 0;
}
.about-github-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--color-border-strong);
  background: rgba(255, 255, 255, 0.54);
  color: var(--color-gray-700);
  font-size: var(--font-sm);
  font-weight: 500;
  text-decoration: none;
  transition: background-color var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
  white-space: nowrap;
}
.about-github-link:hover {
  border-color: var(--color-primary);
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
}
.about-lead {
  margin: 0 0 12px;
  color: var(--color-gray-700);
  font-size: var(--font-md);
  line-height: 1.7;
}
.about-feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.about-feature-card {
  position: relative;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.34);
  overflow: hidden;
}
.about-feature-card::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  background: var(--color-primary);
  opacity: 0.88;
}
.about-feature-label {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0;
  color: var(--color-primary-dark);
  font-size: var(--font-sm);
  font-weight: 700;
}
.about-feature-card h4,
.about-info-card h4 {
  position: relative;
  margin: 8px 0 4px;
  color: var(--color-gray-900);
  font-size: var(--font-lg);
}
.about-feature-card p,
.about-info-card p {
  position: relative;
  margin: 0;
  color: var(--color-gray-600);
  font-size: var(--font-md);
  line-height: 1.6;
}
.about-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.about-info-card {
  padding: 10px 2px 2px;
  border-top: 1px solid var(--color-border);
}
.about-info-card.span-2 {
  grid-column: 1 / -1;
}
.about-info-card ul {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--color-gray-600);
  font-size: var(--font-md);
  line-height: 1.65;
}
.arch-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 24px;
  padding-left: 18px;
}
.tips-list {
  columns: 2;
  column-gap: 28px;
}
.tips-list li {
  break-inside: avoid;
  margin-bottom: 4px;
}
@media (max-width: 1100px) {
  .about-feature-grid,
  .about-info-grid {
    grid-template-columns: 1fr;
  }

  .tips-list {
    columns: 1;
  }

  .arch-list {
    grid-template-columns: 1fr;
  }
}
.settings-message {
  margin: 0 0 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
  font-size: var(--font-md);
}
.settings-hint {
  color: var(--color-gray-500);
  font-size: var(--font-sm);
}

/* 更新页 */
.update-status {
  font-size: var(--font-sm);
  min-height: 32px;
  padding: 6px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.update-status.loading { border-color: var(--color-border); background: var(--color-gray-50); color: var(--color-gray-600); }
.update-status.error { border-color: color-mix(in srgb, var(--color-danger) 22%, transparent); background: color-mix(in srgb, var(--color-danger-bg) 42%, transparent); color: var(--color-danger); }
.update-status.ok { border-color: color-mix(in srgb, var(--color-success) 20%, transparent); background: color-mix(in srgb, var(--color-success-bg) 38%, transparent); color: var(--color-success); }
.update-status.info { border-color: rgba(37, 99, 235, 0.16); background: color-mix(in srgb, var(--color-primary-bg) 54%, transparent); color: var(--color-primary-dark); }
.release-panel {
  flex: 1;
  min-height: 0;
  margin-top: 12px;
  padding: 16px 2px 2px;
  border-top: 1px solid var(--color-border);
  background: transparent;
}
.update-empty-card {
  flex: 1;
  min-height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
  padding: 24px;
  border-top: 1px solid var(--color-border);
  background: transparent;
  text-align: center;
}
.update-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: 2px;
  border-radius: var(--radius);
  color: var(--color-primary);
  background: var(--color-primary-bg);
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.08);
}
.release-panel.has-update {
  display: flex;
  flex-direction: column;
}
.release-panel.current-version {
  border-color: var(--color-border);
}
.release-panel.update-unsupported {
  border-color: var(--color-border);
}
.release-panel.update-error-panel {
  border-color: color-mix(in srgb, var(--color-danger) 24%, var(--color-border));
}
.release-topline {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.release-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 9px;
  border-radius: 999px;
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
  font-size: var(--font-xs);
  font-weight: 800;
}
.release-badge.current {
  color: var(--color-success);
  background: var(--color-success-bg);
}
.release-badge.manual {
  color: var(--color-gray-700);
  background: var(--color-gray-100);
}
.release-badge.error {
  color: var(--color-danger);
  background: var(--color-danger-bg);
}
.release-versions {
  color: var(--color-gray-600);
  font-size: var(--font-sm);
}
.release-panel h4,
.update-empty-title {
  margin: 0 0 8px;
  color: var(--color-gray-900);
  font-size: var(--font-lg);
}
.release-panel p,
.update-empty-card p {
  margin: 0;
  color: var(--color-gray-600);
  font-size: var(--font-sm);
  line-height: 1.65;
}
.update-empty-card p {
  max-width: 360px;
  margin-bottom: 4px;
}
.release-panel h4 {
  flex: 0 0 auto;
}
.release-body {
  flex: 1;
  min-height: 0;
  margin: 12px -4px 0;
  padding: 0 4px 4px;
  overflow: auto;
  color: var(--color-gray-700);
  font-size: var(--font-sm);
  line-height: 1.65;
}
.release-body :deep(h1),
.release-body :deep(h2),
.release-body :deep(h3) {
  margin: 10px 0 4px;
  font-size: var(--font-md);
  font-weight: 700;
  color: var(--color-gray-800);
}
.release-body :deep(h1):first-child,
.release-body :deep(h2):first-child {
  margin-top: 0;
}
.release-body :deep(ul) {
  margin: 4px 0 8px;
  padding-left: 18px;
}
.release-body :deep(li) {
  margin-bottom: 3px;
}
.release-body :deep(p) {
  margin: 4px 0;
}
.release-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
  flex: 0 0 auto;
  margin-top: 12px;
}
.update-state-enter-active,
.update-state-leave-active {
  transition: opacity 0.14s ease, transform 0.16s cubic-bezier(0.2, 0.8, 0.2, 1);
}
.update-state-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.update-state-leave-to {
  opacity: 0;
  transform: translateY(-2px);
}
.update-download-progress {
  margin: 10px 0 2px;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.52);
}
.update-download-progress .progress {
  height: 7px;
}
.update-progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 7px;
  color: var(--color-gray-600);
  font-size: var(--font-sm);
}
.update-progress-meta strong {
  color: var(--color-primary-dark);
  font-variant-numeric: tabular-nums;
}
@media (max-width: 520px) {
  .updates-section .app-brand {
    flex-wrap: wrap;
  }

  .updates-section .app-brand > .btn {
    width: 100%;
  }

  .update-progress-meta {
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }

  .updates-section .release-actions .btn {
    flex: 1 1 120px;
  }
}
.spinner {
  width: 14px; height: 14px;
  border: 2px solid var(--color-gray-300);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}
@keyframes spin { to { transform: rotate(360deg); } }
.log-viewer {
  flex: 1;
  min-height: 140px;
  border: 1px solid rgba(148, 163, 184, 0.42);
  border-radius: var(--radius);
  background: rgba(248, 250, 252, 0.78);
  padding: 0;
  overflow-y: auto;
}
.logs-section {
  overflow: hidden;
}
.logs-toolbar {
  flex-wrap: nowrap;
  gap: 8px;
}
.log-tool-button {
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  padding: 0 10px;
  border: 1px solid rgba(148, 163, 184, 0.36);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.66);
  color: var(--color-gray-600);
  font-size: var(--font-sm);
  font-weight: 600;
  letter-spacing: 0.01em;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
  transform: translateY(0) scale(1);
  transition: background-color 0.14s ease, border-color 0.14s ease, color 0.14s ease, box-shadow 0.14s ease, transform 0.14s ease;
  white-space: nowrap;
}
.log-tool-button:hover:not(:disabled) {
  border-color: rgba(100, 116, 139, 0.44);
  background: rgba(255, 255, 255, 0.88);
  color: var(--color-gray-800);
  transform: translateY(-1px) scale(1.005);
  box-shadow: var(--shadow-sm);
}
.log-tool-button:active:not(:disabled) {
  transform: translateY(0) scale(0.985);
  transition-duration: 0.06s;
  box-shadow: inset 0 1px 3px rgba(15, 23, 42, 0.10), 0 1px 2px rgba(15, 23, 42, 0.04);
}
.log-tool-button:disabled {
  cursor: default;
  opacity: 0.48;
  box-shadow: none;
}
.log-filter-select {
  flex: 0 0 132px;
}
.logs-toolbar .log-filter-select:nth-of-type(2) {
  flex-basis: 140px;
}
.log-search {
  flex: 1 1 180px;
  min-width: 140px;
  height: 30px;
}
.log-error {
  color: var(--color-danger);
  background: var(--color-danger-bg);
  border: 1px solid color-mix(in srgb, var(--color-danger) 24%, transparent);
}
.log-placeholder {
  padding: 12px;
  color: var(--color-gray-500);
  font-size: var(--font-md);
}
.log-record-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.log-sentinel {
  text-align: center;
  padding: 8px;
  font-size: var(--font-sm);
  color: var(--color-gray-400);
}
.log-text {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Consolas, monospace;
  font-size: 13px;
  line-height: 1.62;
  color: var(--color-gray-900);
  white-space: pre-wrap;
  word-break: break-word;
}
.log-record {
  padding: 9px 12px 9px 14px;
  border: 0;
  border-bottom: 1px solid rgba(148, 163, 184, 0.20);
  border-left: 3px solid transparent;
  border-radius: 0;
  background: rgba(255, 255, 255, 0.34);
  content-visibility: auto;
  contain-intrinsic-size: auto 80px;
}
.log-record-success {
  border-left-color: var(--color-success);
  background: color-mix(in srgb, var(--color-success-bg) 48%, white);
}
.log-record-warning {
  border-left-color: #f59e0b;
  background: color-mix(in srgb, #fef3c7 56%, white);
}
.log-record-error {
  border-left-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger-bg) 52%, white);
}
.log-record-debug {
  border-left-color: #7c3aed;
  background: color-mix(in srgb, rgba(124, 58, 237, 0.12) 62%, white);
}
.log-record-info {
  border-left-color: var(--color-info);
  background: color-mix(in srgb, rgba(8, 145, 178, 0.10) 58%, white);
}

@media (max-width: 900px) {
  .logs-toolbar {
    flex-wrap: wrap;
    align-items: stretch;
  }

  .log-filter-select {
    flex: 1 1 128px;
    min-width: 112px !important;
  }

  .logs-toolbar .log-filter-select:nth-of-type(2) {
    flex-basis: 136px;
  }

  .log-search {
    flex: 1 1 220px;
  }
}

@media (max-width: 520px) {
  .logs-toolbar {
    gap: 6px;
  }

  .log-search {
    flex: 1 1 100%;
    min-width: 0;
  }

  .log-tool-button {
    flex: 1 1 68px;
    min-width: 0;
    padding-inline: 6px;
  }

  .log-refresh-button {
    flex-grow: 0;
  }

  .log-viewer {
    padding: 8px;
  }

  .log-text {
    font-size: 12px;
  }
}

</style>
