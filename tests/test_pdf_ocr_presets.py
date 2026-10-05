import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from src.core.pdf_tools_engine import run_pdf_tool
from src.utils.preset_store import PresetStore


class OcrPresetTests(unittest.TestCase):
    def test_hybrid_scan_keeps_native_header_and_adds_searchable_image_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = Image.new('RGB', (1400, 500), 'white')
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 50)
            draw.text((80, 80), '文件工具箱 测试发票', font=font, fill='black')
            draw.text((80, 200), 'Invoice 2026 12345678', font=font, fill='black')
            png = root / 'scan.png'
            image.save(png)
            source = root / 'hybrid.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=700, height=330)
                page.draw_rect(fitz.Rect(0, 0, 700, 65), color=None, fill=(0.85, 0.92, 1))
                page.insert_text((40, 40), 'ARCHIVE HEADER', fontsize=22)
                page.insert_image(fitz.Rect(0, 70, 700, 320), filename=str(png))
                original_pixels = page.get_pixmap().samples
                doc.save(source)
            for _ in range(2):
                result = run_pdf_tool('ocr', [str(source)], {'output_dir': tmp, 'dpi': 200})
                source = Path(result['output_files'][0])
                with fitz.open(source) as doc:
                    text = ''.join(doc[0].get_text().split())
                    self.assertEqual(text.count('ARCHIVEHEADER'), 1)
                    self.assertEqual(text.count('文件工具箱'), 1)
                    self.assertIn('12345678', text)
                    self.assertEqual(doc[0].get_pixmap().samples, original_pixels)

    def test_segment_ocr_documents_close_after_each_output(self):
        from unittest.mock import patch
        from src.core import pdf_ocr
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'many.pdf'
            with fitz.open() as doc:
                for n in range(5):
                    doc.new_page().insert_text((30, 60), f'PAGE {n}')
                doc.save(source)
            original = pdf_ocr.searchable_pdf
            documents, live_counts = [], []
            def observed(*args, **kwargs):
                document, texts = original(*args, **kwargs)
                documents.append(document)
                live_counts.append(sum(not doc.is_closed for doc in documents))
                return document, texts
            with patch.object(pdf_ocr, 'searchable_pdf', observed):
                run_pdf_tool('segments', [str(source)], {'output_dir': tmp, 'segments': [[i] for i in range(5)], 'ocr': True})
            self.assertEqual(live_counts, [1] * 5)
            self.assertTrue(all(doc.is_closed for doc in documents))

    def test_chinese_raster_becomes_searchable_without_replacing_original_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 50)
            image = Image.new('RGB', (1400, 500), 'white')
            draw = ImageDraw.Draw(image)
            draw.text((80, 80), '文件工具箱 测试发票', font=font, fill='black')
            draw.text((80, 200), 'Invoice 2026 12345678', font=font, fill='black')
            png = root / '扫描件.png'
            image.save(png)
            result = run_pdf_tool('ocr', [str(png)], {'output_dir': tmp, 'dpi': 200, 'language': 'chi_sim+eng'})
            with fitz.open(result['output_files'][0]) as doc:
                text = ''.join(doc[0].get_text().split())
                self.assertIn('文件工具箱', text)
                self.assertIn('12345678', text)
                self.assertTrue(doc[0].search_for('Invoice'))
                # Original 1400x500 image remains, not an OCR raster substitute.
                self.assertTrue(any(i[2:4] == (1400, 500) for i in doc[0].get_images()))

    def test_preset_reload_and_delete_are_durable_and_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'presets.json'
            store = PresetStore(path)
            one = store.save('scan', '发票复核', {'prefix': '发票', 'options': {'dpi': 220}})
            store.save('workbench', '归档', {'compression': 'lossless'})
            reloaded = PresetStore(path)
            self.assertEqual(reloaded.list('scan')[0]['settings']['options']['dpi'], 220)
            reloaded.delete('scan', one['id'])
            self.assertEqual(PresetStore(path).list('scan'), [])
            self.assertEqual(len(PresetStore(path).list('workbench')), 1)

    def test_preset_rejects_large_and_unknown_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PresetStore(Path(tmp) / 'presets.json')
            with self.assertRaises(ValueError):
                store.save('../escape', 'x', {})
            with self.assertRaises(ValueError):
                store.save('scan', 'x', {'blob': 'x' * 20000})


if __name__ == '__main__':
    unittest.main()
