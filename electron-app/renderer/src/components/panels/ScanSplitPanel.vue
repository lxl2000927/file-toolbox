<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import type { ScanDetectionMode, ScanSplitOptions } from "../../env";
import { useEngineTask, generateTaskId } from "../../composables/useEngineTask";
import { useAppDialog } from "../../composables/useAppDialog";
import { positiveInt } from "../../utils";
import AppSelect from "../common/AppSelect.vue";
import AppIcon from "../common/AppIcon.vue";

type ScanSplitTaskResult = { output_files: string[]; marker_pages: number[]; total_pages: number; suspect_segments?: any[] };
type TuneResult = { title: string; lines: string[] };

const pdfPath = ref("");
const dialog = useAppDialog();
const referenceImage = ref("");
const outputDir = ref("");
const prefix = ref("");
const error = ref("");
const result = ref<ScanSplitTaskResult | null>(null);
const tuneResult = ref<TuneResult | null>(null);
const pdfPageCount = ref<number | null>(null);
const previewDataUrl = ref("");
const previewLoading = ref(false);
const previewError = ref("");
const keypointInfo = ref("");
const submitting = ref(false);
const previewStageRef = ref<HTMLDivElement | null>(null);
const previewImgRef = ref<HTMLImageElement | null>(null);
const roiImgRef = ref<HTMLImageElement | null>(null);
const roiStageRef = ref<HTMLElement | null>(null);
const dpiInputRef = ref<HTMLInputElement | null>(null);
const maxAttemptsInputRef = ref<HTMLInputElement | null>(null);
const previewNaturalSize = ref({ width: 0, height: 0 });
const roiNaturalSize = ref({ width: 0, height: 0 });
const selectionStart = ref<{ x: number; y: number } | null>(null);
const selectionDraft = ref<[number, number, number, number] | null>(null);
const roiDialogOpen = ref(false);
const roiDrawMode = ref(false);
const previewZoom = ref(1.0);

const opts = ref<Required<Omit<ScanSplitOptions, "reference_roi" | "use_roi">> & { reference_roi: [number, number, number, number] | null }>({
  detection_mode: "auto",
  dpi: 220,
  qrcode_text_contains: "",
  qrcode_no_decode: false,
  qrcode_use_roi: false,
  qrcode_skip_pages: 0,
  qrcode_max_attempts: 180,
  marker_as_first_page: true,
  exclude_marker_page: false,
  max_segment_pages: 10,
  enable_multithread: false,
  enable_gpu: false,
  nfeatures: 1200,
  ratio: 0.75,
  min_matches: 25,
  min_inlier_ratio: 0.45,
  ransac_reproj_threshold: 5,
  reference_roi: null,
});

const useMaxSegment = ref(false);
const probePageIndex = ref(1);
const quickScanPageLimit = ref(30);
const preset = ref<"" | "balanced" | "strict" | "loose" | "high_recall">("balanced");
const advancedOpen = ref(false);
const markerPageMode = computed({
  get: () => {
    if (opts.value.exclude_marker_page) return "exclude";
    return opts.value.marker_as_first_page ? "first" : "previous";
  },
  set: (mode: "first" | "previous" | "exclude") => {
    opts.value.marker_as_first_page = mode === "first";
    opts.value.exclude_marker_page = mode === "exclude";
  },
});

const fileBasename = (p: string) => p.split(/[\\/]/).pop() || p;
const isBrowserPreviewImage = computed(() => /\.(png|jpe?g|bmp|webp|gif|svg)$/i.test(referenceImage.value));
const activeRoi = computed(() => selectionDraft.value || opts.value.reference_roi);
const imageContentRect = computed(() => {
  const natural = previewNaturalSize.value;
  if (!natural.width || !natural.height) return null;
  const z = previewZoom.value;
  const w = natural.width * z;
  const h = natural.height * z;
  return { left: 0, top: 0, width: w, height: h, scale: z };
});
const previewCanvasStyle = computed(() => {
  const content = imageContentRect.value;
  if (!content) return {};
  return {
    width: `${content.width}px`,
    height: `${content.height}px`,
  };
});
const roiStyle = computed(() => {
  const roi = activeRoi.value;
  const content = imageContentRect.value;
  if (!roi || !content) return {};
  return {
    left: `${roi[0] * content.scale}px`,
    top: `${roi[1] * content.scale}px`,
    width: `${roi[2] * content.scale}px`,
    height: `${roi[3] * content.scale}px`,
  };
});
const roiDialogContentRect = computed(() => {
  const natural = roiNaturalSize.value;
  if (!natural.width || !natural.height) return null;
  const z = previewZoom.value;
  const w = natural.width * z;
  const h = natural.height * z;
  return { left: 0, top: 0, width: w, height: h, scale: z };
});
const roiDialogActiveRoi = computed(() => selectionDraft.value || opts.value.reference_roi);
const roiDialogStyle = computed(() => {
  const roi = roiDialogActiveRoi.value;
  const content = roiDialogContentRect.value;
  if (!roi || !content) return {};
  return {
    left: `${content.left + roi[0] * content.scale}px`,
    top: `${content.top + roi[1] * content.scale}px`,
    width: `${roi[2] * content.scale}px`,
    height: `${roi[3] * content.scale}px`,
  };
});

const { state: taskState, logs, start: startTask, cancel: cancelTask, reset: resetTask } = useEngineTask({
  onComplete: (payload) => {
    submitting.value = false;
    if (payload.ok) {
      error.value = "";
      const taskType = payload.taskType || inferScanTaskType(payload.result);
      if (taskType === "scan_probe") {
        result.value = null;
        tuneResult.value = formatProbeTuneResult(payload.result);
        return;
      }
      if (taskType === "scan_only") {
        result.value = null;
        tuneResult.value = formatScanOnlyTuneResult(payload.result);
        return;
      }
      tuneResult.value = null;
      result.value = normalizeScanSplitResult(payload.result);
      const markerCount = Array.isArray(result.value?.marker_pages) ? result.value.marker_pages.length : 0;
      const outputCount = Array.isArray(result.value?.output_files) ? result.value.output_files.length : 0;
      dialog.alert({ title: "扫描任务完成", message: `生成文件：${outputCount} 个\n标记页：${markerCount} 页`, kind: "success" });
    } else {
      const taskType = payload.taskType || inferScanTaskType(payload.result);
      if (payload.result && taskType === "scan_probe") {
        result.value = null;
        tuneResult.value = formatProbeTuneResult(payload.result);
      } else if (payload.result && taskType === "scan_only") {
        result.value = null;
        tuneResult.value = formatScanOnlyTuneResult(payload.result);
      } else if (payload.result) {
        result.value = normalizeScanSplitResult(payload.result);
        tuneResult.value = null;
      }
      error.value = payload.error || "执行失败";
      dialog.alert({ title: payload.cancelled ? "扫描任务已取消" : "扫描任务失败", message: error.value, kind: payload.cancelled ? "info" : "danger" });
    }
  },
});

function inferScanTaskType(payload: any) {
  const taskId = taskState.value.taskId || "";
  if (payload && typeof payload === "object" && "page_index" in payload && "marked" in payload) return "scan_probe";
  if (taskId.startsWith("probe_")) return "scan_probe";
  if (taskId.startsWith("scan_only_")) return "scan_only";
  return "scan_split";
}

function normalizeScanSplitResult(payload: any): ScanSplitTaskResult {
  return {
    output_files: Array.isArray(payload?.output_files) ? payload.output_files : [],
    marker_pages: Array.isArray(payload?.marker_pages) ? payload.marker_pages : [],
    total_pages: Number(payload?.total_pages || 0),
    suspect_segments: Array.isArray(payload?.suspect_segments) ? payload.suspect_segments : [],
  };
}

function modeLabel(mode: string) {
  if (mode === "qrcode") return "二维码";
  if (mode === "stamp") return "印章";
  if (mode === "feature") return "特征点";
  if (mode === "auto") return "自动";
  return mode || "未知";
}

function formatNumber(value: unknown, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "-";
}

function formatProbeTuneResult(payload: any): TuneResult {
  const pageNumber = Number(payload?.page_number || Number(payload?.page_index || 0) + 1 || 0);
  const totalPages = Number(payload?.total_pages || 0);
  const marked = Boolean(payload?.marked);
  const lines = [
    `页码：第 ${pageNumber || "-"} 页${totalPages ? ` / 共 ${totalPages} 页` : ""}`,
    `结果：${marked ? "命中标记页" : "未命中"}${payload?.reason ? `（${payload.reason}）` : ""}`,
    `模式：${modeLabel(String(payload?.detection_mode || ""))}`,
  ];

  const qrcode = payload?.qrcode || {};
  if (qrcode.present || (Array.isArray(qrcode.infos) && qrcode.infos.length) || qrcode.stats) {
    const decoded = Array.isArray(qrcode.infos) ? qrcode.infos.length : 0;
    const stats = qrcode.stats ? `，面积 ${formatNumber(qrcode.stats.area, 0)}，形状 ${formatNumber(qrcode.stats.aspect)}` : "";
    lines.push(`二维码：${qrcode.present || decoded ? "有候选" : "无"}，解码 ${decoded} 个${stats}`);
  }

  const stamp = payload?.stamp || {};
  if (stamp.present || stamp.candidates != null) {
    lines.push(`印章：${stamp.present ? "命中" : "未命中"}，候选 ${Number(stamp.candidates || 0)}，面积占比 ${formatNumber(stamp.area_ratio, 4)}，圆度 ${formatNumber(stamp.circularity)}`);
  }

  const feature = payload?.feature || {};
  if (feature.good_matches || feature.inliers || payload?.detection_mode === "feature" || payload?.detection_mode === "auto") {
    lines.push(`特征点：匹配 ${Number(feature.good_matches || 0)}，内点 ${Number(feature.inliers || 0)}，比例 ${formatNumber(feature.inlier_ratio)}`);
  }

  const params = payload?.params || {};
  lines.push(`参数：DPI ${Number(params.dpi || opts.value.dpi)}，特征点 ${Number(params.nfeatures || opts.value.nfeatures)}，最小匹配 ${Number(params.min_matches || opts.value.min_matches)}`);
  return { title: `单页测试完成：${marked ? "命中" : "未命中"}`, lines };
}

function formatScanOnlyTuneResult(payload: any): TuneResult {
  const normalized = normalizeScanSplitResult(payload);
  const markerPages = normalized.marker_pages.map((p) => Number(p) + 1).filter((p) => Number.isFinite(p));
  const lines = [
    `扫描页数：${normalized.total_pages}`,
    `命中标记页：${markerPages.length ? markerPages.join("、") : "无"}`,
  ];
  if (Array.isArray(normalized.suspect_segments) && normalized.suspect_segments.length) {
    lines.push(`疑似漏检分段：${normalized.suspect_segments.length} 个`);
  }
  return { title: `快速扫描完成：命中 ${markerPages.length} 页`, lines };
}

async function pickPdf() {
  const paths = await window.electronAPI?.openFileDialog({
    multi: false,
    filters: [{ name: "PDF 文件", extensions: ["pdf"] }],
  });
  if (paths?.[0]) pdfPath.value = paths[0];
}

async function pickReference() {
  const paths = await window.electronAPI?.openFileDialog({
    multi: false,
    filters: [
      { name: "图像 / PDF", extensions: ["png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp", "gif", "pdf"] },
    ],
  });
  if (paths?.[0]) referenceImage.value = paths[0];
}

watch(referenceImage, () => {
  opts.value.reference_roi = null;
  selectionDraft.value = null;
  previewZoom.value = 0.5;
  loadReferencePreview();
});

watch(() => opts.value.nfeatures, () => {
  if (referenceImage.value) loadReferencePreview();
});

watch(pdfPath, () => {
  updatePdfPageCount();
});

watch(
  () => opts.value.detection_mode,
  (mode) => {
    opts.value.dpi = scanDpiForMode(mode);
    if (!isQrMode.value) {
      opts.value.qrcode_no_decode = false;
      opts.value.qrcode_text_contains = "";
    }
  },
);

let pdfPageCountToken = 0;
let referencePreviewToken = 0;

async function updatePdfPageCount() {
  const token = ++pdfPageCountToken;
  const currentPdfPath = pdfPath.value;
  pdfPageCount.value = null;
  if (!currentPdfPath || !window.engine) return;
  try {
    const res = await window.engine.pdfSplit.validate(currentPdfPath);
    if (token !== pdfPageCountToken || currentPdfPath !== pdfPath.value) return;
    pdfPageCount.value = res.valid && res.page_count ? res.page_count : null;
    if (pdfPageCount.value) {
      probePageIndex.value = Math.min(Math.max(1, probePageIndex.value), pdfPageCount.value);
      quickScanPageLimit.value = Math.min(Math.max(1, quickScanPageLimit.value), pdfPageCount.value);
    }
  } catch {
    if (token === pdfPageCountToken) pdfPageCount.value = null;
  }
}

async function loadReferencePreview() {
  const token = ++referencePreviewToken;
  const currentReference = referenceImage.value;
  const currentRoi = opts.value.reference_roi ? [...opts.value.reference_roi] as [number, number, number, number] : null;
  const currentNfeatures = opts.value.nfeatures;
  previewDataUrl.value = "";
  previewError.value = "";
  keypointInfo.value = "";
  previewNaturalSize.value = { width: 0, height: 0 };
  roiNaturalSize.value = { width: 0, height: 0 };
  if (!currentReference) return;
  previewLoading.value = true;
  try {
    if (window.engine) {
      const res = await window.engine.scanSplit.previewReference(currentReference, {
        nfeatures: currentNfeatures,
        roi: currentRoi,
      });
      if (token !== referencePreviewToken || currentReference !== referenceImage.value) return;
      if (res?.ok && res.data_url) {
        previewDataUrl.value = res.data_url;
        if (res.width && res.height) {
          const size = { width: res.width, height: res.height };
          previewNaturalSize.value = size;
          roiNaturalSize.value = size;
        }
        const total = res.keypoints_total ?? 0;
        if (currentRoi && res.keypoints_in_roi != null) {
          keypointInfo.value = `检测到 ${total} 个特征点（框选区域内 ${res.keypoints_in_roi} 个用于匹配）`;
        } else {
          keypointInfo.value = `检测到 ${total} 个特征点`;
        }
        return;
      }
      previewError.value = res?.error || "无法生成参考预览";
      return;
    }

    if (isBrowserPreviewImage.value) {
      const dataUrl = await window.electronAPI?.readFileAsDataUrl(currentReference);
      if (token !== referencePreviewToken || currentReference !== referenceImage.value) return;
      if (dataUrl) {
        previewDataUrl.value = dataUrl;
        keypointInfo.value = "引擎未就绪，仅显示原图";
        return;
      }
      previewError.value = "无法读取参考图片，请确认文件路径有效。";
      return;
    }

    previewError.value = "引擎未就绪，请稍后重试。";
  } catch (e: any) {
    if (token === referencePreviewToken) previewError.value = e?.message || "无法预览参考文件";
  } finally {
    if (token === referencePreviewToken) previewLoading.value = false;
  }
}

function syncPreviewNaturalSize(img: HTMLImageElement | null) {
  const size = {
    width: img?.naturalWidth || 0,
    height: img?.naturalHeight || 0,
  };
  previewNaturalSize.value = size;
  roiNaturalSize.value = size;
}

function onPreviewLoaded() {
  syncPreviewNaturalSize(previewImgRef.value);
  fitPreviewToStage();
}

function onRoiImageLoaded() {
  syncPreviewNaturalSize(roiImgRef.value);
}

function onPreviewLoadError() {
  previewDataUrl.value = "";
  previewNaturalSize.value = { width: 0, height: 0 };
  previewError.value = "参考预览加载失败，请换用 PNG/JPG，或确认文件未损坏。";
}

function setPreviewZoom(value: number) {
  const minZoom = _fittedZoom > 0 ? _fittedZoom : 0.2;
  previewZoom.value = Math.min(3, Math.max(minZoom, Number(value) || 1));
}

function zoomPreview(delta: number) {
  setPreviewZoom(Number((previewZoom.value + delta).toFixed(2)));
}

function resetPreviewZoom() {
  fitPreviewToStage();
}

function fitPreviewToStage() {
  const stage = previewStageRef.value;
  const natural = previewNaturalSize.value;
  if (!stage || !natural.width || !natural.height || stage.clientWidth <= 0) {
    previewZoom.value = 0.5;
    return;
  }
  const pad = 16;
  const fit = Math.min(
    (stage.clientWidth - pad) / natural.width,
    (stage.clientHeight - pad) / natural.height,
  );
  _fittedZoom = Math.min(1.0, Math.max(0.1, fit));
  previewZoom.value = _fittedZoom;
}
const roiCanvasSize = computed(() => {
  const natural = roiNaturalSize.value;
  if (!natural.width || !natural.height) return {};
  const z = previewZoom.value;
  return {
    width: `${Math.round(natural.width * z)}px`,
    height: `${Math.round(natural.height * z)}px`,
  };
});

const panStart = ref<{ x: number; y: number; scrollLeft: number; scrollTop: number } | null>(null);
const roiPanStart = ref<{ x: number; y: number; scrollLeft: number; scrollTop: number } | null>(null);

function onStagePointerDown(e: PointerEvent) {
  if (!previewStageRef.value) return;
  panStart.value = {
    x: e.clientX,
    y: e.clientY,
    scrollLeft: previewStageRef.value.scrollLeft,
    scrollTop: previewStageRef.value.scrollTop,
  };
  previewStageRef.value.setPointerCapture(e.pointerId);
  previewStageRef.value.style.cursor = "grabbing";
}

function onStagePointerMove(e: PointerEvent) {
  if (!panStart.value || !previewStageRef.value) return;
  const dx = panStart.value.x - e.clientX;
  const dy = panStart.value.y - e.clientY;
  previewStageRef.value.scrollLeft = panStart.value.scrollLeft + dx;
  previewStageRef.value.scrollTop = panStart.value.scrollTop + dy;
}

function onStagePointerUp(_e: PointerEvent) {
  panStart.value = null;
  if (previewStageRef.value) previewStageRef.value.style.cursor = "grab";
}

function onRoiStageWheel(e: WheelEvent) {
  if (e.ctrlKey) {
    e.preventDefault();
    zoomPreview(e.deltaY > 0 ? -0.1 : 0.1);
  }
}

function onPreviewWheel(e: WheelEvent) {
  if (e.ctrlKey) {
    e.preventDefault();
    zoomPreview(e.deltaY > 0 ? -0.1 : 0.1);
  }
}

function eventToImagePoint(e: PointerEvent) {
  const img = roiDialogOpen.value ? roiImgRef.value : previewImgRef.value;
  const content = roiDialogOpen.value ? roiDialogContentRect.value : imageContentRect.value;
  const natural = roiDialogOpen.value ? roiNaturalSize.value : previewNaturalSize.value;
  if (!img || !content || !natural.width || !natural.height) return null;
  const rect = img.getBoundingClientRect();
  const x = Math.round((e.clientX - rect.left - content.left) / content.scale);
  const y = Math.round((e.clientY - rect.top - content.top) / content.scale);
  return {
    x: Math.max(0, Math.min(natural.width, x)),
    y: Math.max(0, Math.min(natural.height, y)),
  };
}

function onRoiPointerDown(e: PointerEvent) {
  if (!previewDataUrl.value) return;
  if (roiDrawMode.value) {
    const point = eventToImagePoint(e);
    if (!point) return;
    selectionStart.value = point;
    selectionDraft.value = [point.x, point.y, 0, 0];
  } else {
    if (!roiStageRef.value) return;
    roiPanStart.value = {
      x: e.clientX, y: e.clientY,
      scrollLeft: roiStageRef.value.scrollLeft,
      scrollTop: roiStageRef.value.scrollTop,
    };
  }
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
}

function onRoiPointerMove(e: PointerEvent) {
  if (selectionStart.value) {
    const point = eventToImagePoint(e);
    if (!point) return;
    const x = Math.min(selectionStart.value.x, point.x);
    const y = Math.min(selectionStart.value.y, point.y);
    const w = Math.abs(point.x - selectionStart.value.x);
    const h = Math.abs(point.y - selectionStart.value.y);
    selectionDraft.value = [x, y, w, h];
    return;
  }
  if (roiPanStart.value && roiStageRef.value) {
    const dx = roiPanStart.value.x - e.clientX;
    const dy = roiPanStart.value.y - e.clientY;
    roiStageRef.value.scrollLeft = roiPanStart.value.scrollLeft + dx;
    roiStageRef.value.scrollTop = roiPanStart.value.scrollTop + dy;
  }
}

function onRoiPointerUp(e: PointerEvent) {
  if (selectionStart.value && selectionDraft.value) {
    const [, , w, h] = selectionDraft.value;
    opts.value.reference_roi = w >= 6 && h >= 6 ? selectionDraft.value : null;
    selectionDraft.value = null;
    selectionStart.value = null;
  }
  roiPanStart.value = null;
  try {
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  } catch {}
}

function clearRoi() {
  opts.value.reference_roi = null;
  selectionDraft.value = null;
  selectionStart.value = null;
  if (!roiDialogOpen.value) loadReferencePreview();
}

let _savedPreviewZoom = 1.0;
let _fittedZoom = 1.0;
let _roiFittedZoom = 1.0;

function fitRoiDialogToStage() {
  const stage = roiStageRef.value;
  const natural = roiNaturalSize.value;
  if (!stage || !natural.width || !natural.height || stage.clientWidth <= 0 || stage.clientHeight <= 0) return;
  const pad = 24;
  const fit = Math.min(
    (stage.clientWidth - pad) / natural.width,
    (stage.clientHeight - pad) / natural.height,
  );
  _roiFittedZoom = Math.min(1.0, Math.max(0.1, fit));
  previewZoom.value = _roiFittedZoom;
}

function openRoiDialog() {
  if (!previewDataUrl.value) return;
  selectionDraft.value = null;
  selectionStart.value = null;
  _savedPreviewZoom = previewZoom.value;
  roiDialogOpen.value = true;
  nextTick(() => {
    syncPreviewNaturalSize(roiImgRef.value || previewImgRef.value);
    // 等弹窗完成布局后再按弹窗容器计算适配缩放
    requestAnimationFrame(() => fitRoiDialogToStage());
  });
}

function resetRoiZoom() {
  fitRoiDialogToStage();
}

function closeRoiDialog() {
  selectionDraft.value = null;
  selectionStart.value = null;
  roiDialogOpen.value = false;
  previewZoom.value = _savedPreviewZoom;
}

function confirmRoiDialog() {
  if (!opts.value.reference_roi) {
    previewError.value = "请先框选区域，或取消后继续使用全图识别。";
    return;
  }
  closeRoiDialog();
  loadReferencePreview();
}

function copyResults() {
  if (!result.value) return;
  const lines = [
    `生成文件：${result.value.output_files.length}`,
    `总页数：${result.value.total_pages}`,
    `标记页：${result.value.marker_pages.map((p) => p + 1).join(", ") || "无"}`,
    ...result.value.output_files,
  ];
  navigator.clipboard?.writeText(lines.join("\n"));
}

function logLineClass(line: string) {
  if (/失败|错误|不可用|异常|超时/.test(line)) return "danger";
  if (/漏检|未匹配|未命中|忽略|警告/.test(line)) return "warn";
  if (/完成|命中|检测到|生成|成功/.test(line)) return "ok";
  if (/开始|正在|扫描|测试/.test(line)) return "info";
  return "";
}

const scanBannerKind = computed(() => {
  if (taskState.value.running) return "info";
  if (error.value) return "warning";
  if (result.value) return "success";
  if (!pdfPath.value) return "warning";
  if (!outputDir.value) return "info";
  return "success";
});
const scanBannerMessage = computed(() => {
  if (taskState.value.running) return `正在处理：${taskState.value.phase || "扫描中"}…`;
  if (error.value) return error.value;
  if (result.value) return `完成：生成 ${result.value.output_files.length} 个文件，标记页 ${result.value.marker_pages.length} 个`;
  if (!pdfPath.value) return "请先选择 PDF 文件，再选择识别方式并开始扫描。";
  if (!referenceImage.value && opts.value.detection_mode === "feature") return "特征匹配模式需要选择参考文件";
  if (!outputDir.value) return "输出目录未指定，将拆分到 PDF 同目录";
  return `输出目录：${outputDir.value}`;
});

async function pickOutputDir() {
  const dir = await window.electronAPI?.openDirectoryDialog({ title: "选择输出目录" });
  if (dir) outputDir.value = dir;
}

let _applyingPreset = false;

function applyPreset(name: typeof preset.value) {
  _applyingPreset = true;
  if (!name) {
    preset.value = "";
    _applyingPreset = false;
    return;
  }
  if (name === "strict") {
    opts.value.nfeatures = Math.min(10000, Math.max(100, 1000));
    opts.value.ratio = Math.min(1.0, Math.max(0.1, 0.70));
    opts.value.min_matches = Math.min(1000, Math.max(1, 35));
    opts.value.ransac_reproj_threshold = Math.min(50.0, Math.max(0.1, 4.0));
    opts.value.min_inlier_ratio = Math.min(1.0, Math.max(0.01, 0.55));
  } else if (name === "high_recall") {
    opts.value.nfeatures = Math.min(10000, Math.max(100, 3000));
    opts.value.ratio = Math.min(1.0, Math.max(0.1, 0.90));
    opts.value.min_matches = Math.min(1000, Math.max(1, 12));
    opts.value.ransac_reproj_threshold = Math.min(50.0, Math.max(0.1, 8.0));
    opts.value.min_inlier_ratio = Math.min(1.0, Math.max(0.01, 0.25));
  } else if (name === "loose") {
    opts.value.nfeatures = Math.min(10000, Math.max(100, 2000));
    opts.value.ratio = Math.min(1.0, Math.max(0.1, 0.85));
    opts.value.min_matches = Math.min(1000, Math.max(1, 18));
    opts.value.ransac_reproj_threshold = Math.min(50.0, Math.max(0.1, 6.0));
    opts.value.min_inlier_ratio = Math.min(1.0, Math.max(0.01, 0.35));
  } else {
    // balanced
    opts.value.nfeatures = Math.min(10000, Math.max(100, 1200));
    opts.value.ratio = Math.min(1.0, Math.max(0.1, 0.75));
    opts.value.min_matches = Math.min(1000, Math.max(1, 25));
    opts.value.ransac_reproj_threshold = Math.min(50.0, Math.max(0.1, 5.0));
    opts.value.min_inlier_ratio = Math.min(1.0, Math.max(0.01, 0.45));
  }
  preset.value = name;
  nextTick(() => { _applyingPreset = false; });
}

// 手动修改参数时重置 preset
watch(
  () => [opts.value.nfeatures, opts.value.min_matches, opts.value.ratio, opts.value.ransac_reproj_threshold, opts.value.min_inlier_ratio],
  () => { if (!_applyingPreset) preset.value = ""; },
);

function boundedNumber(value: number, min: number, max?: number) {
  const n = Number.isFinite(value) ? value : min;
  const lower = Math.max(min, n);
  return max == null ? lower : Math.min(max, lower);
}

function nonNegativeInt(value: number) {
  const n = Number.isFinite(value) ? value : 0;
  return Math.max(0, Math.floor(n));
}

function scanDpiForMode(mode: ScanDetectionMode) {
  if (mode === "qrcode") return 200;
  if (mode === "stamp" || mode === "auto") return 220;
  return 180;
}

function boundedProbePage(value: number) {
  const max = pdfPageCount.value || Number.MAX_SAFE_INTEGER;
  return Math.min(max, Math.max(1, Math.floor(Number(value) || 1)));
}

// 命中后跳过：独立 checkbox 状态，防止清空数字输入导致整组控件关闭
const skipPagesEnabled = computed({
  get: () => opts.value.qrcode_skip_pages > 0,
  set: (v: boolean) => { opts.value.qrcode_skip_pages = v ? Math.max(1, opts.value.qrcode_skip_pages || 1) : 0; },
});

function onSkipPagesInput(e: Event) {
  const raw = (e.target as HTMLInputElement).value;
  if (raw === "") return; // 保留当前值，用户继续输入
  const n = nonNegativeInt(Number(raw));
  opts.value.qrcode_skip_pages = Math.min(50, Math.max(1, n));
}

function onDpiInput(e: Event) {
  const raw = (e.target as HTMLInputElement).value;
  if (raw === "") return;
  const n = Number(raw);
  if (!Number.isFinite(n)) return;
  const clamped = Math.min(300, Math.max(72, Math.floor(n)));
  opts.value.dpi = clamped;
  if (n > 300) (e.target as HTMLInputElement).value = String(clamped);
}

function onDpiBlur(e: Event) {
  const raw = (e.target as HTMLInputElement).value;
  const n = Number(raw);
  const clamped = Math.min(300, Math.max(72, Math.floor(Number.isFinite(n) && n > 0 ? n : 180)));
  opts.value.dpi = clamped;
  (e.target as HTMLInputElement).value = String(clamped);
}

function onMaxAttemptsInput(e: Event) {
  const raw = (e.target as HTMLInputElement).value;
  if (raw === "") return;
  const n = Number(raw);
  if (!Number.isFinite(n)) return;
  const clamped = Math.min(500, Math.max(12, Math.floor(n)));
  opts.value.qrcode_max_attempts = clamped;
  if (n > 500) (e.target as HTMLInputElement).value = String(clamped);
}

function onMaxAttemptsBlur(e: Event) {
  const raw = (e.target as HTMLInputElement).value;
  const n = Number(raw);
  const clamped = Math.min(500, Math.max(12, Math.floor(Number.isFinite(n) && n > 0 ? n : 180)));
  opts.value.qrcode_max_attempts = clamped;
  (e.target as HTMLInputElement).value = String(clamped);
}

watch(() => opts.value.dpi, (val) => {
  const el = dpiInputRef.value;
  if (el && el !== document.activeElement) el.value = String(val);
});

watch(() => opts.value.qrcode_max_attempts, (val) => {
  const el = maxAttemptsInputRef.value;
  if (el && el !== document.activeElement) el.value = String(val);
});

function clampScanOptions() {
  opts.value.dpi = Math.min(300, Math.max(72, Math.floor(Number(opts.value.dpi) || 180)));
  opts.value.qrcode_skip_pages = Math.min(50, nonNegativeInt(opts.value.qrcode_skip_pages));
  opts.value.qrcode_max_attempts = Math.min(500, Math.max(12, Math.floor(Number(opts.value.qrcode_max_attempts) || 180)));
  opts.value.nfeatures = Math.min(10000, Math.max(100, Math.floor(Number(opts.value.nfeatures) || 1200)));
  opts.value.min_matches = Math.min(1000, Math.max(1, Math.floor(Number(opts.value.min_matches) || 25)));
  opts.value.ratio = boundedNumber(Number(opts.value.ratio), 0.1, 1.0);
  opts.value.ransac_reproj_threshold = boundedNumber(Number(opts.value.ransac_reproj_threshold), 0.1, 50.0);
  opts.value.min_inlier_ratio = boundedNumber(Number(opts.value.min_inlier_ratio), 0.01, 1.0);
  opts.value.max_segment_pages = Math.min(10000, Math.max(1, positiveInt(Number(opts.value.max_segment_pages))));
  quickScanPageLimit.value = pdfPageCount.value
    ? Math.min(pdfPageCount.value, positiveInt(Number(quickScanPageLimit.value), 30))
    : positiveInt(Number(quickScanPageLimit.value), 30);
  probePageIndex.value = boundedProbePage(probePageIndex.value);
}

function validateRoiSelection() {
  if (!opts.value.qrcode_use_roi || !isRoiSupported.value) return true;
  if (referenceImage.value && opts.value.reference_roi) return true;
  const message = !referenceImage.value
    ? "已勾选框选区域(ROI)，请先选择参考文件并框选有效区域。"
    : "已勾选框选区域(ROI)，请先在参考预览中框选有效区域，或取消勾选 ROI 后再执行。";
  error.value = message;
  dialog.alert({ title: "需要框选 ROI", message, kind: "warning" });
  return false;
}

async function execute() {
  if (submitting.value || taskState.value.running) {
    error.value = "已有任务正在执行，请等待完成后再试。";
    return;
  }
  if (!canRun.value || !window.engine) return;
  if (needsReference.value && !referenceImage.value) {
    error.value = "当前识别方式需要参考文件";
    return;
  }
  if (!validateRoiSelection()) return;
  submitting.value = true;
  error.value = "";
  result.value = null;
  tuneResult.value = null;
  const taskId = generateTaskId("scan_split");
  await nextTick();
  startTask(taskId);
  try {
    await window.engine.scanSplit.executeAsync({
      pdfPath: pdfPath.value,
      referenceImagePath: referenceImage.value,
      outputDir: outputDir.value,
      prefix: prefix.value,
      options: buildScanOptions(),
      taskId,
    });
  } catch (e: any) {
    error.value = e?.message || "提交失败";
    submitting.value = false;
    resetTask();
  }
}

async function runProbePage() {
  if (submitting.value || taskState.value.running) {
    error.value = "已有任务正在执行，请等待完成后再试。";
    return;
  }
  if (!canRun.value || !window.engine) return;
  if (needsReference.value && !referenceImage.value) {
    error.value = "当前识别方式需要参考文件";
    return;
  }
  if (!validateRoiSelection()) return;
  submitting.value = true;
  error.value = "";
  result.value = null;
  tuneResult.value = null;
  const taskId = generateTaskId("probe");
  await nextTick();
  startTask(taskId);
  try {
    await window.engine.scanSplit.probePage({
      pdfPath: pdfPath.value,
      referenceImagePath: referenceImage.value,
      options: buildScanOptions({ max_segment_pages: 0 }),
      pageIndex: Math.max(0, probePageIndex.value - 1),
      taskId,
    });
  } catch (e: any) {
    error.value = e?.message || "提交失败";
    submitting.value = false;
    resetTask();
  }
}

async function runScanOnly() {
  if (submitting.value || taskState.value.running) {
    error.value = "已有任务正在执行，请等待完成后再试。";
    return;
  }
  if (!canRun.value || !window.engine) return;
  if (needsReference.value && !referenceImage.value) {
    error.value = "当前识别方式需要参考文件";
    return;
  }
  if (!validateRoiSelection()) return;
  submitting.value = true;
  error.value = "";
  result.value = null;
  tuneResult.value = null;
  const taskId = generateTaskId("scan_only");
  await nextTick();
  startTask(taskId);
  try {
    await window.engine.scanSplit.scanOnly({
      pdfPath: pdfPath.value,
      referenceImagePath: referenceImage.value,
      options: buildScanOptions(),
      pageLimit: quickScanPageLimit.value,
      taskId,
    });
  } catch (e: any) {
    error.value = e?.message || "提交失败";
    submitting.value = false;
    resetTask();
  }
}

function readFiniteNumber(value: unknown, fallback: number) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function readBoolean(value: unknown, fallback: boolean) {
  return typeof value === "boolean" ? value : fallback;
}

function readRoi(value: unknown): [number, number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 4) return null;
  const roi = value.map((v) => Math.floor(Number(v)));
  if (roi.some((v) => !Number.isFinite(v) || v < 0)) return null;
  return roi as [number, number, number, number];
}

function loadScanSettings() {
  try {
    const raw = sessionStorage.getItem(SCAN_SETTINGS_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return;

    const validModes: ScanDetectionMode[] = ["auto", "qrcode", "stamp", "feature"];
    if (validModes.includes(data.detection_mode)) opts.value.detection_mode = data.detection_mode;

    const numberKeys = [
      "dpi", "qrcode_skip_pages", "qrcode_max_attempts", "max_segment_pages",
      "nfeatures", "ratio", "min_matches", "min_inlier_ratio", "ransac_reproj_threshold",
    ] as const;
    for (const key of numberKeys) {
      if (key in data) opts.value[key] = readFiniteNumber(data[key], opts.value[key]);
    }

    const booleanKeys = [
      "qrcode_no_decode", "qrcode_use_roi", "marker_as_first_page", "exclude_marker_page",
      "enable_multithread", "enable_gpu",
    ] as const;
    for (const key of booleanKeys) {
      if (key in data) opts.value[key] = readBoolean(data[key], opts.value[key]);
    }

    if (typeof data.qrcode_text_contains === "string") opts.value.qrcode_text_contains = data.qrcode_text_contains.slice(0, 200);
    opts.value.reference_roi = readRoi(data.reference_roi);
    if (typeof data.useMaxSegment === "boolean") useMaxSegment.value = data.useMaxSegment;
    if (typeof data.preset === "string" && ["", "balanced", "strict", "loose", "high_recall"].includes(data.preset)) {
      preset.value = data.preset;
    }
    clampScanOptions();
  } catch {}
}

// ── 设置持久化（仅当前窗口会话）────────────────────────────
const SCAN_SETTINGS_KEY = "file-toolbox.scan-settings";

function saveScanSettings() {
  try {
    const data = {
      ...opts.value,
      useMaxSegment: useMaxSegment.value,
      preset: preset.value,
    };
    sessionStorage.setItem(SCAN_SETTINGS_KEY, JSON.stringify(data));
  } catch {}
}

// 参数变更时自动保存
watch(
  [opts, useMaxSegment, preset],
  () => saveScanSettings(),
  { deep: true },
);

// 初始化时恢复当前窗口会话内的参数；关闭程序时由 App.vue 统一清理
onMounted(() => loadScanSettings());
onMounted(() => {
  nextTick(() => {
    if (dpiInputRef.value) dpiInputRef.value.value = String(opts.value.dpi);
    if (maxAttemptsInputRef.value) maxAttemptsInputRef.value.value = String(opts.value.qrcode_max_attempts);
  });
});

onBeforeUnmount(() => {
  if (taskState.value.running) cancelTask();
  pdfPageCountToken++;
  referencePreviewToken++;
});

const detectionOptions: { key: ScanDetectionMode; label: string }[] = [
  { key: "auto", label: "自动（二维码/印章/特征点）" },
  { key: "qrcode", label: "二维码" },
  { key: "stamp", label: "印章" },
  { key: "feature", label: "特征点匹配" },
];
const detectionSelectOptions = detectionOptions.map((item) => ({ label: item.label, value: item.key }));
const presetOptions = [
  { label: "自定义", value: "" },
  { label: "预设：均衡", value: "balanced" },
  { label: "预设：严格", value: "strict" },
  { label: "预设：宽松", value: "loose" },
  { label: "预设：高召回", value: "high_recall" },
];

const isQrMode = computed(() => opts.value.detection_mode === "qrcode" || opts.value.detection_mode === "auto");
const isFeatureMode = computed(() => opts.value.detection_mode === "feature" || opts.value.detection_mode === "auto");
const isRoiSupported = computed(() => ["qrcode", "stamp", "auto", "feature"].includes(opts.value.detection_mode));
const qrcodeTextDisabled = computed(() => !isQrMode.value || opts.value.qrcode_no_decode);
const needsReference = computed(() => opts.value.detection_mode === "feature");
const canRun = computed(() => !!pdfPath.value && !taskState.value.running && !submitting.value);
const detectionModeHintText = computed(() => {
  if (opts.value.detection_mode === "qrcode") return "适合用二维码作为分隔标记；可按二维码文字内容筛选。";
  if (opts.value.detection_mode === "stamp") return "适合用红章、盖章页作为分隔标记；不会使用二维码内容筛选。";
  if (opts.value.detection_mode === "feature") return "适合用固定版式或图片作为分隔标记；需要先选择参考文件。";
  return "推荐默认使用：会先找二维码，再尝试印章和参考特征。";
});
const roiStatusText = computed(() => {
  if (!opts.value.qrcode_use_roi) return "未启用 ROI，将全页识别";
  if (!isRoiSupported.value) return "当前识别方式不支持 ROI";
  if (opts.value.reference_roi) return `已框选 x=${opts.value.reference_roi[0]}，y=${opts.value.reference_roi[1]}，w=${opts.value.reference_roi[2]}，h=${opts.value.reference_roi[3]}`;
  return "已启用 ROI，但未框选有效区域，执行前会要求先框选或取消 ROI";
});
const roiOptionTitle = computed(() => {
  if (opts.value.detection_mode === "qrcode") return "只在框选区域内找二维码；适合二维码位置固定的文件";
  if (opts.value.detection_mode === "stamp") return "只在框选区域内找印章；适合盖章位置固定的文件";
  if (opts.value.detection_mode === "feature") return "只使用框选区域的参考特征进行匹配；适合固定版式的局部标记";
  return "优先在框选区域内识别二维码、印章和参考特征；适合标记位置固定的文件";
});
const dpiHintText = computed(() => {
  if (opts.value.detection_mode === "qrcode") return "二维码清晰用 180-200；模糊可调高";
  if (opts.value.detection_mode === "stamp" || opts.value.detection_mode === "auto") return "印章建议 220；越高越慢";
  return "特征点建议 180；参考图很细可调高";
});
const advancedSummaryText = computed(() => {
  const presetText = preset.value ? `预设 ${presetOptions.find((item) => item.value === preset.value)?.label.replace("预设：", "") || preset.value}` : "自定义参数";
  return `${presetText} · DPI ${opts.value.dpi} · 特征点 ${opts.value.nfeatures}`;
});

function buildScanOptions(extra: Partial<ScanSplitOptions> = {}): ScanSplitOptions {
  clampScanOptions();
  const roiReady = !!referenceImage.value && !!opts.value.reference_roi && !!opts.value.qrcode_use_roi && isRoiSupported.value;
  const noDecode = isQrMode.value ? opts.value.qrcode_no_decode : false;
  const referenceRoi = roiReady && opts.value.reference_roi
    ? ([...opts.value.reference_roi] as [number, number, number, number])
    : null;
  return {
    detection_mode: opts.value.detection_mode,
    dpi: opts.value.dpi,
    qrcode_max_attempts: opts.value.qrcode_max_attempts,
    marker_as_first_page: opts.value.marker_as_first_page,
    exclude_marker_page: opts.value.exclude_marker_page,
    enable_multithread: opts.value.enable_multithread,
    enable_gpu: opts.value.enable_gpu,
    nfeatures: opts.value.nfeatures,
    ratio: opts.value.ratio,
    min_matches: opts.value.min_matches,
    min_inlier_ratio: opts.value.min_inlier_ratio,
    ransac_reproj_threshold: opts.value.ransac_reproj_threshold,
    qrcode_no_decode: noDecode,
    qrcode_text_contains: noDecode || !isQrMode.value ? "" : opts.value.qrcode_text_contains,
    use_roi: roiReady,
    qrcode_use_roi: roiReady,
    reference_roi: referenceRoi,
    qrcode_skip_pages: nonNegativeInt(opts.value.qrcode_skip_pages),
    max_segment_pages: useMaxSegment.value ? positiveInt(Number(opts.value.max_segment_pages)) : 0,
    ...extra,
  };
}
</script>

<template>
  <div class="scan-shell panel-shell panel-shell-responsive">
    <!-- 提示 -->
    <div class="banner panel-header scan-banner" :class="`banner-${scanBannerKind}`">
      <span class="banner-icon">
        <AppIcon :name="scanBannerKind === 'warning' ? 'alert' : scanBannerKind === 'success' ? 'check' : 'scan'" />
      </span>
      <span class="banner-title">扫描拆分</span>
      <span class="banner-text">{{ scanBannerMessage }}</span>
    </div>

    <div class="scan-grid panel-grid">
      <!-- 左：参考输入 + 进度日志 -->
      <section class="left-col">
        <fieldset class="group ref-group glass-card section-card">
          <div class="ref-group-inner">
            <div v-if="!referenceImage" class="ref-empty">
              <div class="ref-icon">📂</div>
              <div class="ref-title">未选择参考文件</div>
              <div class="ref-hint">可选择图像或 PDF 作为参考，用于预览、框选 ROI 和特征匹配。</div>
              <button class="btn btn-outline" @click="pickReference">选择参考</button>
            </div>
            <div v-else class="ref-body">
              <div class="roi-toolbar">
                <button class="btn btn-outline btn-sm" :disabled="!previewDataUrl" @click="openRoiDialog">框选区域</button>
                <span class="roi-summary truncate selectable" :title="opts.reference_roi ? `区域：x=${opts.reference_roi[0]}, y=${opts.reference_roi[1]}, w=${opts.reference_roi[2]}, h=${opts.reference_roi[3]}` : '未框选区域'">
                  {{ opts.reference_roi ? `区域：x=${opts.reference_roi[0]}，y=${opts.reference_roi[1]}，w=${opts.reference_roi[2]}，h=${opts.reference_roi[3]}` : '未框选区域' }}
                </span>
                <button class="btn btn-secondary btn-sm" :disabled="!opts.reference_roi" @click="clearRoi">清除区域</button>
                <button class="btn btn-sm" :class="roiDrawMode ? 'btn-primary' : 'btn-ghost'" @click="roiDrawMode = !roiDrawMode">
                  {{ roiDrawMode ? '选区模式' : '框选模式' }}
                </button>
              </div>
              <div class="keypoint-info truncate selectable" :title="keypointInfo || referenceImage">{{ keypointInfo || fileBasename(referenceImage) }}</div>
              <div v-if="previewLoading" class="skeleton-preview">
                <div class="skeleton-box" />
                <div class="skeleton-line" />
                <div class="skeleton-line short" />
              </div>
              <div v-else-if="previewError" class="error-line">{{ previewError }}</div>
              <div v-else-if="previewDataUrl" class="preview-wrap">
                <div class="preview-zoom-toolbar">
                  <button class="btn btn-ghost btn-sm" @click="zoomPreview(-0.1)">−</button>
                  <span class="zoom-label">{{ Math.round(previewZoom * 100) }}%</span>
                  <button class="btn btn-ghost btn-sm" @click="zoomPreview(0.1)">＋</button>
                  <button class="btn btn-ghost btn-sm" @click="resetPreviewZoom">适中</button>
                </div>
                <div ref="previewStageRef" class="preview-stage" @dblclick="openRoiDialog" @wheel="onPreviewWheel" @pointerdown="onStagePointerDown" @pointermove="onStagePointerMove" @pointerup="onStagePointerUp" @pointercancel="onStagePointerUp">
                  <div class="preview-canvas" :style="previewCanvasStyle">
                    <img ref="previewImgRef" class="preview-img" :src="previewDataUrl" @load="onPreviewLoaded" @error="onPreviewLoadError" draggable="false" />
                    <div v-if="activeRoi" class="roi-box" :style="roiStyle" />
                  </div>
                </div>
              </div>
              <div v-else class="ref-hint">参考文件已选择，等待生成预览。</div>
            </div>
          </div>
        </fieldset>

        <fieldset class="group log-group glass-card section-card">
          <div class="log-group-inner">
          <div v-if="taskState.running" class="progress" :class="{ indeterminate: !taskState.total }">
            <div class="progress-bar" :style="{ width: taskState.total ? (taskState.current / taskState.total * 100) + '%' : undefined }" />
          </div>
          <div v-if="taskState.running" class="progress-line">
            {{ taskState.phase || "扫描中" }} · {{ taskState.current }}/{{ taskState.total }}
          </div>
          <div v-if="error" class="error-line">{{ error }}</div>
          <div v-else-if="tuneResult" class="summary-line ok">
            {{ tuneResult.title }}
          </div>
          <div v-else-if="result" class="summary-line ok">
            完成 · 生成 {{ result.output_files.length }} 个文件 · 共 {{ result.total_pages }} 页 · 标记页 {{ result.marker_pages.length }}
            <button class="btn btn-outline btn-mini" @click="copyResults">复制结果</button>
          </div>
          <div v-if="tuneResult" class="result-box selectable">
            <div v-for="line in tuneResult.lines" :key="line" class="result-line">
              {{ line }}
            </div>
          </div>
          <div v-if="result" class="result-box selectable">
            <div class="result-line">
              标记页：{{ result.marker_pages.map((p) => p + 1).join("、") || "无" }}
            </div>
            <div v-for="file in result.output_files" :key="file" class="result-line truncate" :title="file">
              {{ fileBasename(file) }}
            </div>
          </div>
          <div class="log-box" :class="{ empty: !logs.length }">
            <div v-if="!logs.length" class="log-placeholder">这里会实时显示进度与错误信息</div>
            <div v-else class="log-lines selectable">
              <div v-for="(line, index) in logs" :key="`${index}-${line}`" class="log-line" :class="logLineClass(line)">
                {{ line }}
              </div>
            </div>
          </div>
          </div><!-- /log-group-inner -->
        </fieldset>
      </section>

      <!-- 右：功能区 -->
      <section class="right-col">

        <!-- ① 输入文件 -->
        <fieldset class="group glass-card section-card input-group">
          <div class="row">
            <input class="input flex-1" :value="pdfPath" placeholder="选择要拆分的 PDF" readonly :title="pdfPath" />
            <button class="btn btn-primary btn-action scan-pick-btn" @click="pickPdf">选择 PDF</button>
          </div>
          <div class="row mt-2">
            <input class="input flex-1" :value="outputDir" placeholder="输出目录（留空则与 PDF 同目录）" readonly :title="outputDir" />
            <button class="btn btn-primary btn-action scan-pick-btn" @click="pickOutputDir">选择输出目录</button>
          </div>
          <div class="row mt-2">
            <input class="input flex-1" v-model="prefix" placeholder="输出文件名前缀（可选，例如：split_）" />
          </div>
          <div class="row mt-2">
            <input class="input flex-1" :value="referenceImage" placeholder="参考文件（图像或 PDF，可选；特征点模式必需）" readonly :title="referenceImage" />
            <button class="btn btn-primary btn-action scan-pick-btn" @click="pickReference">{{ referenceImage ? '更换参考' : '选择参考' }}</button>
          </div>
        </fieldset>

        <!-- ② 识别与参数 -->
        <fieldset class="group glass-card section-card params-group">
          <div class="params-scroll">
            <div class="row wrap">
              <label class="label-inline">识别方式：</label>
              <AppSelect class="detection-select" v-model="opts.detection_mode" :options="detectionSelectOptions" min-width="260px" />
              <input v-if="isQrMode" class="input flex-1 qr-text-input" v-model="opts.qrcode_text_contains" placeholder="二维码内容包含（可选）" :disabled="qrcodeTextDisabled" />
              <div class="hint-line params-hint">{{ detectionModeHintText }}</div>
            </div>

            <div class="row mt-2 wrap">
              <template v-if="isQrMode">
                <label class="checkbox" title="只判断有没有二维码，不读取二维码文字；速度更快，但内容筛选不会生效"><input type="checkbox" v-model="opts.qrcode_no_decode" />不解码内容</label>
              </template>
              <label class="checkbox" :title="roiOptionTitle"><input type="checkbox" v-model="opts.qrcode_use_roi" :disabled="!isRoiSupported" />框选区域(ROI)</label>
              <label class="checkbox" title="找到标记页后，后面几页先不检查；适合连续多页都有标记的文件"><input type="checkbox" v-model="skipPagesEnabled" />命中后跳过</label>
              <input class="input input-num input-num-compact" type="number" min="1" max="50" :disabled="!skipPagesEnabled" :value="opts.qrcode_skip_pages" @input="onSkipPagesInput" title="命中标记页后跳过的页数" />
              <span class="text-muted">页</span>
              <span v-if="isQrMode" class="params-sep">|</span>
              <label v-if="isQrMode" class="label-inline" title="每页最多尝试检测的二维码数量">最多尝试：</label>
              <input v-if="isQrMode" ref="maxAttemptsInputRef" class="input input-num" type="number" min="12" max="500" :value="opts.qrcode_max_attempts" @input="onMaxAttemptsInput" @blur="onMaxAttemptsBlur" title="每页最多尝试检测的二维码数量（有效范围 12-500）" />
            </div>

            <div v-if="opts.qrcode_use_roi" class="hint-line params-hint" :class="opts.reference_roi ? '' : 'warn'">{{ roiStatusText }}</div>

            <div class="row mt-2 wrap">
              <label class="label-inline">扫描分辨率：</label>
              <span class="label-inline">DPI</span>
              <input ref="dpiInputRef" class="input input-num" type="number" min="72" max="300" @input="onDpiInput" @blur="onDpiBlur" title="页面渲染分辨率，值越高细节越清晰但速度越慢（有效范围 72-300）" />
              <span class="hint-line compact-hint">{{ dpiHintText }}</span>
            </div>

            <div class="params-divider mt-3"><span>输出行为</span></div>
            <div class="row wrap">
              <label class="label-inline">标记页：</label>
              <label class="checkbox" title="标记页会放到下一份 PDF 的第一页"><input type="radio" value="first" v-model="markerPageMode" />放到下一份开头</label>
              <label class="checkbox" title="标记页会放到上一份 PDF 的最后一页"><input type="radio" value="previous" v-model="markerPageMode" />放到上一份末尾</label>
              <label class="checkbox" title="标记页只用来分隔，不写入输出 PDF"><input type="radio" value="exclude" v-model="markerPageMode" />不保存标记页</label>
            </div>
            <div class="row mt-2 wrap">
              <label class="checkbox" title="限制每份输出 PDF 的最大页数，用来发现可能漏掉的标记页"><input type="checkbox" v-model="useMaxSegment" />每份最多</label>
              <input class="input input-num input-num-compact" type="number" min="1" max="10000" :disabled="!useMaxSegment" :value="opts.max_segment_pages" @input="opts.max_segment_pages = Math.min(10000, Math.max(1, positiveInt(Number(($event.target as HTMLInputElement).value))))" title="单份PDF允许的最大页数，包含标记页" />
              <span class="text-muted">页</span>
              <span class="params-sep">|</span>
              <label class="checkbox" title="启用 OpenCV 内部多线程；提速取决于图像处理负载"><input type="checkbox" v-model="opts.enable_multithread" />OpenCV 多线程</label>
              <label class="checkbox" title="尝试启用 OpenCV OpenCL 优化；仅部分环境和大图场景可能提速，不可用时自动回退 CPU"><input type="checkbox" v-model="opts.enable_gpu" />OpenCL 加速</label>
            </div>

            <div class="params-divider mt-3"><span>高级识别参数</span></div>

          <details class="advanced-group mt-2" :open="advancedOpen" @toggle="advancedOpen = ($event.target as HTMLDetailsElement).open">
            <summary>
              <span>特征点参数</span>
              <span class="advanced-summary">{{ advancedSummaryText }}</span>
            </summary>
            <p class="advanced-hint">默认参数适合多数扫描件。仅在误检、漏检或速度不理想时调整这些阈值。</p>
            <div class="grid-2 mt-2">
              <div class="cell"><span class="label-inline">特征点数量：</span><input class="input input-num" type="number" min="100" max="10000" :disabled="!isFeatureMode" :value="opts.nfeatures" @input="opts.nfeatures = Math.min(10000, Math.max(100, Math.floor(Number(($event.target as HTMLInputElement).value) || 1200)))" title="每页提取的ORB特征点上限。值越大越容易匹配到标记，但速度更慢（有效范围 100-10000）" /></div>
              <div class="cell"><span class="label-inline">最小匹配数：</span><input class="input input-num" type="number" min="1" max="1000" :disabled="!isFeatureMode" :value="opts.min_matches" @input="opts.min_matches = Math.min(1000, Math.max(1, Math.floor(Number(($event.target as HTMLInputElement).value) || 25)))" title="判定为标记页所需的最少有效匹配数量。值越大越严格，误报更少但更易漏检（有效范围 1-1000）" /></div>
              <div class="cell"><span class="label-inline">比例阈值：</span><input class="input input-num" type="number" step="0.05" min="0.1" max="1.0" :disabled="!isFeatureMode" :value="opts.ratio" @input="opts.ratio = boundedNumber(Number(($event.target as HTMLInputElement).value), 0.1, 1.0)" title="KNN匹配的比例阈值（Lowe ratio test）。越小越严格，误匹配更少但可能漏检（有效范围 0.1-1.0）" /></div>
              <div class="cell"><span class="label-inline">RANSAC 阈值：</span><input class="input input-num" type="number" step="0.5" min="0.1" max="50.0" :disabled="!isFeatureMode" :value="opts.ransac_reproj_threshold" @input="opts.ransac_reproj_threshold = boundedNumber(Number(($event.target as HTMLInputElement).value), 0.1, 50.0)" title="RANSAC重投影阈值（像素）。越大越宽松，内点可能变多但误报风险增加（有效范围 0.1-50.0）" /></div>
              <div class="cell"><span class="label-inline">内点比例阈值：</span><input class="input input-num" type="number" step="0.05" min="0.01" max="1.0" :disabled="!isFeatureMode" :value="opts.min_inlier_ratio" @input="opts.min_inlier_ratio = boundedNumber(Number(($event.target as HTMLInputElement).value), 0.01, 1.0)" title="内点比例阈值，用于兜底判定：比例越高越严格（有效范围 0.01-1.0）" /></div>
              <div class="cell" title="仅影响特征匹配参数；二维码/印章模式不会使用这些特征点参数"><span class="label-inline">参数预设：</span><AppSelect :model-value="preset" :options="presetOptions" :disabled="!isFeatureMode" min-width="130px" @update:model-value="applyPreset($event as any)" /></div>
            </div>
          </details>

          </div><!-- /params-scroll -->
        </fieldset>

        <!-- ③ 调参与操作 -->
        <fieldset class="tune-group glass-card section-card">
          <div class="scan-card-title">参数测试</div>
          <div class="tune-grid">
            <div class="tune-line">
              <label class="label-inline">页码：</label>
              <input class="input tune-input" type="number" min="1" :max="pdfPageCount || undefined" :value="probePageIndex" @input="probePageIndex = boundedProbePage(Number(($event.target as HTMLInputElement).value))" title="对指定页进行一次识别测试，并输出匹配/内点统计" />
              <button class="btn btn-outline tune-btn" :disabled="!canRun" @click="runProbePage" title="对指定页执行一次完整识别流程，查看是否命中标记">测试单页</button>
            </div>
            <div class="tune-line">
              <label class="label-inline">前 N 页：</label>
              <input class="input tune-input" type="number" min="1" :value="quickScanPageLimit" @input="quickScanPageLimit = positiveInt(Number(($event.target as HTMLInputElement).value), 30)" title="只扫描前N页，便于快速调参" />
              <button class="btn btn-outline tune-btn" :disabled="!canRun" @click="runScanOnly" title="只扫描前N页查找标记页，不输出文件，便于快速验证参数">快速扫描</button>
            </div>
          </div>
        </fieldset>

        <div class="row scan-actions action-footer">
          <button class="btn btn-primary btn-lg flex-1" :disabled="!canRun" @click="execute">开始扫描拆分</button>
          <button class="btn btn-secondary btn-lg" :disabled="!taskState.running" @click="cancelTask">停止</button>
        </div>

      </section>
    </div>

    <div v-if="roiDialogOpen" class="modal-backdrop" @click.self="closeRoiDialog" @keydown.esc="closeRoiDialog" tabindex="-1">
      <div class="roi-dialog modal-panel modal-panel-lg glass-card">
        <div class="modal-header">
          <div class="roi-dialog-title">
            <h3>框选区域</h3>
            <p>框选模式：拖拽平移 · 选区模式：直接拖拽框选</p>
          </div>
        </div>
        <div class="preview-zoom-toolbar">
          <button class="btn btn-ghost btn-sm" @click="zoomPreview(-0.1)">−</button>
          <span class="zoom-label">{{ Math.round(previewZoom * 100) }}%</span>
          <button class="btn btn-ghost btn-sm" @click="zoomPreview(0.1)">＋</button>
          <button class="btn btn-ghost btn-sm" @click="resetRoiZoom">适中</button>
          <div class="roi-mode-toggle">
            <button class="btn btn-sm" :class="roiDrawMode ? 'btn-primary' : 'btn-outline'" @click="roiDrawMode = !roiDrawMode">
              {{ roiDrawMode ? '选区模式' : '框选模式' }}
            </button>
          </div>
        </div>
        <div
          ref="roiStageRef"
          class="roi-dialog-stage"
          @wheel="onRoiStageWheel"
        >
          <div class="roi-dialog-canvas" :style="roiCanvasSize"
            @pointerdown="onRoiPointerDown"
            @pointermove="onRoiPointerMove"
            @pointerup="onRoiPointerUp"
            @pointercancel="onRoiPointerUp"
          >
            <img ref="roiImgRef" class="roi-dialog-img" :src="previewDataUrl" @load="onRoiImageLoaded" draggable="false" />
            <div v-if="roiDialogActiveRoi" class="roi-box" :style="roiDialogStyle" />
          </div>
        </div>
        <div class="roi-dialog-actions modal-footer">
          <button class="btn btn-secondary" @click="clearRoi">清除</button>
          <span class="roi-summary flex-1 selectable">
            {{ opts.reference_roi ? `区域：x=${opts.reference_roi[0]}，y=${opts.reference_roi[1]}，w=${opts.reference_roi[2]}，h=${opts.reference_roi[3]}` : '未框选区域' }}
          </span>
          <button class="btn btn-outline" @click="closeRoiDialog">取消</button>
          <button class="btn btn-primary" @click="confirmRoiDialog">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scan-grid {
  --panel-grid-columns: minmax(320px, 1fr) minmax(320px, 1fr);
}

/* 左侧 */
.left-col {
  display: grid;
  grid-template-rows: minmax(0, 1.55fr) minmax(220px, 1fr);
  gap: var(--space-3);
  min-height: 0;
  overflow: hidden;
}
.scan-card-title {
  flex-shrink: 0;
  margin: 0 0 8px;
  font-size: var(--font-md);
  font-weight: 600;
  color: var(--color-gray-800);
  line-height: 1.4;
}
.ref-group-inner {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.ref-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
}
.ref-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
}
.ref-icon { font-size: 36px; }
.ref-title { font-size: var(--font-md); font-weight: 600; color: var(--color-gray-800); max-width: 80%; }
.ref-hint {
  text-align: center;
  font-size: var(--font-sm);
  color: var(--color-gray-500);
  max-width: 280px;
  line-height: 1.5;
}

/* 骨架屏 */
.skeleton-preview {
  flex: 1 1 0;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
}
.skeleton-box {
  width: 100%;
  flex: 1 1 0;
  min-height: 80px;
  background: var(--color-gray-200);
  border-radius: var(--radius);
  animation: skeleton-pulse 1.6s ease-in-out infinite;
}
.skeleton-line {
  height: 12px;
  width: 100%;
  background: var(--color-gray-200);
  border-radius: 6px;
  animation: skeleton-pulse 1.6s ease-in-out infinite;
}
.skeleton-line.short {
  width: 60%;
}
@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.preview-wrap {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preview-zoom-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-shrink: 0;
  padding-top: 2px;
  overflow: visible;
}
.zoom-label {
  min-width: 44px;
  text-align: center;
  font-size: var(--font-sm);
  color: var(--color-gray-600);
}
.preview-stage {
  position: relative;
  flex: 1 1 0;
  min-height: 160px;
  width: 100%;
  max-width: 100%;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.25);
  overflow: auto;
  cursor: grab;
}
.preview-canvas {
  position: relative;
  margin: auto;
  min-width: 1px;
  min-height: 1px;
}
.preview-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: fill;
  user-select: none;
  pointer-events: none;
}
.roi-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-shrink: 0;
  min-height: 34px;
  padding-top: 2px;
  overflow: visible;
}
.keypoint-info {
  font-size: var(--font-sm);
  color: var(--color-gray-600);
  flex-shrink: 0;
}
.roi-box {
  position: absolute;
  border: 2px solid var(--color-primary);
  background: rgba(35, 115, 245, 0.16);
  box-shadow: 0 0 0 9999px rgba(17, 24, 39, 0.12);
  pointer-events: none;
}
.roi-summary {
  font-size: var(--font-sm);
  color: var(--color-primary);
}

.log-group-inner {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.log-box {
  flex: 1 1 0;
  min-height: 0;
  border: 1px solid rgba(148, 163, 184, 0.42);
  border-radius: var(--radius);
  padding: 10px 12px;
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.92), rgba(241, 245, 249, 0.68)),
    rgba(255, 255, 255, 0.34);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.62);
  overflow: auto;
  margin-top: 6px;
}
.log-box.empty { display: flex; align-items: center; justify-content: center; }
.log-placeholder { color: var(--color-gray-500); font-size: var(--font-md); }
.log-lines {
  font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Consolas, monospace;
  font-size: 13px;
  line-height: 1.62;
  color: var(--color-gray-900);
}
.log-line {
  position: relative;
  padding: 3px 8px 3px 12px;
  border-left: 3px solid rgba(148, 163, 184, 0.36);
  border-radius: 5px;
  white-space: pre-wrap;
  word-break: break-word;
}
.log-line + .log-line {
  margin-top: 3px;
}
.log-line.info {
  color: var(--color-primary-dark);
  border-left-color: rgba(37, 99, 235, 0.68);
  background: rgba(239, 246, 255, 0.74);
}
.log-line.ok {
  color: #166534;
  border-left-color: rgba(22, 163, 74, 0.68);
  background: rgba(220, 252, 231, 0.72);
}
.log-line.warn {
  color: #92400e;
  border-left-color: rgba(217, 119, 6, 0.68);
  background: rgba(254, 243, 199, 0.72);
}
.log-line.danger {
  color: #991b1b;
  border-left-color: rgba(220, 38, 38, 0.68);
  background: rgba(254, 226, 226, 0.72);
}
.progress { margin-top: 6px; }
.progress-line { font-size: var(--font-md); color: var(--color-gray-600); margin: 5px 0; }
.error-line { color: var(--color-danger); font-size: var(--font-md); margin: 5px 0; }
.summary-line { font-size: var(--font-md); margin: 5px 0; }
.summary-line.ok { color: var(--color-success); }
.btn-mini {
  margin-left: 8px;
  padding: 2px 8px;
  font-size: var(--font-sm);
}
.result-box {
  max-height: 76px;
  overflow: auto;
  border: 0.5px solid var(--glass-border);
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  background: rgba(255, 255, 255, 0.22);
  font-size: var(--font-sm);
  color: var(--color-gray-700);
}
.result-line + .result-line { margin-top: 3px; }

/* 右侧 */
.right-col {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-height: 0;
  min-width: 0;
}

.input-group {
  padding: 10px;
  flex-shrink: 0;
}

.params-group {
  padding: 10px;
  flex: 1 1 0;
}
.params-scroll {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 2px;
}
.qr-text-input {
  min-width: 150px;
  max-width: 220px;
}
.input-num-compact {
  width: 64px;
  flex: 0 0 64px;
  padding-left: 8px;
  padding-right: 4px;
}
.scan-actions {
  flex-shrink: 0;
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
}
.scan-pick-btn {
  width: 128px;
  min-width: 100px;
  flex-shrink: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.row.wrap { flex-wrap: wrap; }
.row.mt-2 { margin-top: 7px; }
.row.mt-3 { margin-top: 10px; }
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 10px; }
.label-inline {
  font-size: var(--font-md);
  color: var(--color-gray-700);
  font-weight: 500;
  white-space: nowrap;
}
.detection-select { flex: 0 0 260px; }
.grid-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 8px 10px;
}
.cell {
  display: grid;
  grid-template-columns: minmax(74px, max-content) minmax(72px, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.cell .input-num {
  width: 100%;
  flex-basis: auto;
  min-width: 0;
}
.cell .app-select {
  width: 100%;
}
.note {
  margin-top: 6px;
  margin-bottom: 0;
  font-size: var(--font-sm);
  color: var(--color-gray-500);
  line-height: 1.35;
}
.hint-chip {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: var(--font-sm);
  line-height: 1.4;
}
.hint-chip.info {
  color: var(--color-primary-dark);
  background: var(--color-primary-bg);
}
.hint-chip.warn {
  color: var(--color-warning);
  background: var(--color-warning-bg);
}
.hint-line {
  justify-content: flex-start;
  color: var(--color-gray-500);
  font-size: var(--font-sm);
}
.params-hint {
  margin-top: 4px;
}
.params-hint.warn { color: var(--color-warning); }
.compact-hint {
  margin: 0;
  flex: 1 1 160px;
  min-width: 0;
}
.span-2 { grid-column: 1 / -1; }
.params-divider {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 0;
  color: var(--color-gray-400);
  font-size: var(--font-sm);
  font-weight: 500;
}
.params-divider::before,
.params-divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--color-border);
}
.params-sep {
  color: var(--color-gray-300);
  font-size: var(--font-sm);
  user-select: none;
  padding: 0 2px;
}
.advanced-group {
  padding: 6px 8px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.24);
  min-width: 0;
  overflow: hidden;
}
.advanced-group summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  cursor: pointer;
  font-weight: 600;
  color: var(--color-gray-700);
  user-select: none;
  min-width: 0;
}
.advanced-group summary > span:first-child {
  flex: 0 0 auto;
}
.advanced-summary {
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-gray-500);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.advanced-hint {
  margin: 6px 0 0;
  font-size: var(--font-sm);
  color: var(--color-gray-500);
  line-height: 1.45;
}
.advanced-group .grid-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.advanced-group .cell {
  grid-template-columns: minmax(0, 1fr);
  align-items: stretch;
  gap: 4px;
}
.advanced-group .cell .label-inline {
  font-size: var(--font-sm);
  color: var(--color-gray-500);
}
.advanced-group .cell .app-select {
  min-width: 0 !important;
}
.tune-group {
  padding: 8px 10px;
  min-width: 0;
}
.tune-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: center;
  gap: 8px 10px;
  min-width: 0;
}
.tune-line {
  display: grid;
  grid-template-columns: max-content minmax(48px, 1fr) minmax(78px, max-content);
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.tune-input {
  width: 100%;
  min-width: 0;
}
.tune-btn {
  min-width: 78px;
  padding-left: 10px;
  padding-right: 10px;
  white-space: nowrap;
}
@media (max-width: 1120px) {
  .scan-grid {
    --panel-grid-columns: minmax(260px, 0.92fr) minmax(0, 1.08fr);
  }

  .detection-select { flex: 0 0 180px; }
  .scan-pick-btn { width: 110px; min-width: 90px; }
  .input-num {
    width: 84px;
    flex-basis: 84px;
  }
  .tune-grid {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .tune-line {
    grid-template-columns: max-content minmax(54px, 1fr) minmax(84px, max-content);
  }
  .grid-2 {
    grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
    gap: 6px;
  }
  .advanced-group .grid-2 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .cell {
    grid-template-columns: minmax(68px, max-content) minmax(64px, 1fr);
  }
  .advanced-group .cell {
    grid-template-columns: minmax(0, 1fr);
  }
  .params-group .row.wrap {
    row-gap: 6px;
  }
}
.roi-dialog {
  padding: 14px;
}
.roi-dialog-title p {
  display: inline-flex;
  align-items: center;
  margin-top: 6px;
  padding: 5px 10px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 999px;
  color: var(--color-primary-dark);
  background: rgba(239, 246, 255, 0.86);
  font-size: var(--font-md);
  font-weight: 500;
}
.roi-dialog-stage {
  display: flex;
  flex: 1 1 0;
  min-height: 0;
  margin-top: 6px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.38);
  overflow: auto;
}
.roi-dialog-canvas {
  position: relative;
  margin: auto;
  cursor: crosshair;
  flex-shrink: 0;
}
.roi-dialog-img {
  display: block;
  width: 100%;
  height: 100%;
  user-select: none;
  pointer-events: none;
}
.roi-dialog-actions {
  margin: 12px -14px -14px;
}
.roi-mode-toggle {
  margin-left: auto;
}
</style>
