import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import cv2
import fitz
import numpy as np

from src.core.pdf_scan_split_engine import PdfScanSplitEngine, PdfScanSplitOptions, _QRCodeScanCache


class PdfScanLoggingTests(unittest.TestCase):
    @staticmethod
    def _blank_pdf(path: str, pages: int) -> None:
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()

    def test_execute_logs_parameters_results_and_detector_summary(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-log-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "input.pdf")
            self._blank_pdf(pdf_path, 2)
            logs: list[str] = []

            with patch.object(PdfScanSplitEngine, "_scan_markers", return_value=([0], 2)):
                result = PdfScanSplitEngine.execute(
                    pdf_path,
                    "",
                    output_dir=temp_dir,
                    options=PdfScanSplitOptions(detection_mode="qrcode", dpi=180, qrcode_max_attempts=24),
                    log=logs.append,
                )

            self.assertEqual(len(result.output_files), 1)
            self.assertTrue(any(line.startswith("扫描参数：") for line in logs))
            self.assertIn("识别结果：PDF 共 2 页，标记页：1", logs)
            self.assertIn("识别阶段总耗时：", "\n".join(logs))
            joined = "\n".join(logs)
            self.assertIn("拆分写入完成：1 个文件", joined)
            self.assertFalse(any(line.startswith("PDF页数：") for line in logs))

    def test_cancelled_scan_logs_cancelled_instead_of_completed(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-log-cancel-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "input.pdf")
            self._blank_pdf(pdf_path, 8)
            logs: list[str] = []
            cancelled = threading.Event()

            def progress(current: int, _total: int) -> None:
                if current >= 2:
                    cancelled.set()

            result = PdfScanSplitEngine.execute(
                pdf_path,
                "",
                output_dir=temp_dir,
                options=PdfScanSplitOptions(detection_mode="stamp", dpi=72),
                progress=progress,
                log=logs.append,
                cancel_check=cancelled.is_set,
            )

            self.assertEqual(result.output_files, [])
            joined = "\n".join(logs)
            self.assertIn("扫描已取消：", joined)
            self.assertIn("任务已取消：未开始写入", joined)
            self.assertNotIn("页面扫描完成：", joined)
            self.assertNotIn("拆分写入完成：", joined)

    def test_detector_cancellation_still_logs_scan_cancelled(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-log-cancel-detector-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "input.pdf")
            self._blank_pdf(pdf_path, 2)
            logs: list[str] = []
            cancelled = threading.Event()

            def cancel_during_qr(*_args, **_kwargs):
                cancelled.set()
                raise RuntimeError("已取消")

            with patch.object(PdfScanSplitEngine, "_detect_qr_for_scan", side_effect=cancel_during_qr):
                with self.assertRaisesRegex(RuntimeError, "已取消"):
                    PdfScanSplitEngine.find_marker_pages(
                        pdf_path,
                        "",
                        PdfScanSplitOptions(detection_mode="qrcode", dpi=72),
                        log=logs.append,
                        cancel_check=cancelled.is_set,
                    )

            self.assertIn("扫描已取消：实际处理 1/2 页", "\n".join(logs))

    def test_auto_stamp_hit_logs_short_circuit(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-log-auto-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "input.pdf")
            self._blank_pdf(pdf_path, 1)
            logs: list[str] = []

            with patch.object(
                PdfScanSplitEngine,
                "_detect_stamp_for_scan",
                return_value=(True, {"present": True, "candidates": 1}),
            ):
                markers = PdfScanSplitEngine.find_marker_pages(
                    pdf_path,
                    "",
                    PdfScanSplitOptions(detection_mode="auto"),
                    log=logs.append,
                )

            self.assertEqual(markers, [0])
            self.assertIn("二维码和特征点匹配已跳过（印章已命中）", "\n".join(logs))

    def test_qr_decode_failure_with_keyword_is_logged(self):
        image = np.full((200, 200, 3), 255, dtype=np.uint8)
        logs: list[str] = []
        options = PdfScanSplitOptions(detection_mode="qrcode", qrcode_text_contains="marker")

        def undecoded(*_args, details=None, **_kwargs):
            details.update({"candidate_present": True, "candidate_confident": True, "variant": "", "bbox": None})
            return []

        with (
            patch.object(PdfScanSplitEngine, "_detect_qrcodes", side_effect=undecoded),
            patch.object(PdfScanSplitEngine, "_qrcode_fallback_dpis", return_value=[]),
        ):
            marked = PdfScanSplitEngine._detect_qr_for_scan(
                None,
                2,
                image,
                options,
                cv2=cv2,
                log=logs.append,
            )

        self.assertFalse(marked)
        self.assertIn("检测到二维码候选但未能解码", "\n".join(logs))
        self.assertIn("无法匹配关键字“marker”", "\n".join(logs))

    def test_qr_decoder_exception_is_logged_once_per_scan(self):
        image = np.full((200, 200, 3), 255, dtype=np.uint8)
        logs: list[str] = []
        options = PdfScanSplitOptions(detection_mode="qrcode", qrcode_max_attempts=12)
        cache = _QRCodeScanCache()

        class BrokenZxing:
            class BarcodeFormat:
                QRCode = object()

            @staticmethod
            def read_barcodes(*_args, **_kwargs):
                raise RuntimeError("decoder unavailable")

        with (
            patch.object(PdfScanSplitEngine, "_load_zxingcpp", return_value=BrokenZxing),
            patch.object(PdfScanSplitEngine, "_qrcode_fallback_dpis", return_value=[]),
        ):
            for page_index in range(2):
                PdfScanSplitEngine._detect_qr_for_scan(
                    None,
                    page_index,
                    image,
                    options,
                    cv2=cv2,
                    scan_cache=cache,
                    log=logs.append,
                )

        matching = [line for line in logs if "ZXing original 解码失败" in line]
        self.assertEqual(len(matching), 1)
        self.assertIn("RuntimeError: decoder unavailable", matching[0])

    def test_stamp_pipeline_exception_is_logged_once_per_scan(self):
        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        logs: list[str] = []
        logged_diagnostics: set[str] = set()

        class BrokenCv2:
            COLOR_BGR2HSV = 1
            COLOR_BGR2LAB = 2

            @staticmethod
            def cvtColor(*_args, **_kwargs):
                raise RuntimeError("color conversion unavailable")

        for page_index in range(2):
            PdfScanSplitEngine._detect_stamp_for_scan(
                page_index,
                image,
                cv2=BrokenCv2,
                log=logs.append,
                logged_diagnostics=logged_diagnostics,
            )

        hsv_logs = [line for line in logs if "HSV 红色掩码失败" in line]
        lab_logs = [line for line in logs if "LAB 红色掩码失败" in line]
        self.assertEqual(len(hsv_logs), 1)
        self.assertEqual(len(lab_logs), 1)
        self.assertIn("RuntimeError: color conversion unavailable", hsv_logs[0])

    def test_skip_pages_are_logged(self):
        with tempfile.TemporaryDirectory(prefix="file-toolbox-log-skip-") as temp_dir:
            pdf_path = os.path.join(temp_dir, "input.pdf")
            self._blank_pdf(pdf_path, 5)
            logs: list[str] = []

            with patch.object(
                PdfScanSplitEngine,
                "_detect_stamp_for_scan",
                return_value=(True, {"present": True, "candidates": 1}),
            ):
                markers = PdfScanSplitEngine.find_marker_pages(
                    pdf_path,
                    "",
                    PdfScanSplitOptions(detection_mode="stamp", qrcode_skip_pages=2),
                    log=logs.append,
                )

            self.assertEqual(markers, [0, 3])
            self.assertIn("按设置跳过后续 2 页：第 2-3 页", "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
