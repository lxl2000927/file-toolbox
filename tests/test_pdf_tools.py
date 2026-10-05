import os
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz
from PIL import Image

from src.core.pdf_tools_engine import run_pdf_tool


class PdfToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root / '源文件.pdf'
        with fitz.open() as doc:
            for text in ['PAGE ONE', '', 'PAGE THREE']:
                page = doc.new_page(width=300, height=400)
                if text:
                    page.insert_text((30, 70), text, fontsize=20)
            doc.save(self.pdf)
        self.files = [str(self.pdf)]

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, action, **options):
        return run_pdf_tool(action, self.files, {'output_dir': str(self.root / 'out'), **options})

    def test_inspect_thumbnail_and_signature(self):
        result = self.run_tool('inspect')
        self.assertEqual(result['sources'][0]['page_count'], 3)
        self.assertEqual(len(result['sources'][0]['signature']), 64)
        thumbs = self.run_tool('thumbnails', pages=[{'source': 0, 'index': 0, 'rotation': 90}])
        self.assertTrue(thumbs['thumbnails'][0]['data_url'].startswith('data:image/jpeg;base64,'))

    def test_page_order_rotation_and_no_overwrite(self):
        options = {'pages': [{'source': 0, 'index': 2, 'rotation': 90}, {'source': 0, 'index': 0}], 'filename': '整理'}
        one = self.run_tool('assemble', **options)
        two = self.run_tool('assemble', **options)
        self.assertNotEqual(one['output_files'], two['output_files'])
        with fitz.open(one['output_files'][0]) as doc:
            self.assertEqual(len(doc), 2)
            self.assertIn('THREE', doc[0].get_text())
            self.assertEqual(doc[0].rotation, 90)
            self.assertIn('ONE', doc[1].get_text())
        self.assertEqual(len(fitz.open(self.pdf)), 3)

    def test_interleave_reverse_keeps_unequal_tail(self):
        back = self.root / 'back.pdf'
        with fitz.open() as doc:
            for text in ['BACK TWO', 'BACK ONE']:
                doc.new_page().insert_text((20, 40), text)
            doc.save(back)
        self.files.append(str(back))
        result = self.run_tool('interleave', reverse_back=True)
        with fitz.open(result['output_files'][0]) as doc:
            self.assertEqual(len(doc), 5)
            self.assertIn('BACK ONE', doc[1].get_text())
            self.assertIn('BACK TWO', doc[3].get_text())
            self.assertIn('THREE', doc[4].get_text())

    def test_blank_detection_does_not_delete_or_mark_black_or_text(self):
        with fitz.open(self.pdf) as doc:
            page = doc.new_page(width=300, height=400)
            page.draw_rect(page.rect, fill=(0, 0, 0), color=(0, 0, 0))
            doc.save(self.root / 'blank-cases.pdf')
        self.files = [str(self.root / 'blank-cases.pdf')]
        result = self.run_tool('detect_blank')
        self.assertEqual([p['index'] for p in result['candidates']], [1])
        self.assertEqual(result['output_files'], [])

    def test_images_pdf_export_and_original_extraction(self):
        img = self.root / '照片.png'
        Image.new('RGB', (160, 90), '#3377cc').save(img)
        self.files = [str(img)]
        pdf = self.run_tool('assemble')['output_files'][0]
        self.files = [pdf]
        exported = self.run_tool('export_images', dpi=72)
        original = self.run_tool('extract_images')
        with Image.open(exported['output_files'][0]) as picture:
            self.assertEqual(picture.size, (160, 90))
        with Image.open(original['output_files'][0]) as picture:
            self.assertEqual(picture.size, (160, 90))

    def test_structural_compression_retains_searchable_text(self):
        result = self.run_tool('compress', compression='lossless')
        with fitz.open(result['output_files'][0]) as doc:
            self.assertIn('ONE', doc[0].get_text())
        self.assertGreater(result['bytes_before'], 0)
        self.assertGreater(result['bytes_after'], 0)

    def test_structural_compression_keeps_bookmarks_and_internal_links(self):
        linked = self.root / 'linked.pdf'
        with fitz.open(self.pdf) as doc:
            doc[0].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(20, 20, 80, 40), 'page': 2})
            doc.set_toc([[1, '第三页', 3]])
            doc.set_metadata({'title': 'original title'})
            doc.save(linked)
        self.files = [str(linked)]
        result = self.run_tool('compress', compression='lossless')
        with fitz.open(result['output_files'][0]) as doc:
            self.assertEqual(doc.get_toc(), [[1, '第三页', 3]])
            self.assertEqual(doc[0].get_links()[0]['page'], 2)
            self.assertEqual(doc.metadata['title'], 'original title')

    def test_raster_compression_with_ocr_keeps_searchable_layer(self):
        result = self.run_tool('assemble', compression='raster', ocr=True, language='eng', dpi=150)
        with fitz.open(result['output_files'][0]) as doc:
            self.assertIn('ONE', doc[0].get_text())

    def test_failed_second_output_retains_first_and_removes_temporary_files(self):
        from unittest.mock import patch
        from src.core import pdf_tools_engine as engine
        original = engine.write_new_output
        calls = 0
        def failing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('disk full')
            return original(*args, **kwargs)
        with patch.object(engine, 'write_new_output', failing):
            result = self.run_tool('export_images')
        self.assertEqual(len(result['output_files']), 1)
        self.assertEqual(result['errors'], ['disk full'])
        self.assertEqual(len(list((self.root / 'out').iterdir())), 1)

    def test_segments_use_explicit_reviewed_page_lists(self):
        result = self.run_tool('segments', segments=[[0, 1], [2]], filename='复核')
        self.assertEqual(len(result['output_files']), 2)
        self.assertEqual([len(fitz.open(p)) for p in result['output_files']], [2, 1])

    def test_stale_signature_and_bad_page_fail_before_writing(self):
        for options in [{'signatures': ['wrong']}, {'pages': [{'source': 0, 'index': 500}]}]:
            with self.assertRaises(ValueError):
                self.run_tool('assemble', **options)
        self.assertFalse((self.root / 'out').exists())

    def test_cancel_does_not_publish_partial_document(self):
        result = run_pdf_tool('assemble', self.files, {'output_dir': str(self.root / 'out')}, cancel_check=lambda: True)
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['output_files'], [])
        self.assertFalse(any((self.root / 'out').glob('*')))

    def test_waiting_for_shared_pdf_resource_can_be_cancelled(self):
        import threading
        from src.utils.pdf_native_lock import PDF_NATIVE_LOCK
        cancel = threading.Event()
        started = threading.Event()
        completed = []
        def worker():
            started.set()
            completed.append(run_pdf_tool('inspect', self.files, cancel_check=cancel.is_set))
        with PDF_NATIVE_LOCK:
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(started.wait(1))
            cancel.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertTrue(completed[0]['cancelled'])

    def test_cancel_multi_output_retains_completed_files(self):
        stopped = False
        def progress(phase, current, total):
            nonlocal stopped
            if phase == '已输出' and current == 1:
                stopped = True
        result = run_pdf_tool('export_images', self.files, {'output_dir': str(self.root / 'out')},
                              cancel_check=lambda: stopped, progress=progress)
        self.assertTrue(result['cancelled'])
        self.assertEqual(len(result['output_files']), 1)
        self.assertTrue(os.path.isfile(result['output_files'][0]))
        self.assertEqual(len(list((self.root / 'out').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
