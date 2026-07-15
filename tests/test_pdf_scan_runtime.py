import unittest
import threading
import time

import cv2
import numpy as np

from src.core.pdf_scan_split_engine import (
    PdfScanSplitEngine,
    PdfScanSplitOptions,
    _OPENCV_TASK_LOCK,
    _serialized_opencv_task,
)


class PdfScanRuntimeTests(unittest.TestCase):
    def test_legacy_qr_effort_values_map_to_effective_levels(self):
        cases = {
            12: 12,
            23: 12,
            24: 24,
            71: 24,
            72: 72,
            143: 72,
            144: 144,
            180: 144,
            500: 144,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                options = PdfScanSplitOptions(qrcode_max_attempts=raw)
                self.assertEqual(options.qrcode_max_attempts, expected)

    def test_serialized_task_restores_opencv_process_state(self):
        original_threads = int(cv2.getNumThreads())
        original_opencl = bool(cv2.ocl.useOpenCL())
        original_optimized = bool(cv2.useOptimized())
        try:
            cv2.setNumThreads(2)
            cv2.ocl.setUseOpenCL(False)
            cv2.setUseOptimized(False)

            @_serialized_opencv_task
            def task(_pdf_path, _reference_path, options):
                self.assertGreaterEqual(cv2.getNumThreads(), 1)
                self.assertTrue(cv2.useOptimized())
                return "done"

            result = task("", "", PdfScanSplitOptions(enable_multithread=True, enable_gpu=True))

            self.assertEqual(result, "done")
            self.assertEqual(cv2.getNumThreads(), 2)
            self.assertFalse(cv2.ocl.useOpenCL())
            self.assertFalse(cv2.useOptimized())
        finally:
            cv2.setNumThreads(original_threads)
            cv2.ocl.setUseOpenCL(original_opencl)
            cv2.setUseOptimized(original_optimized)

    def test_roi_clip_keeps_padding_for_qr_and_stamp_but_not_feature(self):
        roi_clip = np.zeros((190, 190, 3), dtype=np.uint8)

        qr, stamp, feature = PdfScanSplitEngine._prepare_page_images_from_roi_clip(
            roi_clip,
            use_qr=True,
            use_stamp=True,
            use_feature=True,
            reference_roi=(100, 100, 100, 100),
            roi_base_size=(400, 400),
            pad_ratio=0.45,
        )

        self.assertEqual(qr.shape[:2], (190, 190))
        self.assertEqual(stamp.shape[:2], (190, 190))
        self.assertEqual(feature.shape[:2], (100, 100))

        full_page = np.zeros((400, 400, 3), dtype=np.uint8)
        options = PdfScanSplitOptions(
            use_roi=True,
            reference_roi=(100, 100, 100, 100),
        )
        full_qr, full_stamp, full_feature = PdfScanSplitEngine._prepare_page_images(
            full_page,
            use_qr=True,
            use_stamp=True,
            use_feature=True,
            ref_size=(400, 400),
            roi_base_size=(400, 400),
            options=options,
        )
        self.assertEqual(full_qr.shape[:2], qr.shape[:2])
        self.assertEqual(full_stamp.shape[:2], stamp.shape[:2])
        self.assertEqual(full_feature.shape[:2], feature.shape[:2])

    def test_waiting_visual_task_can_be_cancelled(self):
        lock_held = threading.Event()
        release_lock = threading.Event()

        def holder() -> None:
            with _OPENCV_TASK_LOCK:
                lock_held.set()
                release_lock.wait(timeout=2)

        holder_thread = threading.Thread(target=holder)
        holder_thread.start()
        self.assertTrue(lock_held.wait(timeout=1))

        @_serialized_opencv_task
        def task(_pdf_path, _reference_path, _options, **_kwargs):
            return "unexpected"

        started = time.perf_counter()
        try:
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                task("", "", PdfScanSplitOptions(), cancel_check=lambda: True)
        finally:
            release_lock.set()
            holder_thread.join(timeout=2)

        self.assertLess(time.perf_counter() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
