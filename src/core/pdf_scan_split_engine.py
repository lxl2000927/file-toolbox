from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Literal
from urllib.parse import unquote
import unicodedata

import PyPDF2
import fitz

from src.utils.pdf_output import PdfOutputJob, write_pdf_output_jobs


ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


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
    qrcode_max_attempts: int = 180
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
        object.__setattr__(self, "qrcode_max_attempts", max(12, min(500, int(self.qrcode_max_attempts or 180))))
        object.__setattr__(self, "max_segment_pages", max(0, min(10000, int(self.max_segment_pages or 0))))

        # 布尔参数类型保护（防御前端传入字符串或数字的情况）
        _gpu = getattr(self, "enable_gpu", False)
        _mt = getattr(self, "enable_multithread", False)
        _roi = getattr(self, "use_roi", False)
        _qr_roi = getattr(self, "qrcode_use_roi", False)
        _use_roi = (bool(_roi) if isinstance(_roi, bool) else False) or (bool(_qr_roi) if isinstance(_qr_roi, bool) else False)
        object.__setattr__(self, "enable_gpu", bool(_gpu) if isinstance(_gpu, bool) else False)
        object.__setattr__(self, "enable_multithread", bool(_mt) if isinstance(_mt, bool) else False)
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
    markers_found: int = 0
    scan_seconds: float = 0.0
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


class PdfScanSplitEngine:
    _zxingcpp = None
    _zxingcpp_checked = False
    _zxingcpp_import_lock = threading.Lock()

    @staticmethod
    def _configure_acceleration(options: PdfScanSplitOptions, *, cv2=None, log: Optional[LogCallback] = None):
        # 注意：cv2.setNumThreads / cv2.ocl.setUseOpenCL 是进程全局设置。
        # 当前 engine 为单请求串行模型，不存在并发覆盖问题。
        # 若未来引入多请求并发，需为此函数加锁或改为每次请求独立配置。
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

        if bool(getattr(options, "enable_gpu", False)):
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
            "markers_found": int(stats.markers_found),
            "scan_seconds": float(stats.scan_seconds),
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
        return int(options.qrcode_max_attempts or 180)

    @staticmethod
    def _qrcode_fallback_dpis(current_dpi: int) -> list[int]:
        current = int(current_dpi or 180)
        if current < 200:
            candidates = [200, 220]
        elif current == 200:
            candidates = [180, 220]
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
        PdfScanSplitEngine._configure_acceleration(options, cv2=cv2, log=log)
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
    def _qr_decode_failure_hint(strong: bool = False) -> str:
        if strong:
            return "多页疑似二维码均未能解析内容。后续页已自动降级为仅检测码是否存在；该码图可能无法被通用二维码解码器识别，或需要特定扫码环境解析。"
        return "已连续 3 页检测到疑似二维码，但本地解码器未解析到内容。若只需识别标记页，可勾选“不解码内容”。"

    @staticmethod
    def _probe_qr_decode_failure_hint() -> str:
        return "检测到疑似二维码，但未能解析内容。内容匹配仅适用于可被通用二维码解码器识别的码图；如只需判断标记页，请勾选“不解码内容”。"

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
    def _get_pdf_page_count(pdf_path: str) -> int:
        doc = fitz.open(pdf_path)
        try:
            return PdfScanSplitEngine._load_ready_pdf(doc)
        finally:
            doc.close()

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
            log("开始识别标记页（自动模式：二维码/印章/特征匹配）…")

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
    def _log_execute_summary(
        *,
        outputs: list[str],
        total_elapsed_s: float,
        scan_elapsed_s: float,
        write_elapsed_s: float,
        log: Optional[LogCallback],
    ) -> None:
        if not log:
            return
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
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.22)
            page_bgr_qr = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        if use_stamp and roi_base_size and options.reference_roi and options.use_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=roi_base_size, dst_size=dst_size)
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.22)
            page_bgr_stamp = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        return page_bgr_qr, page_bgr_stamp, page_bgr_feature

    @staticmethod
    def _should_use_roi_clip(options: PdfScanSplitOptions, roi_base_size: tuple[int, int] | None) -> bool:
        return bool(options.use_roi and options.reference_roi and roi_base_size)

    @staticmethod
    def _prepare_page_images_from_roi_clip(page_bgr_roi, *, use_qr: bool, use_stamp: bool, use_feature: bool):
        """Return per-detector input images from the ROI crop.
        Callers must ensure page_bgr_roi is not None before calling.
        """
        if page_bgr_roi is None:
            raise ValueError(
                "_prepare_page_images_from_roi_clip: page_bgr_roi must not be None"
            )
        return (
            page_bgr_roi if use_qr else None,
            page_bgr_roi if use_stamp else None,
            page_bgr_roi if use_feature else None,
        )
    @staticmethod
    def _new_probe_result(page_index: int, total: int, mode: str, options: PdfScanSplitOptions) -> dict:
        return {
            "page_index": int(page_index),
            "page_number": int(page_index) + 1,
            "total_pages": int(total),
            "detection_mode": mode,
            "marked": False,
            "reason": "",
            "qrcode": {"present": False, "infos": []},
            "stamp": {"present": False},
            "feature": {"good_matches": 0, "inliers": 0, "inlier_ratio": 0.0},
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
    def _detect_qr_for_probe(
        result: dict,
        page_bgr_qr,
        options: PdfScanSplitOptions,
        *,
        detector=None,
        cv2=None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> None:
        needle = (options.qrcode_text_contains or "").strip()
        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        present = PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2)
        if not present:
            present = PdfScanSplitEngine._qr_detect_likely(page_bgr_qr, detector=detector, cv2=cv2)
        result["qrcode"]["present"] = bool(present)
        if not present:
            return
        try:
            area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_qr, detector=detector, cv2=cv2)
            result["qrcode"]["stats"] = {"area": float(area), "aspect": float(aspect), "solidity": float(solidity)}
        except Exception as exc:
            result["qrcode"]["stats"] = None
            if log:
                log(f"[警告] 二维码统计收集失败: {exc}")

        if options.qrcode_no_decode:
            result["marked"] = True
            result["reason"] = "二维码存在（未解码）"
            return

        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        infos = PdfScanSplitEngine._detect_qrcodes(
            page_bgr_qr,
            detector=detector,
            cv2=cv2,
            max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
            cancel_check=cancel_check,
        )
        result["qrcode"]["infos"] = list(infos or [])
        if infos:
            matched_infos = PdfScanSplitEngine._match_texts(infos, needle)
            if matched_infos:
                result["marked"] = True
                result["reason"] = "二维码解码命中"
            else:
                result["marked"] = False
                result["reason"] = "二维码解码到内容，但未命中关键字"
            return

        if present and not needle:
            result["marked"] = False
            result["reason"] = "检测到二维码但未解码（未勾选“二维码存在（未解码）”，未视为标记页）"
        elif present:
            result["marked"] = False
            result["reason"] = "检测到二维码但未能解析内容"

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
        status: Optional[dict] = None,
        decode_content: bool = True,
        perf: Optional[_PerfStats] = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> bool:
        needle = (options.qrcode_text_contains or "").strip()
        fallback_dpis = PdfScanSplitEngine._qrcode_fallback_dpis(int(options.dpi))
        if status is not None:
            status.clear()

        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        present = PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2)
        if not present:
            present = PdfScanSplitEngine._qr_detect_likely(page_bgr_qr, detector=detector, cv2=cv2)
        if not present:
            if options.qrcode_no_decode or not decode_content:
                if status is not None:
                    status.update({"present": False, "decoded": False, "decode_failed": False})
                return False
            enhance_initial_miss = options.detection_mode == "qrcode" or bool(options.use_roi and options.reference_roi)
            if not enhance_initial_miss:
                if status is not None:
                    status.update({"present": False, "decoded": False, "decode_failed": False})
                return False
            infos_light = PdfScanSplitEngine._detect_qrcodes(
                page_bgr_qr,
                detector=detector,
                cv2=cv2,
                max_robust_attempts=24,
                cancel_check=cancel_check,
            )
            if infos_light:
                if status is not None:
                    status.update({"present": True, "decoded": True, "decode_failed": False})
                matched_infos = PdfScanSplitEngine._match_texts(infos_light, needle)
                if matched_infos:
                    if log:
                        sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                        log(f"第 {page_index + 1} 页：二维码初筛未通过，增强解码命中 {sample}")
                    return True
                if log and needle:
                    sample = PdfScanSplitEngine._sample_text(infos_light[0])
                    log(f"第 {page_index + 1} 页：二维码初筛未通过，但增强解码内容不包含关键字“{needle}” {sample}")
                return False
            if status is not None:
                status.update({"present": False, "decoded": False, "decode_failed": False})
            return False
        if status is not None:
            status.update({"present": True, "decoded": False, "decode_failed": False})

        if options.qrcode_no_decode or not decode_content:
            if needle and log:
                log(f"第 {page_index + 1} 页：检测到二维码（未解码），已忽略“二维码内容包含”筛选")
            if log:
                log(f"第 {page_index + 1} 页：检测到二维码（未解码）")
            return True

        infos = PdfScanSplitEngine._detect_qrcodes(
            page_bgr_qr,
            detector=detector,
            cv2=cv2,
            max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
            cancel_check=cancel_check,
        )
        if infos:
            if status is not None:
                status.update({"present": True, "decoded": True, "decode_failed": False})
            matched_infos = PdfScanSplitEngine._match_texts(infos, needle)
            if matched_infos:
                if log:
                    sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                    log(f"第 {page_index + 1} 页：识别到二维码（{len(matched_infos)} 个） {sample}")
                return True
            if log and needle:
                sample = PdfScanSplitEngine._sample_text(infos[0])
                log(f"第 {page_index + 1} 页：识别到二维码，但不包含关键字“{needle}” {sample}")
            return False

        if not infos and status is not None:
            status.update({"present": True, "decoded": False, "decode_failed": True})

        for retry_dpi in fallback_dpis:
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
            infos_retry = PdfScanSplitEngine._detect_qrcodes(
                page_bgr_retry,
                detector=detector,
                cv2=cv2,
                max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
                cancel_check=cancel_check,
            )
            if infos_retry:
                if status is not None:
                    status.update({"present": True, "decoded": True, "decode_failed": False})
                matched_infos = PdfScanSplitEngine._match_texts(infos_retry, needle)
                if matched_infos:
                    if perf is not None:
                        perf.dpi_fallback_hits += 1
                    if log:
                        sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                        log(f"第 {page_index + 1} 页：二维码 {retry_dpi} DPI 兜底重试命中（{len(matched_infos)} 个） {sample}")
                    return True
                if log and needle:
                    sample = PdfScanSplitEngine._sample_text(infos_retry[0])
                    log(f"第 {page_index + 1} 页：二维码 {retry_dpi} DPI 兜底重试识别到，但不包含关键字“{needle}” {sample}")
            else:
                present_after_retry = PdfScanSplitEngine._qr_detect_confident(page_bgr_retry, detector=detector, cv2=cv2)
                if present_after_retry and status is not None:
                    status.update({"present": True, "decoded": False, "decode_failed": True})
        if not needle and log:
            area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_qr, detector=detector, cv2=cv2)
            log(
                f"第 {page_index + 1} 页：检测到二维码但未能解码（已尝试增强解码和 DPI 兜底；未勾选“二维码存在（未解码）”，未视为标记页）"
                f"  面积 {int(area)}  形状 {aspect:.2f}  填充 {solidity:.2f}"
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
    def _detect_stamp_for_scan(page_index: int, page_bgr_stamp, *, cv2=None, log: Optional[LogCallback] = None) -> tuple[bool, dict]:
        stamp = PdfScanSplitEngine._detect_red_stamp(page_bgr_stamp, cv2=cv2)
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
        result["stamp"] = dict(stamp or {})
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
    def _detect_qrcodes(
        img_bgr,
        *,
        detector=None,
        cv2=None,
        max_robust_attempts: int | None = None,
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
        max_robust_attempts = max(24, int(max_robust_attempts or 180))

        def _cancelled() -> bool:
            return PdfScanSplitEngine._is_cancelled(cancel_check)

        def _min_area_threshold(gray_img) -> float:
            h, w = gray_img.shape[:2]
            return max(35.0, 0.00006 * float(h * w))

        def _filter_decoded(decoded: list[str], pts, area_threshold: float) -> list[str]:
            infos: list[str] = []
            if not decoded:
                return infos
            if pts is None:
                for s in decoded:
                    s = str(s or "").strip()
                    if len(s) >= 1:
                        infos.append(s)
                return infos
            try:
                pts_arr = pts
                if hasattr(pts_arr, "shape") and len(getattr(pts_arr, "shape", ())) >= 3:
                    for i, s in enumerate(decoded):
                        s = str(s or "").strip()
                        if len(s) < 1:
                            continue
                        try:
                            poly = pts_arr[i]
                            area = float(cv2.contourArea(poly.astype("float32")))
                        except Exception:
                            area = 0.0
                        if area >= area_threshold:
                            infos.append(s)
                    return infos
            except Exception:
                pass
            for s in decoded:
                s = str(s or "").strip()
                if len(s) >= 1:
                    infos.append(s)
            return infos

        def _decode_zxing(gray_img) -> list[str]:
            zxingcpp = PdfScanSplitEngine._load_zxingcpp()
            if zxingcpp is None:
                return []
            try:
                results = zxingcpp.read_barcodes(gray_img)
            except Exception:
                return []
            infos: list[str] = []
            try:
                items = list(results or [])
            except Exception:
                items = []
            for item in items:
                try:
                    fmt = str(getattr(item, "format", "") or "").lower()
                    if fmt and "qr" not in fmt:
                        continue
                    text = str(getattr(item, "text", "") or "").strip()
                    if text:
                        infos.append(text)
                except Exception:
                    continue
            return infos

        def _decode_from_detect(gray_img, *, area_threshold: float) -> list[str]:
            polys: list = []
            try:
                if hasattr(detector, "detectMulti"):
                    ok, pts = detector.detectMulti(gray_img)
                    ok_flag = False
                    try:
                        ok_flag = bool(ok)
                    except Exception:
                        try:
                            ok_flag = bool(ok.any()) if hasattr(ok, "any") else False
                        except Exception:
                            ok_flag = False
                    if ok_flag and pts is not None:
                        polys = list(pts)
            except Exception:
                polys = []
            if not polys:
                try:
                    detected, pts = detector.detect(gray_img)
                    detected_flag = False
                    try:
                        detected_flag = bool(detected)
                    except Exception:
                        try:
                            detected_flag = bool(detected.any()) if hasattr(detected, "any") else False
                        except Exception:
                            detected_flag = False
                    if detected_flag and pts is not None:
                        polys = [pts]
                except Exception:
                    polys = []

            def _normalize_poly(poly):
                try:
                    p = poly
                    if hasattr(p, "shape") and tuple(getattr(p, "shape", ())) == (1, 4, 2):
                        p = p[0]
                    if hasattr(p, "shape") and tuple(getattr(p, "shape", ())) == (4, 2):
                        return p.astype("float32")
                except Exception:
                    return None
                return None

            def _order_quad(quad):
                s = quad.sum(axis=1)
                diff = (quad[:, 0] - quad[:, 1])
                tl = quad[s.argmin()]
                br = quad[s.argmax()]
                tr = quad[diff.argmin()]
                bl = quad[diff.argmax()]
                try:
                    return np.array([tl, tr, br, bl], dtype="float32")
                except Exception:
                    return None

            for poly in polys:
                quad = _normalize_poly(poly)
                if quad is None:
                    continue
                try:
                    area = float(cv2.contourArea(quad))
                except Exception:
                    area = 0.0
                if area < float(area_threshold):
                    continue
                try:
                    x, y, bw, bh = cv2.boundingRect(quad.astype("float32"))
                    if bw <= 0 or bh <= 0:
                        continue
                    ratio = float(max(bw, bh)) / float(min(bw, bh))
                    if ratio > 2.2:
                        continue
                except Exception:
                    pass

                try:
                    ordered = _order_quad(quad)
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
                    size = int(max(320, min(960, side * 2.2)))
                    dst = np.array(
                        [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]],
                        dtype="float32",
                    )
                    M = cv2.getPerspectiveTransform(ordered, dst)
                    warped = cv2.warpPerspective(gray_img, M, (size, size), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
                except Exception:
                    continue

                infos = _decode_variants(warped, allow_warp=False)
                if infos:
                    return infos
                infos = _try_threshold_variants(warped, allow_warp=False)
                if infos:
                    return infos
            return []

        def _decode_variants(gray_img, *, allow_warp: bool = True):
            area_threshold = _min_area_threshold(gray_img)
            infos: list[str] = []
            try:
                if hasattr(detector, "detectAndDecodeCurved"):
                    data, pts, _ = detector.detectAndDecodeCurved(gray_img)
                    if data:
                        infos = _filter_decoded([data], pts, area_threshold)
                        if infos:
                            return infos
            except Exception:
                pass
            try:
                data, pts, _ = detector.detectAndDecode(gray_img)
                if data:
                    infos = _filter_decoded([data], pts, area_threshold)
                    if infos:
                        return infos
            except Exception:
                pass
            try:
                if hasattr(detector, "detectAndDecodeMulti"):
                    ok, decoded_info, pts, _ = detector.detectAndDecodeMulti(gray_img)
                    has_items = False
                    if decoded_info is not None:
                        try:
                            has_items = len(decoded_info) > 0
                        except Exception:
                            try:
                                has_items = bool(decoded_info is not None)
                            except Exception:
                                has_items = False
                    ok_flag = False
                    try:
                        ok_flag = bool(ok)
                    except Exception:
                        try:
                            ok_flag = bool(ok.any()) if hasattr(ok, "any") else False
                        except Exception:
                            ok_flag = False
                    if ok_flag and has_items:
                        try:
                            decoded_list = list(decoded_info)
                        except Exception:
                            decoded_list = [str(decoded_info)]
                        infos = _filter_decoded(decoded_list, pts, area_threshold)
                        if infos:
                            return infos
            except Exception:
                pass
            if allow_warp:
                infos = _decode_from_detect(gray_img, area_threshold=area_threshold)
                if infos:
                    return infos
            infos = _decode_zxing(gray_img)
            if infos:
                return infos
            return []

        def _try_threshold_variants(gray_img, *, allow_warp: bool = True) -> list[str]:
            try:
                _, th = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                infos = _decode_variants(th, allow_warp=allow_warp)
                if infos:
                    return infos
                infos = _decode_variants(cv2.bitwise_not(th), allow_warp=allow_warp)
                if infos:
                    return infos
            except Exception:
                pass
            try:
                h, w = gray_img.shape[:2]
                min_dim = min(h, w)
                block = 31 if min_dim >= 31 else (max(3, (min_dim // 2) * 2 - 1))
                block = int(block)
                if block >= 3:
                    for c in (3, 5, 7):
                        th2 = cv2.adaptiveThreshold(
                            gray_img,
                            255,
                            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                            cv2.THRESH_BINARY,
                            block,
                            int(c),
                        )
                        infos = _decode_variants(th2, allow_warp=allow_warp)
                        if infos:
                            return infos
                        infos = _decode_variants(cv2.bitwise_not(th2), allow_warp=allow_warp)
                        if infos:
                            return infos
            except Exception:
                pass
            return []

        def _rotate_variants(gray_img):
            yield gray_img
            try:
                yield cv2.rotate(gray_img, cv2.ROTATE_90_CLOCKWISE)
                yield cv2.rotate(gray_img, cv2.ROTATE_180)
                yield cv2.rotate(gray_img, cv2.ROTATE_90_COUNTERCLOCKWISE)
            except Exception:
                return

        def _scale_variants(gray_img):
            yield gray_img
            try:
                max_dim = max(gray_img.shape[:2])
                if max_dim >= 2200:
                    down = cv2.resize(gray_img, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
                    yield down
                if max_dim < 1800:
                    up = cv2.resize(gray_img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
                    yield up
                if max_dim < 1400:
                    up2 = cv2.resize(gray_img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                    yield up2
            except Exception:
                return

        def _preprocess_variants(gray_img):
            yield gray_img
            try:
                norm = cv2.normalize(gray_img, None, 0, 255, cv2.NORM_MINMAX)
                yield norm
            except Exception:
                pass
            try:
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                yield clahe.apply(gray_img)
            except Exception:
                pass
            try:
                med = cv2.medianBlur(gray_img, 3)
                yield med
            except Exception:
                pass
            try:
                blur = cv2.GaussianBlur(gray_img, (0, 0), 1.2)
                sharp = cv2.addWeighted(gray_img, 1.55, blur, -0.55, 0)
                yield sharp
            except Exception:
                pass

        def _try_decode_robust(gray_img) -> list[str]:
            attempts = 0

            def _can_try() -> bool:
                return attempts < max_robust_attempts and not _cancelled()

            def _counted_decode(fn):
                nonlocal attempts
                if not _can_try():
                    return []
                attempts += 1
                return fn()

            for pre in _preprocess_variants(gray_img):
                if _cancelled():
                    return []
                infos = _counted_decode(lambda: _decode_variants(pre, allow_warp=True))
                if infos:
                    return infos
                if not _can_try():
                    return []
                for rot in _rotate_variants(pre):
                    if _cancelled():
                        return []
                    infos = _counted_decode(lambda: _decode_variants(rot, allow_warp=True))
                    if infos:
                        return infos
                    infos = _counted_decode(lambda: _try_threshold_variants(rot, allow_warp=True))
                    if infos:
                        return infos
                    if not _can_try():
                        return []
                    for scaled in _scale_variants(rot):
                        if _cancelled():
                            return []
                        infos = _counted_decode(lambda: _decode_variants(scaled, allow_warp=True))
                        if infos:
                            return infos
                        infos = _counted_decode(lambda: _try_threshold_variants(scaled, allow_warp=True))
                        if infos:
                            return infos
                        if not _can_try():
                            return []
            return []

        gray_full = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray_full.shape[:2]
        if h <= 2 or w <= 2:
            return []

        top_right_x = int(w * 0.55)
        top_right_h = int(h * 0.55)
        bottom_y = int(h * 0.6)
        bottom_right_y = int(h * 0.45)
        rois = [
            (top_right_x, 0, w - top_right_x, max(1, top_right_h)),
            (0, 0, w, max(1, int(h * 0.35))),
            (0, bottom_y, w, max(1, h - bottom_y)),
            (top_right_x, bottom_right_y, w - top_right_x, max(1, h - bottom_right_y)),
            (0, 0, w, h),
        ]

        for x, y, rw, rh in rois:
            if _cancelled():
                return []
            roi = gray_full[y : y + rh, x : x + rw]
            if roi.size <= 0:
                continue
            infos = _decode_variants(roi, allow_warp=True)
            if infos:
                return infos

        for x, y, rw, rh in rois:
            if _cancelled():
                return []
            roi = gray_full[y : y + rh, x : x + rw]
            if roi.size <= 0:
                continue
            infos = _try_decode_robust(roi)
            if infos:
                return infos

        return []

    @staticmethod
    def _detect_red_stamp(img_bgr, *, cv2=None) -> dict:
        if cv2 is None:
            np, cv2 = PdfScanSplitEngine._require_deps()
        else:
            try:
                import numpy as np  # type: ignore
            except Exception:
                np, _ = PdfScanSplitEngine._require_deps()

        h, w = img_bgr.shape[:2]
        if h <= 2 or w <= 2:
            return {"present": False}

        try:
            hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
            mask1 = cv2.inRange(hsv, (0, 45, 35), (10, 255, 255))
            mask2 = cv2.inRange(hsv, (160, 45, 35), (180, 255, 255))
            mask = cv2.bitwise_or(mask1, mask2)
        except Exception:
            mask = None

        try:
            lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
            a = lab[:, :, 1]
            _, mask_a = cv2.threshold(a, 155, 255, cv2.THRESH_BINARY)
            mask = mask_a if mask is None else cv2.bitwise_or(mask, mask_a)
        except Exception:
            pass

        if mask is None:
            return {"present": False}

        try:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        except Exception:
            pass

        try:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception:
            return {"present": False}

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
            return {"present": False, "candidates": int(candidates)}

        present = False
        if best["area_ratio"] >= 0.0012 and best["circularity"] >= 0.10 and best["solidity"] >= 0.20 and best["aspect"] <= 2.2:
            present = True
        elif best["area_ratio"] >= 0.0025 and best["solidity"] >= 0.18 and best["aspect"] <= 2.2:
            present = True

        out = {"present": bool(present), "candidates": int(candidates), "score": float(best_score)}
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
            markers: list[int] = []
            started_at = time.perf_counter()
            processed = 0
            i = 0
            qr_decode_fail_streak = 0
            qr_decode_hint_logged = False
            qr_decode_strong_hint_logged = False
            qr_decode_content_enabled = not bool(options.qrcode_no_decode)
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
                stamp_debug = None
                qr_status: dict = {}
                if use_qr:
                    qr_started_at = time.perf_counter()
                    marked = PdfScanSplitEngine._detect_qr_for_scan(
                        doc,
                        i,
                        page_bgr_qr,
                        options,
                        detector=detector,
                        cv2=cv2,
                        roi_base_size=roi_base_size,
                        status=qr_status,
                        decode_content=qr_decode_content_enabled,
                        perf=perf,
                        log=log,
                        cancel_check=cancel_check,
                    )
                    if perf is not None:
                        perf.qr_seconds += time.perf_counter() - qr_started_at
                    PdfScanSplitEngine._raise_if_cancelled(cancel_check)
                    if qr_status.get("decode_failed"):
                        qr_decode_fail_streak += 1
                        if log and qr_decode_fail_streak >= 3 and not qr_decode_hint_logged:
                            log(PdfScanSplitEngine._qr_decode_failure_hint(False))
                            qr_decode_hint_logged = True
                        if log and qr_decode_fail_streak >= 5 and not qr_decode_strong_hint_logged:
                            if options.qrcode_text_contains:
                                log("多页疑似二维码均未能解析内容；已保留关键字匹配要求，避免将任意二维码误判为标记页。")
                            else:
                                log(PdfScanSplitEngine._qr_decode_failure_hint(True))
                                qr_decode_content_enabled = False
                            qr_decode_strong_hint_logged = True
                    else:
                        qr_decode_fail_streak = 0

                if not marked and use_stamp:
                    stamp_started_at = time.perf_counter()
                    marked, stamp_debug = PdfScanSplitEngine._detect_stamp_for_scan(i, page_bgr_stamp, cv2=cv2, log=log)
                    if perf is not None:
                        perf.stamp_seconds += time.perf_counter() - stamp_started_at

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
                    markers.append(i)
                else:
                    PdfScanSplitEngine._log_scan_miss(i, total, mode, stamp_debug, log)
                if progress:
                    progress(i + 1, total)
                if marked and int(options.qrcode_skip_pages or 0) > 0:
                    i += int(options.qrcode_skip_pages or 0) + 1
                else:
                    i += 1

            elapsed_s = time.perf_counter() - started_at
            if perf is not None:
                perf.scan_seconds = elapsed_s
                perf.pages_scanned = processed
                perf.markers_found = len(markers)
            if log:
                avg_ms = int(round((elapsed_s * 1000.0) / float(max(1, processed))))
                log(
                    f"标记页扫描完成：处理 {processed}/{total} 页，命中 {len(markers)} 页，耗时 {PdfScanSplitEngine._fmt_seconds(elapsed_s)}，平均 {avg_ms}ms/页"
                )
            return markers, total
        finally:
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
        build_started_at = time.perf_counter()
        segments = PdfScanSplitEngine.build_segments(int(total), markers, options)
        perf.build_seconds = time.perf_counter() - build_started_at
        suspect_segments = PdfScanSplitEngine.analyze_suspect_segments(segments, int(options.max_segment_pages or 0))
        PdfScanSplitEngine.log_suspect_segments(suspect_segments, log)
        perf.total_seconds = perf.scan_seconds + perf.build_seconds
        return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=int(total), suspect_segments=suspect_segments, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))

    @staticmethod
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
        finally:
            doc.close()
        PdfScanSplitEngine._raise_if_cancelled(cancel_check)

        if page_bgr_roi is not None:
            page_bgr_qr, page_bgr_stamp, page_bgr_feature = PdfScanSplitEngine._prepare_page_images_from_roi_clip(
                page_bgr_roi,
                use_qr=use_qr,
                use_stamp=use_stamp,
                use_feature=use_feature,
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

        if (not result["marked"]) and use_qr:
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
            qr_started_at = time.perf_counter()
            PdfScanSplitEngine._detect_qr_for_probe(result, page_bgr_qr, options, detector=detector, cv2=cv2, cancel_check=cancel_check)
            perf.qr_seconds += time.perf_counter() - qr_started_at

        if (not result["marked"]) and use_stamp:
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
            stamp_started_at = time.perf_counter()
            PdfScanSplitEngine._detect_stamp_for_probe(result, page_bgr_stamp, cv2=cv2)
            perf.stamp_seconds += time.perf_counter() - stamp_started_at

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

        if not result["reason"]:
            result["reason"] = "未命中"
        perf.pages_scanned = 1
        perf.markers_found = 1 if result.get("marked") else 0
        perf.scan_seconds = time.perf_counter() - total_started_at
        perf.total_seconds = perf.scan_seconds
        result["performance_stats"] = PdfScanSplitEngine._perf_to_dict(perf)
        return result

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
            perf.total_seconds = time.perf_counter() - total_started_at
            return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=total_pages, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))

        if log:
            log(f"PDF页数：{total_pages}")
            log(f"识别到标记页：{', '.join(str(p + 1) for p in markers) if markers else '无'}")
            log(f"标记页识别耗时：{PdfScanSplitEngine._fmt_seconds(scan_elapsed_s)}")
            log("开始写入拆分结果…")

        build_started_at = time.perf_counter()
        segments = PdfScanSplitEngine.build_segments(total_pages, markers, options)
        build_elapsed_s = time.perf_counter() - build_started_at
        perf.build_seconds = build_elapsed_s
        if PdfScanSplitEngine._is_cancelled(cancel_check):
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
        if PdfScanSplitEngine._is_cancelled(cancel_check) and outputs and log:
            log(f"任务已取消，已保留 {len(outputs)} 个已生成文件")
        total_elapsed_s = time.perf_counter() - total_started_at
        perf.total_seconds = total_elapsed_s
        PdfScanSplitEngine._log_execute_summary(
            outputs=outputs,
            total_elapsed_s=total_elapsed_s,
            scan_elapsed_s=scan_elapsed_s,
            write_elapsed_s=write_elapsed_s,
            log=log,
        )

        return PdfScanSplitResult(output_files=outputs, marker_pages=markers, total_pages=total_pages, suspect_segments=suspect_segments, performance_stats=PdfScanSplitEngine._perf_to_dict(perf))
