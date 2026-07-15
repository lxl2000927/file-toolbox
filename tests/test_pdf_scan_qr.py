import unittest
from unittest.mock import patch
import os
import tempfile

import cv2
import fitz
import numpy as np

from src.core.pdf_scan_split_engine import (
    PdfScanSplitEngine,
    PdfScanSplitOptions,
    _QRCodeScanCache,
)

try:
    import zxingcpp
except ImportError:
    zxingcpp = None


@unittest.skipIf(zxingcpp is None, "zxingcpp is not installed")
class PdfScanQrTests(unittest.TestCase):
    @staticmethod
    def _barcode_image(fmt, text: str, width: int, height: int):
        try:
            barcode = zxingcpp.create_barcode(text, fmt)
            scale = max(1, min(width, height) // 64)
            image = zxingcpp.write_barcode_to_image(barcode, scale=scale)
        except Exception:
            image = zxingcpp.write_barcode(fmt, text, width, height)
        gray = np.asarray(image, dtype=np.uint8)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    def test_orders_qr_quad_clockwise_from_top_left(self):
        quad = np.float32([[90, 80], [10, 10], [15, 90], [100, 20]])

        ordered = PdfScanSplitEngine._order_qr_quad(quad, np=np)

        np.testing.assert_array_equal(
            ordered,
            np.float32([[10, 10], [100, 20], [90, 80], [15, 90]]),
        )

    def test_zxing_pipeline_decodes_qr_and_rejects_code128(self):
        qr = self._barcode_image(zxingcpp.BarcodeFormat.QRCode, "scan-marker", 320, 320)
        code128 = self._barcode_image(zxingcpp.BarcodeFormat.Code128, "scan-marker", 500, 180)

        qr_details = {}
        qr_infos = PdfScanSplitEngine._detect_qrcodes(qr, details=qr_details)
        code128_infos = PdfScanSplitEngine._detect_qrcodes(code128, details={})

        self.assertEqual(qr_infos, ["scan-marker"])
        self.assertEqual(qr_details["variant"], "original")
        self.assertIsNotNone(qr_details["bbox"])
        self.assertEqual(code128_infos, [])

    def test_cached_position_is_tried_before_full_image(self):
        qr = self._barcode_image(zxingcpp.BarcodeFormat.QRCode, "cached-marker", 260, 260)
        canvas = np.full((700, 900, 3), 255, dtype=np.uint8)
        qr_h, qr_w = qr.shape[:2]
        canvas[80 : 80 + qr_h, 560 : 560 + qr_w] = qr
        first_details = {}
        self.assertEqual(PdfScanSplitEngine._detect_qrcodes(canvas, details=first_details), ["cached-marker"])
        cache = _QRCodeScanCache(bbox=first_details["bbox"])

        second_details = {}
        infos = PdfScanSplitEngine._detect_qrcodes(canvas, scan_cache=cache, details=second_details)

        self.assertEqual(infos, ["cached-marker"])
        self.assertEqual(second_details["variant"], "cache")

    def test_keyword_cache_tracks_matching_code_and_keeps_matching_next_page(self):
        other = self._barcode_image(zxingcpp.BarcodeFormat.QRCode, "other-code", 260, 260)
        target = self._barcode_image(zxingcpp.BarcodeFormat.QRCode, "target-code", 260, 260)
        canvas = np.full((700, 1400, 3), 255, dtype=np.uint8)
        other_h, other_w = other.shape[:2]
        target_h, target_w = target.shape[:2]
        canvas[100 : 100 + other_h, 100 : 100 + other_w] = other
        canvas[100 : 100 + target_h, 950 : 950 + target_w] = target
        options = PdfScanSplitOptions(
            detection_mode="qrcode",
            qrcode_text_contains="target-code",
            dpi=220,
        )
        cache = _QRCodeScanCache()

        first_status = {}
        first_marked = PdfScanSplitEngine._detect_qr_for_scan(
            None,
            0,
            canvas,
            options,
            scan_cache=cache,
            status=first_status,
        )
        second_status = {}
        second_marked = PdfScanSplitEngine._detect_qr_for_scan(
            None,
            1,
            canvas,
            options,
            scan_cache=cache,
            status=second_status,
        )

        self.assertTrue(first_marked)
        self.assertTrue(second_marked)
        self.assertIn("target-code", first_status["infos"])
        self.assertEqual(second_status["infos"], ["target-code"])
        self.assertEqual(second_status["variant"], "cache")
        self.assertIsNotNone(cache.bbox)
        self.assertGreater(cache.bbox[0], 0.5)

    def test_auto_mode_does_not_mark_undecoded_candidate(self):
        image = np.full((300, 300, 3), 255, dtype=np.uint8)
        options = PdfScanSplitOptions(detection_mode="auto")
        status = {}

        def undecoded_candidate(*_args, details=None, **_kwargs):
            details.update(
                {
                    "candidate_present": True,
                    "candidate_confident": True,
                    "variant": "",
                    "bbox": None,
                }
            )
            return []

        with (
            patch.object(PdfScanSplitEngine, "_detect_qrcodes", side_effect=undecoded_candidate),
            patch.object(PdfScanSplitEngine, "_qrcode_fallback_dpis", return_value=[]),
            patch.object(PdfScanSplitEngine, "_qr_detect_stats", return_value=(100.0, 1.0, 0.8)),
        ):
            marked = PdfScanSplitEngine._detect_qr_for_scan(
                None,
                0,
                image,
                options,
                status=status,
            )

        self.assertFalse(marked)
        self.assertTrue(status["present"])
        self.assertTrue(status["decode_failed"])

    def test_probe_uses_same_dpi_fallback_as_scan(self):
        temp_dir = tempfile.mkdtemp(prefix="file-toolbox-probe-")
        pdf_path = os.path.join(temp_dir, "probe.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()
        calls = 0

        def decode_after_retry(*_args, details=None, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                details.update(
                    {
                        "candidate_present": True,
                        "candidate_confident": True,
                        "variant": "",
                        "bbox": None,
                    }
                )
                return []
            details.update(
                {
                    "candidate_present": True,
                    "candidate_confident": True,
                    "variant": "original",
                    "bbox": (0.1, 0.1, 0.2, 0.2),
                }
            )
            return ["retry-marker"]

        try:
            with patch.object(PdfScanSplitEngine, "_detect_qrcodes", side_effect=decode_after_retry):
                result = PdfScanSplitEngine.probe_page(
                    pdf_path,
                    "",
                    PdfScanSplitOptions(detection_mode="qrcode", dpi=180),
                    page_index=0,
                )
        finally:
            try:
                os.remove(pdf_path)
                os.rmdir(temp_dir)
            except OSError:
                pass

        self.assertTrue(result["marked"])
        self.assertEqual(result["qrcode"]["infos"], ["retry-marker"])
        self.assertEqual(result["qrcode"]["dpi"], 200)
        self.assertEqual(calls, 2)

    def test_auto_scan_stops_after_stamp_hit(self):
        temp_dir = tempfile.mkdtemp(prefix="file-toolbox-auto-order-")
        pdf_path = os.path.join(temp_dir, "auto.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()

        try:
            with (
                patch.object(PdfScanSplitEngine, "_detect_stamp_for_scan", return_value=(True, {"present": True})) as stamp,
                patch.object(PdfScanSplitEngine, "_detect_qr_for_scan", return_value=True) as qr,
                patch.object(PdfScanSplitEngine, "_detect_feature_for_scan", return_value=True) as feature,
            ):
                markers = PdfScanSplitEngine.find_marker_pages(
                    pdf_path,
                    "",
                    PdfScanSplitOptions(detection_mode="auto"),
                )
        finally:
            try:
                os.remove(pdf_path)
                os.rmdir(temp_dir)
            except OSError:
                pass

        self.assertEqual(markers, [0])
        stamp.assert_called_once()
        qr.assert_not_called()
        feature.assert_not_called()

    def test_auto_probe_stops_after_stamp_hit(self):
        temp_dir = tempfile.mkdtemp(prefix="file-toolbox-auto-probe-")
        pdf_path = os.path.join(temp_dir, "auto-probe.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()

        def stamp_hit(result, *_args, **_kwargs):
            result["stamp"] = {"present": True, "candidates": 1}
            result["marked"] = True
            result["reason"] = "检测到印章"

        try:
            with (
                patch.object(PdfScanSplitEngine, "_detect_stamp_for_probe", side_effect=stamp_hit) as stamp,
                patch.object(PdfScanSplitEngine, "_detect_qr_for_scan", return_value=True) as qr,
                patch.object(PdfScanSplitEngine, "_detect_feature_for_probe") as feature,
            ):
                result = PdfScanSplitEngine.probe_page(
                    pdf_path,
                    "",
                    PdfScanSplitOptions(detection_mode="auto"),
                    page_index=0,
                )
        finally:
            try:
                os.remove(pdf_path)
                os.rmdir(temp_dir)
            except OSError:
                pass

        self.assertTrue(result["marked"])
        self.assertEqual(result["reason"], "检测到印章")
        self.assertEqual(result["qrcode"]["skipped_reason"], "印章已命中")
        self.assertEqual(result["feature"]["skipped_reason"], "印章已命中")
        stamp.assert_called_once()
        qr.assert_not_called()
        feature.assert_not_called()


if __name__ == "__main__":
    unittest.main()
