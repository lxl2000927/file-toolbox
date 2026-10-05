import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
from src.core import pdf_tools_engine as engine


class WorkbenchRefinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'many.pdf'
        with fitz.open() as doc:
            for i in range(23):
                doc.new_page().insert_text((30, 60), f'FULL TEXT PAGE {i + 1}')
            doc.save(self.source)
        self.options = {'output_dir': str(self.root / 'out'), 'filename': 'result'}

    def tearDown(self): self.tmp.cleanup()

    def test_ocr_preview_is_bounded_but_txt_contains_every_completed_page(self):
        r = engine.run_pdf_tool('ocr', [str(self.source)], {**self.options, 'ocr_text': True})
        self.assertEqual(r['ocr_total_pages'], 23)
        self.assertEqual(len(r['ocr_pages']), 20)
        self.assertEqual(r['ocr_pages'][0]['page'], 1)
        self.assertEqual(r['ocr_pages'][-1]['page'], 20)
        self.assertEqual(r['ocr_pages'][0]['output_file'], r['output_files'][0])
        text = Path(r['text_files'][0]).read_text(encoding='utf-8-sig')
        self.assertIn('FULL TEXT PAGE 23', text)
        self.assertEqual(len(r['output_files']), 1)

    def test_subset_is_not_reported_as_compression_savings(self):
        r = engine.run_pdf_tool('compress', [str(self.source)], {**self.options, 'pages': [{'source': 0, 'index': 0}]})
        self.assertFalse(r['comparison_eligible'])
        self.assertEqual(r['input_pages'], 23)
        self.assertEqual(r['output_pages'], 1)
        full = engine.run_pdf_tool('compress', [str(self.source)], self.options)
        self.assertTrue(full['comparison_eligible'])

    def test_long_page_preview_is_explicitly_truncated_but_spooled_txt_is_complete(self):
        import io
        from src.core.pdf_text import OcrTextCapture
        text = '完整文字' * 300000
        with OcrTextCapture(True) as capture:
            capture.accept(0, text)
            self.assertEqual(len(capture.pages[0]['text']), 4000)
            self.assertTrue(capture.pages[0]['truncated'])
            self.assertTrue(capture.stream._rolled, 'large full text should spill to disk')
            output = io.BytesIO(); capture.write_to(output, lambda: None)
            self.assertIn(text, output.getvalue().decode('utf-8-sig'))

    def test_multiple_ocr_outputs_share_preview_budget_but_each_has_full_txt(self):
        r = engine.run_pdf_tool('segments', [str(self.source)], {**self.options, 'ocr': True, 'ocr_text': True, 'segments': [list(range(12)), list(range(12, 23))]})
        self.assertEqual(len(r['ocr_pages']), 20)
        self.assertEqual(r['ocr_total_pages'], 23)
        self.assertEqual(len(r['text_files']), 2)
        self.assertIn('FULL TEXT PAGE 23', Path(r['text_files'][1]).read_text(encoding='utf-8-sig'))
        self.assertEqual(r['ocr_pages'][12]['page'], 1)
        self.assertEqual(r['ocr_pages'][12]['output_file'], r['output_files'][1])

    def test_cancel_during_text_save_keeps_pdf_without_publishing_partial_text(self):
        original = engine.write_new_output
        cancelled = False
        def cancel_text(directory, filename, writer, check):
            nonlocal cancelled
            if filename.endswith('.txt'): cancelled = True
            return original(directory, filename, writer, check)
        with patch.object(engine, 'write_new_output', cancel_text):
            r = engine.run_pdf_tool('ocr', [str(self.source)], {**self.options, 'ocr_text': True}, lambda: cancelled)
        self.assertTrue(r['cancelled']); self.assertEqual(len(r['output_files']), 1)
        self.assertFalse(list((self.root / 'out').glob('*.txt')))
        self.assertFalse(list((self.root / 'out').glob('.toolbox-*')))

    def test_real_invalid_output_directory_then_retry_preserves_source(self):
        invalid = self.root / 'ordinary-file'; invalid.write_text('KEEP ME')
        with self.assertRaises(OSError):
            engine.run_pdf_tool('assemble', [str(self.source)], {**self.options, 'output_dir': str(invalid)})
        self.assertEqual(invalid.read_text(), 'KEEP ME')
        self.assertEqual(len(engine.run_pdf_tool('assemble', [str(self.source)], self.options)['output_files']), 1)

    def test_extract_keeps_native_jpeg2000_output_readable(self):
        import io
        from PIL import Image
        encoded = io.BytesIO()
        Image.new('RGB', (64, 64), 'navy').save(encoded, format='JPEG2000')
        source = self.root / 'jpeg2000.pdf'
        with fitz.open() as doc:
            page = doc.new_page(width=64, height=64)
            page.insert_image(page.rect, stream=encoded.getvalue()); doc.save(source)
        result = engine.run_pdf_tool('extract_images', [str(source)], self.options)
        self.assertEqual(Path(result['output_files'][0]).suffix, '.jpx')
        with Image.open(result['output_files'][0]) as extracted:
            self.assertEqual(extracted.size, (64, 64))

    def test_txt_failure_retains_finished_pdf_and_reports_partial_failure(self):
        original = engine.write_new_output
        def fail_text(directory, filename, *args, **kwargs):
            if filename.endswith('.txt'): raise PermissionError('TXT destination denied')
            return original(directory, filename, *args, **kwargs)
        with patch.object(engine, 'write_new_output', fail_text):
            r = engine.run_pdf_tool('ocr', [str(self.source)], {**self.options, 'ocr_text': True})
        self.assertEqual(len(r['output_files']), 1)
        self.assertTrue(Path(r['output_files'][0]).exists())
        self.assertIn('TXT destination denied', r['errors'][0])
        self.assertFalse(list((self.root / 'out').glob('.toolbox-*')))

    def test_unwritable_output_fails_cleanly_and_retry_works(self):
        with patch.object(engine, 'write_new_output', side_effect=PermissionError('output denied')):
            with self.assertRaises(PermissionError): engine.run_pdf_tool('assemble', [str(self.source)], self.options)
        self.assertFalse((self.root / 'out').exists())
        r = engine.run_pdf_tool('assemble', [str(self.source)], self.options)
        self.assertEqual(len(r['output_files']), 1)

    def test_repeated_cancellation_does_not_poison_retry_or_publish_partials(self):
        for _ in range(3):
            r = engine.run_pdf_tool('ocr', [str(self.source)], {**self.options, 'ocr_text': True}, lambda: True)
            self.assertTrue(r['cancelled']); self.assertEqual(r['output_files'], [])
        r = engine.run_pdf_tool('ocr', [str(self.source)], {**self.options, 'ocr_text': True})
        self.assertEqual(len(r['text_files']), 1)
        self.assertFalse(list((self.root / 'out').glob('.toolbox-*')))
