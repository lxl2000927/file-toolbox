from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional, Literal

import PyPDF2
from PyQt6.QtCore import QSize, QEventLoop, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

from utils.path_utils import make_unique_output_path, make_unique_temp_path


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
    qrcode_use_roi: bool = False
    qrcode_max_attempts: int = 180
    max_segment_pages: int = 0
    enable_multithread: bool = False
    enable_gpu: bool = False


@dataclass(frozen=True)
class PdfScanSplitResult:
    output_files: list[str]
    marker_pages: list[int]
    total_pages: int
    suspect_segments: list[dict] | None = None


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
                    log(f"多线程优化：OpenCV 线程数 = {int(cv2.getNumThreads())}")
            except Exception:
                if log:
                    log("多线程优化：启用失败（OpenCV 未支持或受限）")

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
                            enabled = True
                            detail = f"CUDA设备数={n}"
                except Exception:
                    pass
            if log:
                if enabled:
                    log(f"GPU加速：已启用（{detail}）")
                else:
                    log("GPU加速：当前环境不可用，已回退到CPU")

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
    def _normalize_detection_mode(mode: str) -> str:
        mode = (mode or "qrcode").lower()
        return mode if mode in ("feature", "qrcode", "stamp", "auto") else "qrcode"

    @staticmethod
    def _qrcode_max_attempts(options: PdfScanSplitOptions) -> int:
        return int(options.qrcode_max_attempts or 180)

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
            except Exception:
                if mode == "feature":
                    raise
                ref_img_full = None
                ref_size = None
                roi_base_size = None

        if use_feature and ref_img_full is not None:
            ref_img = PdfScanSplitEngine._apply_roi(ref_img_full, options.reference_roi)
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
    def _match_texts(infos: list[str], needle: str) -> list[str]:
        if not needle:
            return list(infos or [])
        return [s for s in infos if needle in s]

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
    def _load_ready_pdf(doc: QPdfDocument, pdf_path: str) -> int:
        status = PdfScanSplitEngine._load_pdf_wait(doc, pdf_path)
        if status != QPdfDocument.Status.Ready:
            raise RuntimeError(f"PDF加载失败（状态：{status.name}）")
        total = int(doc.pageCount() or 0)
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
        doc: QPdfDocument,
        page_index: int,
        dpi: int,
        *,
        render_options: Optional[QPdfDocumentRenderOptions] = None,
        cancel_check: Optional[CancelCheck] = None,
    ):
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return None
        page_bgr = PdfScanSplitEngine._render_page_bgr(doc, page_index, dpi, render_options=render_options)
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return None
        return page_bgr

    @staticmethod
    def _get_pdf_page_count(pdf_path: str) -> int:
        doc = QPdfDocument(None)
        try:
            return PdfScanSplitEngine._load_ready_pdf(doc, pdf_path)
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
        if use_feature and ref_size and options.reference_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=ref_size, dst_size=dst_size)
            page_bgr_feature = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        if use_qr and roi_base_size and options.reference_roi and options.qrcode_use_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=roi_base_size, dst_size=dst_size)
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.22)
            page_bgr_qr = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        if use_stamp and roi_base_size and options.reference_roi and options.qrcode_use_roi:
            dst_size = (int(page_bgr_full.shape[1]), int(page_bgr_full.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=roi_base_size, dst_size=dst_size)
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.22)
            page_bgr_stamp = PdfScanSplitEngine._apply_roi(page_bgr_full, page_roi)
        return page_bgr_qr, page_bgr_stamp, page_bgr_feature

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
        cancel_check: Optional[CancelCheck] = None,
    ) -> None:
        needle = (options.qrcode_text_contains or "").strip()
        if options.qrcode_no_decode:
            present = PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2)
            result["qrcode"]["present"] = bool(present)
            try:
                area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_qr, detector=detector, cv2=cv2)
                result["qrcode"]["stats"] = {"area": float(area), "aspect": float(aspect), "solidity": float(solidity)}
            except Exception:
                pass
            if present:
                result["marked"] = True
                result["reason"] = "二维码存在（未解码）"
            return

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

        present = PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2)
        result["qrcode"]["present"] = bool(present)
        try:
            area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_qr, detector=detector, cv2=cv2)
            result["qrcode"]["stats"] = {"area": float(area), "aspect": float(aspect), "solidity": float(solidity)}
        except Exception:
            pass
        if present and not needle:
            result["marked"] = False
            result["reason"] = "检测到二维码但未解码（未勾选“二维码存在（未解码）”，未视为标记页）"

    @staticmethod
    def _detect_qr_for_scan(
        doc: QPdfDocument,
        page_index: int,
        page_bgr_qr,
        options: PdfScanSplitOptions,
        *,
        detector=None,
        cv2=None,
        render_options: Optional[QPdfDocumentRenderOptions] = None,
        roi_base_size: tuple[int, int] | None = None,
        log: Optional[LogCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> bool:
        needle = (options.qrcode_text_contains or "").strip()
        if options.qrcode_no_decode:
            if PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2):
                if needle and log:
                    log(f"第 {page_index + 1} 页：检测到二维码（未解码），已忽略“二维码内容包含”筛选")
                if log:
                    log(f"第 {page_index + 1} 页：检测到二维码（未解码）")
                return True
            return False

        infos = PdfScanSplitEngine._detect_qrcodes(
            page_bgr_qr,
            detector=detector,
            cv2=cv2,
            max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
            cancel_check=cancel_check,
        )
        if infos:
            matched_infos = PdfScanSplitEngine._match_texts(infos, needle)
            if matched_infos:
                if log:
                    sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                    log(f"第 {page_index + 1} 页：识别到二维码（{len(matched_infos)} 个） {sample}")
                return True
            if log and needle:
                sample = PdfScanSplitEngine._sample_text(infos[0])
                log(f"第 {page_index + 1} 页：识别到二维码，但不包含关键字“{needle}” {sample}")

        if not needle and PdfScanSplitEngine._qr_detect_confident(page_bgr_qr, detector=detector, cv2=cv2):
            if log:
                area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_qr, detector=detector, cv2=cv2)
                log(
                    f"第 {page_index + 1} 页：检测到二维码但未能解码（未勾选“二维码存在（未解码）”，未视为标记页）"
                    f"  面积 {int(area)}  形状 {aspect:.2f}  填充 {solidity:.2f}"
                )
            return False

        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        if not PdfScanSplitEngine._qr_detect_likely(page_bgr_qr, detector=detector, cv2=cv2):
            return False

        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        retry_dpi = min(360, max(240, int(int(options.dpi) * 1.6)))
        if retry_dpi <= int(options.dpi):
            return False
        page_bgr_retry = PdfScanSplitEngine._render_page_checked(
            doc,
            page_index,
            retry_dpi,
            render_options=render_options,
            cancel_check=cancel_check,
        )
        if page_bgr_retry is None:
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
            return False
        if roi_base_size and options.reference_roi and options.qrcode_use_roi:
            dst_size = (int(page_bgr_retry.shape[1]), int(page_bgr_retry.shape[0]))
            page_roi = PdfScanSplitEngine._scale_roi(options.reference_roi, src_size=roi_base_size, dst_size=dst_size)
            page_roi = PdfScanSplitEngine._expand_roi(page_roi, dst_size=dst_size, pad_ratio=0.22)
            page_bgr_retry = PdfScanSplitEngine._apply_roi(page_bgr_retry, page_roi)
        PdfScanSplitEngine._raise_if_cancelled(cancel_check)
        infos_retry = PdfScanSplitEngine._detect_qrcodes(
            page_bgr_retry,
            detector=detector,
            cv2=cv2,
            max_robust_attempts=PdfScanSplitEngine._qrcode_max_attempts(options),
            cancel_check=cancel_check,
        )
        if infos_retry:
            matched_infos = PdfScanSplitEngine._match_texts(infos_retry, needle)
            if matched_infos:
                if log:
                    sample = PdfScanSplitEngine._sample_text(matched_infos[0])
                    log(f"第 {page_index + 1} 页：二维码高分辨率重试命中（{len(matched_infos)} 个） {sample}")
                return True
            if log and needle:
                sample = PdfScanSplitEngine._sample_text(infos_retry[0])
                log(f"第 {page_index + 1} 页：二维码高分辨率重试识别到，但不包含关键字“{needle}” {sample}")
        elif not needle and PdfScanSplitEngine._qr_detect_confident(page_bgr_retry, detector=detector, cv2=cv2):
            if log:
                area, aspect, solidity = PdfScanSplitEngine._qr_detect_stats(page_bgr_retry, detector=detector, cv2=cv2)
                log(
                    f"第 {page_index + 1} 页：二维码高分辨率重试仍未解码（未勾选“二维码存在（未解码）”，未视为标记页）"
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
    def _load_pdf_wait(doc: QPdfDocument, pdf_path: str, *, timeout_ms: int = 8000) -> QPdfDocument.Status:
        doc.load(pdf_path)
        status = doc.status()
        if status not in (QPdfDocument.Status.Loading, QPdfDocument.Status.Null):
            return status

        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        doc.statusChanged.connect(loop.quit)
        timer.start(int(timeout_ms))
        loop.exec()
        return doc.status()

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
        doc = QPdfDocument(None)
        try:
            total = PdfScanSplitEngine._load_ready_pdf(doc, path)
            idx = PdfScanSplitEngine._validate_page_index(page_index, total)
            return PdfScanSplitEngine._render_page_bgr(doc, idx, dpi, render_options=QPdfDocumentRenderOptions())
        finally:
            doc.close()

    @staticmethod
    def _qimage_to_bgr(image: QImage):
        np, cv2 = PdfScanSplitEngine._require_deps()
        img = image.convertToFormat(QImage.Format.Format_RGBA8888)
        w = img.width()
        h = img.height()
        ptr = img.bits()
        ptr.setsize(img.sizeInBytes())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, w, 4)
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

    @staticmethod
    def _render_page_bgr(doc: QPdfDocument, page_index: int, dpi: int, *, render_options: Optional[QPdfDocumentRenderOptions] = None):
        page_size_pt = doc.pagePointSize(page_index)
        scale = float(dpi) / 72.0
        target_size = QSize(max(1, int(page_size_pt.width() * scale)), max(1, int(page_size_pt.height() * scale)))
        qimg = doc.render(page_index, target_size, render_options or QPdfDocumentRenderOptions())
        if qimg.isNull():
            raise RuntimeError("渲染PDF页面失败")
        return PdfScanSplitEngine._qimage_to_bgr(qimg)

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
                    if len(s) >= 3:
                        infos.append(s)
                return infos
            try:
                pts_arr = pts
                if hasattr(pts_arr, "shape") and len(getattr(pts_arr, "shape", ())) >= 3:
                    for i, s in enumerate(decoded):
                        s = str(s or "").strip()
                        if len(s) < 3:
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
                if len(s) >= 3:
                    infos.append(s)
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

        doc = QPdfDocument(None)
        try:
            total = PdfScanSplitEngine._load_ready_pdf(doc, pdf_path)
            if int(page_limit or 0) > 0:
                total = min(total, int(page_limit))
            markers: list[int] = []
            render_options = QPdfDocumentRenderOptions()
            started_at = time.perf_counter()
            processed = 0
            i = 0
            if log and mode == "auto" and ref_des is None:
                log("自动模式：未选择参考文件或参考文件特征不足，已跳过特征匹配")
            while i < total:
                page_bgr_full = PdfScanSplitEngine._render_page_checked(
                    doc,
                    i,
                    options.dpi,
                    render_options=render_options,
                    cancel_check=cancel_check,
                )
                if page_bgr_full is None:
                    break
                processed += 1
                page_bgr_qr, page_bgr_stamp, page_bgr_feature = PdfScanSplitEngine._prepare_page_images(
                    page_bgr_full,
                    use_qr=use_qr,
                    use_stamp=use_stamp,
                    use_feature=use_feature,
                    ref_size=ref_size,
                    roi_base_size=roi_base_size,
                    options=options,
                )
                marked = False
                stamp_debug = None
                if use_qr:
                    marked = PdfScanSplitEngine._detect_qr_for_scan(
                        doc,
                        i,
                        page_bgr_qr,
                        options,
                        detector=detector,
                        cv2=cv2,
                        render_options=render_options,
                        roi_base_size=roi_base_size,
                        log=log,
                        cancel_check=cancel_check,
                    )
                    PdfScanSplitEngine._raise_if_cancelled(cancel_check)

                if not marked and use_stamp:
                    marked, stamp_debug = PdfScanSplitEngine._detect_stamp_for_scan(i, page_bgr_stamp, cv2=cv2, log=log)

                if not marked and use_feature and ref_des is not None:
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

            if log:
                elapsed_s = time.perf_counter() - started_at
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
        markers, total = PdfScanSplitEngine._scan_markers(
            pdf_path,
            reference_image_path,
            options,
            page_limit=page_limit,
            progress=progress,
            log=log,
            cancel_check=cancel_check,
        )
        segments = PdfScanSplitEngine.build_segments(int(total), markers, options)
        suspect_segments = PdfScanSplitEngine.analyze_suspect_segments(segments, int(options.max_segment_pages or 0))
        PdfScanSplitEngine.log_suspect_segments(suspect_segments, log)
        return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=int(total), suspect_segments=suspect_segments)

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

        doc = QPdfDocument(None)
        try:
            total = PdfScanSplitEngine._load_ready_pdf(doc, pdf_path)
            idx = PdfScanSplitEngine._validate_page_index(page_index, total)

            render_options = QPdfDocumentRenderOptions()
            page_bgr_full = PdfScanSplitEngine._render_page_checked(
                doc,
                idx,
                options.dpi,
                render_options=render_options,
                cancel_check=cancel_check,
            )
            if page_bgr_full is None:
                raise RuntimeError("已取消")
        finally:
            doc.close()
        PdfScanSplitEngine._raise_if_cancelled(cancel_check)

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
            PdfScanSplitEngine._detect_qr_for_probe(result, page_bgr_qr, options, detector=detector, cv2=cv2, cancel_check=cancel_check)

        if (not result["marked"]) and use_stamp:
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
            PdfScanSplitEngine._detect_stamp_for_probe(result, page_bgr_stamp, cv2=cv2)

        if (not result["marked"]) and use_feature and ref_des is not None:
            PdfScanSplitEngine._raise_if_cancelled(cancel_check)
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

        if not result["reason"]:
            result["reason"] = "未命中"
        return result

    @staticmethod
    def build_segments(total_pages: int, marker_pages: list[int], options: PdfScanSplitOptions) -> list[list[int]]:
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
        os.makedirs(output_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        outputs: list[str] = []
        used_paths: set[str] = set()

        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for idx, pages in enumerate(segments, start=1):
                if PdfScanSplitEngine._is_cancelled(cancel_check):
                    break
                seg_started_at = time.perf_counter()
                writer = PyPDF2.PdfWriter()
                for p in pages:
                    if PdfScanSplitEngine._is_cancelled(cancel_check):
                        return outputs
                    writer.add_page(reader.pages[p])
                if PdfScanSplitEngine._is_cancelled(cancel_check):
                    return outputs
                out_name = f"{prefix}{stem}_scan{idx}.pdf"
                out_path = make_unique_output_path(output_dir, out_name, used_paths)
                tmp_name = os.path.basename(out_path)
                tmp_path = make_unique_temp_path(output_dir, tmp_name, used_paths)
                try:
                    with open(tmp_path, "wb") as out_f:
                        writer.write(out_f)
                    os.replace(tmp_path, out_path)
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    raise
                outputs.append(out_path)
                if log:
                    first_page = pages[0] + 1
                    last_page = pages[-1] + 1
                    seg_elapsed_s = time.perf_counter() - seg_started_at
                    log(f"已生成：{os.path.basename(out_path)}  ({first_page}-{last_page})  用时 {PdfScanSplitEngine._fmt_seconds(seg_elapsed_s)}")

        return outputs

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
        total_started_at = time.perf_counter()
        options = options or PdfScanSplitOptions()
        mode = PdfScanSplitEngine._normalize_detection_mode(options.detection_mode)

        if not output_dir:
            output_dir = os.path.dirname(pdf_path)

        PdfScanSplitEngine._log_execute_scan_start(mode, 0, log)

        scan_started_at = time.perf_counter()
        markers, total_pages = PdfScanSplitEngine._scan_markers(
            pdf_path,
            reference_image_path,
            options,
            progress=progress,
            log=log,
            cancel_check=cancel_check,
        )
        scan_elapsed_s = time.perf_counter() - scan_started_at

        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=total_pages)

        if log:
            log(f"PDF页数：{total_pages}")
            log(f"识别到标记页：{', '.join(str(p + 1) for p in markers) if markers else '无'}")
            log(f"标记页识别耗时：{PdfScanSplitEngine._fmt_seconds(scan_elapsed_s)}")
            log("开始写入拆分结果…")

        build_started_at = time.perf_counter()
        segments = PdfScanSplitEngine.build_segments(total_pages, markers, options)
        build_elapsed_s = time.perf_counter() - build_started_at
        if PdfScanSplitEngine._is_cancelled(cancel_check):
            return PdfScanSplitResult(output_files=[], marker_pages=markers, total_pages=total_pages)
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
        if PdfScanSplitEngine._is_cancelled(cancel_check) and outputs and log:
            log(f"任务已取消，已保留 {len(outputs)} 个已生成文件")
        total_elapsed_s = time.perf_counter() - total_started_at
        PdfScanSplitEngine._log_execute_summary(
            outputs=outputs,
            total_elapsed_s=total_elapsed_s,
            scan_elapsed_s=scan_elapsed_s,
            write_elapsed_s=write_elapsed_s,
            log=log,
        )

        return PdfScanSplitResult(output_files=outputs, marker_pages=markers, total_pages=total_pages, suspect_segments=suspect_segments)
