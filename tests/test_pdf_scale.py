import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
from src.core import pdf_tools_engine as engine
from src.utils.rpc_validation import validate_path_params
from src.utils import input_guard
from contextlib import contextmanager


class PdfScaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with fitz.open() as doc:
            doc.new_page(width=120, height=160).insert_text((12, 30), 'SCALE')
            self.data = doc.tobytes()
        self.files = []
        for i in range(3):
            file = self.root / f'{i}.pdf'
            file.write_bytes(self.data)
            self.files.append(str(file))

    def tearDown(self):
        self.tmp.cleanup()

    def test_small_file_read_does_not_allocate_the_maximum_file_size(self):
        original = input_guard.open_input
        requested = []
        @contextmanager
        def observed(path):
            with original(path) as stream:
                class Reader:
                    def fileno(self): return stream.fileno()
                    def read(self, size):
                        requested.append(size)
                        return stream.read(size)
                yield Reader()
        with patch.object(input_guard, 'open_input', observed):
            self.assertEqual(input_guard.read_input_bytes(self.files[0], 512 * 1024 * 1024), self.data)
        self.assertLessEqual(max(requested), len(self.data) + 1)

    def test_3000_sources_are_accepted_and_3001_rejected_at_both_boundaries(self):
        files = [self.files[0]] * 3000
        validate_path_params('pdf_tools.run', {'files': files})
        result = engine.run_pdf_tool('inspect', files, {'compact_inspect': True})
        self.assertEqual(len(result['sources']), 3000)
        self.assertTrue(all(s['page_count'] == 1 and s['pages'] == [] for s in result['sources']))
        for call in [lambda: validate_path_params('pdf_tools.run', {'files': files + files[:1]}),
                     lambda: engine.run_pdf_tool('inspect', files + files[:1])]:
            with self.assertRaisesRegex(ValueError, '3000'):
                call()

    def test_inspection_does_not_keep_previous_source_documents_open(self):
        opened, peak = [], 0
        original = engine._open_source
        def tracked(*args):
            nonlocal peak
            doc, kind = original(*args)
            opened.append(doc)
            peak = max(peak, sum(not d.is_closed for d in opened))
            return doc, kind
        with patch.object(engine, '_open_source', tracked):
            engine.run_pdf_tool('inspect', self.files)
        self.assertEqual(peak, 1)
        self.assertTrue(all(d.is_closed for d in opened))

    def test_thumbnail_does_not_read_unrelated_sources(self):
        reads = []
        original = engine.read_input_bytes
        def tracked(path, limit):
            reads.append(path)
            return original(path, limit)
        with patch.object(engine, 'read_input_bytes', tracked):
            result = engine.run_pdf_tool('thumbnails', self.files, {'pages': [{'source': 2, 'index': 0}]})
        self.assertEqual(reads, [self.files[2]])
        self.assertEqual(result['thumbnails'][0]['source'], 2)

    def test_partial_inspection_reports_bad_file_and_retains_good_sources(self):
        Path(self.files[1]).write_bytes(b'broken PDF')
        result = engine.run_pdf_tool('inspect', self.files, {'partial_inspect': True, 'compact_inspect': True})
        self.assertEqual([s['path'] for s in result['sources']], [self.files[0], self.files[2]])
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('1.pdf', result['errors'][0])

    def test_cancel_inspection_retains_completed_sources(self):
        stop = False
        def progress(phase, current, total):
            nonlocal stop
            if phase == '已导入文件' and current == 1:
                stop = True
        result = engine.run_pdf_tool('inspect', self.files, {'partial_inspect': True}, lambda: stop, progress)
        self.assertTrue(result['cancelled'])
        self.assertEqual(len(result['sources']), 1)

    def test_merge_closes_sources_and_preserves_interleaved_order(self):
        opened, peak = [], 0
        original = engine._open_source
        def tracked(*args):
            nonlocal peak
            doc, kind = original(*args)
            opened.append(doc)
            peak = max(peak, sum(not d.is_closed for d in opened))
            return doc, kind
        with patch.object(engine, '_open_source', tracked):
            result = engine.run_pdf_tool('assemble', self.files, {
                'output_dir': str(self.root / 'out'),
                'pages': [{'source': n, 'index': 0} for n in [2, 0, 1, 2]],
            })
        self.assertEqual(peak, 1)
        with fitz.open(result['output_files'][0]) as doc:
            self.assertEqual(len(doc), 4)
