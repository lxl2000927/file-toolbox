import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from engine import server
from src.core.pdf_split_engine import PdfSplitEngine
from src.core.rename_engine import RenameEngine


class CoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='toolbox-core-')
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.pdf'
        writer = PdfWriter()
        for _ in range(5):
            writer.add_blank_page(width=100, height=100)
        writer.add_outline_item('Chapter A', 2)
        writer.add_outline_item('Alias A', 2)
        writer.add_outline_item('Chapter B', 4)
        writer.write(str(self.source))
        self.config = {'mode': 'by_page_count', 'page_count': 1, 'output_dir': str(self.root / 'output')}

    def tearDown(self):
        self.directory.cleanup()

    def test_pdf_retains_completed_paths_if_later_output_fails(self):
        real_write = PdfWriter.write
        writes = 0
        def fail_second(writer, stream):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError('disk full')
            return real_write(writer, stream)
        with patch.object(PdfWriter, 'write', fail_second):
            result = PdfSplitEngine().execute_split([str(self.source)], self.config)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(len(result['output_files']), 1)
        self.assertEqual(result['operations'][0]['output_count'], 1)
        self.assertEqual(len(PdfReader(result['output_files'][0]).pages), 1)

    def test_pdf_cancel_before_first_output_is_not_success(self):
        cancelled = False
        engine = PdfSplitEngine()
        plan = engine.plan_outputs_for_file
        def plan_then_cancel(*args):
            nonlocal cancelled
            result = plan(*args); cancelled = True
            return result
        with patch.object(engine, 'plan_outputs_for_file', side_effect=plan_then_cancel):
            result = engine.execute_split([str(self.source)], self.config, cancel_check=lambda: cancelled)
        self.assertEqual(result['successful'], 0)
        self.assertEqual(result['output_files'], [])

    def test_invalid_output_directory_records_failure(self):
        self.config['output_dir'] = str(self.source / 'impossible')
        result = PdfSplitEngine().execute_split([str(self.source)], self.config)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(len(result['operations']), 1)

    def test_bookmarks_cover_leading_pages_and_duplicate_starts_once(self):
        self.config['mode'] = 'by_bookmark'
        result = PdfSplitEngine().execute_split([str(self.source)], self.config)
        counts = [len(PdfReader(path).pages) for path in result['output_files']]
        self.assertEqual(counts, [2, 2, 1])

    def test_partial_undo_can_retry_only_remaining_operations(self):
        first, second = self.root / 'first-new', self.root / 'second-new'
        original = self.root / 'second'
        first.write_text('first'); second.write_text('second'); original.write_text('conflict')
        operations = [
            {'success': True, 'operation': 'copy', 'original_path': str(self.root / 'first'), 'new_path': str(first)},
            {'success': True, 'operation': 'overwrite', 'original_path': str(original), 'new_path': str(second)},
        ]
        for operation in operations:
            operation['_undo_identity'] = server._rename_output_identity(operation['new_path'])
        with patch.dict(server._UNDO_RECORDS, {'test-token': operations}):
            result = server.handle_rename_undo({'undo_token': 'test-token'})
            self.assertEqual(len(result['restored']), 1)
            self.assertEqual(len(result['failed']), 1)
            original.unlink()
            retried = server.handle_rename_undo({'undo_token': 'test-token'})
            self.assertEqual(retried['failed'], [])
            self.assertEqual(original.read_text(), 'second')
            self.assertNotIn('test-token', server._UNDO_RECORDS)

    def test_rename_reports_missing_inputs_and_output_directory_errors(self):
        engine = RenameEngine()
        engine.set_rules([{'type': 'insert_text', 'text': '_new'}])
        result = engine.execute_rename([str(self.root / 'missing.txt'), str(self.source)],
                                       output_dir=str(self.source / 'impossible'))
        self.assertEqual(result['failed'], 2)
        self.assertEqual(len(result['operations']), 2)

    def test_rename_rule_failure_preserves_previous_results(self):
        engine = RenameEngine()
        engine.set_rules([{'type': 'insert_text', 'text': '_new'}])
        other = self.root / 'second.txt'; other.write_text('second')
        with patch.object(engine, 'generate_new_filename', side_effect=['copied.pdf', ValueError('cannot read metadata')]):
            result = engine.execute_rename([str(self.source), str(other)], output_dir=str(self.root / 'copies'))
        self.assertEqual(result['successful'], 1)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(len(result['operations']), 2)
        self.assertTrue(Path(result['operations'][0]['new_path']).exists())
