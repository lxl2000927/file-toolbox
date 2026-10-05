<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount, onActivated, onDeactivated } from "vue";
import type { ScanDetectionMode, ScanSplitOptions } from "../../env";
import { useEngineTask, generateTaskId } from "../../composables/useEngineTask";
import { useAppDialog } from "../../composables/useAppDialog";
import { useToast } from "../../composables/useToast";
import { fileBasename, positiveInt, formatEngineError } from "../../utils";
import AppSelect from "../common/AppSelect.vue";
import PanelBanner from "../common/PanelBanner.vue";
import AppTabs from "../common/AppTabs.vue";
import AppIcon from "../common/AppIcon.vue";
import ScanReview from "./ScanReview.vue";
import ProcessingPresets from "../common/ProcessingPresets.vue";
import { normalizeScanSplitResult, scanCompletionNotice, scanPhaseLabel, type ScanSplitTaskResult } from "../../scan-results";
import { useWorkspacePersistence } from '../../composables/useWorkspacePersistence';
import type { WorkspaceState, WorkspaceSource } from '../../../../shared/workspace-types';
import { reviewSegments } from '../../pdf-workbench';

type TuneResult = { title: string; lines: string[] };
type PresetName = "" | "balanced" | "strict" | "loose" | "high_recall";

const pdfPath = ref("");
const dialog = useAppDialog();
const toast = useToast();
const referenceImage = ref("");
const outputDir = ref("");
const prefix = ref("");
const error = ref("");
const result = ref<ScanSplitTaskResult | null>(null);
const tuneResult = ref<TuneResult | null>(null);
const review = ref<{ pdfPath: string; signature: string; total: number; markers: number[]; segments?: number[][]; options: ScanSplitOptions } | null>(null);
let restoringWorkspace = false;
const outputNeedsSelection = ref(false);
const restoredSourceMetadata = new Map<string, WorkspaceSource>();
const reviewBusy = ref(false);
const reviewExpanded = ref(true);
const reviewInvalidated = ref(false);
const scanOutputCompression = ref<'none' | 'lossless'>('lossless');
const scanOutputOcr = ref(false);
let pendingReview: { pdfPath: string; options: ScanSplitOptions; configuration: string } | null = null;
const reviewConfiguration = () => JSON.stringify([pdfPath.value, referenceImage.value, buildScanOptions()]);
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
const roiDialogRef = ref<HTMLElement | null>(null);
const roiConfirmBtnRef = ref<HTMLButtonElement | null>(null);
const dpiInputRef = ref<HTMLInputElement | null>(null);
const previewNaturalSize = ref({ width: 0, height: 0 });
const roiNaturalSize = ref({ width: 0, height: 0 });
const selectionStart = ref<{ x: number; y: number } | null>(null);
const selectionDraft = ref<[number, number, number, number] | null>(null);
const roiDialogOpen = ref(false);
const roiDrawMode = ref(false);
const previewZoom = ref(1.0);
let previewNeedsFit = false;
let previewResizeObserver: ResizeObserver | null = null;

const opts = ref<Required<Omit<ScanSplitOptions, "reference_roi" | "use_roi">> & { reference_roi: [number, number, number, number] | null }>({
  detection_mode: "auto",
  dpi: 220,
  qrcode_text_contains: "",
  qrcode_no_decode: false,
  qrcode_use_roi: false,
  qrcode_skip_pages: 0,
  qrcode_max_attempts: 144,
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
  auto_detect_stamp: true,
  auto_detect_qrcode: true,
  auto_detect_feature: true,
  feature_strict: true,
  qrcode_dpi_retries: 2,
});

const useMaxSegment = ref(false);
const probePageIndex = ref(1);
const quickScanPageLimit = ref(30);
const preset = ref<PresetName>("balanced");
const parameterTab = ref("basic");
const activityTab = ref("reference");
const parameterTabs = computed(() => [
  { value: "basic", label: "基础设置" },
  { value: "qrcode", label: "二维码", disabled: !isQrMode.value },
  { value: "feature", label: "特征匹配", disabled: !isFeatureMode.value },
]);

watch([result, tuneResult], () => {
  if (result.value || tuneResult.value) activityTab.value = "results";
});
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

const { state: taskState, logs, busy: scanTaskBusy, cancellable: taskCancellable, start: startTask, markSubmitted, markQueued, cancel: cancelTask, reset: resetTask } = useEngineTask({
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
        if (pendingReview && pendingReview.configuration === reviewConfiguration()) {
          const raw = asRecord(payload.result);
          const signature = String(raw.file_signature || '');
          if (signature) {
            review.value = { pdfPath: pendingReview.pdfPath, signature, total: Number(raw.total_pages),
              markers: Array.isArray(raw.marker_pages) ? raw.marker_pages.map(Number) : [], options: pendingReview.options };
            reviewExpanded.value = true;
            tuneResult.value = { title: '全量扫描完成，等待人工复核', lines: tuneResult.value?.lines || [] };
          }
          else error.value = '扫描结果缺少文件快照，请重启程序后重新扫描。';
        }
        pendingReview = null;
        activityTab.value = 'results';
        return;
      }
      tuneResult.value = null;
      result.value = normalizeScanSplitResult(payload.result);
      dialog.alert(scanCompletionNotice(result.value));
    } else {
      pendingReview = null;
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
      error.value = formatEngineError(payload);
      // #27 取消时已生成的文件不删除，提示用户保留了多少个
      const partialResult = normalizeScanSplitResult(payload.result);
      if (payload.cancelled && partialResult.output_files.length > 0) {
        toast.info(`已取消，但保留了 ${partialResult.output_files.length} 个已生成的文件`);
      }
      const partialMessage = payload.result && taskType === "scan_split"
        ? scanCompletionNotice({ ...partialResult, error: partialResult.error || error.value }).message : error.value;
      dialog.alert({ title: payload.cancelled ? "扫描任务已取消" : "扫描任务失败", message: partialMessage, kind: payload.cancelled ? "info" : "danger" });
    }
  },
});

const taskBusy = computed(() => scanTaskBusy.value || reviewBusy.value || workspace.restoring.value || !workspace.ready.value);
const savedScanSettings = computed(() => ({ options: buildScanOptions(), prefix: prefix.value,
  useMaxSegment: useMaxSegment.value, compression: scanOutputCompression.value, ocr: scanOutputOcr.value }));
function applySavedScan(settings: Record<string, unknown>) {
  const saved = asRecord(settings.options);
  for (const key of Object.keys(opts.value) as (keyof typeof opts.value)[]) {
    const value = saved[key];
    if (value === undefined) continue;
    if (key === 'reference_roi') opts.value.reference_roi = readRoi(value);
    else if (typeof value === typeof opts.value[key]) (opts.value as Record<string, unknown>)[key] = value;
  }
  if (!['auto', 'stamp', 'qrcode', 'feature'].includes(opts.value.detection_mode)) opts.value.detection_mode = 'auto';
  if (typeof settings.prefix === 'string') prefix.value = settings.prefix.slice(0, 100);
  useMaxSegment.value = settings.useMaxSegment === true;
  scanOutputCompression.value = settings.compression === 'none' ? 'none' : 'lossless';
  scanOutputOcr.value = settings.ocr === true;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function inferScanTaskType(payload: unknown) {
  const taskId = taskState.value.taskId || "";
  const value = asRecord(payload);
  if ("page_index" in value && "marked" in value) return "scan_probe";
  if (taskId.startsWith("probe_")) return "scan_probe";
  if (taskId.startsWith("scan_only_")) return "scan_only";
  return "scan_split";
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

function formatProbeTuneResult(payload: unknown): TuneResult {
  const value = asRecord(payload);
  const pageNumber = Number(value.page_number || Number(value.page_index || 0) + 1 || 0);
  const totalPages = Number(value.total_pages || 0);
  const marked = Boolean(value.marked);
  const reason = String(value.reason || "");
  const lines = [
    `页码：第 ${pageNumber || "-"} 页${totalPages ? ` / 共 ${totalPages} 页` : ""}`,
    `结果：${marked ? "命中标记页" : "未命中"}${reason ? `（${reason}）` : ""}`,
    `模式：${modeLabel(String(value.detection_mode || ""))}`,
  ];

  const stamp = asRecord(value.stamp);
  if (stamp.executed || stamp.present || stamp.candidates != null) {
    lines.push(`印章：${stamp.present ? "命中" : "未命中"}，候选 ${Number(stamp.candidates || 0)}，面积占比 ${formatNumber(stamp.area_ratio, 4)}，圆度 ${formatNumber(stamp.circularity)}`);
  } else if (value.detection_mode === "auto" && stamp.skipped_reason) {
    lines.push(`印章：未执行（${stamp.skipped_reason}）`);
  }
  if (Array.isArray(stamp.diagnostics)) {
    for (const diagnostic of stamp.diagnostics) lines.push(`印章降级：${String(diagnostic)}`);
  }

  const qrcode = asRecord(value.qrcode);
  if (qrcode.executed || qrcode.present || (Array.isArray(qrcode.infos) && qrcode.infos.length) || qrcode.stats) {
    const decoded = Array.isArray(qrcode.infos) ? qrcode.infos.length : 0;
    const qrStats = asRecord(qrcode.stats);
    const stats = qrcode.stats ? `，面积 ${formatNumber(qrStats.area, 0)}，形状 ${formatNumber(qrStats.aspect)}` : "";
    lines.push(`二维码：${qrcode.present || decoded ? "有候选" : "无"}，解码 ${decoded} 个${stats}`);
  } else if (value.detection_mode === "auto" && qrcode.skipped_reason) {
    lines.push(`二维码：未执行（${qrcode.skipped_reason}）`);
  }
  if (Array.isArray(qrcode.diagnostics)) {
    for (const diagnostic of qrcode.diagnostics) lines.push(`二维码降级：${String(diagnostic)}`);
  }

  const feature = asRecord(value.feature);
  if (feature.executed || feature.good_matches || feature.inliers) {
    lines.push(`特征点：匹配 ${Number(feature.good_matches || 0)}，内点 ${Number(feature.inliers || 0)}，比例 ${formatNumber(feature.inlier_ratio)}`);
  } else if (value.detection_mode === "auto" && feature.skipped_reason) {
    lines.push(`特征点：未执行（${feature.skipped_reason}）`);
  }

  const params = asRecord(value.params);
  lines.push(`参数：DPI ${Number(params.dpi || opts.value.dpi)}，特征点 ${Number(params.nfeatures || opts.value.nfeatures)}，最小匹配 ${Number(params.min_matches || opts.value.min_matches)}`);
  return { title: `单页测试完成：${marked ? "命中" : "未命中"}`, lines };
}

function formatScanOnlyTuneResult(payload: unknown): TuneResult {
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
  if (!workspace.ready.value || workspace.restoring.value) return;
  const paths = await window.electronAPI?.openFileDialog({
    multi: false,
    filters: [{ name: "PDF 文件", extensions: ["pdf"] }],
  });
  if (paths?.[0]) {
    restoredSourceMetadata.delete(paths[0]); review.value = null;
    workspace.statuses.value = workspace.statuses.value.filter(item => item.path !== paths[0]);
    pdfPath.value = paths[0]; await updatePdfPageCount(); workspace.markDirty();
  }
}

async function pickReference() {
  if (!workspace.ready.value || workspace.restoring.value) return;
  const paths = await window.electronAPI?.openFileDialog({
    multi: false,
    filters: [
      { name: "图像 / PDF", extensions: ["png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp", "gif", "pdf"] },
    ],
  });
  if (paths?.[0]) {
    restoredSourceMetadata.delete(paths[0]); review.value = null;
    workspace.statuses.value = workspace.statuses.value.filter(item => item.path !== paths[0]);
    referenceImage.value = paths[0]; loadReferencePreview(); workspace.markDirty();
  }
}

watch(referenceImage, () => {
  if (restoringWorkspace) return;
  opts.value.reference_roi = null;
  selectionDraft.value = null;
  previewZoom.value = 0.5;
  loadReferencePreview();
});

watch(() => opts.value.nfeatures, () => {
  if (restoringWorkspace) return;
  if (referenceImage.value) loadReferencePreview();
});

watch(pdfPath, () => {
  if (restoringWorkspace) return;
  updatePdfPageCount();
});

watch(
  () => opts.value.detection_mode,
  (mode) => {
    if (restoringScanSettings || restoringWorkspace) return;
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
      quickScanPageLimit.value = Math.max(1, quickScanPageLimit.value);
    }
  } catch {
    if (token === pdfPageCountToken) pdfPageCount.value = null;
  }
}

let referencePreviewTimer: ReturnType<typeof setTimeout> | null = null;

function loadReferencePreview() {
  const token = ++referencePreviewToken;
  if (referencePreviewTimer) clearTimeout(referencePreviewTimer);
  referencePreviewTimer = null;
  previewLoading.value = !!referenceImage.value;
  if (!referenceImage.value) {
    previewDataUrl.value = "";
    previewNaturalSize.value = { width: 0, height: 0 };
    roiNaturalSize.value = { width: 0, height: 0 };
    previewError.value = "";
    keypointInfo.value = "";
    return;
  }
  referencePreviewTimer = setTimeout(() => {
    referencePreviewTimer = null;
    generateReferencePreview(token);
  }, 180);
}

async function generateReferencePreview(token: number) {
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
      const previewResult = await window.electronAPI?.getFilePreviewUrl(currentReference);
      if (token !== referencePreviewToken || currentReference !== referenceImage.value) return;
      if (previewResult?.ok) {
        previewDataUrl.value = previewResult.value;
        keypointInfo.value = "引擎未就绪，仅显示原图";
        return;
      }
      previewError.value = previewResult?.error.message || "无法读取参考图片，请确认文件路径有效。";
      return;
    }

    previewError.value = "引擎未就绪，请稍后重试。";
  } catch (caught) {
    if (token === referencePreviewToken) previewError.value = formatEngineError(caught);
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
  const fitted = roiDialogOpen.value ? _roiFittedZoom : _fittedZoom;
  const minZoom = fitted > 0 ? fitted : 0.2;
  previewZoom.value = Math.min(3, Math.max(minZoom, Number(value) || 1));
}

function zoomPreview(delta: number) {
  setPreviewZoom(Number((previewZoom.value + delta).toFixed(2)));
}

function resetPreviewZoom() {
  fitPreviewToStage();
}

function fitPreviewToStage(preserveManualZoom = false) {
  const stage = previewStageRef.value;
  const natural = previewNaturalSize.value;
  if (!stage || !natural.width || !natural.height || stage.clientWidth <= 0 || stage.clientHeight <= 0) {
    previewNeedsFit = true;
    return;
  }
  const pad = 16;
  const fit = Math.min(
    (stage.clientWidth - pad) / natural.width,
    (stage.clientHeight - pad) / natural.height,
  );
  const wasFitted = Math.abs(previewZoom.value - _fittedZoom) < 0.001;
  _fittedZoom = Math.min(1.0, Math.max(0.1, fit));
  previewZoom.value = !preserveManualZoom || previewNeedsFit || wasFitted
    ? _fittedZoom : Math.max(_fittedZoom, previewZoom.value);
  previewNeedsFit = false;
}

watch(previewStageRef, (stage) => {
  previewResizeObserver?.disconnect();
  previewResizeObserver = null;
  if (!stage || typeof ResizeObserver === "undefined") return;
  previewResizeObserver = new ResizeObserver(() => {
    if (stage.clientWidth > 0 && stage.clientHeight > 0 && !roiDialogOpen.value) fitPreviewToStage(true);
  });
  previewResizeObserver.observe(stage);
}, { flush: "post" });
watch(activityTab, async (tab) => {
  if (tab !== "reference" || !previewNeedsFit) return;
  await nextTick();
  fitPreviewToStage();
});
onActivated(() => nextTick(() => {
  if (previewDataUrl.value && activityTab.value === "reference") fitPreviewToStage(true);
}));
onDeactivated(() => {
  selectionDraft.value = null;
  selectionStart.value = null;
  panStart.value = null;
  roiPanStart.value = null;
  if (roiDialogOpen.value) closeRoiDialog();
});
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
  if (e.button !== 0) return;
  if (roiDrawMode.value) { onRoiPointerDown(e); return; }
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
  if (selectionStart.value) { onRoiPointerMove(e); return; }
  if (!panStart.value || !previewStageRef.value) return;
  const dx = panStart.value.x - e.clientX;
  const dy = panStart.value.y - e.clientY;
  previewStageRef.value.scrollLeft = panStart.value.scrollLeft + dx;
  previewStageRef.value.scrollTop = panStart.value.scrollTop + dy;
}

function onStagePointerUp(e: PointerEvent) {
  if (selectionStart.value) { onRoiPointerUp(e); return; }
  panStart.value = null;
  if (previewStageRef.value) previewStageRef.value.style.cursor = "";
  try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch {}
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
  if (e.button !== 0 || !previewDataUrl.value) return;
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
    if (w >= 6 && h >= 6) opts.value.reference_roi = selectionDraft.value;
    selectionDraft.value = null;
    selectionStart.value = null;
    if (!roiDialogOpen.value) loadReferencePreview();
  }
  roiPanStart.value = null;
  try {
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  } catch {}
}

function onRoiPointerCancel(e: PointerEvent) {
  selectionDraft.value = null;
  selectionStart.value = null;
  panStart.value = null;
  roiPanStart.value = null;
  if (previewStageRef.value) previewStageRef.value.style.cursor = "";
  try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch {}
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
let roiPreviouslyFocused: HTMLElement | null = null;

function roiFocusableElements(): HTMLElement[] {
  const root = roiDialogRef.value;
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => element.offsetParent !== null || element === document.activeElement);
}

function onRoiDialogKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeRoiDialog();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = roiFocusableElements();
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !roiDialogRef.value?.contains(active))) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && (active === last || !roiDialogRef.value?.contains(active))) {
    event.preventDefault();
    first?.focus();
  }
}

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
  roiPreviouslyFocused = document.activeElement as HTMLElement | null;
  selectionDraft.value = null;
  selectionStart.value = null;
  _savedPreviewZoom = previewZoom.value;
  roiDialogOpen.value = true;
  nextTick(() => {
    syncPreviewNaturalSize(roiImgRef.value || previewImgRef.value);
    roiConfirmBtnRef.value?.focus();
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
  const restore = roiPreviouslyFocused;
  roiPreviouslyFocused = null;
  nextTick(() => {
    fitPreviewToStage(true);
    if (restore?.isConnected) restore.focus();
  });
}

function confirmRoiDialog() {
  if (!opts.value.reference_roi) {
    previewError.value = "请先框选区域，或取消后继续使用全图识别。";
    return;
  }
  closeRoiDialog();
  loadReferencePreview();
}

async function copyResults() {
  if (!result.value) return;
  const lines = [
    `生成文件：${result.value.output_files.length}`,
    `总页数：${result.value.total_pages}`,
    `标记页：${result.value.marker_pages.map((p) => p + 1).join(", ") || "无"}`,
    ...(result.value.error ? [`写入失败：${result.value.error}`] : []),
    ...result.value.warnings,
    ...result.value.suspect_segments.map((segment) => `疑似漏检：第 ${segment.index} 段，第 ${segment.start_page}-${segment.end_page} 页，共 ${segment.page_count} 页`),
    ...result.value.failed_segments.map((segment) => `失败分段：第 ${segment.index} 段，第 ${segment.start_page}-${segment.end_page} 页`),
    ...result.value.pending_segments.map((segment) => `待处理分段：第 ${segment.index} 段，第 ${segment.start_page}-${segment.end_page} 页`),
    ...result.value.output_files,
  ];
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    toast.success("结果已复制");
  } catch {
    toast.error("复制失败，请检查剪贴板权限");
  }
}

function logLineClass(line: string) {
  if (/失败|错误|不可用|异常|超时/.test(line)) return "danger";
  if (/未能解码|未视为|不包含关键字|漏检|未匹配|未命中|忽略|警告|取消|回退/.test(line)) return "warn";
  if (/按设置跳过|已跳过/.test(line)) return "info";
  if (/完成|命中|检测到|生成|成功/.test(line)) return "ok";
  if (/开始|正在|扫描|测试/.test(line)) return "info";
  return "";
}

const scanBannerKind = computed(() => {
  if (taskState.value.running) return "info";
  if (error.value) return "warning";
  if (result.value && scanCompletionNotice(result.value).kind === "warning") return "warning";
  if (result.value) return "success";
  if (!pdfPath.value) return "warning";
  if (!outputDir.value) return "info";
  return "success";
});
const scanBannerMessage = computed(() => {
  if (taskState.value.running) return `正在处理：${scanPhaseLabel(taskState.value.phase) || "扫描中"}…`;
  if (error.value) return error.value;
  if (review.value) return reviewBusy.value ? '正在输出复核结果，请等待完成或取消。' : `已扫描 ${review.value.total} 页，请检查分隔标记后确认导出。`;
  if (result.value && !result.value.marker_pages.length) return "未发现标记页，未发生拆分，请调整识别方式或参数。";
  if (result.value?.suspect_segments.length) return `完成：发现 ${result.value.suspect_segments.length} 个疑似漏检分段，请检查结果。`;
  if (result.value) return `完成：生成 ${result.value.output_files.length} 个文件，标记页 ${result.value.marker_pages.length} 个`;
  if (!pdfPath.value) return "请先选择 PDF 文件，再选择识别方式并开始扫描。";
  if (!referenceImage.value && opts.value.detection_mode === "feature") return "特征匹配模式需要选择参考文件";
  if (!outputDir.value) return "输出目录未指定，确认导出时选择目录";
  return `输出目录：${outputDir.value}`;
});

async function pickOutputDir() {
  const dir = await window.electronAPI?.openDirectoryDialog({ title: "选择输出目录" });
  if (dir) { outputDir.value = dir; outputNeedsSelection.value = false; }
}

let _applyingPreset = false;

function applyPreset(name: PresetName) {
  _applyingPreset = true;
  try {
    if (!name) {
      preset.value = "";
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
      opts.value.nfeatures = Math.min(10000, Math.max(100, 1200));
      opts.value.ratio = Math.min(1.0, Math.max(0.1, 0.75));
      opts.value.min_matches = Math.min(1000, Math.max(1, 25));
      opts.value.ransac_reproj_threshold = Math.min(50.0, Math.max(0.1, 5.0));
      opts.value.min_inlier_ratio = Math.min(1.0, Math.max(0.01, 0.45));
    }
    preset.value = name;
  } finally {
    _applyingPreset = false;
  }
}

// 手动修改参数时重置 preset
watch(
  () => [opts.value.nfeatures, opts.value.min_matches, opts.value.ratio, opts.value.ransac_reproj_threshold, opts.value.min_inlier_ratio],
  () => { if (!_applyingPreset) preset.value = ""; },
  { flush: "sync" },
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

function boundedQuickScanPageLimit(value: number) {
  return positiveInt(Number(value), 30);
}

const quickScanPageLimitExceeded = computed(() => (
  !!pdfPageCount.value && quickScanPageLimit.value > pdfPageCount.value
));

const effectiveQuickScanPageLimit = computed(() => (
  pdfPageCount.value
    ? Math.min(quickScanPageLimit.value, pdfPageCount.value)
    : quickScanPageLimit.value
));

const quickScanPageLimitHint = computed(() => (
  quickScanPageLimitExceeded.value
    ? `PDF 共 ${pdfPageCount.value} 页，请输入 1-${pdfPageCount.value}`
    : ""
));

function onProbePageInput(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.value === "") return;
  probePageIndex.value = boundedProbePage(Number(input.value));
}

function onProbePageBlur(e: Event) {
  const input = e.target as HTMLInputElement;
  const value = input.value === ""
    ? boundedProbePage(probePageIndex.value)
    : boundedProbePage(Number(input.value));
  probePageIndex.value = value;
  input.value = String(value);
}

function onQuickScanPageLimitInput(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.value === "") return;
  quickScanPageLimit.value = boundedQuickScanPageLimit(Number(input.value));
}

function onQuickScanPageLimitBlur(e: Event) {
  const input = e.target as HTMLInputElement;
  const value = input.value === ""
    ? boundedQuickScanPageLimit(quickScanPageLimit.value)
    : boundedQuickScanPageLimit(Number(input.value));
  quickScanPageLimit.value = value;
  input.value = String(value);
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

function setDpi(value: number) {
  opts.value.dpi = value;
  if (dpiInputRef.value) dpiInputRef.value.value = String(value);
}

function onDpiBlur(e: Event) {
  const raw = (e.target as HTMLInputElement).value;
  const n = Number(raw);
  const clamped = Math.min(300, Math.max(72, Math.floor(Number.isFinite(n) && n > 0 ? n : 180)));
  opts.value.dpi = clamped;
  (e.target as HTMLInputElement).value = String(clamped);
}

function onMaxSegmentInput(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.value === "") return;
  opts.value.max_segment_pages = Math.min(10000, Math.max(1, positiveInt(input.value)));
}

function onFeatureIntInput(e: Event, key: "nfeatures" | "min_matches") {
  const input = e.target as HTMLInputElement;
  if (input.value === "") return;
  const limits = key === "nfeatures" ? [100, 10000] : [1, 1000];
  opts.value[key] = Math.min(limits[1], Math.max(limits[0], Math.floor(Number(input.value)))) as never;
}

function onFeatureNumberInput(e: Event, key: "ratio" | "ransac_reproj_threshold" | "min_inlier_ratio") {
  const input = e.target as HTMLInputElement;
  if (input.value === "") return;
  const limits = key === "ratio"
    ? [0.1, 1.0]
    : key === "min_inlier_ratio" ? [0.01, 1.0] : [0.1, 50.0];
  opts.value[key] = boundedNumber(Number(input.value), limits[0], limits[1]) as never;
}

watch(() => opts.value.dpi, (val) => {
  const el = dpiInputRef.value;
  if (el && el !== document.activeElement) el.value = String(val);
});

function clampScanOptions() {
  opts.value.dpi = Math.min(300, Math.max(72, Math.floor(Number(opts.value.dpi) || 180)));
  opts.value.qrcode_skip_pages = Math.min(50, nonNegativeInt(opts.value.qrcode_skip_pages));
  opts.value.qrcode_max_attempts = normalizeQrStrength(opts.value.qrcode_max_attempts);
  opts.value.qrcode_dpi_retries = Math.min(2, nonNegativeInt(opts.value.qrcode_dpi_retries));
  opts.value.nfeatures = Math.min(10000, Math.max(100, Math.floor(Number(opts.value.nfeatures) || 1200)));
  opts.value.min_matches = Math.min(1000, Math.max(1, Math.floor(Number(opts.value.min_matches) || 25)));
  opts.value.ratio = boundedNumber(Number(opts.value.ratio), 0.1, 1.0);
  opts.value.ransac_reproj_threshold = boundedNumber(Number(opts.value.ransac_reproj_threshold), 0.1, 50.0);
  opts.value.min_inlier_ratio = boundedNumber(Number(opts.value.min_inlier_ratio), 0.01, 1.0);
  opts.value.max_segment_pages = Math.min(10000, Math.max(1, positiveInt(Number(opts.value.max_segment_pages))));
  quickScanPageLimit.value = boundedQuickScanPageLimit(quickScanPageLimit.value);
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
  if (sourceProblems.value.length) { error.value = '请先重新定位缺失或已变化的来源。'; return; }
  if (submitting.value || taskBusy.value) {
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
  review.value = null; reviewInvalidated.value = false;
  pendingReview = { pdfPath: pdfPath.value, options: buildScanOptions(), configuration: reviewConfiguration() };
  const taskId = generateTaskId("scan_review");
  await nextTick();
  startTask(taskId);
  try {
    const res = await window.engine.scanSplit.scanOnly({
      pdfPath: pdfPath.value,
      referenceImagePath: referenceImage.value,
      pageLimit: 0,
      options: buildScanOptions(),
      taskId,
    });
    markSubmitted(taskId);
    if (res?.queued) markQueued(res.position || 1, taskId);
  } catch (caught) {
    if (taskState.value.taskId !== taskId) return;
    pendingReview = null;
    error.value = formatEngineError(caught);
    submitting.value = false;
    resetTask();
  }
}

async function runProbePage() {
  if (sourceProblems.value.length) { error.value = '请先重新定位缺失或已变化的来源。'; return; }
  if (submitting.value || taskBusy.value) {
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
    const res = await window.engine.scanSplit.probePage({
      pdfPath: pdfPath.value,
      referenceImagePath: referenceImage.value,
      options: buildScanOptions({ max_segment_pages: 0 }),
      pageIndex: Math.max(0, probePageIndex.value - 1),
      taskId,
    });
    markSubmitted(taskId);
    if (res?.queued) markQueued(res.position || 1, taskId);
  } catch (caught) {
    if (taskState.value.taskId !== taskId) return;
    error.value = formatEngineError(caught);
    submitting.value = false;
    resetTask();
  }
}

async function runScanOnly() {
  if (sourceProblems.value.length) { error.value = '请先重新定位缺失或已变化的来源。'; return; }
  if (submitting.value || taskBusy.value) {
    error.value = "已有任务正在执行，请等待完成后再试。";
    return;
  }
  if (!canRun.value || !window.engine) return;
  if (quickScanPageLimitExceeded.value) {
    error.value = quickScanPageLimitHint.value;
    return;
  }
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
    const res = await window.engine.scanSplit.scanOnly({
      pdfPath: pdfPath.value,
      referenceImagePath: referenceImage.value,
      options: buildScanOptions(),
      pageLimit: effectiveQuickScanPageLimit.value,
      taskId,
    });
    markSubmitted(taskId);
    if (res?.queued) markQueued(res.position || 1, taskId);
  } catch (caught) {
    if (taskState.value.taskId !== taskId) return;
    error.value = formatEngineError(caught);
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

let restoringScanSettings = false;

function normalizeQrStrength(value: unknown) {
  const effort = Number(value);
  if (!Number.isFinite(effort) || effort < 24) return 12;
  if (effort < 72) return 24;
  if (effort < 144) return 72;
  return 144;
}

function loadScanSettings() {
  restoringScanSettings = true;
  try {
    const raw = sessionStorage.getItem(SCAN_SETTINGS_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return;

    const validModes: ScanDetectionMode[] = ["auto", "qrcode", "stamp", "feature"];
    if (validModes.includes(data.detection_mode)) opts.value.detection_mode = data.detection_mode;

    const numberKeys = [
      "dpi", "qrcode_skip_pages", "qrcode_max_attempts", "max_segment_pages",
      "nfeatures", "ratio", "min_matches", "min_inlier_ratio", "ransac_reproj_threshold", "qrcode_dpi_retries",
    ] as const;
    for (const key of numberKeys) {
      if (key in data) opts.value[key] = readFiniteNumber(data[key], opts.value[key]);
    }

    const booleanKeys = [
      "qrcode_no_decode", "qrcode_use_roi", "marker_as_first_page", "exclude_marker_page",
      "enable_multithread", "enable_gpu",
      "auto_detect_stamp", "auto_detect_qrcode", "auto_detect_feature", "feature_strict",
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
  } catch {
  } finally {
    nextTick(() => { restoringScanSettings = false; });
  }
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
  });
});

onBeforeUnmount(() => {
  if (taskCancellable.value) cancelTask();
  pdfPageCountToken++;
  referencePreviewToken++;
  if (referencePreviewTimer) clearTimeout(referencePreviewTimer);
  previewResizeObserver?.disconnect();
  roiPreviouslyFocused = null;
});

const detectionOptions: { key: ScanDetectionMode; label: string }[] = [
  { key: "auto", label: "自动识别" },
  { key: "qrcode", label: "二维码" },
  { key: "stamp", label: "印章" },
  { key: "feature", label: "特征匹配" },
];

const qrStrengthOptions = [
  { label: "快速", value: 12 },
  { label: "标准", value: 24 },
  { label: "增强", value: 72 },
  { label: "极强", value: 144 },
];
const presetOptions = [
  { label: "自定义", value: "" },
  { label: "预设：均衡", value: "balanced" },
  { label: "预设：严格", value: "strict" },
  { label: "预设：宽松", value: "loose" },
  { label: "预设：高召回", value: "high_recall" },
];

const isQrMode = computed(() => opts.value.detection_mode === "qrcode" || (opts.value.detection_mode === "auto" && opts.value.auto_detect_qrcode));
const isFeatureMode = computed(() => opts.value.detection_mode === "feature" || (opts.value.detection_mode === "auto" && opts.value.auto_detect_feature));
watch(parameterTabs, (tabs) => {
  if (tabs.find((tab) => tab.value === parameterTab.value)?.disabled) parameterTab.value = "basic";
});
const isRoiSupported = computed(() => ["qrcode", "stamp", "auto", "feature"].includes(opts.value.detection_mode));
const qrcodeTextDisabled = computed(() => !isQrMode.value || opts.value.qrcode_no_decode);
const needsReference = computed(() => opts.value.detection_mode === "feature" || (
  opts.value.detection_mode === "auto" && opts.value.auto_detect_feature && !opts.value.auto_detect_stamp && !opts.value.auto_detect_qrcode
));
const autoDetectorSelected = computed(() => opts.value.detection_mode !== "auto" || opts.value.auto_detect_stamp || opts.value.auto_detect_qrcode || opts.value.auto_detect_feature);
watch(referenceImage, (path) => { if (path) activityTab.value = "reference"; });
watch(scanTaskBusy, (busy) => { if (busy) activityTab.value = "logs"; });
watch(error, (message) => { if (message) activityTab.value = "results"; });
const canRun = computed(() => !!pdfPath.value && !taskBusy.value && !submitting.value && autoDetectorSelected.value);
const detectionModeHintText = computed(() => {
  if (opts.value.detection_mode === "qrcode") return "适合用二维码作为分隔标记；可按二维码文字内容筛选。";
  if (opts.value.detection_mode === "stamp") return "适合用红章、盖章页作为分隔标记；不会使用二维码内容筛选。";
  if (opts.value.detection_mode === "feature") return "适合用固定版式或图片作为分隔标记；需要先选择参考文件。";
  return autoDetectorSelected.value ? "按印章、二维码、参考特征顺序检查已启用的方式；任一命中即作为标记页。二维码内容筛选只约束二维码检测。" : "请至少启用一种识别方式。";
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
  return "优先在框选区域内识别印章、二维码和参考特征；适合标记位置固定的文件";
});
const dpiHintText = computed(() => {
  if (opts.value.detection_mode === "qrcode") return "二维码清晰用 180-200；模糊可调高";
  if (opts.value.detection_mode === "stamp" || opts.value.detection_mode === "auto") return "印章建议 220；越高越慢";
  return "特征点建议 180；参考图很细可调高";
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
    auto_detect_stamp: opts.value.auto_detect_stamp,
    auto_detect_qrcode: opts.value.auto_detect_qrcode,
    auto_detect_feature: opts.value.auto_detect_feature,
    feature_strict: opts.value.feature_strict,
    qrcode_dpi_retries: opts.value.qrcode_dpi_retries,
    ...extra,
  };
}
watch(() => reviewConfiguration(), () => {
  if (restoringWorkspace) return;
  if (!review.value) return;
  if (reviewBusy.value) { reviewInvalidated.value = true; return; }
  review.value = null; error.value = '输入或识别参数已变化，请重新扫描后复核。';
});
watch(reviewBusy, busy => {
  if (!busy && reviewInvalidated.value) {
    review.value = null; reviewInvalidated.value = false;
    error.value = '输入或识别参数已变化，本次输出采用原复核快照；再次输出前请重新扫描。';
  }
});
function snapshotScanWorkspace(): WorkspaceState {
  const sources: WorkspaceSource[] = [];
  if (pdfPath.value) {
    const known = restoredSourceMetadata.get(pdfPath.value);
    sources.push({ path: pdfPath.value, name: fileBasename(pdfPath.value), kind: 'pdf', role: 'document', size: known?.size || 0,
      signature: review.value?.pdfPath === pdfPath.value ? review.value.signature : known?.signature || '', page_count: review.value?.pdfPath === pdfPath.value ? review.value.total : pdfPageCount.value || known?.page_count || 0 });
  }
  if (referenceImage.value) {
    const known = restoredSourceMetadata.get(referenceImage.value);
    sources.push({ path: referenceImage.value, name: fileBasename(referenceImage.value), kind: /\.pdf$/i.test(referenceImage.value) ? 'pdf' : 'image', role: 'reference', size: known?.size || 0, signature: known?.signature || '', page_count: 0 });
  }
  const state: WorkspaceState = { version: 1, sources, pages: [], settings: { ...savedScanSettings.value, options: { ...opts.value }, outputDir: outputDir.value, preset: preset.value, probePageIndex: probePageIndex.value, quickScanPageLimit: quickScanPageLimit.value } };
  if (review.value && review.value.pdfPath === pdfPath.value) state.review = { source: 0, total: review.value.total, markers: [...review.value.markers],
    segments: review.value.segments?.map(group => [...group]) || reviewSegments(review.value.total, review.value.markers, review.value.options.marker_as_first_page !== false, Boolean(review.value.options.exclude_marker_page)),
    options: { ...review.value.options }, expanded: reviewExpanded.value };
  return state;
}
const workspace = useWorkspacePersistence('scan', snapshotScanWorkspace, async saved => {
  restoringWorkspace = true;
  try {
    restoredSourceMetadata.clear(); saved.sources.forEach(source => restoredSourceMetadata.set(source.path, source));
    const document = saved.sources.find(s => s.role !== 'reference'), reference = saved.sources.find(s => s.role === 'reference');
    pdfPath.value = document?.path || ''; referenceImage.value = reference?.path || ''; pdfPageCount.value = document?.page_count || null;
    applySavedScan(saved.settings); clampScanOptions();
    outputDir.value = String(saved.settings.outputDir || ''); outputNeedsSelection.value = Boolean(outputDir.value);
    if (['', 'balanced', 'strict', 'loose', 'high_recall'].includes(String(saved.settings.preset))) preset.value = saved.settings.preset as PresetName;
    probePageIndex.value = boundedProbePage(Number(saved.settings.probePageIndex) || 1);
    quickScanPageLimit.value = boundedQuickScanPageLimit(Number(saved.settings.quickScanPageLimit) || 30);
    const r = saved.review, source = r ? saved.sources[r.source] : null;
    review.value = r && source ? { pdfPath: source.path, signature: source.signature, total: r.total, markers: [...r.markers], segments: r.segments.map(group => [...group]), options: r.options as ScanSplitOptions } : null;
    reviewExpanded.value = r?.expanded !== false;
    if (review.value) activityTab.value = 'results';
    await nextTick();
  } finally { restoringWorkspace = false; }
});
const sourceProblems = computed(() => {
  const current = [
    ...(pdfPath.value ? [{ path: pdfPath.value, role: 'document' }] : []),
    ...(referenceImage.value ? [{ path: referenceImage.value, role: 'reference' }] : []),
  ];
  return workspace.statuses.value.filter(item => item.status !== 'ready')
    .map(item => ({ ...item, index: current.findIndex(source => source.path === item.path && (!item.role || item.role === source.role)) }))
    .filter(item => item.index >= 0);
});
const documentUnavailable = computed(() => sourceProblems.value.some(item => item.path === pdfPath.value));
watch(workspace.restoring, restoring => {
  if (restoring) return;
  if (!documentUnavailable.value) void updatePdfPageCount();
  if (!sourceProblems.value.some(item => item.path === referenceImage.value)) loadReferencePreview();
});
function saveReviewEdits(value: { markers: number[]; segments: number[][] }) {
  if (review.value) review.value = { ...review.value, markers: value.markers, segments: value.segments };
}
function reviewOutputSelected(path: string) { outputDir.value = path; outputNeedsSelection.value = false; }
</script>

<template>
  <div class="scan-shell panel-shell panel-shell-responsive">
    <PanelBanner
      class="scan-banner"
      :kind="scanBannerKind"
      :icon="scanBannerKind === 'warning' ? 'alert' : scanBannerKind === 'success' ? 'check' : 'scan'"
      title="扫描拆分"
      :message="scanBannerMessage"
    />
    <div class="workspace-restore-status" role="status">{{ workspace.label.value }}<span v-if="workspace.error.value"> · {{ workspace.error.value }} <button class="btn btn-sm" :disabled="workspace.restoring.value" @click="workspace.retry">{{ workspace.ready.value ? '重试保存' : '重试恢复' }}</button></span><div v-for="item in sourceProblems" :key="item.path">{{ fileBasename(item.path) }}：{{ item.status === 'missing' ? '来源缺失' : '来源已变化' }}，复核标记已保留。<button class="btn btn-sm" :disabled="taskBusy" @click="workspace.relocate(item.index)">重新定位相同内容</button></div></div>

    <div v-show="review && reviewExpanded" class="scan-review-page glass-card">
      <ScanReview v-if="review" :key="review.pdfPath + review.signature + JSON.stringify(review.options)" :pdf-path="review.pdfPath" :signature="review.signature" :total="review.total" :initial-markers="review.markers" :initial-segments="review.segments" :options="review.options" :output-dir="outputDir" :prefix="prefix" :unavailable="documentUnavailable" :output-needs-selection="outputNeedsSelection" v-model:compression="scanOutputCompression" v-model:ocr="scanOutputOcr" @change="saveReviewEdits" @output-selected="reviewOutputSelected" @busy="reviewBusy = $event" @back="reviewExpanded = false" />
    </div>
    <div v-show="!review || !reviewExpanded" class="scan-grid panel-grid">
      <!-- 左：参考输入 + 进度日志 -->
      <section class="left-col">
        <fieldset class="group glass-card section-card input-group" :disabled="taskBusy">
          <div class="scan-card-heading"><span>文件与输出</span><span class="section-caption">01</span></div>
          <div class="path-row">
            <label for="scan-pdf">源文件</label>
            <input id="scan-pdf" class="input" :value="pdfPath" placeholder="选择要拆分的 PDF" readonly :title="pdfPath" />
            <button class="btn btn-outline" @click="pickPdf">选择 PDF</button>
          </div>
          <div class="path-row">
            <label for="scan-output">保存到</label>
            <input id="scan-output" class="input" :value="outputDir" placeholder="确认导出时选择目录" readonly :title="outputDir" />
            <button class="btn btn-outline" @click="pickOutputDir">选择目录</button>
          </div>
          <div class="path-row prefix-row">
            <label for="scan-prefix">命名前缀</label>
            <input id="scan-prefix" class="input" v-model="prefix" placeholder="可选，例如 split_" />
          </div>
        </fieldset>

        <section class="scan-workspace glass-card section-card">
          <AppTabs id="scan-activity" v-model="activityTab" label="参考预览与任务反馈" :options="[
            { value: 'reference', label: '参考与选区' }, { value: 'results', label: '识别结果' },
            { value: 'logs', label: `运行日志${logs.length ? ' · ' + logs.length : ''}` }
          ]" />
        <section v-show="activityTab === 'reference'" id="scan-activity-reference-panel" class="ref-group" role="tabpanel" aria-labelledby="scan-activity-reference-tab">
          <div class="reference-heading">
            <div class="reference-heading-text"><span class="param-label" :title="referenceImage">{{ referenceImage ? fileBasename(referenceImage) : '参考文件' }}</span><span v-if="!referenceImage" class="section-caption">{{ needsReference ? '特征匹配必需' : '可选' }}</span></div>
            <button class="btn btn-outline btn-sm" :disabled="taskBusy" @click="pickReference">{{ referenceImage ? '更换参考' : '选择参考' }}</button>
          </div>
          <div class="ref-group-inner">
            <div v-if="!referenceImage" class="ref-empty">
              <div class="ref-icon"><AppIcon name="scan" :size="26" /></div>
              <div class="ref-title">{{ needsReference ? "选择参考，开始特征匹配" : "按需添加参考文件" }}</div>
              <div class="ref-hint">{{ needsReference ? "添加图像或 PDF 作为标记样本，可按需框选局部区域。" : "印章和二维码可直接扫描。添加参考后可框选区域或匹配特征。" }}</div>
            </div>
            <div v-else class="ref-body">
              <div class="roi-toolbar">
                <div class="preview-mode-picker" role="group" aria-label="参考预览操作">
                  <button class="btn btn-sm" :class="!roiDrawMode ? 'btn-primary' : 'btn-ghost'" :aria-pressed="!roiDrawMode" @click="roiDrawMode = false">拖动预览</button>
                  <button class="btn btn-sm" :class="roiDrawMode ? 'btn-primary' : 'btn-ghost'" :aria-pressed="roiDrawMode" :disabled="!previewDataUrl" @click="roiDrawMode = true">框选区域</button>
                </div>
                <button class="btn btn-outline btn-sm" :disabled="!previewDataUrl" @click="openRoiDialog">展开预览</button>
              </div>
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
                <div ref="previewStageRef" class="preview-stage" :class="{ 'is-drawing': roiDrawMode }" @dblclick="openRoiDialog" @wheel="onPreviewWheel" @pointerdown="onStagePointerDown" @pointermove="onStagePointerMove" @pointerup="onStagePointerUp" @pointercancel="onRoiPointerCancel">
                  <div class="preview-canvas" :style="previewCanvasStyle">
                    <img ref="previewImgRef" class="preview-img" :src="previewDataUrl" @load="onPreviewLoaded" @error="onPreviewLoadError" draggable="false" />
                    <div v-if="activeRoi" class="roi-box" :style="roiStyle" />
                  </div>
                </div>
              </div>
              <div v-else class="ref-hint">参考文件已选择，等待生成预览。</div>
              <div class="reference-status">
                <span v-if="keypointInfo" class="keypoint-info truncate selectable" :title="keypointInfo">{{ keypointInfo }}</span>
                <div class="selection-status">
                  <span class="roi-summary selectable" :title="roiStatusText">{{ opts.reference_roi ? `选区：x=${opts.reference_roi[0]}，y=${opts.reference_roi[1]}，w=${opts.reference_roi[2]}，h=${opts.reference_roi[3]}` : '尚未框选区域' }}</span>
                  <button class="btn btn-ghost btn-sm" :disabled="!opts.reference_roi" @click="clearRoi">清除选区</button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section v-show="activityTab !== 'reference'" class="log-group">
          <div class="scan-card-heading"><span>任务反馈</span><span class="section-caption">{{ taskBusy ? '处理中' : '任务记录' }}</span></div>
          <div class="log-group-inner">
          <div v-if="taskBusy" class="progress" :class="{ indeterminate: !taskState.total }">
            <div class="progress-bar" :style="{ width: taskState.total ? (taskState.current / taskState.total * 100) + '%' : undefined }" />
          </div>
          <div v-if="taskBusy" class="progress-line">
            {{ scanPhaseLabel(taskState.phase) || (taskState.queued ? "排队中" : "扫描中") }} · {{ taskState.current }}/{{ taskState.total }}
          </div>
          <div v-if="error" class="error-line">{{ error }}</div>
          <div v-else-if="tuneResult" class="summary-line ok">
            {{ tuneResult.title }}
          </div>
          <div v-else-if="result" class="summary-line" :class="scanCompletionNotice(result).kind === 'warning' ? 'warn' : 'ok'">
            {{ result.marker_pages.length ? '处理完成' : '未发生拆分' }} · 生成 {{ result.output_files.length }} 个文件 · 共 {{ result.total_pages }} 页 · 标记页 {{ result.marker_pages.length }}
            <button class="btn btn-outline btn-mini" @click="copyResults">复制结果</button>
          </div>
          <div v-show="activityTab === 'results'" id="scan-activity-results-panel" class="activity-results" role="tabpanel" aria-labelledby="scan-activity-results-tab">
          <div v-if="review" class="review-return"><strong>分段已就绪，等待复核</strong><button class="btn btn-primary" @click="reviewExpanded = true">进入复核工作区</button></div>
          <div v-if="!review && !result && !tuneResult" class="activity-empty"><AppIcon name="info" :size="24" /><strong>先测试，再开始拆分</strong><span>单页测试和快速扫描的统计会显示在这里，便于比较识别参数。</span></div>
          <div v-if="tuneResult && !review" class="result-box selectable">
            <div v-for="line in tuneResult.lines" :key="line" class="result-line">
              {{ line }}
            </div>
          </div>
          <div v-if="result" class="result-box selectable">
            <div v-for="warning in result.warnings" :key="warning" class="result-line error-line">{{ warning }}</div>
            <div v-for="segment in result.suspect_segments" :key="`suspect-${segment.index}`" class="result-line error-line">
              疑似漏检：第 {{ segment.index }} 段，第 {{ segment.start_page }}-{{ segment.end_page }} 页，共 {{ segment.page_count }} 页，超过 {{ segment.max_pages }} 页
            </div>
            <div v-for="segment in result.failed_segments" :key="`failed-${segment.index}`" class="result-line error-line">
              写入失败：第 {{ segment.index }} 段，第 {{ segment.start_page }}-{{ segment.end_page }} 页
            </div>
            <div v-for="segment in result.pending_segments" :key="`pending-${segment.index}`" class="result-line">
              待处理：第 {{ segment.index }} 段，第 {{ segment.start_page }}-{{ segment.end_page }} 页
            </div>
            <div class="result-line">
              标记页：{{ result.marker_pages.map((p) => p + 1).join("、") || "无" }}
            </div>
            <div v-for="file in result.output_files" :key="file" class="result-line truncate" :title="file">
              {{ fileBasename(file) }}
            </div>
          </div>
          </div>
          <div v-show="activityTab === 'logs'" id="scan-activity-logs-panel" class="log-box" :class="{ empty: !logs.length }" role="tabpanel" aria-labelledby="scan-activity-logs-tab">
            <div v-if="!logs.length" class="log-placeholder">这里会实时显示进度与错误信息</div>
            <div v-else class="log-lines selectable">
              <div v-for="(line, index) in logs" :key="`${index}-${line}`" class="log-line" :class="logLineClass(line)">
                {{ line }}
              </div>
            </div>
          </div>
          </div><!-- /log-group-inner -->
        </section>
        </section>
      </section>

      <!-- 右：功能区 -->
      <section class="right-col">

        <fieldset class="group glass-card section-card params-group" :disabled="taskBusy">
          <div class="scan-card-heading"><span>识别与参数</span><span class="section-caption">02</span></div>
          <div class="mode-picker" role="group" aria-label="识别方式">
            <button v-for="mode in detectionOptions" :key="mode.key" class="mode-chip"
              :class="{ active: opts.detection_mode === mode.key }" :aria-pressed="opts.detection_mode === mode.key"
              @click="opts.detection_mode = mode.key">{{ mode.label }}</button>
          </div>
          <p class="mode-hint" :class="{ warn: !autoDetectorSelected }">{{ detectionModeHintText }}</p>
          <AppTabs id="scan-parameters" v-model="parameterTab" label="扫描参数分组" :options="parameterTabs" />
          <div class="params-scroll">
            <div v-show="parameterTab === 'basic'" id="scan-parameters-basic-panel" class="param-pane" role="tabpanel" aria-labelledby="scan-parameters-basic-tab">
              <div v-if="opts.detection_mode === 'auto'" class="detector-row">
                <span class="param-label">自动检测</span>
                <label class="checkbox option-chip"><input type="checkbox" v-model="opts.auto_detect_stamp" />印章</label>
                <label class="checkbox option-chip"><input type="checkbox" v-model="opts.auto_detect_qrcode" />二维码</label>
                <label class="checkbox option-chip"><input type="checkbox" v-model="opts.auto_detect_feature" />参考特征</label>
              </div>
              <div class="param-row">
                <label for="scan-dpi" class="param-label">分辨率 <small>DPI</small></label>
                <div class="dpi-control">
                  <input id="scan-dpi" ref="dpiInputRef" class="input" type="number" min="72" max="300" @input="onDpiInput" @blur="onDpiBlur" title="页面渲染分辨率，有效范围 72–300" />
                  <div class="dpi-presets" role="group" aria-label="常用分辨率">
                    <button v-for="value in [180, 220, 300]" :key="value" class="dpi-preset" :class="{ active: opts.dpi === value }"
                      :aria-pressed="opts.dpi === value" @click="setDpi(value)">{{ value }}</button>
                  </div>
                </div>
              </div>
              <p class="field-help">{{ dpiHintText }}</p>
              <div class="param-row">
                <label class="checkbox param-label" :title="roiOptionTitle"><input type="checkbox" v-model="opts.qrcode_use_roi" />局部识别 ROI</label>
                <button class="btn btn-outline btn-sm" :disabled="!previewDataUrl" @click="openRoiDialog">{{ opts.reference_roi ? '调整选区' : '框选区域' }}</button>
              </div>
              <p v-if="opts.qrcode_use_roi" class="field-help" :class="{ warn: !opts.reference_roi }">{{ roiStatusText }}</p>
              <div class="param-row">
                <label for="scan-marker-mode" class="param-label">标记页位置</label>
                <AppSelect input-id="scan-marker-mode" v-model="markerPageMode" min-width="0" :options="[
                  { label: '下一份开头', value: 'first' }, { label: '上一份末尾', value: 'previous' }, { label: '不保存标记页', value: 'exclude' }
                ]" />
              </div>
              <div class="param-row">
                <label class="checkbox param-label" for="scan-skip-enabled" title="找到标记页后，后面几页不再检查"><input id="scan-skip-enabled" type="checkbox" v-model="skipPagesEnabled" />命中后跳过</label>
                <div class="number-with-unit"><input class="input" aria-label="命中后跳过页数" type="number" min="1" max="50" :disabled="!skipPagesEnabled" :value="opts.qrcode_skip_pages" @input="onSkipPagesInput" /><span>页</span></div>
              </div>
              <div class="param-row">
                <label class="checkbox param-label" title="只提示超长分段，不强制切断文档"><input type="checkbox" v-model="useMaxSegment" />疑似漏检提醒</label>
                <div class="number-with-unit"><span>超过</span><input class="input" aria-label="疑似漏检分段页数上限" type="number" min="1" max="10000" :disabled="!useMaxSegment" :value="opts.max_segment_pages" @input="onMaxSegmentInput" /><span>页</span></div>
              </div>
              <details class="performance-options">
                <summary>性能选项 <span>多线程 / OpenCL</span></summary>
                <div class="row wrap">
                  <label class="checkbox" title="最多使用 8 个 OpenCV 线程"><input type="checkbox" v-model="opts.enable_multithread" />OpenCV 多线程</label>
                  <label class="checkbox" title="尝试 OpenCL，不可用时回退 CPU"><input type="checkbox" v-model="opts.enable_gpu" />OpenCL 加速</label>
                </div>
                <p class="field-help">加速效果取决于设备和页面内容。</p>
              </details>
            </div>

            <div v-show="parameterTab === 'qrcode'" id="scan-parameters-qrcode-panel" class="param-pane" role="tabpanel" aria-labelledby="scan-parameters-qrcode-tab">
              <div class="param-field">
                <label for="scan-qr-text" class="param-label">内容筛选</label>
                <input id="scan-qr-text" class="input" v-model="opts.qrcode_text_contains" placeholder="二维码内容包含…（可选）" :disabled="qrcodeTextDisabled" />
                <p class="field-help">留空识别所有二维码；不解码时内容筛选不生效。</p>
              </div>
              <div class="param-row"><span class="param-label">识别强度</span><AppSelect ariaLabel="二维码识别强度" :model-value="opts.qrcode_max_attempts" :options="qrStrengthOptions" min-width="0" @update:model-value="opts.qrcode_max_attempts = Number($event)" /></div>
              <p class="field-help">强度越高，兜底识别越充分，扫描耗时也会增加。</p>
              <div class="param-row"><span class="param-label">DPI 兜底</span><AppSelect ariaLabel="二维码 DPI 重试次数" :model-value="opts.qrcode_dpi_retries" min-width="0" :options="[{ label: '关闭', value: 0 }, { label: '重试 1 次', value: 1 }, { label: '重试 2 次', value: 2 }]" @update:model-value="opts.qrcode_dpi_retries = Number($event)" /></div>
              <p class="field-help">发现候选二维码时重试；自动模式最多重试 1 次。</p>
              <label class="checkbox option-chip"><input type="checkbox" v-model="opts.qrcode_no_decode" />仅检测二维码，不解码内容</label>
            </div>

            <div v-show="parameterTab === 'feature'" id="scan-parameters-feature-panel" class="param-pane" role="tabpanel" aria-labelledby="scan-parameters-feature-tab">
              <div class="param-row"><span class="param-label">参数预设</span><AppSelect ariaLabel="特征匹配参数预设" :model-value="preset" :options="presetOptions" min-width="0" @update:model-value="applyPreset($event as PresetName)" /></div>
              <p class="field-help">先选择预设，再根据单页测试的匹配统计微调。</p>
              <div class="feature-fields">
                <label class="param-field">特征点数量<input class="input" type="number" min="100" max="10000" :value="opts.nfeatures" @input="onFeatureIntInput($event, 'nfeatures')" /><span class="field-help">越多越容易命中，速度越慢</span></label>
                <label class="param-field">最小匹配数<input class="input" type="number" min="1" max="1000" :value="opts.min_matches" @input="onFeatureIntInput($event, 'min_matches')" /><span class="field-help">越高越严格，可能增加漏检</span></label>
                <label class="param-field">比例阈值<input class="input" type="number" step="0.05" min="0.1" max="1" :value="opts.ratio" @input="onFeatureNumberInput($event, 'ratio')" /><span class="field-help">越低越严格，减少误匹配</span></label>
                <label class="param-field">RANSAC 阈值<input class="input" type="number" step="0.5" min="0.1" max="50" :value="opts.ransac_reproj_threshold" @input="onFeatureNumberInput($event, 'ransac_reproj_threshold')" /><span class="field-help">越高越宽松，单位为像素</span></label>
                <label class="param-field">内点比例阈值<input class="input" type="number" step="0.05" min="0.01" max="1" :value="opts.min_inlier_ratio" @input="onFeatureNumberInput($event, 'min_inlier_ratio')" /><span class="field-help">越高越严格</span></label>
                <label class="checkbox strict-option" title="校验内点数量、比例、覆盖范围和几何变换"><input type="checkbox" v-model="opts.feature_strict" />严格匹配校验</label>
              </div>
              <p v-if="!referenceImage" class="field-help warn">特征匹配需要参考文件，请在左侧选择。</p>
            </div>
          </div>
        </fieldset>

        <ProcessingPresets scope="scan" :settings="savedScanSettings" :disabled="taskBusy" @apply="applySavedScan" />
        <fieldset class="tune-group glass-card section-card" :disabled="taskBusy">
          <div class="tune-grid">
            <div class="tune-line">
              <label for="scan-probe-page">页码</label>
              <input id="scan-probe-page" class="input tune-input" type="number" min="1" :max="pdfPageCount || undefined" :value="probePageIndex" @input="onProbePageInput" @blur="onProbePageBlur" />
              <button class="btn btn-outline tune-btn" :disabled="!canRun" @click="runProbePage" title="测试指定页是否命中标记，查看识别统计">测试单页</button>
            </div>
            <div class="tune-line">
              <label for="scan-quick-pages">前 N 页</label>
              <input id="scan-quick-pages" class="input tune-input" :class="{ 'tune-input-warning': quickScanPageLimitExceeded }" type="number" min="1" :max="pdfPageCount || undefined" :value="quickScanPageLimit" :aria-invalid="quickScanPageLimitExceeded" @input="onQuickScanPageLimitInput" @blur="onQuickScanPageLimitBlur" />
              <button class="btn btn-outline tune-btn" :disabled="!canRun || quickScanPageLimitExceeded" @click="runScanOnly" title="仅扫描，不输出文件，用于快速调参">快速扫描</button>
            </div>
          </div>
          <p v-if="quickScanPageLimitExceeded" class="tune-warning">{{ quickScanPageLimitHint }}</p>
        </fieldset>

        <div class="row scan-actions action-footer">
          <button
            class="btn btn-primary btn-lg flex-1"
            :disabled="!canRun"
            :aria-busy="taskBusy"
            @click="execute"
          >
            <span v-if="taskBusy" class="btn-spinner" aria-hidden="true" />
            {{ taskBusy ? (taskState.queued ? "排队中…" : "执行中…") : "全量扫描并复核" }}
          </button>
          <button class="btn btn-secondary btn-lg" :disabled="!taskCancellable" @click="cancelTask">停止</button>
        </div>

      </section>
    </div>

    <Transition name="overlay-fade">
    <div
      v-if="roiDialogOpen"
      ref="roiDialogRef"
      class="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="roi-dialog-title"
      @click.self="closeRoiDialog"
      @keydown="onRoiDialogKeydown"
      tabindex="-1"
    >
      <div class="roi-dialog modal-panel modal-panel-lg glass-card">
        <div class="modal-header">
          <div class="roi-dialog-title">
            <h3 id="roi-dialog-title">框选区域</h3>
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
            @pointercancel="onRoiPointerCancel"
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
          <button ref="roiConfirmBtnRef" class="btn btn-primary" @click="confirmRoiDialog">确定</button>
        </div>
      </div>
    </div>
    </Transition>
  </div>
</template>

<style scoped>
.workspace-restore-status { padding: 0 4px 6px; flex-shrink: 0; font-size: 11px; color: var(--color-text-secondary); }
.workspace-restore-status>div { color: #b45309; padding-top: 4px; }.workspace-restore-status .btn { margin-left: 8px; }
.scan-review-page { flex: 1; min-height: 0; padding: 18px 22px; border: 1px solid var(--color-border); border-radius: 14px; overflow: hidden; }
.scan-review-page { animation: surfaceReveal var(--motion-scene) var(--motion-out); }
.scan-review-page :deep(.review-summary) { animation: surfaceReveal 280ms var(--motion-out) 45ms both; }
.review-return { display: flex; flex-direction: column; align-items: center; gap: 15px; padding: 32px; color: var(--color-text-secondary); }

.scan-grid {
  --panel-grid-columns: minmax(300px, 0.88fr) minmax(440px, 1.12fr);
}

/* 左侧 */
.left-col {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: var(--space-3);
  min-height: 0;
  overflow: hidden;
}
.scan-workspace { padding: 12px 14px; gap: 14px; }
.ref-group, .log-group { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; }
.reference-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; flex-shrink: 0; }
.reference-heading-text { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.reference-heading-text .param-label { overflow: hidden; text-overflow: ellipsis; }
.reference-heading .section-caption { font-size: 11px; color: var(--color-gray-500); }
.reference-status { display: flex; flex-direction: column; gap: 6px; padding-top: 8px; border-top: 1px solid var(--color-border); flex-shrink: 0; min-width: 0; }
.selection-status { display: flex; align-items: center; gap: 12px; min-width: 0; }
.selection-status .roi-summary { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.preview-mode-picker { display: flex; gap: 3px; padding: 3px; border-radius: 8px; background: var(--color-gray-100); }
.preview-mode-picker .btn-primary { background: var(--color-white); border-color: var(--color-border); color: var(--color-primary-dark); box-shadow: var(--shadow-sm); }
.preview-mode-picker .btn-primary:hover:not(:disabled) { background: var(--color-white); border-color: var(--color-primary-light); color: var(--color-primary-dark); }
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
  gap: 12px;
  padding: 20px 8px;
  min-height: 0;
  overflow: auto;
}
.ref-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
}
.ref-icon { width: 52px; height: 52px; flex-shrink: 0; display: grid; place-items: center; border-radius: 14px; background: var(--color-primary-bg); color: var(--color-primary); }
.ref-title { font-size: var(--font-md); font-weight: 600; color: var(--color-gray-800); max-width: 80%; }
.ref-hint {
  text-align: center;
  font-size: var(--font-sm);
  color: var(--color-gray-500);
  max-width: 320px;
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
  position: relative;
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
.preview-wrap > .preview-zoom-toolbar {
  position: absolute;
  right: 8px;
  bottom: 8px;
  z-index: 2;
  padding: 3px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--glass-bg-hover);
  box-shadow: var(--shadow-sm);
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
  min-height: 120px;
  width: 100%;
  max-width: 100%;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.25);
  overflow: auto;
  cursor: grab;
  touch-action: none;
}
.preview-stage.is-drawing { cursor: crosshair; }
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
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  flex-shrink: 0;
  min-width: 0;
}
.keypoint-info {
  font-size: var(--font-sm);
  color: var(--color-gray-600);
  flex-shrink: 0;
}
.roi-box {
  position: absolute;
  border: 2px solid var(--color-primary);
  background: rgba(35, 99, 245, 0.16);
  box-shadow: 0 0 0 9999px rgba(17, 24, 39, 0.12);
  pointer-events: none;
}
.roi-summary {
  font-size: var(--font-sm);
  color: var(--color-primary);
}

.activity-results { flex: 1; min-height: 0; overflow: auto; margin-top: 8px; }
.activity-empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 10px; color: var(--color-gray-400); padding: 16px; }
.activity-empty strong { color: var(--color-gray-700); font-size: 14px; font-weight: 500; }
.activity-empty span { max-width: 260px; color: var(--color-gray-500); font-size: 12px; }
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
.summary-line.warn { color: var(--color-warning); }
.btn-mini {
  margin-left: 8px;
  padding: 2px 8px;
  font-size: var(--font-sm);
}
.result-box {
  max-height: none;
  overflow: auto;
  border: 0.5px solid var(--glass-border);
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  background: rgba(255, 255, 255, 0.22);
  font-size: var(--font-sm);
  color: var(--color-gray-700);
}
.result-line + .result-line { margin-top: 3px; }

/* 参数工作台 */
.right-col { display: flex; flex-direction: column; gap: 10px; min-height: 0; min-width: 0; }
.input-group { padding: 12px 14px; flex-shrink: 0; gap: 7px; }
.scan-card-heading { display: flex; align-items: center; gap: 8px; flex-shrink: 0; font-size: 14px; font-weight: 650; color: var(--color-gray-900); margin-bottom: 8px; }
.scan-card-heading > .section-caption { margin-left: auto; color: var(--color-gray-500); font-size: 11px; font-weight: 500; }
.path-row { display: grid; grid-template-columns: 60px minmax(0, 1fr) 88px; gap: 8px; align-items: center; }
.path-row label { font-size: 12px; color: var(--color-gray-600); }
.path-row .btn { padding: 6px 8px; }
.prefix-row { grid-template-columns: 60px minmax(0, 1fr); }
.params-group { padding: 12px 14px; flex: 1 1 0; gap: 0; }
.mode-picker { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; flex-shrink: 0; }
.mode-chip { min-height: 34px; border: 1px solid var(--color-border); border-radius: 8px; color: var(--color-gray-600); font-size: 13px; background: var(--color-gray-50); transition: var(--transition-fast); }
.mode-chip:hover { border-color: var(--color-primary); }
.mode-chip.active { color: var(--color-primary-dark); background: var(--color-primary-bg); border-color: var(--color-primary); font-weight: 600; }
.mode-hint { min-height: 36px; margin: 7px 0 10px; color: var(--color-gray-500); font-size: 12px; line-height: 1.5; flex-shrink: 0; }
.params-scroll { flex: 1 1 0; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 8px 2px 0 0; scrollbar-gutter: stable; }
.param-pane { animation: formReveal 220ms var(--motion-out); }
.param-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 37px; padding: 3px 0; border-bottom: 1px solid var(--color-border); }
.param-label { font-size: 13px; font-weight: 500; color: var(--color-gray-700); white-space: nowrap; }
.param-label small { font-size: 11px; font-weight: 400; color: var(--color-gray-500); }
.param-row > .app-select { flex: 0 1 210px; min-width: 0 !important; }
.param-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; color: var(--color-gray-700); font-size: 13px; padding: 5px 0; }
.field-help { margin: 4px 0 6px; font-size: 12px; color: var(--color-gray-500); line-height: 1.5; }
.warn { color: var(--color-warning); }
.detector-row { display: flex; gap: 7px; align-items: center; padding: 3px 0 7px; flex-wrap: wrap; }
.option-chip { padding: 3px 7px; background: var(--color-gray-50); border: 1px solid var(--color-border); border-radius: 6px; }
.option-chip:has(input:checked) { background: var(--color-primary-bg); border-color: var(--color-primary-light); color: var(--color-primary-dark); }
.dpi-control { display: flex; align-items: center; gap: 8px; }
.dpi-control .input { width: 76px; }
.dpi-presets { display: flex; gap: 3px; }
.dpi-preset { padding: 5px 7px; border-radius: 5px; color: var(--color-gray-500); font-size: 12px; }
.dpi-preset:hover, .dpi-preset.active { background: var(--color-primary-bg); color: var(--color-primary-dark); }
.number-with-unit { display: flex; align-items: center; gap: 6px; color: var(--color-gray-500); font-size: 12px; }
.number-with-unit .input { width: 76px; }
.feature-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 14px; }
.feature-fields .field-help { margin: 0; font-size: 11px; }
.strict-option { align-self: center; }
.performance-options { margin-top: 8px; font-size: 12px; color: var(--color-gray-600); }
.performance-options summary { padding: 5px 0; cursor: pointer; }
.performance-options summary span { margin-left: 8px; color: var(--color-gray-500); }
.performance-options .row { padding: 8px 0 2px; }
.tune-group { flex-shrink: 0; padding: 10px 12px; }
.tune-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.tune-line { display: grid; grid-template-columns: max-content minmax(40px, 1fr) max-content; gap: 6px; align-items: center; min-width: 0; font-size: 12px; color: var(--color-gray-600); }
.tune-input { width: 100%; min-width: 0; padding-left: 6px; padding-right: 2px; }
.tune-btn { padding: 6px 8px; font-size: 12px; }
.tune-input-warning { border-color: var(--color-warning); }
.tune-warning { color: var(--color-warning); font-size: 12px; margin-top: 5px; }
.btn-spinner { width: 14px; height: 14px; border: 2px solid currentColor; border-top-color: transparent; border-radius: 50%; animation: scanspin 600ms linear infinite; }
@keyframes scanspin { to { transform: rotate(360deg); } }
.scan-actions { flex-shrink: 0; margin: 0; padding: 0; }
.row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.row.wrap { flex-wrap: wrap; }
@media (max-width: 860px) {
  .right-col { min-height: 640px; }
  .left-col { min-height: 520px; }
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
