import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from src.core.pdf_split_engine import PdfSplitEngine
from src.core.pdf_scan_split_engine import PdfScanSplitEngine, ScanWriteError


class TaskCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='toolbox-checkpoint-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'input.pdf'
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=100, height=100)
        writer.write(self.source)
        self.saved = None

    def save(self, value):
        self.saved = copy.deepcopy(value)

    def failing_writer(self):
        real = PdfWriter.write
        writes = 0
        def write(writer, stream):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError('disk full')
            return real(writer, stream)
        return patch.object(PdfWriter, 'write', write)

    def test_ordinary_retry_reuses_committed_outputs_even_for_failed_input(self):
        config = {'mode': 'by_page_count', 'page_count': 1, 'output_dir': str(self.root / 'outputs')}
        with self.failing_writer():
            failed = PdfSplitEngine().execute_split([str(self.source)], config, on_checkpoint=self.save)
        self.assertEqual(failed['failed'], 1)
        first = Path(failed['output_files'][0])
        before = first.stat().st_mtime_ns
        result = PdfSplitEngine().execute_split([str(self.source)], config, checkpoint=self.saved, on_checkpoint=self.save)
        self.assertEqual(result['successful'], 1)
        self.assertEqual(len(list(first.parent.glob('*.pdf'))), 3)
        self.assertEqual(first.stat().st_mtime_ns, before)
        self.assertEqual(result['output_files'][0], str(first))

    def test_scan_retry_reuses_completed_segments_and_rejects_modified_output(self):
        options = {'output_dir': str(self.root / 'outputs'), 'prefix': ''}
        with self.failing_writer(), self.assertRaises(ScanWriteError) as failed:
            PdfScanSplitEngine.write_segments(str(self.source), [[0], [1], [2]], **options, on_checkpoint=self.save)
        first = Path(failed.exception.output_files[0])
        result = PdfScanSplitEngine.write_segments(str(self.source), [[0], [1], [2]], **options,
                                                  checkpoint=self.saved, on_checkpoint=self.save)
        self.assertEqual(len(result), 3)
        self.assertEqual(len(list(first.parent.glob('*.pdf'))), 3)
        first.write_bytes(b'changed')
        with self.assertRaisesRegex((ValueError, ScanWriteError), '变化'):
            PdfScanSplitEngine.write_segments(str(self.source), [[0], [1], [2]], **options,
                                              checkpoint=self.saved, on_checkpoint=self.save)

    def test_retry_does_not_plan_completed_input_again(self):
        second = self.root / 'second.pdf'
        second.write_bytes(self.source.read_bytes())
        config = {'mode': 'by_page_count', 'page_count': 1, 'output_dir': str(self.root / 'outputs')}
        real_plan = PdfSplitEngine.plan_outputs_for_file
        def fail_second(engine, path, config):
            if path == str(second):
                raise OSError('temporary input failure')
            return real_plan(engine, path, config)
        with patch.object(PdfSplitEngine, 'plan_outputs_for_file', fail_second):
            result = PdfSplitEngine().execute_split([str(self.source), str(second)], config, on_checkpoint=self.save)
        self.assertEqual(result['successful'], 1)
        def assert_only_failed(engine, path, config):
            if path == str(self.source):
                raise AssertionError('completed input was planned again')
            return real_plan(engine, path, config)
        with patch.object(PdfSplitEngine, 'plan_outputs_for_file', assert_only_failed):
            result = PdfSplitEngine().execute_split([str(self.source), str(second)], config,
                                                    checkpoint=self.saved, on_checkpoint=self.save)
        self.assertEqual(result['successful'], 2)
        self.assertEqual(result['errors'], [])

    def test_scan_execute_resumes_saved_detection_without_rescanning(self):
        with patch.object(PdfScanSplitEngine, '_scan_markers', return_value=([0, 1, 2], 3)), self.failing_writer():
            result = PdfScanSplitEngine.execute(str(self.source), '', output_dir=str(self.root / 'outputs'),
                                                 on_checkpoint=self.save)
        self.assertEqual(len(result.output_files), 1)
        with patch.object(PdfScanSplitEngine, '_scan_markers', side_effect=AssertionError('completed scan repeated')):
            retried = PdfScanSplitEngine.execute(str(self.source), '', output_dir=str(self.root / 'outputs'),
                                                  checkpoint=self.saved, on_checkpoint=self.save)
        self.assertEqual(len(retried.output_files), 3)
        self.assertFalse(retried.error)
        self.assertEqual(len(list((self.root / 'outputs').glob('*.pdf'))), 3)
