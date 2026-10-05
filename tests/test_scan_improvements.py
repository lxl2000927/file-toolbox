import itertools
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import fitz
import numpy as np
from pypdf import PdfReader, PdfWriter

from src.core.pdf_scan_split_engine import PdfScanSplitEngine as Engine, PdfScanSplitOptions as Options


class ScanImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="toolbox-scan-regression-")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def pdf(self, name="input.pdf", pages=3, draw=None):
        path = self.root / name
        doc = fitz.open()
        for index in range(pages):
            page = doc.new_page(width=595, height=842)
            if draw:
                draw(page, index)
        doc.save(path)
        doc.close()
        return str(path)

    def test_auto_does_not_split_on_a_solid_red_logo(self):
        path = self.pdf(draw=lambda page, index: page.draw_rect(
            fitz.Rect(80, 80, 140, 140), color=(1, 0, 0), fill=(1, 0, 0)
        ) if index == 1 else None)
        result = Engine.scan_only(path, "", Options(detection_mode="auto", dpi=72))
        self.assertEqual(result.marker_pages, [])

    def test_stamp_outline_is_detected_consistently_with_roi(self):
        path = self.pdf(pages=1, draw=lambda page, _: page.draw_circle(
            fitz.Point(130, 130), 25, color=(1, 0, 0), width=3
        ))
        reference = self.root / "reference.png"
        image = np.full((842, 595, 3), 255, dtype=np.uint8)
        cv2.circle(image, (130, 130), 25, (0, 0, 255), 3)
        cv2.imencode(".png", image)[1].tofile(reference)
        full = Engine.scan_only(path, str(reference), Options(detection_mode="stamp", dpi=180))
        roi = Engine.scan_only(path, str(reference), Options(
            detection_mode="stamp", dpi=180, use_roi=True, reference_roi=(90, 90, 80, 80)
        ))
        self.assertEqual(full.marker_pages, [0])
        self.assertEqual(roi.marker_pages, [0])

    def test_tiny_red_outline_does_not_become_a_stamp_after_roi_crop(self):
        path = self.pdf(pages=1, draw=lambda page, _: page.draw_circle(
            fitz.Point(110, 110), 8, color=(1, 0, 0), width=2
        ))
        reference = self.root / "reference.png"
        image = np.full((842, 595, 3), 255, dtype=np.uint8)
        cv2.circle(image, (110, 110), 8, (0, 0, 255), 2)
        cv2.imencode(".png", image)[1].tofile(reference)
        result = Engine.scan_only(path, str(reference), Options(
            detection_mode="stamp", dpi=180, use_roi=True, reference_roi=(80, 80, 60, 60)
        ))
        self.assertEqual(result.marker_pages, [])

    def test_keyword_search_continues_after_an_unrelated_code(self):
        class Decoder:
            BarcodeFormat = SimpleNamespace(QRCode=object())

            @staticmethod
            def read_barcodes(image, **_kwargs):
                text = "unrelated" if image.ndim == 3 else "target-marker"
                return [SimpleNamespace(valid=True, text=text)]

        image = np.full((300, 300, 3), 220, dtype=np.uint8)
        with patch.object(Engine, "_load_zxingcpp", return_value=Decoder):
            infos = Engine._detect_qrcodes(image, text_contains="target-marker", max_robust_attempts=144)
        self.assertIn("target-marker", infos)

    def test_contrast_success_does_not_generate_unused_threshold_images(self):
        class Decoder:
            BarcodeFormat = SimpleNamespace(QRCode=object())

            @staticmethod
            def read_barcodes(image, **_kwargs):
                return [] if image.ndim == 3 else [SimpleNamespace(valid=True, text="marker")]

        def unwanted(*_args, **_kwargs):
            raise AssertionError("A successful contrast decode must stop before thresholding")

        with patch.object(Engine, "_load_zxingcpp", return_value=Decoder), \
                patch.object(cv2, "threshold", side_effect=unwanted), \
                patch.object(cv2, "adaptiveThreshold", side_effect=unwanted):
            details = {}
            infos = Engine._detect_qrcodes(np.full((300, 300, 3), 220, dtype=np.uint8),
                                          max_robust_attempts=144, details=details)
        self.assertEqual(infos, ["marker"])
        self.assertEqual(details["diagnostics"], [])

    def test_blank_pages_do_not_trigger_dpi_retry_without_candidates(self):
        result = Engine.scan_only(self.pdf(), "", Options(detection_mode="qrcode", dpi=200))
        self.assertEqual(result.marker_pages, [])
        self.assertEqual(result.performance_stats["dpi_fallback_attempts"], 0)

    def test_dpi_retry_budget_is_respected_for_candidates(self):
        image = np.full((300, 300, 3), 255, dtype=np.uint8)
        def candidate(*_args, details=None, **_kwargs):
            details.update(candidate_present=True, candidate_confident=True)
            return []
        for mode, budget, expected in [("qrcode", 0, 0), ("qrcode", 1, 1),
                                       ("qrcode", 2, 2), ("auto", 2, 1)]:
            with self.subTest(mode=mode, budget=budget), \
                    patch.object(Engine, "_detect_qrcodes", side_effect=candidate), \
                    patch.object(Engine, "_render_page_checked", return_value=image) as render:
                marked = Engine._detect_qr_for_scan(None, 0, image, Options(
                    detection_mode=mode, dpi=180, qrcode_dpi_retries=budget
                ))
                self.assertFalse(marked)
                self.assertEqual(render.call_count, expected)

    def test_keyword_search_can_find_target_on_a_dpi_retry(self):
        image = np.full((300, 300, 3), 255, dtype=np.uint8)
        calls = 0
        def decode(*_args, details=None, **_kwargs):
            nonlocal calls
            calls += 1
            details.update(candidate_present=True, candidate_confident=True)
            return ["unrelated" if calls == 1 else "target-marker"]
        with patch.object(Engine, "_detect_qrcodes", side_effect=decode), \
                patch.object(Engine, "_render_page_checked", return_value=image):
            status = {}
            marked = Engine._detect_qr_for_scan(None, 0, image, Options(
                detection_mode="qrcode", dpi=180, qrcode_text_contains="target-marker"
            ), status=status)
        self.assertTrue(marked)
        self.assertEqual(calls, 2)
        self.assertEqual(status["infos"], ["target-marker"])
        self.assertEqual(status["dpi"], 200)

    def test_strict_features_reject_low_inlier_ratio(self):
        self.assertFalse(Engine._is_feature_match(200, 25, .125, Options(min_inlier_ratio=.55)))

    def test_strict_features_reject_matches_confined_to_a_small_header(self):
        rng = np.random.default_rng(4)
        reference_des = rng.integers(0, 256, (100, 32), dtype=np.uint8)
        page_des = np.vstack([reference_des[:25], rng.integers(0, 256, (75, 32), dtype=np.uint8)])
        reference_points = [cv2.KeyPoint(10 + (i % 5) * 2, 10 + (i // 5) * 2, 3) for i in range(25)]
        reference_points += [cv2.KeyPoint(100 + (i % 10) * 40, 100 + (i // 10) * 50, 3) for i in range(75)]
        geometry = {}
        score = Engine._match_score_with_ransac(reference_points, reference_des, reference_points, page_des,
                                               .75, details=geometry)
        self.assertGreaterEqual(score[1], 25)
        self.assertFalse(Engine._is_feature_match(*score, Options(), geometry))
        self.assertTrue(Engine._is_feature_match(*score, Options(feature_strict=False), geometry))

    def test_strict_features_still_accept_a_full_reference_match(self):
        image = np.random.default_rng(12).integers(0, 256, (400, 300, 3), dtype=np.uint8)
        keypoints, descriptors = Engine._extract_features(image, 1200)
        geometry = {}
        score = Engine._match_score_with_ransac(keypoints, descriptors, keypoints, descriptors,
                                               .75, details=geometry)
        self.assertTrue(Engine._is_feature_match(*score, Options(), geometry))

    def test_auto_detector_selection_can_disable_stamp_boundaries(self):
        path = self.pdf(pages=1, draw=lambda page, _: page.draw_circle(
            fitz.Point(130, 130), 25, color=(1, 0, 0), width=3
        ))
        result = Engine.scan_only(path, "", Options(detection_mode="auto", dpi=180,
                                                    auto_detect_stamp=False, auto_detect_feature=False))
        self.assertEqual(result.marker_pages, [])

    def test_auto_rejects_an_empty_detector_selection(self):
        with self.assertRaisesRegex(ValueError, "至少"):
            Engine.scan_only(self.pdf(), "", Options(detection_mode="auto", auto_detect_stamp=False,
                                                     auto_detect_qrcode=False, auto_detect_feature=False))

    def test_disabling_multithreading_uses_one_thread_and_restores_state(self):
        original = cv2.getNumThreads()
        try:
            cv2.setNumThreads(4)
            observed = []
            original_extract = Engine._detect_stamp_for_scan

            def capture(*args, **kwargs):
                observed.append(cv2.getNumThreads())
                return original_extract(*args, **kwargs)

            with patch.object(Engine, "_detect_stamp_for_scan", side_effect=capture):
                Engine.scan_only(self.pdf(pages=1), "", Options(detection_mode="stamp", enable_multithread=False))
            self.assertEqual(observed, [1])
            self.assertEqual(cv2.getNumThreads(), 4)
        finally:
            cv2.setNumThreads(original)

    def test_zero_markers_returns_a_warning_without_copying_the_pdf(self):
        result = Engine.execute(self.pdf(), "", output_dir=str(self.root / "outputs"),
                                options=Options(detection_mode="stamp", dpi=72))
        self.assertEqual(result.output_files, [])
        self.assertTrue(result.warnings)
        self.assertFalse((self.root / "outputs").exists())

    def test_write_failure_returns_completed_failed_and_pending_segments(self):
        source = self.pdf(pages=3)
        original = PdfWriter.write
        writes = 0

        def fail_second(writer, stream):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("simulated disk failure")
            return original(writer, stream)

        with patch.object(Engine, "_scan_markers", return_value=([0, 1, 2], 3)), \
                patch.object(PdfWriter, "write", new=fail_second):
            result = Engine.execute(source, "", output_dir=str(self.root / "outputs"))
        self.assertEqual(len(result.output_files), 1)
        self.assertEqual(len(PdfReader(result.output_files[0]).pages), 1)
        self.assertEqual(result.failed_segments[0]["index"], 2)
        self.assertEqual(result.pending_segments[0]["index"], 3)
        self.assertIn("simulated disk failure", result.error)
        self.assertEqual(list((self.root / "outputs").glob(".*")), [])

    def test_write_phase_reports_segment_progress(self):
        phases = []
        with patch.object(Engine, "_scan_markers", return_value=([0, 1], 3)):
            Engine.execute(self.pdf(), "", output_dir=str(self.root / "outputs"),
                           phase_progress=lambda phase, current, total: phases.append((phase, current, total)))
        self.assertEqual(phases, [("writing", 0, 2), ("writing", 1, 2), ("writing", 2, 2)])

    def test_all_segment_modes_preserve_each_expected_page_once(self):
        for first, exclude in itertools.product([False, True], repeat=2):
            with self.subTest(first=first, exclude=exclude):
                segments = Engine.build_segments(6, [0, 1, 4, 5], Options(
                    marker_as_first_page=first, exclude_marker_page=exclude
                ))
                self.assertEqual([p for segment in segments for p in segment], [2, 3] if exclude else list(range(6)))
                self.assertTrue(all(segments))


if __name__ == "__main__":
    unittest.main()
