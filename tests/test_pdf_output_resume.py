import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from src.core import pdf_tools_engine as engine


class PdfOutputResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = []
        for source in ('A', 'B'):
            path = self.root / f'{source}.pdf'
            with fitz.open() as doc:
                for page in range(3):
                    doc.new_page(width=120, height=160).insert_text((10, 30), f'{source}{page}')
                doc.save(path)
            self.files.append(str(path))
        self.options = {'output_dir': str(self.root / 'out'), 'filename': 'result',
                        'output_mode': 'chunks', 'chunk_pages': 2}

    def tearDown(self):
        self.tmp.cleanup()

    def texts(self, paths):
        result = []
        for path in paths:
            with fitz.open(path) as doc:
                result.append([page.get_text().strip() for page in doc])
        return result

    def test_chunks_preserve_global_selected_order_and_rotations(self):
        options = {**self.options, 'pages': [
            {'source': 1, 'index': 2, 'rotation': 90}, {'source': 0, 'index': 1},
            {'source': 1, 'index': 0}, {'source': 0, 'index': 2}, {'source': 1, 'index': 2}]}
        result = engine.run_pdf_tool('assemble', self.files, options)
        self.assertEqual(self.texts(result['output_files']), [['B2', 'A1'], ['B0', 'A2'], ['B2']])
        with fitz.open(result['output_files'][0]) as doc:
            self.assertEqual(doc[0].rotation, 90)

    def test_repeated_pages_keep_independent_rotations_inside_a_chunk(self):
        options = {**self.options, 'chunk_pages': 3, 'pages': [
            {'source': 0, 'index': 0, 'rotation': 90},
            {'source': 0, 'index': 0, 'rotation': 180},
            {'source': 0, 'index': 0, 'rotation': 0}]}
        result = engine.run_pdf_tool('assemble', self.files[:1], options)
        with fitz.open(result['output_files'][0]) as doc:
            self.assertEqual([p.rotation for p in doc], [90, 180, 0])
            self.assertEqual([p.get_text().strip() for p in doc], ['A0', 'A0', 'A0'])

    def test_source_mode_uses_first_appearance_and_chosen_relative_order(self):
        options = {**self.options, 'output_mode': 'source', 'pages': [
            {'source': 1, 'index': 2}, {'source': 0, 'index': 1},
            {'source': 1, 'index': 0}, {'source': 0, 'index': 2}]}
        result = engine.run_pdf_tool('assemble', self.files, options)
        self.assertEqual(self.texts(result['output_files']), [['B2', 'B0'], ['A1', 'A2']])

    def test_interleave_is_applied_before_chunk_boundaries(self):
        result = engine.run_pdf_tool('interleave', self.files, {**self.options, 'reverse_back': True})
        self.assertEqual(self.texts(result['output_files']), [['A0', 'B2'], ['A1', 'B1'], ['A2', 'B0']])

    def test_invalid_output_controls_rejected_before_writing(self):
        for options in ({'output_mode': 'bad'}, {'chunk_pages': 0}, {'chunk_pages': 10001}, {'chunk_pages': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                engine.run_pdf_tool('assemble', self.files, {**self.options, **options})
        self.assertFalse((self.root / 'out').exists())

    def test_retry_retains_completed_unit_without_duplicate(self):
        checkpoints = []
        stop = False
        def committed(checkpoint):
            nonlocal stop
            checkpoints.append(copy.deepcopy(checkpoint))
            stop = True
        first = engine.run_pdf_tool('assemble', self.files, self.options,
            cancel_check=lambda: stop, on_checkpoint=committed)
        self.assertTrue(first['cancelled'])
        self.assertEqual(len(first['output_files']), 1)
        resumed = engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=checkpoints[-1])
        self.assertEqual(resumed['output_files'][0], first['output_files'][0])
        self.assertEqual(self.texts(resumed['output_files']), [['A0', 'A1'], ['A2', 'B0'], ['B1', 'B2']])
        self.assertEqual(len(list((self.root / 'out').glob('*.pdf'))), 3)

    def test_changed_output_only_reprocesses_that_unit_without_overwrite(self):
        checkpoints = []
        first = engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        changed = Path(first['output_files'][1])
        changed.write_bytes(b'USER CONTENT')
        resumed = engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=checkpoints[-1])
        self.assertEqual(resumed['output_files'][0], first['output_files'][0])
        self.assertNotEqual(resumed['output_files'][1], first['output_files'][1])
        self.assertEqual(resumed['output_files'][2], first['output_files'][2])
        self.assertEqual(changed.read_bytes(), b'USER CONTENT')
        self.assertEqual(self.texts(resumed['output_files'])[1], ['A2', 'B0'])

    def test_same_size_output_tampering_is_not_reused(self):
        checkpoints = []
        first = engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        changed = Path(first['output_files'][0])
        data = changed.read_bytes()
        changed.write_bytes(b'X' + data[1:])
        resumed = engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=checkpoints[-1])
        self.assertNotEqual(resumed['output_files'][0], str(changed))
        self.assertEqual(resumed['output_files'][1:], first['output_files'][1:])
        self.assertEqual(changed.read_bytes(), b'X' + data[1:])

    def test_deleted_output_only_reprocesses_missing_unit(self):
        checkpoints = []
        first = engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        Path(first['output_files'][0]).unlink()
        resumed = engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=checkpoints[-1])
        self.assertEqual(resumed['output_files'][1:], first['output_files'][1:])
        self.assertEqual(self.texts(resumed['output_files'])[0], ['A0', 'A1'])

    def test_options_and_source_content_are_checked_before_reuse(self):
        checkpoints = []
        engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        with self.assertRaisesRegex(ValueError, '检查点'):
            engine.run_pdf_tool('assemble', self.files, {**self.options, 'chunk_pages': 3}, checkpoint=checkpoints[-1])
        self.assertEqual(len(list((self.root / 'out').glob('*.pdf'))), 3)
        signatures = [source['signature'] for source in engine.run_pdf_tool('inspect', self.files)['sources']]
        Path(self.files[0]).write_bytes(Path(self.files[1]).read_bytes())
        with self.assertRaisesRegex(ValueError, '变化'):
            engine.run_pdf_tool('assemble', self.files, {**self.options, 'signatures': signatures}, checkpoint=checkpoints[-1])

    def test_changed_unsigned_source_invalidates_old_checkpoint(self):
        checkpoints = []
        first = engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        Path(self.files[0]).write_bytes(Path(self.files[1]).read_bytes())
        with self.assertRaisesRegex(ValueError, '检查点'):
            engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=checkpoints[-1])
        self.assertEqual(self.texts(first['output_files']), [['A0', 'A1'], ['A2', 'B0'], ['B1', 'B2']])
        self.assertEqual(len(list((self.root / 'out').glob('*.pdf'))), 3)

    def test_export_images_reuses_completed_page(self):
        checkpoints = []
        first = engine.run_pdf_tool('export_images', self.files[:1], self.options, on_checkpoint=checkpoints.append)
        second = engine.run_pdf_tool('export_images', self.files[:1], self.options, checkpoint=checkpoints[-1])
        self.assertEqual(second['output_files'], first['output_files'])
        self.assertEqual(len(list((self.root / 'out').glob('*.png'))), 3)

    def test_checkpoint_failure_reports_committed_pdf(self):
        def fail(_):
            raise OSError('checkpoint storage full')
        result = engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=fail)
        self.assertEqual(len(result['output_files']), 1)
        self.assertIn('checkpoint storage full', result['errors'][0])
        self.assertTrue(Path(result['output_files'][0]).is_file())

    def test_disk_preflight_reports_space_before_first_output(self):
        # The OS disk probe is external; real page preparation and writes stay active.
        from src.utils import output_resources
        with patch.object(output_resources.shutil, 'disk_usage', return_value=(100, 99, 1)):
            with self.assertRaisesRegex(OSError, '空间'):
                engine.run_pdf_tool('assemble', self.files, self.options)
        self.assertFalse(list((self.root / 'out').glob('*.pdf')))

    def test_memory_guard_allows_chunks_when_whole_document_is_too_large(self):
        from src.utils import output_resources
        many = self.root / 'many.pdf'
        with fitz.open() as doc:
            for n in range(100):
                doc.new_page(width=100, height=100).insert_text((5, 15), str(n))
            doc.save(many)
        with patch.object(output_resources, 'available_memory_bytes', return_value=38 * 1024 * 1024):
            with self.assertRaisesRegex(ValueError, '分卷'):
                engine.run_pdf_tool('assemble', [str(many)], {**self.options, 'output_mode': 'single'})
            result = engine.run_pdf_tool('assemble', [str(many)], {**self.options, 'chunk_pages': 10})
        self.assertEqual(len(result['output_files']), 10)
        self.assertEqual([text for group in self.texts(result['output_files']) for text in group], [str(i) for i in range(100)])

    def test_memory_guard_counts_duplicate_selected_pages(self):
        from src.utils import output_resources
        options = {**self.options, 'output_mode': 'single',
                   'pages': [{'source': 0, 'index': 0}] * 100}
        with patch.object(output_resources, 'available_memory_bytes', return_value=38 * 1024 * 1024):
            with self.assertRaisesRegex(ValueError, '分卷'):
                engine.run_pdf_tool('assemble', self.files[:1], options)
            result = engine.run_pdf_tool('assemble', self.files[:1], {**options, 'output_mode': 'chunks', 'chunk_pages': 10})
        self.assertEqual([text for group in self.texts(result['output_files']) for text in group], ['A0'] * 100)

    def test_disk_filling_before_atomic_commit_does_not_publish_partial(self):
        from src.utils import output_resources
        calls = 0
        def disk_usage(_):
            nonlocal calls
            calls += 1
            # Planning, pre-save and first write succeed; final commit check fails.
            return (10**12, 0, 10**12 if calls <= 3 else 0)
        with patch.object(output_resources.shutil, 'disk_usage', disk_usage):
            with self.assertRaisesRegex(OSError, '空间'):
                engine.run_pdf_tool('assemble', self.files, {**self.options, 'output_mode': 'single'})
        self.assertEqual(list((self.root / 'out').iterdir()), [])

    def test_resume_complete_outputs_does_not_require_new_disk_capacity(self):
        from src.utils import output_resources
        checkpoints = []
        first = engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        with patch.object(output_resources.shutil, 'disk_usage', return_value=(1, 1, 0)):
            resumed = engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=checkpoints[-1])
        self.assertEqual(resumed['output_files'], first['output_files'])

    def test_invalid_checkpoint_shape_reprocesses_instead_of_crashing(self):
        checkpoints = []
        engine.run_pdf_tool('assemble', self.files, self.options, on_checkpoint=checkpoints.append)
        broken = copy.deepcopy(checkpoints[-1])
        broken['units']['pdf:0'] = {'files': {'pdf': {'path': [], 'size': 'bad'}}}
        result = engine.run_pdf_tool('assemble', self.files, self.options, checkpoint=broken)
        self.assertEqual(self.texts(result['output_files']), [['A0', 'A1'], ['A2', 'B0'], ['B1', 'B2']])

    def test_segments_keep_reviewed_boundaries_even_when_chunks_requested(self):
        result = engine.run_pdf_tool('segments', self.files[:1], {
            **self.options, 'chunk_pages': 1, 'segments': [[2, 0], [1]]})
        self.assertEqual(self.texts(result['output_files']), [['A2', 'A0'], ['A1']])

    def test_pdf_survives_text_failure_and_retry_does_not_duplicate_pdf(self):
        checkpoints = []
        original = engine.write_new_output
        def fail_text(directory, filename, *args, **kwargs):
            if filename.endswith('.txt'):
                raise OSError('text disk full')
            return original(directory, filename, *args, **kwargs)
        options = {**self.options, 'output_mode': 'single', 'ocr_text': True, 'language': 'eng', 'dpi': 72}
        with patch.object(engine, 'write_new_output', fail_text):
            first = engine.run_pdf_tool('ocr', self.files[:1], options, on_checkpoint=checkpoints.append)
        self.assertEqual(len(first['output_files']), 1)
        self.assertEqual(first['errors'], ['text disk full'])
        second = engine.run_pdf_tool('ocr', self.files[:1], options, checkpoint=checkpoints[-1])
        self.assertEqual(second['output_files'], first['output_files'])
        self.assertEqual(len(second['text_files']), 1)
        self.assertEqual(len(list((self.root / 'out').glob('*.pdf'))), 1)
        self.assertIn('A0', Path(second['text_files'][0]).read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
