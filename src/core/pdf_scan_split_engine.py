from __future__ import annotations

import os
import threading
import time
from functools import wraps
from dataclasses import dataclass, field
from typing import Callable, Optional, Literal
from urllib.parse import unquote
import unicodedata

try:
    import pypdf
except ImportError:
    pypdf = None
import fitz

from src.utils.pdf_output import PdfOutputJob, write_pdf_output_jobs


ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


_OPENCV_TASK_LOCK = threading.RLock()


def _serialized_opencv_task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        options = args[2] if len(args) > 2 else kwargs.get("options")
        log = kwargs.get("log")
        cancel_check = kwargs.get("cancel_check")
        while not _OPENCV_TASK_LOCK.acquire(timeout=0.1):
            try:
                if cancel_check and cancel_check():
                    if log:
                        log("任务已取消：等待视觉识别资源时取消")
                    raise RuntimeError("已取消")
            except RuntimeError:
                raise
            except Exception:
                pass
        try:
            _, cv2 = PdfScanSplitEngine._require_deps()
            previous_threads = None
            previous_opencl = None
            previous_optimized = None
            try:
                if hasattr(cv2, "getNumThreads"):
                    previous_threads = int(cv2.getNumThreads())
                if hasattr(cv2, "ocl") and hasattr(cv2.ocl, "useOpenCL"):
                    previous_opencl = bool(cv2.ocl.useOpenCL())
                if hasattr(cv2, "useOptimized"):
                    previous_optimized = bool(cv2.useOptimized())
                PdfScanSplitEngine._configure_acceleration(options, cv2=cv2, log=log)
                return func(*args, **kwargs)
            finally:
                if previous_threads is not None and hasattr(cv2, "setNumThreads"):
                    try:
                        cv2.setNumThreads(previous_threads)
                    except Exception:
                        pass
                if previous_opencl is not None and hasattr(cv2, "ocl") and hasattr(cv2.ocl, "setUseOpenCL"):
                    try:
                        cv2.ocl.setUseOpenCL(previous_opencl)
                    except Exception:
                        pass
                if previous_optimized is not None and hasattr(cv2, "setUseOptimized"):
                    try:
                        cv2.setUseOptimized(previous_optimized)
                    except Exception:
                        pass
        finally:
            _OPENCV_TASK_LOCK.release()

    return wrapper


def _safe_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return default


def _normalize_qr_effort(value) -> int:
    try:
        effort = int(value)
    except (TypeError, ValueError):
        effort = 144
    if effort < 24:
        return 12
    if effort < 72:
        return 24
    if effort < 144:
        return 72
    return 144


@dataclass(frozen=True)
class PdfScanSplitOptions:
    dpi: int = 180
    nfeatures: int = 1200
    ratio: float = 0.75
    min_matches: int = 25
    ransac_reproj_threshold: float = 5.0
    min_inlier_ratio: float = 0.45
    marker_as_first_page: bool = True
    exclude_marker_page: bool = False
    reference_roi: tuple[int, int, int, int] | None = None
    detection_mode: Literal["feature", "qrcode", "stamp", "auto"] = "qrcode"
    qrcode_text_contains: str = ""
    qrcode_no_decode: bool = False
    qrcode_skip_pages: int = 0
    use_roi: bool = False
    qrcode_use_roi: bool = False
    qrcode_max_attempts: int = 144
    max_segment_pages: int = 0
    enable_multithread: bool = False
    enable_gpu: bool = False

    def __post_init__(self):
        # 数值参数边界保护
        object.__setattr__(self, "dpi", max(72, min(600, int(self.dpi or 180))))
        object.__setattr__(self, "nfeatures", max(100, min(10000, int(self.nfeatures or 1200))))
        object.__setattr__(self, "ratio", max(0.1, min(1.0, float(self.ratio or 0.75))))
        object.__setattr__(self, "min_matches", max(1, min(1000, int(self.min_matches or 25))))
        object.__setattr__(self, "ransac_reproj_threshold", max(0.1, min(50.0, float(self.ransac_reproj_threshold or 5.0))))
        object.__setattr__(self, "min_inlier_ratio", max(0.01, min(1.0, float(self.min_inlier_ratio or 0.45))))
        object.__setattr__(self, "qrcode_skip_pages", max(0, min(50, int(self.qrcode_skip_pages or 0))))
        object.__setattr__(self, "qrcode_max_attempts", _normalize_qr_effort(self.qrcode_max_attempts))
        object.__setattr__(self, "max_segment_pages", max(0, min(10000, int(self.max_segment_pages or 0))))

        _gpu = getattr(self, "enable_gpu", False)
        _mt = getattr(self, "enable_multithread", False)
        _roi = getattr(self, "use_roi", False)
        _qr_roi = getattr(self, "qrcode_use_roi", False)
        _use_roi = _safe_bool(_roi) or _safe_bool(_qr_roi)
        object.__setattr__(self, "marker_as_first_page", _safe_bool(getattr(self, "marker_as_first_page", True), True))
        object.__setattr__(self, "exclude_marker_page", _safe_bool(getattr(self, "exclude_marker_page", False)))
        object.__setattr__(self, "qrcode_no_decode", _safe_bool(getattr(self, "qrcode_no_decode", False)))
        object.__setattr__(self, "enable_gpu", _safe_bool(_gpu))
        object.__setattr__(self, "enable_multithread", _safe_bool(_mt))
        object.__setattr__(self, "use_roi", _use_roi)
        object.__setattr__(self, "qrcode_use_roi", _use_roi)


@dataclass(frozen=True)
class PdfScanSplitResult:
    output_files: list[str]
    marker_pages: list[int]
    total_pages: int
    suspect_segments: list[dict] | None = None
    performance_stats: dict | None = None


@dataclass
class _PerfStats:
    pages_scanned: int = 0
    pages_skipped: int = 0
    markers_found: int = 0
    stamp_hits: int = 0
    qr_hits: int = 0
    feature_hits: int = 0
    scan_seconds: float = 0.0
    page_scan_seconds: float = 0.0
    render_seconds: float = 0.0
    qr_seconds: float = 0.0
    stamp_seconds: float = 0.0
    feature_seconds: float = 0.0
    dpi_fallback_seconds: float = 0.0
    build_seconds: float = 0.0
    write_seconds: float = 0.0
    total_seconds: float = 0.0
    roi_clip_pages: int = 0
    roi_clip_fallback_pages: int = 0
    dpi_fallback_attempts: int = 0
    dpi_fallback_hits: int = 0


@dataclass(frozen=True)
class _DetectionContext:
    mode: str
    use_qr: bool
    use_stamp: bool
    use_feature: bool
    np: object
    cv2: object
    detector: object | None
    orb: object | None
    matcher: object | None
    ref_kps: list
    ref_des: object | None
    ref_size: tuple[int, int] | None
    roi_base_size: tuple[int, int] | None


@dataclass
class _QRCodeScanCache:
    bbox: tuple[float, float, float, float] | None = None
    variant: str = "original"
    hits: int = 0
    misses: int = 0
    logged_diagnostics: set[str] = field(default_factory=set)


class PdfScanSplitEngine:
    _zxingcpp = None
    _zxingcpp_checked = False
    _zxingcpp_import_lock = threading.Lock()

    @staticmethod
    def _configure_acceleration(options: PdfScanSplitOptions, *, cv2=None, log: Optional[LogCallback] = None):
        if cv2 is None:
            _, cv2 = PdfScanSplitEngine._require_deps()
        if hasattr(cv2, "setUseOptimized"):
            try:
                cv2.setUseOptimized(True)
            except Exception:
                pass

        if bool(getattr(options, "enable_multithread", False)) and hasattr(cv2, "setNumThreads"):
            try:
                cpu = os.cpu_count() or 1
                cv2.setNumThreads(int(max(1, cpu)))
                if log and hasattr(cv2, "getNumThreads"):
                    log(f"OpenCV多线程：线程数 = {int(cv2.getNumThreads())}")
            except Exception:
                if log:
                    log("OpenCV多线程：启用失败（OpenCV 未支持或受限）")

        use_gpu = bool(getattr(options, "enable_gpu", False))
        if not use_gpu and hasattr(cv2, "ocl") and hasattr(cv2.ocl, "setUseOpenCL"):
            try:
                cv2.ocl.setUseOpenCL(False)
            except Exception:
                pass
        if use_gpu:
            enabled = False
            detail = ""
            try:
                if hasattr(cv2, "ocl") and hasattr(cv2.ocl, "haveOpenCL") and cv2.ocl.haveOpenCL():
                    cv2.ocl.setUseOpenCL(True)
                    enabled = bool(cv2.ocl.useOpenCL())
                    detail = "OpenCL" if enabled else "OpenCL不可用"
            except Exception:
                pass
            if not enabled:
                try:
                    if hasattr(cv2, "cuda") and hasattr(cv2.cuda, "getCudaEnabledDeviceCount"):
                        n = int(cv2.cuda.getCudaEnabledDeviceCount() or 0)
                        if n > 0:
                            # 注意：当前版本仅检测到 CUDA 设备，尚未接入 cv2.cuda_* 实际加速路径
                            detail = f"检测到 CUDA 设备 {n} 个（当前未接入 CUDA 加速路径）"
                except Exception:
                    pass
            if log:
                if enabled:
                    log(f"OpenCL加速：已启用（{detail}）")
                else:
                    log(f"OpenCL加速：{detail if detail else '当前环境不可用'}，已回退到 CPU")

    @staticmethod
    def _fmt_seconds(seconds: float) -> str:
        try:
            seconds = float(seconds)
        except Exception:
            return ""
        if seconds < 1:
            return f"{int(round(seconds * 1000.0))}ms"
        if seconds < 60:
            return f"{seconds:.2f}s"
        m = int(seconds // 60)
        s = seconds - float(m * 60)
        return f"{m}m{s:.1f}s"

    @staticmethod
    def _perf_to_dict(stats: Optional[_PerfStats]) -> dict | None:
        if stats is None:
            return None
        scan = float(stats.scan_seconds or 0.0)
        measured = float(stats.render_seconds + stats.qr_seconds + stats.stamp_seconds + stats.feature_seconds)
        other = max(0.0, scan - measured)
        return {
            "pages_scanned": int(stats.pages_scanned),
            "pages_skipped": int(stats.pages_skipped),
            "markers_found": int(stats.markers_found),
            "stamp_hits": int(stats.stamp_hits),
            "qr_hits": int(stats.qr_hits),
            "feature_hits": int(stats.feature_hits),
            "scan_seconds": float(stats.scan_seconds),
            "page_scan_seconds": float(stats.page_scan_seconds),
            "render_seconds": float(stats.render_seconds),
            "qr_seconds": float(stats.qr_seconds),
            "stamp_seconds": float(stats.stamp_seconds),
            "feature_seconds": float(stats.feature_seconds),
            "dpi_fallback_seconds": float(stats.dpi_fallback_seconds),
            "build_seconds": float(stats.build_seconds),
            "write_seconds": float(stats.write_seconds),
            "total_seconds": float(stats.total_seconds),
            "other_seconds": float(other),
            "roi_clip_pages": int(stats.roi_clip_pages),
            "roi_clip_fallback_pages": int(stats.roi_clip_fallback_pages),
            "dpi_fallback_attempts": int(stats.dpi_fallback_attempts),
            "dpi_fallback_hits": int(stats.dpi_fallback_hits),
        }

    @staticmethod
    def _normalize_detection_mode(mode: str) -> str:
        mode = (mode or "qrcode").lower()
        return mode if mode in ("feature", "qrcode", "stamp", "auto") else "qrcode"

    @staticmethod
    def _qrcode_max_attempts(options: PdfScanSplitOptions) -> int:
        return int(options.qrcode_max_attempts or 144)

    @staticmethod
    def _qrcode_fallback_dpis(current_dpi: int) -> list[int]:
        current = int(current_dpi or 180)
        if current < 200:
            candidates = [200, 220]
        elif current == 200:
            candidates = [220, 180]
        else:
            candidates = [200, 180]
        result: list[int] = []
        for dpi in candidates:
            dpi = max(72, min(300, int(dpi)))
            if dpi != current and dpi not in result:
                result.append(dpi)
        return result[:2]

    @staticmethod
    def _prepare_detection_context(
        reference_image_path: str,
        options: PdfScanSplitOptions,
        *,
        log: Optional[LogCallback] = None,
    ) -> _DetectionContext:
        mode = PdfScanSplitEngine._normalize_detection_mode(options.detection_mode)
        use_qr = mode in ("qrcode", "auto")
        use_stamp = mode in ("stamp", "auto")
        use_feature = mode in ("feature", "auto")
        np, cv2 = PdfScanSplitEngine._require_deps()
        detector = cv2.QRCodeDetector() if use_qr else None
        orb = cv2.ORB_create(nfeatures=int(options.nfeatures)) if use_feature else None
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING) if use_feature else None
        ref_kps: list = []
        ref_des = None
        ref_size: tuple[int, int] | None = None
        roi_base_size: tuple[int, int] | None = None
        ref_img_full = None

        if reference_image_path and os.path.exists(reference_image_path):
            try:
                ref_img_full = PdfScanSplitEngine._read_reference_bgr(reference_image_path)
                ref_size = (int(ref_img_full.shape[1]), int(ref_img_full.shape[0]))
                roi_base_size = ref_size
            except Exception as exc:
                if mode == "feature":
                    raise
                ref_img_full = None
                ref_size = None
                roi_base_size = None
                if log:
                    log(f"[警告] 参考文件读取失败，将跳过特征匹配回退: {exc}")

        if use_feature and ref_img_full is not None:
            ref_img = PdfScanSplitEngine._apply_roi(ref_img_full, options.reference_roi if options.use_roi else None)
            ref_kps, ref_des = PdfScanSplitEngine._extract_features(ref_img, options.nfeatures, orb=orb, cv2=cv2)
            if ref_des is None or len(ref_kps) < 4:
                if mode == "feature":
                    raise RuntimeError("参考文件未检测到足够特征点")
                ref_kps = []
                ref_des = None
        elif mode == "feature":
            raise FileNotFoundError("参考文件不存在")

        return _DetectionContext(
            mode=mode,
            use_qr=use_qr,
            use_stamp=use_stamp,
            use_feature=use_feature,
            np=np,
            cv2=cv2,
            detector=detector,
            orb=orb,
            matcher=matcher,
            ref_kps=ref_kps,
            ref_des=ref_des,
            ref_size=ref_size,
            roi_base_size=roi_base_size,
        )

    @staticmethod
    def _sample_text(text: str, *, limit: int = 60) -> str:
        text = str(text or "")
        return (text[:limit] + "…") if len(text) > limit else text

    @staticmethod
    def _normalize_qr_text(text: str) -> str:
        text = unicodedata.normalize("NFKC", str(text or "")).strip()
        try:
            text = unquote(text)
        except Exception:
            pass
        return "".join(text.split()).casefold()

    @staticmethod
    def _match_texts(infos: list[str], needle: str) -> list[str]:
        if not needle:
            return list(infos or [])
        normalized_needle = PdfScanSplitEngine._normalize_qr_text(needle)
        if not normalized_needle:
            return list(infos or [])
        return [s for s in infos if normalized_needle in PdfScanSplitEngine._normalize_qr_text(s)]

    @classmethod
    def _load_zxingcpp(cls):
        if cls._zxingcpp_checked:  # fast path, no lock
            return cls._zxingcpp
        with cls._zxingcpp_import_lock:  # Bug9 Fix: double-checked locking
            if cls._zxingcpp_checked:
                return cls._zxingcpp
            try:
                import zxingcpp  # type: ignore
                cls._zxingcpp = zxingcpp
            except Exception:
                cls._zxingcpp = None
            cls._zxingcpp_checked = True
            return cls._zxingcpp

    @staticmethod
    def _is_feature_match(good_count: int, inliers: int, inlier_ratio: float, options: PdfScanSplitOptions) -> bool:
        threshold = max(1, int(options.min_matches))
        if int(inliers) >= threshold:
            return True
        min_ratio = float(options.min_inlier_ratio)
        if not (0.0 <= min_ratio <= 1.0):
            min_ratio = 0.45
        return int(good_count) >= threshold and int(inliers) >= max(8, threshold // 3) and float(inlier_ratio) >= min_ratio

    @staticmethod
    def _is_cancelled(cancel_check: Optional[CancelCheck]) -> bool:
        return bool(cancel_check and cancel_check())

    @staticmethod
    def _raise_if_cancelled(cancel_check: Optional[CancelCheck]) -> None:
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            raise RuntimeError("已取消")

    @staticmethod
    def _load_ready_pdf(doc) -> int:
        total = doc.page_count
        if total <= 0:
            raise RuntimeError("PDF没有页面")
        return total

    @staticmethod
    def _validate_page_index(page_index: int, total: int) -> int:
        idx = int(page_index)
        if not (0 <= idx < int(total)):
            raise ValueError(f"页码超出范围：{idx + 1} / {int(total)}")
        return idx

    @staticmethod
    def _render_page_checked(
        doc,
        page_index: int,
        dpi: int,
        *,
        cancel_check: Optional[CancelCheck] = None,
    ):
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return None
        page_bgr = PdfScanSplitEngine._render_page_bgr(doc, page_index, dpi)
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return None
        return page_bgr

    @staticmethod
    def _render_page_roi_checked(
        doc,
        page_index: int,
        dpi: int,
        roi: tuple[int, int, int, int] | None,
        roi_base_size: tuple[int, int] | None,
        *,
        pad_ratio: float = 0.45,
        cancel_check: Optional[CancelCheck] = None,
    ):
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return None
        page_bgr = PdfScanSplitEngine._render_page_roi_bgr(doc, page_index, dpi, roi, roi_base_size, pad_ratio=pad_ratio)
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return None
        return page_bgr

    @staticmethod
    def _log_execute_scan_start(mode: str, total_pages: int, log: Optional[LogCallback]) -> None:
        if not log:
            return
        if total_pages > 0:
            log(f"PDF页数：{total_pages}")
        if mode == "qrcode":
            log("开始识别标记页（二维码模式）…")
        elif mode == "feature":
            log("开始识别标记页（特征匹配模式）…")
        elif mode == "stamp":
            log("开始识别标记页（印章识别模式）…")
        elif mode == "auto":
            log("开始识别标记页（自动模式：印章/二维码/特征匹配）…")

    @staticmethod
    def _format_roi_log(options: PdfScanSplitOptions, roi_base_size: tuple[int, int] | None) -> str:
        roi = options.reference_roi
        if not options.use_roi:
            return "ROI：未启用"
        if not roi:
            return "ROI：已启用，但未框选区域"
        x, y, w, h = roi
        if roi_base_size:
            return f"ROI：已启用，区域 x={x}, y={y}, w={w}, h={h}，参考尺寸 {roi_base_size[0]}×{roi_base_size[1]}"
        return f"ROI：已启用，区域 x={x}, y={y}, w={w}, h={h}"

    @staticmethod
    def _format_scan_options_log(options: PdfScanSplitOptions) -> str:
        mode_labels = {
            "auto": "自动",
            "qrcode": "二维码",
            "stamp": "印章",
            "feature": "特征点",
        }
        strength_labels = {12: "快速", 24: "标准", 72: "增强", 144: "极强"}
        mode = PdfScanSplitEngine._normalize_detection_mode(options.detection_mode)
        parts = [f"模式 {mode_labels.get(mode, mode)}", f"DPI {int(options.dpi)}"]
        if mode in ("qrcode", "auto"):
            strength = int(options.qrcode_max_attempts or 144)
            parts.append(f"二维码强度 {strength_labels.get(strength, strength)}")
            if options.qrcode_no_decode:
                parts.append("二维码不解码")
            elif (options.qrcode_text_contains or "").strip():
                parts.append(f"二维码内容包含“{str(options.qrcode_text_contains).strip()}”")
        if int(options.qrcode_skip_pages or 0) > 0:
            parts.append(f"命中后跳过 {int(options.qrcode_skip_pages)} 页")
        if options.exclude_marker_page:
            parts.append("不保存标记页")
        elif options.marker_as_first_page:
            parts.append("标记页放下一份开头")
        else:
            parts.append("标记页放上一份末尾")
        if int(options.max_segment_pages or 0) > 0:
            parts.append(f"每份最多 {int(options.max_segment_pages)} 页")
        if options.enable_multithread:
            parts.append("OpenCV 多线程")
        if options.enable_gpu:
            parts.append("OpenCL 加速")
        return "扫描参数：" + "，".join(parts)

    @staticmethod
    def _log_execute_summary(
        *,
        outputs: list[str],
        total_elapsed_s: float,
        scan_elapsed_s: float,
        write_elapsed_s: float,
        log: Optional[LogCallback],
        cancelled: bool = False,
    ) -> None:
        if not log:
            return
        if cancelled:
            log(f"写入阶段结束：任务已取消，已保留 {len(outputs)} 个已生成文件")
        else:
            log(f"拆分写入完成：{len(outputs)} 个文件，用时 {PdfScanSplitEngine._fmt_seconds(write_elapsed_s)}")
        log(f"总耗时：{PdfScanSplitEngine._fmt_seconds(total_elapsed_s)} (识别 {PdfScanSplitEngine._fmt_seconds(scan_elapsed_s)} + 写入 {PdfScanSplitEngine._fmt_seconds(write_elapsed_s)})")

    @staticmethod
    def _prepare_page_images(
        page_bgr_full,
        *,
        use_qr: bool,
        use_stamp: bool,
        use_feature: bool,
        ref_size: tuple[int, int] | None,
        roi_base_size: tuple[int, int] | None,
        options: PdfScanSplitOptions,
    ):
        page_bgr_qr = page_bgr_full
        page_bgr_stamp = page_bgr_full
        page_bgr_feature = page_bgr_full
        if use_feature and ref_size and options.reference_roi and options.use_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=ref_size, dst_size=dst_size)
            page_bgr_feature = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        if use_qr and roi_base_size and options.reference_roi and options.use_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=roi_base_size, dst_size=dst_size)
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.45)
            page_bgr_qr = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        if use_stamp and roi_base_size and options.reference_roi and options.use_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=roi_base_size, dst_size=dst_size)
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.45)
            page_bgr_stamp = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        return page_bgr_qr, page_bgr_stamp, page_bgr_feature

    @staticmethod
    def _should_use_roi_clip(options: PdfScanSplitOptions, roi_base_size: tuple[int, int] | None) -> bool:
        return bool(options.use_roi and options.reference_roi and roi_base_size)

    @staticmethod
    def _prepare_page_images_from_roi_clip(
        page_bgr_roi,
        *,
        use_qr: bool,
        use_stamp: bool,
        use_feature: bool,
        reference_roi: tuple[int, int, int, int] | None = None,
        roi_base_size: tuple[int, int] | None = None,
        pad_ratio: float = 0.45,
    ):
        """Return per-detector input images from the ROI crop.
        Callers must ensure page_bgr_roi is not None before calling.
        """
        if page_bgr_roi is None:
            raise ValueError(
                "_prepare_page_images_from_roi_clip: page_bgr_roi must not be None"
            )
        feature_image = page_bgr_roi
        if use_feature and reference_roi and roi_base_size:
            try:
                src_w, src_h = [float(v) for v in roi_base_size]
                x, y, w, h = [float(v) for v in reference_roi]
                pad_x = w * max(0.0, float(pad_ratio))
                pad_y = h * max(0.0, float(pad_ratio))
                expanded_left = max(0.0, x - pad_x)
                expanded_top = max(0.0, y - pad_y)
                expanded_right = min(src_w, x + w + pad_x)
                expanded_bottom = min(src_h, y + h + pad_y)
                image_h, image_w = page_bgr_roi.shape[:2]
                expanded_w = expanded_right - expanded_left
                expanded_h = expanded_bottom - expanded_top
                if expanded_w > 0 and expanded_h > 0:
                    left = int(round((x - expanded_left) / expanded_w * image_w))
                    top = int(round((y - expanded_top) / expanded_h * image_h))
                    right = int(round((x + w - expanded_left) / expanded_w * image_w))
                    bottom = int(round((y + h - expanded_top) / expanded_h * image_h))
                    left = max(0, min(left, image_w - 1))
                    top = max(0, min(top, image_h - 1))
                    right = max(left + 1, min(right, image_w))
                    bottom = max(top + 1, min(bottom, image_h))
                    feature_image = page_bgr_roi[top:bottom, left:right]
            except Exception:
                feature_image = page_bgr_roi
        return (
            page_bgr_roi if use_qr else None,
            page_bgr_roi if use_stamp else None,
            feature_image if use_feature else None,
        )

    @staticmethod
    def _new_probe_result(page_index: int, total: int, mode: str, options: PdfScanSplitOptions) -> dict:
        stamp_enabled = mode in ("stamp", "auto")
        qr_enabled = mode in ("qrcode", "auto")
        feature_enabled = mode in ("feature", "auto")
        return {
            "page_index": int(page_index),
            "page_number": int(page_index) + 1,
            "total_pages": int(total),
            "detection_mode": mode,
            "marked": False,
            "reason": "",
            "qrcode": {
                "present": False,
                "infos": [],
                "executed": False,
                "skipped_reason": "" if qr_enabled else "当前模式未启用",
            },
            "stamp": {
                "present": False,
                "executed": False,
                "skipped_reason": "" if stamp_enabled else "当前模式未启用",
            },
            "feature": {
                "good_matches": 0,
                "inliers": 0,
                "inlier_ratio": 0.0,
                "executed": False,
                "skipped_reason": "" if feature_enabled else "当前模式未启用",
            },
            "params": {
                "dpi": int(options.dpi),
                "nfeatures": int(options.nfeatures),
                "ratio": float(options.ratio),
                "min_matches": int(options.min_matches),
                "ransac_reproj_threshold": float(options.ransac_reproj_threshold),
                "min_inlier_ratio": float(options.min_inlier_ratio),
            },
        }

    @staticmethod
    def _detect_qr_for_scan(
        doc,
        page_index: int,
        page_bgr_qr,
        options: PdfScanSplitOptions,
        *,
        detector=None,
        cv2=None,
        roi_base_size: tuple[int, int] | None = None,
        scan_cache: _QRCodeScanCache | None = None,
        status: Optional[dict] = None,
        perf: Optional[_PerfStats] = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> bool:
        needle = (options.qrcode_text_contains or "").strip()
        fallback_dpis = PdfScanSplitEngine._qrcode_fallback_dpis(int(options.dpi))
        if status is not None:
            status.clear()

        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        if options.qrcode_no_decode:
            present = PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2)
            if status is not None:
                status.update(
                    {
                        "present": bool(present),
                        "candidate_confident": bool(present),
                        "decoded": False,
                        "decode_failed": False,
                        "infos": [],
                        "variant": "candidate",
                        "bbox": None,
                        "dpi": int(options.dpi),
                    }
                )
            if not present:
                return False
            if needle and log:
                log(f"第 {page_index + 1} 页：检测到高可信二维码候选（未解码），已忽略“二维码内容包含”筛选")
            if log:
                log(f"第 {page_index + 1} 页：检测到高可信二维码候选（未解码）")
            return True

        qr_details: dict = {}
        infos = PdfScanSplitEngine._detect_qrcodes(
            page_bgr_qr,
            detector=detector,
            cv2=cv2,
            max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
            text_contains=needle,
            scan_cache=scan_cache,
            details=qr_details,
            cancel_check=cancel_check,
        )
        if log:
            for diagnostic in [str(item) for item in qr_details.get("diagnostics", []) if item]:
                if scan_cache is not None:
                    if diagnostic in scan_cache.logged_diagnostics:
                        continue
                    scan_cache.logged_diagnostics.add(diagnostic)
                log(f"二维码识别降级：{diagnostic}")
        candidate_present = bool(qr_details.get("candidate_present"))
        candidate_confident = bool(qr_details.get("candidate_confident"))
        if status is not None:
            status.update(
                {
                    "present": bool(infos or candidate_present),
                    "candidate_confident": candidate_confident,
                    "decoded": bool(infos),
                    "decode_failed": bool(candidate_confident and not infos),
                    "infos": list(infos or []),
                    "variant": str(qr_details.get("variant") or ""),
                    "bbox": qr_details.get("bbox"),
                    "dpi": int(options.dpi),
                    "diagnostics": list(qr_details.get("diagnostics") or []),
                }
            )
        if infos:
            matched_infos = PdfScanSplitEngine._match_texts(infos, needle)
            if matched_infos:
                if scan_cache is not None and qr_details.get("bbox"):
                    scan_cache.bbox = tuple(float(v) for v in qr_details["bbox"])
                    scan_cache.variant = str(qr_details.get("variant") or "original")
                    scan_cache.hits += 1
                    scan_cache.misses = 0
                if log:
                    sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                    variant = str(qr_details.get("variant") or "original")
                    log(f"第 {page_index + 1} 页：识别到二维码（{len(matched_infos)} 个，{variant}） {sample}")
                return True
            if log and needle:
                sample = PdfScanSplitEngine._sample_text(infos[0])
                log(f"第 {page_index + 1} 页：识别到二维码，但不包含关键字“{needle}” {sample}")
            if scan_cache is not None:
                scan_cache.misses += 1
                if scan_cache.misses >= 3:
                    scan_cache.bbox = None
                    scan_cache.variant = "original"
            return False

        retry_dpis: list[int] = []
        if candidate_confident:
            retry_dpis = fallback_dpis[:2 if options.detection_mode == "qrcode" else 1]
        elif options.detection_mode == "qrcode":
            higher_dpis = [dpi for dpi in fallback_dpis if dpi > int(options.dpi)]
            retry_dpis = higher_dpis[:1]
        for retry_dpi in retry_dpis:
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
            retry_started_at = time.perf_counter()
            if perf is not None:
                perf.dpi_fallback_attempts += 1
            try:
                if roi_base_size and options.reference_roi and options.qrcode_use_roi:
                    page_bgr_retry = PdfScanSplitEngine._render_page_roi_checked(
                        doc,
                        page_index,
                        retry_dpi,
                        options.reference_roi,
                        roi_base_size,
                        cancel_check=cancel_check,
                    )
                else:
                    page_bgr_retry = PdfScanSplitEngine._render_page_checked(
                        doc,
                        page_index,
                        retry_dpi,
                        cancel_check=cancel_check,
                    )
            except Exception:
                page_bgr_retry = None
            if perf is not None:
                perf.dpi_fallback_seconds += time.perf_counter() - retry_started_at
            if page_bgr_retry is None:
                PdfScanSplitEngine._raise_if_cancelled(cancel_check)
                continue
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
            retry_details: dict = {}
            infos_retry = PdfScanSplitEngine._detect_qrcodes(
                page_bgr_retry,
                detector=detector,
                cv2=cv2,
                max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
                text_contains=needle,
                scan_cache=scan_cache,
                details=retry_details,
                cancel_check=cancel_check,
            )
            if log:
                for diagnostic in [str(item) for item in retry_details.get("diagnostics", []) if item]:
                    if scan_cache is not None:
                        if diagnostic in scan_cache.logged_diagnostics:
                            continue
                        scan_cache.logged_diagnostics.add(diagnostic)
                    log(f"二维码识别降级：{diagnostic}")
            if infos_retry:
                if status is not None:
                    status.update(
                        {
                            "present": True,
                            "decoded": True,
                            "decode_failed": False,
                            "infos": list(infos_retry),
                            "variant": str(retry_details.get("variant") or ""),
                            "bbox": retry_details.get("bbox"),
                            "dpi": int(retry_dpi),
                            "diagnostics": list(retry_details.get("diagnostics") or []),
                        }
                    )
                matched_infos = PdfScanSplitEngine._match_texts(infos_retry, needle)
                if matched_infos:
                    if scan_cache is not None and retry_details.get("bbox"):
                        scan_cache.bbox = tuple(float(v) for v in retry_details["bbox"])
                        scan_cache.variant = str(retry_details.get("variant") or "original")
                        scan_cache.hits += 1
                        scan_cache.misses = 0
                    if perf is not None:
                        perf.dpi_fallback_hits += 1
                    if log:
                        sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                        log(f"第 {page_index + 1} 页：二维码 {retry_dpi} DPI 兜底重试命中（{len(matched_infos)} 个） {sample}")
                    return True
                if log and needle:
                    sample = PdfScanSplitEngine._sample_text(infos_retry[0])
                    log(f"第 {page_index + 1} 页：二维码 {retry_dpi} DPI 兜底重试识别到，但不包含关键字“{needle}” {sample}")
            elif status is not None and retry_details.get("candidate_confident"):
                status.update({"present": True, "candidate_confident": True, "decode_failed": True})
        if scan_cache is not None:
            scan_cache.misses += 1
            if scan_cache.misses >= 3:
                scan_cache.bbox = None
                scan_cache.variant = "original"
        if candidate_present and not needle and log:
            area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_qr, detector=detector, cv2=cv2)
            log(
                f"第 {page_index + 1} 页：检测到二维码候选但未能解码（未视为标记页）"
                f"  面积 {int(area)}  形状 {aspect:.2f}  填充 {solidity:.2f}"
            )
        elif candidate_present and log:
            log(
                f"第 {page_index + 1} 页：检测到二维码候选但未能解码，"
                f"无法匹配关键字“{needle}”，未视为标记页"
            )
        return False

    @staticmethod
    def _log_scan_miss(page_index: int, total: int, mode: str, stamp_debug, log: Optional[LogCallback]) -> None:
        if not log or not (page_index % 5 == 0 or page_index == total - 1):
            return
        if mode in ("stamp", "auto") and isinstance(stamp_debug, dict) and int(stamp_debug.get("candidates") or 0) > 0:
            log(
                f"第 {page_index + 1} 页：未匹配（印章候选 {int(stamp_debug.get('candidates') or 0)}，"
                f"面积占比 {float(stamp_debug.get('area_ratio') or 0.0):.4f}，"
                f"圆度 {float(stamp_debug.get('circularity') or 0.0):.2f}）"
            )
        else:
            log(f"第 {page_index + 1} 页：未匹配")

    @staticmethod
    def _detect_stamp_for_scan(
        page_index: int,
        page_bgr_stamp,
        *,
        cv2=None,
        log: Optional[LogCallback] = None,
        logged_diagnostics: Optional[set[str]] = None,
    ) -> tuple[bool, dict]:
        stamp = PdfScanSplitEngine._detect_red_stamp(page_bgr_stamp, cv2=cv2)
        if log:
            for diagnostic in [str(item) for item in stamp.get("diagnostics", []) if item]:
                if logged_diagnostics is not None:
                    if diagnostic in logged_diagnostics:
                        continue
                    logged_diagnostics.add(diagnostic)
                log(f"印章识别降级：{diagnostic}")
        if stamp.get("present"):
            if log:
                log(
                    f"第 {page_index + 1} 页：检测到印章（候选 {int(stamp.get('candidates') or 0)}，面积占比 {float(stamp.get('area_ratio') or 0.0):.4f}）"
                )
            return True, stamp
        return False, stamp

    @staticmethod
    def _detect_feature_for_scan(
        page_index: int,
        page_bgr_feature,
        options: PdfScanSplitOptions,
        *,
        ref_kps,
        ref_des,
        orb=None,
        matcher=None,
        np=None,
        cv2=None,
        log: Optional[LogCallback] = None,
    ) -> bool:
        page_kps, page_des = PdfScanSplitEngine._extract_features(page_bgr_feature, options.nfeatures, orb=orb, cv2=cv2)
        good_count, inliers, inlier_ratio = PdfScanSplitEngine._match_score_with_ransac(
            ref_kps,
            ref_des,
            page_kps,
            page_des,
            options.ratio,
            ransac_reproj_threshold=float(options.ransac_reproj_threshold),
            matcher=matcher,
            np=np,
            cv2=cv2,
        )
        if not PdfScanSplitEngine._is_feature_match(good_count, inliers, inlier_ratio, options):
            return False
        if log:
            log(f"第 {page_index + 1} 页：匹配到标记（匹配 {good_count} / 内点 {inliers} / 比例 {inlier_ratio:.2f}）")
        return True

    @staticmethod
    def _detect_stamp_for_probe(result: dict, page_bgr_stamp, *, cv2=None) -> None:
        stamp = PdfScanSplitEngine._detect_red_stamp(page_bgr_stamp, cv2=cv2)
        result["stamp"] = {**dict(stamp or {}), "executed": True, "skipped_reason": ""}
        if stamp.get("present"):
            result["marked"] = True
            result["reason"] = "检测到印章"

    @staticmethod
    def _detect_feature_for_probe(
        result: dict,
        page_bgr_feature,
        options: PdfScanSplitOptions,
        *,
        ref_kps,
        ref_des,
        orb=None,
        matcher=None,
        np=None,
        cv2=None,
    ) -> None:
        page_kps, page_des = PdfScanSplitEngine._extract_features(page_bgr_feature, options.nfeatures, orb=orb, cv2=cv2)
        good_count, inliers, inlier_ratio = PdfScanSplitEngine._match_score_with_ransac(
            ref_kps,
            ref_des,
            page_kps,
            page_des,
            options.ratio,
            ransac_reproj_threshold=float(options.ransac_reproj_threshold),
            matcher=matcher,
            np=np,
            cv2=cv2,
        )
        result["feature"] = {
            "good_matches": int(good_count),
            "inliers": int(inliers),
            "inlier_ratio": float(inlier_ratio),
            "executed": True,
            "skipped_reason": "",
        }
        if PdfScanSplitEngine._is_feature_match(good_count, inliers, inlier_ratio, options):
            result["marked"] = True
            result["reason"] = f"特征匹配命中（匹配 {good_count} / 内点 {inliers} / 比例 {inlier_ratio:.2f}）"
        elif not result["reason"]:
            result["reason"] = f"特征未命中（匹配 {good_count} / 内点 {inliers} / 比例 {inlier_ratio:.2f}）"

    @staticmethod
    def _apply_roi(img_bgr, roi: tuple[int, int, int, int] | None):
        if not roi:
            return img_bgr
        x, y, w, h = [int(v) for v in roi]
        if w <= 0 or h <= 0:
            return img_bgr
        height, width = img_bgr.shape[:2]
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = max(1, min(w, width - x))
        h = max(1, min(h, height - y))
        return img_bgr[y : y + h, x : x + w]

    @staticmethod
    def _scale_roi(
        roi: tuple[int, int, int, int] | None,
        *,
        src_size: tuple[int, int],
        dst_size: tuple[int, int],
    ) -> tuple[int, int, int, int] | None:
        if not roi:
            return None
        src_w, src_h = [int(v) for v in src_size]
        dst_w, dst_h = [int(v) for v in dst_size]
        if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
            return None
        x, y, w, h = [int(v) for v in roi]
        sx = dst_w / float(src_w)
        sy = dst_h / float(src_h)
        return (
            int(round(x * sx)),
            int(round(y * sy)),
            int(round(w * sx)),
            int(round(h * sy)),
        )

    @staticmethod
    def _expand_roi(
        roi: tuple[int, int, int, int] | None,
        *,
        dst_size: tuple[int, int],
        pad_ratio: float = 0.18,
    ) -> tuple[int, int, int, int] | None:
        if not roi:
            return None
        try:
            dst_w, dst_h = [int(v) for v in dst_size]
            if dst_w <= 0 or dst_h <= 0:
                return roi
            x, y, w, h = [int(v) for v in roi]
            if w <= 0 or h <= 0:
                return roi
            r = float(pad_ratio)
            if r <= 0:
                return roi
            pad_w = int(round(w * r))
            pad_h = int(round(h * r))
            x2 = max(0, x - pad_w)
            y2 = max(0, y - pad_h)
            w2 = min(dst_w - x2, w + pad_w * 2)
            h2 = min(dst_h - y2, h + pad_h * 2)
            if w2 <= 0 or h2 <= 0:
                return roi
            return (int(x2), int(y2), int(w2), int(h2))
        except Exception:
            return roi

    @staticmethod
    def _roi_to_page_clip(
        page,
        roi: tuple[int, int, int, int] | None,
        roi_base_size: tuple[int, int] | None,
        *,
        pad_ratio: float = 0.45,
    ):
        if not roi or not roi_base_size:
            return None
        src_w, src_h = [int(v) for v in roi_base_size]
        if src_w <= 0 or src_h <= 0:
            return None
        x, y, w, h = [float(v) for v in roi]
        if w <= 0 or h <= 0:
            return None
        rect = page.rect
        pad_x = w * max(0.0, float(pad_ratio))
        pad_y = h * max(0.0, float(pad_ratio))
        left = max(0.0, x - pad_x) / float(src_w)
        top = max(0.0, y - pad_y) / float(src_h)
        right = min(float(src_w), x + w + pad_x) / float(src_w)
        bottom = min(float(src_h), y + h + pad_y) / float(src_h)
        clip = fitz.Rect(
            rect.x0 + rect.width * left,
            rect.y0 + rect.height * top,
            rect.x0 + rect.width * right,
            rect.y0 + rect.height * bottom,
        )
        clip = clip & rect
        if clip.is_empty or clip.width <= 0 or clip.height <= 0:
            return None
        return clip



    @staticmethod
    def _require_deps():
        try:
            import numpy as np
            import cv2
        except Exception as e:
            raise RuntimeError("缺少依赖：numpy / opencv-python") from e
        return np, cv2

    @staticmethod
    def _read_image_bgr(path: str):
        np, cv2 = PdfScanSplitEngine._require_deps()
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("无法读取参考文件")
        return img

    @staticmethod
    def _is_pdf_path(path: str) -> bool:
        return str(path or "").lower().endswith(".pdf")

    @staticmethod
    def _read_reference_bgr(path: str, *, page_index: int = 0, dpi: int = 180):
        if PdfScanSplitEngine._is_pdf_path(path):
            return PdfScanSplitEngine._render_pdf_reference_bgr(path, page_index=page_index, dpi=dpi)
        return PdfScanSplitEngine._read_image_bgr(path)

    @staticmethod
    def _render_pdf_reference_bgr(path: str, *, page_index: int = 0, dpi: int = 180):
        doc = fitz.open(path)
        try:
            total = PdfScanSplitEngine._load_ready_pdf(doc)
            idx = PdfScanSplitEngine._validate_page_index(page_index, total)
            return PdfScanSplitEngine._render_page_bgr(doc, idx, dpi)
        finally:
            doc.close()

    @staticmethod
    def _pix_to_bgr(pix):
        np, cv2 = PdfScanSplitEngine._require_deps()
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif pix.n == 1:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        raise ValueError(f"不支持的像素通道数: {pix.n}")

    @staticmethod
    def _render_page_bgr(doc, page_index: int, dpi: int):
        page = doc[page_index]
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=matrix)
        if pix is None or pix.samples is None:
            raise RuntimeError("渲染PDF页面失败")
        return PdfScanSplitEngine._pix_to_bgr(pix)

    @staticmethod
    def _render_page_roi_bgr(
        doc,
        page_index: int,
        dpi: int,
        roi: tuple[int, int, int, int] | None,
        roi_base_size: tuple[int, int] | None,
        *,
        pad_ratio: float = 0.45,
    ):
        page = doc[page_index]
        clip = PdfScanSplitEngine._roi_to_page_clip(page, roi, roi_base_size, pad_ratio=pad_ratio)
        if clip is None:
            raise ValueError("ROI区域无效")
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=matrix, clip=clip)
        if pix is None or pix.samples is None:
            raise RuntimeError("渲染PDF页面ROI失败")
        return PdfScanSplitEngine._pix_to_bgr(pix)

    @staticmethod
    def _extract_features(img_bgr, nfeatures: int, *, orb=None, cv2=None):
        if cv2 is None:
            _, cv2 = PdfScanSplitEngine._require_deps()
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        orb = orb or cv2.ORB_create(nfeatures=int(nfeatures))
        kps, des = orb.detectAndCompute(gray, None)
        if des is None or kps is None:
            return [], None
        return kps, des

    @staticmethod
    def _match_score(ref_kps, ref_des, page_kps, page_des, ratio: float, *, matcher=None, np=None, cv2=None):
        return PdfScanSplitEngine._match_score_with_ransac(
            ref_kps,
            ref_des,
            page_kps,
            page_des,
            ratio,
            ransac_reproj_threshold=5.0,
            matcher=matcher,
            np=np,
            cv2=cv2,
        )

    @staticmethod
    def _match_score_with_ransac(
        ref_kps,
        ref_des,
        page_kps,
        page_des,
        ratio: float,
        *,
        ransac_reproj_threshold: float = 5.0,
        matcher=None,
        np=None,
        cv2=None,
    ):
        if np is None or cv2 is None:
            np, cv2 = PdfScanSplitEngine._require_deps()
        if ref_des is None or page_des is None:
            return 0, 0, 0.0
        matcher = matcher or cv2.BFMatcher(cv2.NORM_HAMMING)
        knn = matcher.knnMatch(ref_des, page_des, k=2)
        good = []
        for pair in knn:
            if len(pair) != 2:
                continue
            m, n = pair
            if m.distance < float(ratio) * n.distance:
                good.append(m)
        good_count = len(good)
        if good_count < 4:
            return good_count, 0, 0.0
        src_pts = np.float32([ref_kps[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([page_kps[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        thr = float(ransac_reproj_threshold)
        if not (thr > 0.0):
            thr = 5.0
        _, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, thr)
        inliers = int(mask.sum()) if mask is not None else 0
        inlier_ratio = (float(inliers) / float(good_count)) if good_count > 0 else 0.0
        return good_count, inliers, inlier_ratio

    @staticmethod
    def _order_qr_quad(quad, *, np=None):
        if np is None:
            np, _ = PdfScanSplitEngine._require_deps()
        try:
            points = np.asarray(quad, dtype="float32").reshape(4, 2)
            sums = points.sum(axis=1)
            diffs = points[:, 0] - points[:, 1]
            return np.array(
                [
                    points[sums.argmin()],
                    points[diffs.argmax()],
                    points[sums.argmax()],
                    points[diffs.argmin()],
                ],
                dtype="float32",
            )
        except Exception:
            return None

    @staticmethod
    def _detect_qrcodes(
        img_bgr,
        *,
        detector=None,
        cv2=None,
        max_robust_attempts: int | None = None,
        text_contains: str = "",
        scan_cache: _QRCodeScanCache | None = None,
        details: Optional[dict] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> list[str]:
        if cv2 is None:
            np, cv2 = PdfScanSplitEngine._require_deps()
        else:
            try:
                import numpy as np  # type: ignore
            except Exception:
                np, _ = PdfScanSplitEngine._require_deps()
        detector = detector or cv2.QRCodeDetector()
        if max_robust_attempts is None:
            try:
                max_robust_attempts = int(os.getenv("FILETOOLBOX_QR_MAX_ATTEMPTS", "180") or "180")
            except Exception:
                max_robust_attempts = 180
        effort = max(12, int(max_robust_attempts or 180))
        required_text = str(text_contains or "").strip()
        out = details if details is not None else {}
        out.update({"candidate_present": False, "candidate_confident": False, "variant": "", "bbox": None, "decoded_items": [], "diagnostics": []})

        def _diagnostic(stage: str, exc: Exception) -> None:
            message = f"{stage}失败：{type(exc).__name__}: {exc}"
            diagnostics = out.setdefault("diagnostics", [])
            if message not in diagnostics:
                diagnostics.append(message)

        def _cancelled() -> bool:
            return PdfScanSplitEngine._is_cancelled(cancel_check)

        def _position_bbox(item, *, offset_x: int, offset_y: int, base_w: int, base_h: int):
            try:
                pos = item.position
                pts = [pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left]
                xs = [float(p.x) + float(offset_x) for p in pts]
                ys = [float(p.y) + float(offset_y) for p in pts]
                left = max(0.0, min(xs)) / float(base_w)
                top = max(0.0, min(ys)) / float(base_h)
                right = min(float(base_w), max(xs)) / float(base_w)
                bottom = min(float(base_h), max(ys)) / float(base_h)
                if right > left and bottom > top:
                    return (left, top, right - left, bottom - top)
            except Exception:
                pass
            return None

        def _decode_zxing(image, *, variant: str, offset_x: int = 0, offset_y: int = 0) -> list[str]:
            if _cancelled():
                return []
            zxingcpp = PdfScanSplitEngine._load_zxingcpp()
            if zxingcpp is None:
                return []
            try:
                results = zxingcpp.read_barcodes(
                    image,
                    formats=zxingcpp.BarcodeFormat.QRCode,
                    try_rotate=True,
                    try_downscale=True,
                    try_invert=True,
                )
            except Exception as exc:
                _diagnostic(f"ZXing {variant} 解码", exc)
                return []
            infos: list[str] = []
            decoded_items: list[dict] = []
            for item in list(results or []):
                try:
                    if not bool(getattr(item, "valid", True)):
                        continue
                    text = str(getattr(item, "text", "") or "").strip()
                    if not text:
                        continue
                    infos.append(text)
                    decoded_items.append(
                        {
                            "text": text,
                            "bbox": _position_bbox(item, offset_x=offset_x, offset_y=offset_y, base_w=w, base_h=h),
                        }
                    )
                except Exception:
                    continue
            if infos:
                selected = next(
                    (
                        item
                        for item in decoded_items
                        if not required_text or PdfScanSplitEngine._match_texts([str(item.get("text") or "")], required_text)
                    ),
                    None,
                )
                out.update(
                    {
                        "candidate_present": True,
                        "candidate_confident": True,
                        "variant": variant,
                        "bbox": selected.get("bbox") if selected else None,
                        "decoded_items": decoded_items,
                    }
                )
            return infos

        def _cached_crop():
            if scan_cache is None or scan_cache.bbox is None:
                return None
            try:
                nx, ny, nw, nh = scan_cache.bbox
                pad_x = nw * 0.75
                pad_y = nh * 0.75
                x1 = max(0, int(round((nx - pad_x) * w)))
                y1 = max(0, int(round((ny - pad_y) * h)))
                x2 = min(w, int(round((nx + nw + pad_x) * w)))
                y2 = min(h, int(round((ny + nh + pad_y) * h)))
                if x2 - x1 < 16 or y2 - y1 < 16:
                    return None
                return img_bgr[y1:y2, x1:x2], x1, y1
            except Exception as exc:
                _diagnostic("二维码缓存区域裁剪", exc)
                return None

        def _candidate_quads(gray_img) -> list:
            polys: list = []
            try:
                if hasattr(detector, "detectMulti"):
                    ok, pts = detector.detectMulti(gray_img)
                    if bool(ok) and pts is not None:
                        polys = list(pts)
            except Exception as exc:
                _diagnostic("OpenCV 多二维码候选检测", exc)
                polys = []
            if not polys:
                try:
                    ok, pts = detector.detect(gray_img)
                    if bool(ok) and pts is not None:
                        polys = [pts]
                except Exception as exc:
                    _diagnostic("OpenCV 二维码候选检测", exc)
                    polys = []
            normalized: list = []
            for poly in polys:
                try:
                    quad = poly[0] if tuple(getattr(poly, "shape", ())) == (1, 4, 2) else poly
                    if tuple(getattr(quad, "shape", ())) == (4, 2):
                        normalized.append(quad.astype("float32"))
                except Exception as exc:
                    _diagnostic("二维码候选坐标标准化", exc)
                    continue
            return normalized

        def _decode_warped_candidates(gray_img) -> list[str]:
            area_threshold = max(55.0, 0.00006 * float(h * w))
            first_nonmatching_infos: list[str] = []
            first_nonmatching_bbox = None
            for quad in _candidate_quads(gray_img):
                try:
                    area = abs(float(cv2.contourArea(quad)))
                    x, y, bw, bh = cv2.boundingRect(quad)
                    if area < area_threshold or bw <= 0 or bh <= 0:
                        continue
                    aspect = float(max(bw, bh)) / float(min(bw, bh))
                    solidity = area / float(bw * bh)
                    confident = aspect <= 2.0 and solidity >= 0.18
                    out["candidate_present"] = True
                    out["candidate_confident"] = bool(out.get("candidate_confident") or confident)
                    if not confident:
                        continue
                    ordered = PdfScanSplitEngine._order_qr_quad(quad, np=np)
                    if ordered is None:
                        continue
                    side = max(
                        float(cv2.norm(ordered[0] - ordered[1])),
                        float(cv2.norm(ordered[1] - ordered[2])),
                        float(cv2.norm(ordered[2] - ordered[3])),
                        float(cv2.norm(ordered[3] - ordered[0])),
                    )
                    if side <= 8:
                        continue
                    code_size = int(max(240, min(900, side * 2.5)))
                    quiet = max(20, int(round(code_size * 0.12)))
                    canvas_size = code_size + quiet * 2
                    dst = np.array(
                        [
                            [quiet, quiet],
                            [quiet + code_size - 1, quiet],
                            [quiet + code_size - 1, quiet + code_size - 1],
                            [quiet, quiet + code_size - 1],
                        ],
                        dtype="float32",
                    )
                    matrix = cv2.getPerspectiveTransform(ordered, dst)
                    warped = cv2.warpPerspective(
                        gray_img,
                        matrix,
                        (canvas_size, canvas_size),
                        flags=cv2.INTER_CUBIC,
                        borderMode=cv2.BORDER_CONSTANT,
                        borderValue=255,
                    )
                    infos = _decode_zxing(warped, variant="perspective")
                    if infos:
                        if required_text and not PdfScanSplitEngine._match_texts(infos, required_text):
                            if not first_nonmatching_infos:
                                first_nonmatching_infos = infos
                                first_nonmatching_bbox = (x / float(w), y / float(h), bw / float(w), bh / float(h))
                            continue
                        out["bbox"] = (x / float(w), y / float(h), bw / float(w), bh / float(h))
                        return infos
                except Exception as exc:
                    _diagnostic("二维码候选透视校正", exc)
                    continue
            if first_nonmatching_infos:
                out["bbox"] = first_nonmatching_bbox
                return first_nonmatching_infos
            return []

        try:
            h, w = img_bgr.shape[:2]
        except Exception as exc:
            _diagnostic("读取二维码图像尺寸", exc)
            return []
        if h <= 2 or w <= 2:
            return []

        cached = _cached_crop()
        if cached is not None:
            crop, x, y = cached
            infos = _decode_zxing(crop, variant="cache", offset_x=x, offset_y=y)
            if infos and (not required_text or PdfScanSplitEngine._match_texts(infos, required_text)):
                return infos
            if infos:
                # 缓存区域可能含有另一个二维码；关键词未命中时继续整页扫描。
                out.update({"variant": "", "bbox": None, "decoded_items": []})

        infos = _decode_zxing(img_bgr, variant="original")
        if infos:
            return infos

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        variants: list[tuple[str, object]] = []
        if effort >= 24:
            try:
                variants.append(("contrast", cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)))
            except Exception as exc:
                _diagnostic("二维码对比度增强", exc)
        if effort >= 72:
            try:
                _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                variants.append(("otsu", otsu))
            except Exception as exc:
                _diagnostic("二维码 Otsu 二值化", exc)
        if effort >= 144:
            try:
                block = min(51, max(15, (min(h, w) // 40) | 1))
                adaptive = cv2.adaptiveThreshold(
                    gray,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    block,
                    5,
                )
                variants.append(("adaptive", adaptive))
            except Exception as exc:
                _diagnostic("二维码自适应二值化", exc)
        if scan_cache is not None and scan_cache.variant:
            variants.sort(key=lambda item: item[0] != scan_cache.variant)
        for name, image in variants:
            infos = _decode_zxing(image, variant=name)
            if infos:
                return infos

        return _decode_warped_candidates(gray)

    @staticmethod
    def _detect_red_stamp(img_bgr, *, cv2=None) -> dict:
        if cv2 is None:
            np, cv2 = PdfScanSplitEngine._require_deps()
        else:
            try:
                import numpy as np  # type: ignore
            except Exception:
                np, _ = PdfScanSplitEngine._require_deps()

        diagnostics: list[str] = []

        def _diagnostic(stage: str, exc: Exception) -> None:
            message = f"{stage}失败：{type(exc).__name__}: {exc}"
            if message not in diagnostics:
                diagnostics.append(message)

        h, w = img_bgr.shape[:2]
        if h <= 2 or w <= 2:
            return {"present": False, "diagnostics": ["图像尺寸无效"]}

        try:
            hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
            mask1 = cv2.inRange(hsv, (0, 45, 35), (10, 255, 255))
            mask2 = cv2.inRange(hsv, (160, 45, 35), (180, 255, 255))
            mask = cv2.bitwise_or(mask1, mask2)
        except Exception as exc:
            _diagnostic("HSV 红色掩码", exc)
            mask = None

        try:
            lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
            a = lab[:, :, 1]
            _, mask_a = cv2.threshold(a, 155, 255, cv2.THRESH_BINARY)
            mask = mask_a if mask is None else cv2.bitwise_or(mask, mask_a)
        except Exception as exc:
            _diagnostic("LAB 红色掩码", exc)

        if mask is None:
            return {"present": False, "diagnostics": diagnostics}

        try:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        except Exception as exc:
            _diagnostic("印章掩码形态学处理", exc)

        try:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception as exc:
            _diagnostic("印章轮廓检测", exc)
            return {"present": False, "diagnostics": diagnostics}

        min_area = max(140.0, 0.00035 * float(h * w))
        best = None
        best_score = 0.0
        candidates = 0

        for c in contours or []:
            try:
                area = float(cv2.contourArea(c))
            except Exception:
                continue
            if area < min_area:
                continue
            try:
                x, y, bw, bh = cv2.boundingRect(c)
            except Exception:
                continue
            if bw <= 0 or bh <= 0:
                continue
            aspect = float(max(bw, bh)) / float(min(bw, bh))
            if aspect > 2.4:
                continue
            try:
                peri = float(cv2.arcLength(c, True))
            except Exception:
                peri = 0.0
            if peri <= 1e-6:
                continue
            circularity = float(4.0 * float(np.pi) * area / (peri * peri))
            try:
                hull = cv2.convexHull(c)
                hull_area = float(cv2.contourArea(hull))
                solidity = float(area / hull_area) if hull_area > 1e-6 else 0.0
            except Exception:
                solidity = 0.0

            area_ratio = float(area / float(h * w))
            score = area_ratio * max(0.0, min(1.0, circularity)) * (0.6 + 0.4 * max(0.0, min(1.0, solidity)))
            candidates += 1
            if score > best_score:
                best_score = float(score)
                best = {
                    "area": float(area),
                    "area_ratio": float(area_ratio),
                    "aspect": float(aspect),
                    "circularity": float(circularity),
                    "solidity": float(solidity),
                    "bbox": [int(x), int(y), int(bw), int(bh)],
                }

        if best is None:
            return {"present": False, "candidates": int(candidates), "diagnostics": diagnostics}

        present = False
        if best["area_ratio"] >= 0.0012 and best["circularity"] >= 0.10 and best["solidity"] >= 0.20 and best["aspect"] <= 2.2:
            present = True
        elif best["area_ratio"] >= 0.0025 and best["solidity"] >= 0.18 and best["aspect"] <= 2.2:
            present = True

        out = {"present": bool(present), "candidates": int(candidates), "score": float(best_score), "diagnostics": diagnostics}
        out.update(best)
        return out

    @staticmethod
    def _qr_detect_area(img_bgr, *, detector=None, cv2=None) -> float:
        area, _, _ = PdfScanSplitEngine._qr_detect_stats(img_bgr, detector=detector, cv2=cv2)
        return float(area)

    @staticmethod
    def _qr_detect_stats(img_bgr, *, detector=None, cv2=None) -> tuple[float, float, float]:
        if cv2 is None:
            _, cv2 = PdfScanSplitEngine._require_deps()
        detector = detector or cv2.QRCodeDetector()
        try:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        except Exception:
            return 0.0, 0.0, 0.0
        try:
            detected, pts = detector.detect(gray)
            if not detected or pts is None:
                return 0.0, 0.0, 0.0
            try:
                poly = pts[0] if hasattr(pts, "__len__") and len(pts) > 0 else pts
                poly = poly.astype("float32")
                area = float(cv2.contourArea(poly))
                x, y, w, h = cv2.boundingRect(poly)
                if w <= 0 or h <= 0:
                    return float(area), 0.0, 0.0
                aspect = float(max(w, h)) / float(min(w, h))
                solidity = float(area) / float(w * h)
                return float(area), float(aspect), float(solidity)
            except Exception:
                return 0.0, 0.0, 0.0
        except Exception:
            return 0.0, 0.0, 0.0

    @staticmethod
    def _qr_detect_likely(img_bgr, *, detector=None, cv2=None) -> bool:
        if cv2 is None:
            _, cv2 = PdfScanSplitEngine._require_deps()
        detector = detector or cv2.QRCodeDetector()
        try:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        except Exception:
            return False
        try:
            detected, _ = detector.detect(gray)
            return bool(detected)
        except Exception:
            return False

    @staticmethod
    def _qr_detect_confident(img_bgr, *, detector=None, cv2=None) -> bool:
        if cv2 is None:
            _, cv2 = PdfScanSplitEngine._require_deps()
        try:
            h, w = img_bgr.shape[:2]
            threshold = max(55.0, 0.00006 * float(h * w))
        except Exception:
            threshold = 75.0
        area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(img_bgr, detector=detector, cv2=cv2)
        if area < float(threshold):
            return False
        if aspect and aspect > 2.0:
            return False
        if solidity and solidity < 0.18:
            return False
        return True

    @staticmethod
    @_serialized_opencv_task
    def _scan_markers(
        pdf_path: str,
        reference_image_path: str,
        options: PdfScanSplitOptions,
        *,
        page_limit: int = 0,
        perf: Optional[_PerfStats] = None,
        progress: Optional[ProgressCallback] = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> tuple[list[int], int]:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError("PDF文件不存在")
        ctx = PdfScanSplitEngine._prepare_detection_context(reference_image_path, options, log=log)
        mode = ctx.mode
        use_qr = ctx.use_qr
        use_stamp = ctx.use_stamp
        use_feature = ctx.use_feature
        np = ctx.np
        cv2 = ctx.cv2
        detector = ctx.detector
        orb = ctx.orb
        matcher = ctx.matcher
        ref_kps = ctx.ref_kps
        ref_des = ctx.ref_des
        ref_size = ctx.ref_size
        roi_base_size = ctx.roi_base_size

        doc = fitz.open(pdf_path)
        cancel_log_emitted = False
        total = 0
        processed = 0
        markers: list[int] = []
        try:
            total = PdfScanSplitEngine._load_ready_pdf(doc)
            original_total = total
            if int(page_limit or 0) > 0:
                total = min(total, int(page_limit))
            if log:
                if int(page_limit or 0) > 0:
                    log(f"PDF 总页数：{original_total}，本次扫描前 {total} 页")
                else:
                    log(f"PDF 总页数：{original_total}，本次扫描全部页")
            started_at = time.perf_counter()
            i = 0
            qr_scan_cache = _QRCodeScanCache() if use_qr and not options.qrcode_no_decode else None
            stamp_logged_diagnostics: set[str] = set()
            use_roi_clip = PdfScanSplitEngine._should_use_roi_clip(options, roi_base_size)
            roi_clip_logged = False
            if log:
                log(PdfScanSplitEngine._format_roi_log(options, roi_base_size))
            if log and mode == "auto" and ref_des is None:
                log("自动模式：未选择参考文件或参考文件特征不足，已跳过特征匹配")
            while i < total:
                page_bgr_full = None
                page_bgr_roi = None
                if use_roi_clip:
                    render_started_at = time.perf_counter()
                    try:
                        page_bgr_roi = PdfScanSplitEngine._render_page_roi_checked(
                            doc,
                            i,
                            options.dpi,
                            options.reference_roi,
                            roi_base_size,
                            cancel_check=cancel_check,
                        )
                        if page_bgr_roi is not None and log and not roi_clip_logged:
                            log("ROI局部渲染：已启用")
                            roi_clip_logged = True
                        if page_bgr_roi is not None and perf is not None:
                            perf.roi_clip_pages += 1
                    except Exception as exc:
                        if perf is not None:
                            perf.roi_clip_fallback_pages += 1
                        if log and not roi_clip_logged:
                            log(f"ROI局部渲染失败，已回退整页渲染：{exc}")
                            roi_clip_logged = True
                        page_bgr_roi = None
                    if perf is not None:
                        perf.render_seconds += time.perf_counter() - render_started_at
                if page_bgr_roi is not None:
                    page_bgr_qr, page_bgr_stamp, page_bgr_feature = PdfScanSplitEngine._prepare_page_images_from_roi_clip(
                        page_bgr_roi,
                        use_qr=use_qr,
                        use_stamp=use_stamp,
                        use_feature=use_feature,
                        reference_roi=options.reference_roi,
                        roi_base_size=roi_base_size,
                    )
                else:
                    render_started_at = time.perf_counter()
                    page_bgr_full = PdfScanSplitEngine._render_page_checked(
                        doc,
                        i,
                        options.dpi,
                        cancel_check=cancel_check,
                    )
                    if page_bgr_full is None:
                        break
                    if perf is not None:
                        perf.render_seconds += time.perf_counter() - render_started_at
                    page_bgr_qr, page_bgr_stamp, page_bgr_feature = PdfScanSplitEngine._prepare_page_images(
                        page_bgr_full,
                        use_qr=use_qr,
                        use_stamp=use_stamp,
                        use_feature=use_feature,
                        ref_size=ref_size,
                        roi_base_size=roi_base_size,
                        options=options,
                    )
                processed += 1
                marked = False
                hit_detector = ""
                stamp_debug = None
                qr_status: dict = {}

                if use_stamp:
                    stamp_started_at = time.perf_counter()
                    marked, stamp_debug = PdfScanSplitEngine._detect_stamp_for_scan(
                        i,
                        page_bgr_stamp,
                        cv2=cv2,
                        log=log,
                        logged_diagnostics=stamp_logged_diagnostics,
                    )
                    if perf is not None:
                        perf.stamp_seconds += time.perf_counter() - stamp_started_at
                    if marked:
                        hit_detector = "stamp"
                        if perf is not None:
                            perf.stamp_hits += 1
                        if mode == "auto" and log:
                            log(f"第 {i + 1} 页：二维码和特征点匹配已跳过（印章已命中）")

                if not marked and use_qr:
                    qr_started_at = time.perf_counter()
                    marked = PdfScanSplitEngine._detect_qr_for_scan(
                        doc,
                        i,
                        page_bgr_qr,
                        options,
                        detector=detector,
                        cv2=cv2,
                        roi_base_size=roi_base_size,
                        scan_cache=qr_scan_cache,
                        status=qr_status,
                        perf=perf,
                        log=log,
                        cancel_check=cancel_check,
                    )
                    if perf is not None:
                        perf.qr_seconds += time.perf_counter() - qr_started_at
                    PdfScanSplitEngine._raise_if_cancelled(cancel_check)
                    if marked:
                        hit_detector = "qr"
                        if perf is not None:
                            perf.qr_hits += 1
                        if mode == "auto" and log and ref_des is not None:
                            log(f"第 {i + 1} 页：二维码命中，特征点匹配已跳过")

                if not marked and use_feature and ref_des is not None:
                    feature_started_at = time.perf_counter()
                    marked = PdfScanSplitEngine._detect_feature_for_scan(
                        i,
                        page_bgr_feature,
                        options,
                        ref_kps=ref_kps,
                        ref_des=ref_des,
                        orb=orb,
                        matcher=matcher,
                        np=np,
                        cv2=cv2,
                        log=log,
                    )
                    if perf is not None:
                        perf.feature_seconds += time.perf_counter() - feature_started_at
                    if marked:
                        hit_detector = "feature"
                        if perf is not None:
                            perf.feature_hits += 1

                if marked:
                    markers.append(i)
                else:
                    PdfScanSplitEngine._log_scan_miss(i, total, mode, stamp_debug, log)
                if progress:
                    progress(i + 1, total)
                if marked and int(options.qrcode_skip_pages or 0) > 0:
                    skip_count = min(int(options.qrcode_skip_pages or 0), max(0, total - i - 1))
                    if skip_count > 0:
                        if perf is not None:
                            perf.pages_skipped += skip_count
                        if log:
                            skip_start = i + 2
                            skip_end = i + 1 + skip_count
                            skip_range = f"第 {skip_start} 页" if skip_start == skip_end else f"第 {skip_start}-{skip_end} 页"
                            log(
                                f"第 {i + 1} 页命中{('印章' if hit_detector == 'stamp' else '二维码' if hit_detector == 'qr' else '特征点')}，"
                                f"按设置跳过后续 {skip_count} 页：{skip_range}"
                            )
                    i += int(options.qrcode_skip_pages or 0) + 1
                else:
                    i += 1

            elapsed_s = time.perf_counter() - started_at
            if perf is not None:
                perf.scan_seconds = elapsed_s
                perf.page_scan_seconds = elapsed_s
                perf.pages_scanned = processed
                perf.markers_found = len(markers)
            if log and PdfScanSplitEngine._is_cancelled(cancel_check):
                log(
                    f"扫描已取消：实际处理 {processed}/{total} 页，跳过 {int(perf.pages_skipped if perf else 0)} 页，"
                    f"已命中 {len(markers)} 页"
                )
                cancel_log_emitted = True
            elif log:
                avg_ms = int(round((elapsed_s * 1000.0) / float(max(1, processed))))
                log(
                    f"页面扫描完成：实际处理 {processed}/{total} 页，跳过 {int(perf.pages_skipped if perf else 0)} 页，"
                    f"命中 {len(markers)} 页，耗时 {PdfScanSplitEngine._fmt_seconds(elapsed_s)}，平均 {avg_ms}ms/页"
                )
                if perf is not None:
                    log(
                        f"识别汇总：印章命中 {perf.stamp_hits} 页，二维码命中 {perf.qr_hits} 页，"
                        f"特征点命中 {perf.feature_hits} 页，全部检测器未命中 {max(0, processed - len(markers))} 页"
                    )
            return markers, total
        finally:
            if log and PdfScanSplitEngine._is_cancelled(cancel_check) and not cancel_log_emitted:
                log(
                    f"扫描已取消：实际处理 {processed}/{total if 'total' in locals() else 0} 页，"
                    f"跳过 {int(perf.pages_skipped if perf else 0)} 页，已命中 {len(markers)} 页"
                )
            doc.close()

    @staticmethod
    def find_marker_pages(
        pdf_path: str,
        reference_image_path: str,
        options: PdfScanSplitOptions,
        *,
        progress: Optional[ProgressCallback] = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> list[int]:
        if isinstance(options, dict):
            options = PdfScanSplitOptions(**options)
        markers, _ = PdfScanSplitEngine._scan_markers(
            pdf_path,
            reference_image_path,
            options,
            progress=progress,
            log=log,
            cancel_check=cancel_check,
        )
        return markers

    @staticmethod
    def scan_only(
        pdf_path: str,
        reference_image_path: str,
        options: PdfScanSplitOptions,
        *,
        page_limit: int = 0,
        progress: Optional[ProgressCallback] = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> PdfScanSplitResult:
        if isinstance(options, dict):
            options = PdfScanSplitOptions(**options)
        perf = _PerfStats()
        scan_started_at = time.perf_counter()
        markers, total = PdfScanSplitEngine._scan_markers(
            pdf_path,
            reference_image_path,
            options,
            page_limit=page_limit,
            perf=perf,
            progress=progress,
            log=log,
            cancel_check=cancel_check,
        )
        perf.scan_seconds = time.perf_counter() - scan_started_at
        build_started_at = time.perf_counter()
        segments = PdfScanSplitEngine.build_segments(int(total), markers, options)
        perf.build_seconds = time.perf_counter() - build_started_at
        suspect_segments = PdfScanSplitEngine.analyze_suspect_segments(segments, int(options.max_segment_pages or 0))
        PdfScanSplitEngine.log_suspect_segments(suspect_segments, log)
        perf.total_seconds = perf.scan_seconds + perf.build_seconds
        return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=int(total), suspect_segments=suspect_segments, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))

    @staticmethod
    @_serialized_opencv_task
    def probe_page(
        pdf_path: str,
        reference_image_path: str,
        options: PdfScanSplitOptions,
        *,
        page_index: int,
        cancel_check: Optional[CancelCheck] = None,
    ) -> dict:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError("PDF文件不存在")
        perf = _PerfStats()
        total_started_at = time.perf_counter()
        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        ctx = PdfScanSplitEngine._prepare_detection_context(reference_image_path, options, log=None)
        mode = ctx.mode
        use_qr = ctx.use_qr
        use_stamp = ctx.use_stamp
        use_feature = ctx.use_feature
        np = ctx.np
        cv2 = ctx.cv2
        detector = ctx.detector
        orb = ctx.orb
        matcher = ctx.matcher
        ref_kps = ctx.ref_kps
        ref_des = ctx.ref_des
        ref_size = ctx.ref_size
        roi_base_size = ctx.roi_base_size

        doc = fitz.open(pdf_path)
        try:
            total = PdfScanSplitEngine._load_ready_pdf(doc)
            idx = PdfScanSplitEngine._validate_page_index(page_index, total)
            page_bgr_full = None
            page_bgr_roi = None
            if PdfScanSplitEngine._should_use_roi_clip(options, roi_base_size):
                render_started_at = time.perf_counter()
                try:
                    page_bgr_roi = PdfScanSplitEngine._render_page_roi_checked(
                        doc,
                        idx,
                        options.dpi,
                        options.reference_roi,
                        roi_base_size,
                        cancel_check=cancel_check,
                    )
                    if page_bgr_roi is not None:
                        perf.roi_clip_pages += 1
                except Exception:
                    perf.roi_clip_fallback_pages += 1
                    page_bgr_roi = None
                perf.render_seconds += time.perf_counter() - render_started_at
            if page_bgr_roi is None:
                render_started_at = time.perf_counter()
                page_bgr_full = PdfScanSplitEngine._render_page_checked(
                    doc,
                    idx,
                    options.dpi,
                    cancel_check=cancel_check,
                )
                if page_bgr_full is None:
                    raise RuntimeError("已取消")
                perf.render_seconds += time.perf_counter() - render_started_at
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)

            if page_bgr_roi is not None:
                page_bgr_qr, page_bgr_stamp, page_bgr_feature = PdfScanSplitEngine._prepare_page_images_from_roi_clip(
                    page_bgr_roi,
                    use_qr=use_qr,
                    use_stamp=use_stamp,
                    use_feature=use_feature,
                    reference_roi=options.reference_roi,
                    roi_base_size=roi_base_size,
                )
            else:
                page_bgr_qr, page_bgr_stamp, page_bgr_feature = PdfScanSplitEngine._prepare_page_images(
                    page_bgr_full,
                    use_qr=use_qr,
                    use_stamp=use_stamp,
                    use_feature=use_feature,
                    ref_size=ref_size,
                    roi_base_size=roi_base_size,
                    options=options,
                )

            result = PdfScanSplitEngine._new_probe_result(page_index, total, mode, options)

            if use_stamp:
                PdfScanSplitEngine._raise_if_cancelled(cancel_check)
                stamp_started_at = time.perf_counter()
                PdfScanSplitEngine._detect_stamp_for_probe(result, page_bgr_stamp, cv2=cv2)
                perf.stamp_seconds += time.perf_counter() - stamp_started_at
                if result["marked"]:
                    result["qrcode"]["skipped_reason"] = "印章已命中"
                    result["feature"]["skipped_reason"] = "印章已命中"

            if (not result["marked"]) and use_qr:
                result["qrcode"].update({"executed": True, "skipped_reason": ""})
                qr_started_at = time.perf_counter()
                qr_status: dict = {}
                result["marked"] = PdfScanSplitEngine._detect_qr_for_scan(
                    doc,
                    idx,
                    page_bgr_qr,
                    options,
                    detector=detector,
                    cv2=cv2,
                    roi_base_size=roi_base_size,
                    status=qr_status,
                    perf=perf,
                    cancel_check=cancel_check,
                )
                perf.qr_seconds += time.perf_counter() - qr_started_at
                result["qrcode"].update(
                    {
                        "present": bool(qr_status.get("present")),
                        "candidate_confident": bool(qr_status.get("candidate_confident")),
                        "infos": list(qr_status.get("infos") or []),
                        "variant": str(qr_status.get("variant") or ""),
                        "bbox": qr_status.get("bbox"),
                        "dpi": int(qr_status.get("dpi") or options.dpi),
                    }
                )
                if result["marked"]:
                    result["reason"] = "二维码解码命中" if qr_status.get("decoded") else "高可信二维码候选（未解码）"
                    result["feature"]["skipped_reason"] = "二维码已命中"
                elif qr_status.get("decoded") and options.qrcode_text_contains:
                    result["reason"] = "二维码解码到内容，但未命中关键字"
                elif qr_status.get("present"):
                    result["reason"] = "检测到二维码候选但未解码，未视为标记页"

            if (not result["marked"]) and use_feature and ref_des is not None:
                PdfScanSplitEngine._raise_if_cancelled(cancel_check)
                feature_started_at = time.perf_counter()
                PdfScanSplitEngine._detect_feature_for_probe(
                    result,
                    page_bgr_feature,
                    options,
                    ref_kps=ref_kps,
                    ref_des=ref_des,
                    orb=orb,
                    matcher=matcher,
                    np=np,
                    cv2=cv2,
                )
                perf.feature_seconds += time.perf_counter() - feature_started_at
            elif (not result["marked"]) and use_feature and ref_des is None:
                result["feature"]["skipped_reason"] = "未提供可用参考特征"

            if not result["reason"]:
                result["reason"] = "未命中"
            perf.pages_scanned = 1
            perf.markers_found = 1 if result.get("marked") else 0
            perf.scan_seconds = time.perf_counter() - total_started_at
            perf.page_scan_seconds = perf.scan_seconds
            perf.total_seconds = perf.scan_seconds
            result["performance_stats"] = PdfScanSplitEngine._perf_to_dict(perf)
            return result
        finally:
            doc.close()

    @staticmethod
    def build_segments(total_pages: int, marker_pages: list[int], options: PdfScanSplitOptions) -> list[list[int]]:
        # 分段构建算法说明：
        # - 始终按页码顺序遍历（i = 0 到 total_pages-1）
        # - marker_as_first_page=True: marker 页作为新分段的第 1 页；False: marker 页追加到当前分段末尾
        # - exclude_marker_page=True: marker 页不包含在任何分段中
        # - i == 0 时：若第 0 页是 marker，current 为空（刚进入循环），不会触发 segments.append
        #   直接根据 exclude_marker_page 决定是否加入 current（即第一个分段）
        marker_set = set(int(p) for p in marker_pages if 0 <= int(p) < total_pages)
        segments: list[list[int]] = []
        current: list[int] = []

        for i in range(total_pages):
            is_marker = i in marker_set
            if options.marker_as_first_page and is_marker and i != 0:
                if current:
                    segments.append(current)
                    current = []
                if options.exclude_marker_page:
                    continue
                current.append(i)
                continue

            if not options.marker_as_first_page and is_marker:
                if not options.exclude_marker_page:
                    current.append(i)
                if current:
                    segments.append(current)
                    current = []
                continue

            if options.marker_as_first_page and is_marker and i == 0 and options.exclude_marker_page:
                continue

            current.append(i)

        if current:
            segments.append(current)

        return segments

    @staticmethod
    def analyze_suspect_segments(segments: list[list[int]], max_pages: int) -> list[dict]:
        max_pages = int(max_pages or 0)
        if max_pages <= 0:
            return []
        suspects: list[dict] = []
        for idx, pages in enumerate(segments, start=1):
            if not pages:
                continue
            page_count = len(pages)
            if page_count <= max_pages:
                continue
            suspects.append(
                {
                    "index": int(idx),
                    "start_page": int(pages[0]) + 1,
                    "end_page": int(pages[-1]) + 1,
                    "page_count": int(page_count),
                    "max_pages": int(max_pages),
                }
            )
        return suspects

    @staticmethod
    def log_suspect_segments(suspect_segments: list[dict], log: Optional[LogCallback]) -> None:
        if not log or not suspect_segments:
            return
        log(f"疑似漏检检查：发现 {len(suspect_segments)} 个超长分段")
        for item in suspect_segments[:20]:
            log(
                f"疑似漏检：第 {int(item.get('index') or 0)} 段 "
                f"{int(item.get('start_page') or 0)}-{int(item.get('end_page') or 0)} 页，"
                f"共 {int(item.get('page_count') or 0)} 页，超过单份最大页数 {int(item.get('max_pages') or 0)}"
            )
        if len(suspect_segments) > 20:
            log(f"疑似漏检：还有 {len(suspect_segments) - 20} 个超长分段未展开显示")



    @staticmethod
    def write_segments(
        pdf_path: str,
        segments: list[list[int]],
        *,
        output_dir: str,
        prefix: str,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> list[str]:
        if not segments:
            return []
        stem = os.path.splitext(os.path.basename(pdf_path))[0]

        def on_output(out_path: str, page_indexes: list[int], elapsed_s: float) -> None:
            if not log or not page_indexes:
                return
            first_page = page_indexes[0] + 1
            last_page = page_indexes[-1] + 1
            log(f"已生成：{os.path.basename(out_path)}  ({first_page}-{last_page})  用时 {PdfScanSplitEngine._fmt_seconds(elapsed_s)}")

        jobs = [
            PdfOutputJob(f"{prefix}{stem}_scan{idx}.pdf", pages)
            for idx, pages in enumerate(segments, start=1)
        ]
        return write_pdf_output_jobs(
            pdf_path,
            output_dir=output_dir,
            jobs=jobs,
            cancel_check=cancel_check,
            on_output=on_output,
            cleanup_outputs_on_cancel=False,
        )

    @staticmethod
    def execute(
        pdf_path: str,
        reference_image_path: str,
        *,
        output_dir: str = "",
        prefix: str = "",
        options: Optional[PdfScanSplitOptions] = None,
        progress: Optional[ProgressCallback] = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> PdfScanSplitResult:
        # 支持传入 dict 配置
        if isinstance(options, dict):
            options = PdfScanSplitOptions(**options)
        elif options is None:
            options = PdfScanSplitOptions()
        total_started_at = time.perf_counter()
        options = options or PdfScanSplitOptions()
        mode = PdfScanSplitEngine._normalize_detection_mode(options.detection_mode)

        if not output_dir:
            output_dir = os.path.dirname(pdf_path)

        PdfScanSplitEngine._log_execute_scan_start(mode, 0, log)
        if log:
            log(PdfScanSplitEngine._format_scan_options_log(options))

        perf = _PerfStats()
        scan_started_at = time.perf_counter()
        markers, total_pages = PdfScanSplitEngine._scan_markers(
            pdf_path,
            reference_image_path,
            options,
            perf=perf,
            progress=progress,
            log=log,
            cancel_check=cancel_check,
        )
        scan_elapsed_s = time.perf_counter() - scan_started_at
        perf.scan_seconds = scan_elapsed_s

        if PdfScanSplitEngine._is_cancelled(cancel_check):
            if log:
                log(f"任务已取消：未开始写入，识别阶段耗时 {PdfScanSplitEngine._fmt_seconds(scan_elapsed_s)}")
            perf.total_seconds = time.perf_counter() - total_started_at
            return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=total_pages, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))

        if log:
            log(f"识别结果：PDF 共 {total_pages} 页，标记页：{', '.join(str(p + 1) for p in markers) if markers else '无'}")
            log(f"识别阶段总耗时：{PdfScanSplitEngine._fmt_seconds(scan_elapsed_s)}")
            log("开始写入拆分结果…")

        build_started_at = time.perf_counter()
        segments = PdfScanSplitEngine.build_segments(total_pages, markers, options)
        build_elapsed_s = time.perf_counter() - build_started_at
        perf.build_seconds = build_elapsed_s
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            if log:
                log(f"任务已取消：未开始写入，识别阶段耗时 {PdfScanSplitEngine._fmt_seconds(scan_elapsed_s)}")
            perf.total_seconds = time.perf_counter() - total_started_at
            return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=total_pages, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))
        if log:
            log(f"分段结果：{len(segments)} 段，用时 {PdfScanSplitEngine._fmt_seconds(build_elapsed_s)}")
        suspect_segments = PdfScanSplitEngine.analyze_suspect_segments(segments, int(options.max_segment_pages or 0))
        PdfScanSplitEngine.log_suspect_segments(suspect_segments, log)

        write_started_at = time.perf_counter()
        outputs = PdfScanSplitEngine.write_segments(
            pdf_path,
            segments,
            output_dir=output_dir,
            prefix=prefix or "",
            log=log,
            cancel_check=cancel_check,
        )
        write_elapsed_s = time.perf_counter() - write_started_at
        perf.write_seconds = write_elapsed_s
        cancelled = PdfScanSplitEngine._is_cancelled(cancel_check)
        total_elapsed_s = time.perf_counter() - total_started_at
        perf.total_seconds = total_elapsed_s
        PdfScanSplitEngine._log_execute_summary(
            outputs=outputs,
            total_elapsed_s=total_elapsed_s,
            scan_elapsed_s=scan_elapsed_s,
            write_elapsed_s=write_elapsed_s,
            cancelled=cancelled,
            log=log,
        )

        return PdfScanSplitResult(output_files=outputs, marker_pages=markers, total_pages=total_pages, suspect_segments=suspect_segments, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))
