import atexit
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from src.utils.history_manager import HistoryManager


class HistoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="file-toolbox-history-")
        self.history_path = os.path.join(self.temp_dir.name, "history.json")
        self.manager = HistoryManager(storage_path=self.history_path)

    def tearDown(self):
        self.manager._flush_exit()
        atexit.unregister(self.manager._flush_exit)
        self.temp_dir.cleanup()

    def test_large_scan_details_are_bounded_and_marked(self):
        details = {
            "log_tail": [f"日志 {index}: " + "印章二维码特征点" * 400 for index in range(200)],
            "output_files": [f"C:/output/part-{index:04d}.pdf" for index in range(240)],
            "performance_stats": {"pages_scanned": 300, "total_seconds": 12.5},
        }

        self.manager.add_record("scan_split", "扫描拆分", details)
        stored = self.manager.get_recent_records(1)[0].details

        encoded = json.dumps(stored, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), HistoryManager._MAX_DETAILS_BYTES)
        self.assertTrue(stored["details_truncated"])
        self.assertTrue(stored["log_tail_truncated"])
        self.assertEqual(stored["log_tail_original_count"], 200)
        self.assertIn("日志 199", stored["log_tail"][-1])
        self.assertEqual(len(details["log_tail"]), 200)

    def test_large_detail_with_single_item_converges(self):
        details = {
            "records": ["记录" * 40000],
            "empty_note": "",
            "already_compact": {"truncated": True},
        }

        self.manager.add_record("internal", "超大记录", details)
        stored = self.manager.get_recent_records(1)[0].details

        self.assertLessEqual(len(json.dumps(stored, ensure_ascii=False).encode("utf-8")), HistoryManager._MAX_DETAILS_BYTES)
        self.assertTrue(stored["details_truncated"])

    def test_cancelled_legacy_record_is_warning(self):
        self.assertEqual(
            HistoryManager.infer_level(False, "已取消", {"cancelled": True}, "快速扫描"),
            "warning",
        )

    def test_clear_history_is_persisted_before_returning(self):
        self.manager.add_record("scan_split", "扫描拆分", {"marker_pages": [1]})
        self.manager._flush_to_disk()

        self.assertTrue(self.manager.clear_history())
        self.assertEqual(self.manager.get_recent_records(10), [])
        with open(self.history_path, "r", encoding="utf-8") as history_file:
            self.assertEqual(json.load(history_file), [])

    def test_clear_history_restores_memory_when_persistence_fails(self):
        self.manager.add_record("scan_split", "扫描拆分", {"marker_pages": [1]})

        with patch.object(self.manager, "_save_to_file_unlocked", return_value=False):
            self.assertFalse(self.manager.clear_history())

        records = self.manager.get_recent_records(10)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].description, "扫描拆分")

    def test_writer_retries_failed_flush_with_backoff(self):
        manager = object.__new__(HistoryManager)
        manager._writer_stop = MagicMock()
        manager._writer_stop.is_set.return_value = False
        manager._writer_stop.wait.side_effect = [False, False, True]
        manager._dirty = threading.Event()
        manager._dirty.set()
        manager._flush_to_disk = MagicMock(side_effect=[False, True])
        manager.last_error = None

        manager._writer_loop()

        self.assertEqual(manager._flush_to_disk.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in manager._writer_stop.wait.call_args_list],
            [5.0, 10.0, 5.0],
        )
        self.assertFalse(manager._dirty.is_set())


if __name__ == "__main__":
    unittest.main()
