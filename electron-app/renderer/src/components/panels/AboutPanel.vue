<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, watch } from "vue";
import type { UpdateCheckResult } from "../../env";
import AppIcon from "../common/AppIcon.vue";
import AppSelect from "../common/AppSelect.vue";
import { marked } from "marked";

type SettingsTab = "logs" | "updates" | "about";
type LogLevelFilter = "all" | "error" | "warning" | "success" | "info" | "debug";
type LogSourceFilter = "all" | "rename" | "pdf_split" | "scan_split" | "system";

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

const updateState = ref<"idle" | "loading" | "error">("idle");
const updateMsg = ref("");
const updateResult = ref<UpdateCheckResult | null>(null);
const releaseBodyHtml = computed(() => {
  const body = updateResult.value?.body;
  if (!body) return "";
  const html = marked.parse(body, { async: false }) as string;
  const template = document.createElement("template");
  template.innerHTML = html;
  template.content.querySelectorAll("script, style, iframe, object, embed, form, input, button, textarea, select, link, meta").forEach((node) => node.remove());
  template.content.querySelectorAll("*").forEach((node) => {
    for (const attr of Array.from(node.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();
      if (name.startsWith("on") || name === "srcdoc" || name === "style") {
        node.removeAttribute(attr.name);
        continue;
      }
      if ((name === "href" || name === "src") && !/^(https?:|mailto:|#|\/)/i.test(value)) {
        node.removeAttribute(attr.name);
      }
    }
    if (node.tagName.toLowerCase() === "a") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
  return template.innerHTML;
});
const dataDirMsg = ref("");
const activeSettingsTab = ref<SettingsTab>("logs");

const logLines = ref<string[]>([]);
const logRaw = ref<any[]>([]);
const logLoading = ref(false);
const logLevelFilter = ref<LogLevelFilter>("all");
const logSourceFilter = ref<LogSourceFilter>("all");
const logSearch = ref("");
let logAutoRefreshTimer: number | null = null;

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

function formatLogRecord(record: any) {
  const _msg = record?.message || record?.description || "日志记录";
  const _src = normalizeLogSource(record?.source || record?.operation_type);
  const _status = record?.success === true ? "成功" : record?.success === false ? "失败" : "未知";
  const details = record?.details && typeof record.details === "object" ? record.details : {};
  const detailLogs = Array.isArray(details.log_tail) ? details.log_tail.map((line: unknown) => String(line)).filter(Boolean) : [];
  const perfText = formatPerformanceStats(details.performance_stats);
  const detailText = detailLogs.length ? `\n  详细日志\n${detailLogs.map((line: string) => `    ${line}`).join("\n")}` : "";
  const metaText = formatLogMeta(details);
  const _rawText = `[${record.timestamp}] [${_src}] [_LEVEL_] ${_msg} · ${_status}` +
    metaText +
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

function formatLogMeta(details: any) {
  const parts: string[] = [];
  if (Array.isArray(details.marker_pages)) parts.push(`标记页 ${details.marker_pages.length}`);
  if (Array.isArray(details.output_files) && details.output_files.length) parts.push(`输出 ${details.output_files.length}`);
  if (Number.isFinite(Number(details.elapsed_ms))) parts.push(`耗时 ${formatDuration(Number(details.elapsed_ms) / 1000)}`);
  return parts.length ? `\n  摘要：${parts.join(" · ")}` : "";
}

function formatPerformanceStats(stats: any) {
  if (!stats || typeof stats !== "object") return "";
  const lines = [
    `扫描：${formatDuration(stats.scan_seconds)} · ${Number(stats.pages_scanned || 0)} 页 · 命中 ${Number(stats.markers_found || 0)} 页`,
    `阶段：渲染 ${formatDuration(stats.render_seconds)} · 二维码 ${formatDuration(stats.qr_seconds)} · 印章 ${formatDuration(stats.stamp_seconds)} · 特征 ${formatDuration(stats.feature_seconds)}`,
    `辅助：DPI兜底 ${formatDuration(stats.dpi_fallback_seconds)}（${Number(stats.dpi_fallback_hits || 0)}/${Number(stats.dpi_fallback_attempts || 0)}） · 分段 ${formatDuration(stats.build_seconds)} · 写入 ${formatDuration(stats.write_seconds)}`,
  ];
  if (Number(stats.roi_clip_pages || 0) || Number(stats.roi_clip_fallback_pages || 0)) {
    lines.push(`ROI：局部渲染 ${Number(stats.roi_clip_pages || 0)} 页 · 回退 ${Number(stats.roi_clip_fallback_pages || 0)} 页`);
  }
  if (Number(stats.total_seconds || 0)) {
    lines.push(`总计：${formatDuration(stats.total_seconds)}`);
  }
  return `\n  耗时统计\n${lines.map((line) => `    ${line}`).join("\n")}`;
}

function normalizeLogLevel(value: unknown, fallbackText: string, record?: any): Exclude<LogLevelFilter, "all"> {
  const level = String(value || "").toLowerCase();
  if (["error", "warning", "success", "info", "debug"].includes(level)) return level as Exclude<LogLevelFilter, "all">;
  return classifyLogLevel(fallbackText, record || {});
}

function normalizeLogSource(operationType: string): LogSourceFilter {
  const type = String(operationType || "").toLowerCase();
  if (type.includes("rename")) return "rename";
  if (type.includes("pdf_split")) return "pdf_split";
  if (type.includes("scan_split")) return "scan_split";
  return "system";
}

function classifyLogLevel(line: string, record: any): Exclude<LogLevelFilter, "all"> {
  if (record?.success === false) return "error";
  if (record?.success === true) return "success";
  if (/失败|错误|异常|超时|不可用|不存在|未生成/.test(line)) return "error";
  if (/未匹配|未命中|漏检|忽略|降级|重试|警告|未框选/.test(line)) return "warning";
  if (/候选|面积|圆度|内点|比例|样本|匹配\s*\d+|解码/.test(line)) return "debug";
  if (/完成|命中|生成|成功|识别到/.test(line)) return "success";
  return "info";
}

const logItems = computed(() => logRaw.value.map((record) => {
  const text = formatLogRecord(record);
  return {
    text,
    level: normalizeLogLevel(record?.level, text, record),
    source: normalizeLogSource(record?.source || record?.operation_type),
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

onMounted(() => {
  refreshLogs();
  startLogAutoRefresh();
});

onBeforeUnmount(() => stopLogAutoRefresh());

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
    if (!logLoading.value) refreshLogs({ silent: true });
  }, 2000);
}

function stopLogAutoRefresh() {
  if (!logAutoRefreshTimer) return;
  window.clearInterval(logAutoRefreshTimer);
  logAutoRefreshTimer = null;
}

async function refreshLogs(options: { silent?: boolean } = {}) {
  if (!window.engine) return;
  if (!options.silent) logLoading.value = true;
  try {
    const res = await window.engine.history.get(100, { currentSession: false });
    const records = res?.records || [];
    logRaw.value = records;
    logLines.value = records.map(formatLogRecord);
  } catch {
    logLines.value = ["（无法获取历史记录）"];
    logRaw.value = [];
  } finally {
    if (!options.silent) logLoading.value = false;
  }
}

async function clearLogs() {
  if (!window.engine || !logRaw.value.length) return;
  try {
    const res = await window.engine.history.clear();
    if (res?.cleared) {
      logRaw.value = [];
      logLines.value = [];
    }
  } catch {
    logLines.value = ["（清空历史记录失败）"];
  }
}

async function exportLogsTxt() {
  if (!filteredLogLines.value.length) return;
  const text = filteredLogLines.value.join("\n");
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `file-toolbox-logs-${new Date().toISOString().slice(0, 10)}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}

async function exportLogsJson() {
  if (!filteredLogRaw.value.length) return;
  const text = JSON.stringify(filteredLogRaw.value, null, 2);
  const blob = new Blob([text], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `file-toolbox-logs-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

const MAX_RETRY = 3;
let retryCount = 0;

function isRateLimitError(msg: string): boolean {
  const lower = (msg || "").toLowerCase();
  return lower.includes("rate limit") || lower.includes("403") || lower.includes("频率");
}

async function checkUpdate() {
  if (!window.electronAPI) return;
  retryCount = 0;
  activeSettingsTab.value = "updates";
  updateResult.value = null;
  updateState.value = "loading";
  updateMsg.value = "正在连接 GitHub 检查更新...";
  await attemptCheckUpdate();
}

async function attemptCheckUpdate() {
  if (!window.electronAPI) return;
  try {
    const r = await window.electronAPI.checkUpdate();
    updateResult.value = r;
    if (!r.ok) {
      retryCount++;
      if (isRateLimitError(r.error || "")) {
        updateState.value = "idle";
        updateMsg.value = `GitHub API 访问频率超限，请稍后再试。（${r.error}）`;
        return;
      }
      if (retryCount < MAX_RETRY) {
        updateMsg.value = `${r.error || "检查失败"} 正在重试 (${retryCount}/${MAX_RETRY})…`;
        await delay(1200);
        return attemptCheckUpdate();
      }
      updateState.value = "error";
      updateMsg.value = `${r.error || "检查失败"}（已重试 ${MAX_RETRY} 次）`;
    } else if (r.hasUpdate) {
      updateState.value = "idle";
      updateMsg.value = `发现新版本 ${r.latest}（当前 ${r.current}）`;
    } else {
      updateState.value = "idle";
      updateMsg.value = `已是最新版本 ${r.current}`;
    }
  } catch (e: any) {
    retryCount++;
    if (retryCount < MAX_RETRY) {
      updateMsg.value = `${e?.message || "网络请求失败"} 正在重试 (${retryCount}/${MAX_RETRY})…`;
      await delay(1200);
      return attemptCheckUpdate();
    }
    updateState.value = "error";
    updateMsg.value = `${e?.message || "网络请求失败"}（已重试 ${MAX_RETRY} 次）`;
  }
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function openRelease(e: Event) {
  e.preventDefault();
  const url = updateResult.value?.url;
  if (url) window.electronAPI?.openExternal(url);
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
  } catch (e: any) {
    dataDirMsg.value = `无法打开数据目录：${e?.message || "未知错误"}`;
  }
}
</script>

<template>
  <div class="about-shell panel-shell panel-shell-responsive">
    <div class="settings-tabs segmented-control" role="tablist" aria-label="设置分组">
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
      <div v-if="activeSettingsTab === 'logs'" class="settings-section glass-card section-card">
        <div class="logs-toolbar section-toolbar">
          <button class="log-tool-button" @click="() => refreshLogs()" :disabled="logLoading">
            {{ logLoading ? "刷新中…" : "刷新" }}
          </button>
          <AppSelect class="log-filter-select" v-model="logLevelFilter" :options="logLevelOptions" min-width="128px" />
          <AppSelect class="log-filter-select" v-model="logSourceFilter" :options="logSourceOptions" min-width="136px" />
          <input v-model="logSearch" class="input log-search" placeholder="搜索日志..." />
          <button class="log-tool-button" :disabled="!logRaw.length" @click="clearLogs">清空</button>
          <button class="log-tool-button" :disabled="!filteredLogLines.length" @click="exportLogsTxt">导出 TXT</button>
          <button class="log-tool-button" :disabled="!filteredLogRaw.length" @click="exportLogsJson">导出 JSON</button>
          <button class="log-tool-button" @click="openDataDir">数据目录</button>
        </div>
        <div v-if="dataDirMsg" class="settings-message">{{ dataDirMsg }}</div>

        <div class="log-viewer">
          <div v-if="!filteredLogItems.length && !logLoading" class="log-placeholder">
            {{ logLines.length ? "没有符合筛选条件的日志" : "暂无操作记录" }}
          </div>
          <div v-else class="log-record-list selectable">
            <pre
              v-for="(item, index) in filteredLogItems"
              :key="`${item.record?.timestamp || index}-${index}`"
              class="log-text log-record"
              :class="`log-record-${item.level}`"
            >{{ item.text }}</pre>
          </div>
        </div>
      </div>

      <div v-else-if="activeSettingsTab === 'updates'" class="settings-section glass-card section-card">
        <div class="app-brand compact">
          <span class="app-logo"><AppIcon name="package" :size="28" /></span>
          <div>
            <span class="app-name">File Toolbox</span>
            <div class="version-pills">
              <span class="version-pill">v2.0.0</span>
              <span class="version-pill version-pill-sub">Electron</span>
            </div>
          </div>
          <span class="flex-1" />
          <button class="btn btn-primary btn-sm" @click="checkUpdate" :disabled="updateState === 'loading'">
            {{ updateState === 'loading' ? '检查中…' : '检查更新' }}
          </button>
        </div>

        <div
          v-if="updateMsg"
          class="update-status"
          :class="{ loading: updateState === 'loading', error: updateState === 'error', ok: updateState === 'idle' }"
        >
          <span v-if="updateState === 'loading'" class="spinner"></span>
          {{ updateMsg }}
        </div>

        <div v-if="updateResult?.hasUpdate" class="release-panel has-update">
          <div class="release-topline">
            <span class="release-badge">发现新版本</span>
            <span class="release-versions">当前 {{ updateResult.current || '未知' }} → 最新 {{ updateResult.latest || '未知' }}</span>
          </div>
          <h4>{{ updateResult.name || `File Toolbox ${updateResult.latest || ''}` }}</h4>
          <div v-if="updateResult.body" class="release-body selectable" v-html="releaseBodyHtml" />
          <p v-else class="settings-hint">该版本暂未提供更新说明。</p>
          <div class="release-actions">
            <a :href="updateResult.url || '#'" class="btn btn-primary" @click="openRelease" target="_blank">
              前往下载
            </a>
          </div>
        </div>

        <div v-else-if="updateResult?.ok" class="release-panel current-version">
          <div class="release-topline">
            <span class="release-badge current">当前已是最新版本</span>
            <span class="release-versions">v{{ updateResult.current || '2.0.0' }}</span>
          </div>
          <p>无需更新。后续版本仍可在这里查看 Release 信息和下载入口。</p>
        </div>

        <div v-else-if="!updateMsg" class="update-empty-card">
          <div class="update-empty-title">版本检查</div>
          <p>点击“检查更新”连接 GitHub Release 获取最新版本信息。发现新版本后，更新说明会直接显示在这里。</p>
        </div>
      </div>

      <div v-else class="settings-section glass-card section-card about-section">
        <div class="about-hero">
          <span class="app-logo"><AppIcon name="package" :size="28" /></span>
          <div class="about-hero-info">
            <span class="app-name">File Toolbox</span>
            <div class="version-pills">
              <span class="version-pill">v2.0.0</span>
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
              文件处理在本机完成，不需要上传到第三方服务器。只有点击"检查更新"时，应用会请求 GitHub Release 信息用于版本检查。
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
}
.settings-section {
  flex: 1;
  overflow: auto;
}

/* 左：应用介绍 */
.app-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.app-brand.compact {
  margin-bottom: 12px;
}
.app-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  color: var(--color-primary-dark);
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
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.68), rgba(255, 255, 255, 0.32));
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
.about-feature-card.accent-2::before {
  background: var(--color-info);
}
.about-feature-card.accent-3::before {
  background: #7c3aed;
}
.about-feature-label {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 1px 8px;
  border-radius: 999px;
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
  font-size: var(--font-sm);
  font-weight: 700;
}
.about-feature-card.accent-2 .about-feature-label {
  color: var(--color-info);
  background: rgba(8, 145, 178, 0.08);
}
.about-feature-card.accent-3 .about-feature-label {
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.08);
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
  padding: 10px 12px;
  border: 1px solid rgba(203, 213, 225, 0.76);
  border-radius: var(--radius-lg);
  background: rgba(248, 250, 252, 0.62);
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

/* 右：运行日志 */
.update-status {
  font-size: var(--font-sm);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.update-status.loading { background: var(--color-gray-100); color: var(--color-gray-600); }
.update-status.error { background: var(--color-danger-bg); color: var(--color-danger); }
.update-status.ok { background: var(--color-success-bg); color: var(--color-success); }
.release-panel,
.update-empty-card {
  margin-top: 12px;
  padding: 14px;
  border-radius: var(--radius-lg);
  border: 1px solid rgba(203, 213, 225, 0.78);
  background:
    radial-gradient(circle at 94% 0%, rgba(37, 99, 235, 0.10), transparent 32%),
    rgba(255, 255, 255, 0.42);
}
.release-panel.has-update {
  border-color: rgba(37, 99, 235, 0.30);
  background:
    radial-gradient(circle at 94% 0%, rgba(37, 99, 235, 0.16), transparent 34%),
    linear-gradient(135deg, rgba(239, 246, 255, 0.82), rgba(255, 255, 255, 0.44));
}
.release-panel.current-version {
  border-color: rgba(22, 163, 74, 0.24);
  background:
    radial-gradient(circle at 94% 0%, rgba(22, 163, 74, 0.12), transparent 32%),
    rgba(255, 255, 255, 0.42);
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
.release-body {
  max-height: 280px;
  margin: 8px 0 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid rgba(203, 213, 225, 0.72);
  border-radius: var(--radius);
  background: rgba(248, 250, 252, 0.82);
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
  margin-top: 12px;
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
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.92), rgba(241, 245, 249, 0.68)),
    rgba(255, 255, 255, 0.34);
  padding: 12px;
  overflow-y: auto;
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
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(248, 250, 252, 0.46)),
    rgba(255, 255, 255, 0.34);
  color: var(--color-gray-600);
  font-size: var(--font-sm);
  font-weight: 600;
  letter-spacing: 0.01em;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72), 0 1px 2px rgba(15, 23, 42, 0.04);
  transform: translateY(0) scale(1);
  transition: background-color 0.14s ease, border-color 0.14s ease, color 0.14s ease, box-shadow 0.14s ease, transform 0.14s ease;
  white-space: nowrap;
}
.log-tool-button:hover:not(:disabled) {
  border-color: rgba(100, 116, 139, 0.44);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.84), rgba(241, 245, 249, 0.62)),
    rgba(255, 255, 255, 0.48);
  color: var(--color-gray-800);
  transform: translateY(-1px) scale(1.005);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.84), 0 4px 10px rgba(15, 23, 42, 0.07);
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
.log-placeholder {
  color: var(--color-gray-500);
  font-size: var(--font-md);
}
.log-record-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
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
  padding: 8px 10px 8px 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-left-width: 4px;
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.48);
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

</style>
