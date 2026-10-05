import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import fitz
import numpy as np
from pypdf import PdfReader, PdfWriter

from src.core.pdf_scan_split_engine import PdfScanSplitEngine
from src.core.pdf_split_engine import PdfSplitConfig
from src.core.rename_engine import RenameEngine
from src.utils.pdf_output import PdfOutputJob, write_pdf_output_jobs
from src.utils.input_guard import input_context


class SecurityBoundaryTests(unittest.TestCase):
    def test_output_directory_is_not_coerced_from_non_strings(self):
        for value in [True, False, 0, 42, [], {}]:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                PdfSplitConfig({"output_dir": value})
        self.assertEqual(PdfSplitConfig({}).output_dir, "")

    def test_large_pdf_is_rejected_before_native_raster_allocation(self):
        with fitz.open() as doc:
            doc.new_page(width=14000, height=14000)
            for roi in [False, True]:
                with self.subTest(roi=roi), patch.object(fitz.Page, "get_pixmap", side_effect=AssertionError("unsafe raster allocation")):
                    with self.assertRaises(ValueError):
                        if roi:
                            PdfScanSplitEngine._render_page_roi_bgr(doc, 0, 300, (0, 0, 100, 100), (100, 100))
                        else:
                            PdfScanSplitEngine._render_page_bgr(doc, 0, 300)

    def test_regular_rotated_pdf_and_small_roi_still_render(self):
        with fitz.open() as doc:
            page = doc.new_page(width=100, height=200)
            page.set_rotation(90)
            full = PdfScanSplitEngine._render_page_bgr(doc, 0, 72)
            self.assertEqual(full.shape, (100, 200, 3))
            roi = PdfScanSplitEngine._render_page_roi_bgr(doc, 0, 72, (0, 0, 50, 50), (200, 100), pad_ratio=0)
            self.assertEqual(roi.shape, (50, 50, 3))

    def test_compressed_image_dimensions_are_checked_before_decode(self):
        def chunk(kind, content):
            return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content))
        header = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 9000, 9000, 8, 2, 0, 0, 0)) + chunk(b'IDAT', b'') + chunk(b'IEND', b'')
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'huge.png'); source.write_bytes(header)
            with patch('cv2.imdecode', side_effect=AssertionError('unsafe image decode')):
                with self.assertRaises(ValueError):
                    PdfScanSplitEngine._read_image_bgr(str(source))

    def test_ordinary_reference_image_preserves_pixels(self):
        import cv2
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'reference.png')
            expected = np.full((40, 60, 3), (10, 20, 30), dtype=np.uint8)
            source.write_bytes(cv2.imencode('.png', expected)[1].tobytes())
            np.testing.assert_array_equal(PdfScanSplitEngine._read_image_bgr(str(source)), expected)

    def test_whole_pdf_copy_uses_the_already_open_source(self):
        with tempfile.TemporaryDirectory() as root:
            original_dir = Path(root, 'original'); original_dir.mkdir()
            private_dir = Path(root, 'private'); private_dir.mkdir()
            alias = Path(root, 'alias')
            def link_to(target):
                if os.name == 'nt':
                    import _winapi
                    _winapi.CreateJunction(str(target), str(alias))
                else:
                    alias.symlink_to(target, target_is_directory=True)
            link_to(original_dir)
            source = alias / 'input.pdf'
            (private_dir / 'input.pdf').write_bytes(b'PRIVATE FILE MUST NOT BE COPIED')
            writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
            with source.open('wb') as output:
                writer.write(output)
            real_reader = PdfReader
            def replace_after_open(stream):
                reader = real_reader(stream)
                if os.name == 'nt':
                    os.rmdir(alias)
                else:
                    alias.unlink()
                link_to(private_dir)
                return reader
            with patch('src.utils.pdf_output.pypdf.PdfReader', side_effect=replace_after_open):
                outputs = write_pdf_output_jobs(str(source), output_dir=root, jobs=[PdfOutputJob('copy.pdf', [0])])
            self.assertTrue(Path(outputs[0]).read_bytes().startswith(b'%PDF-'))

    def test_copy_keeps_exclusive_destination_open_until_write_finishes(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'input.txt'); source.write_bytes(b'normal content')
            out_dir = Path(root, 'out'); out_dir.mkdir()
            private = Path(root, 'private.txt'); private.write_bytes(b'private original')
            engine = RenameEngine()
            engine.set_rules([{"type": "replace", "search": "input", "replace": "copy"}])
            real_remove = os.remove
            def plant_link_after_reservation(path, *args, **kwargs):
                real_remove(path, *args, **kwargs)
                if Path(path).parent == out_dir:
                    os.link(private, path)
            with patch('src.core.rename_engine.os.remove', side_effect=plant_link_after_reservation):
                result = engine.execute_rename([str(source)], output_dir=str(out_dir))
            self.assertEqual(result['successful'], 1)
            self.assertEqual(private.read_bytes(), b'private original')
            self.assertEqual(Path(result['operations'][0]['new_path']).read_bytes(), b'normal content')

    def test_copy_preserves_source_modification_time_without_reopening_destination(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'input.txt'); source.write_bytes(b'normal content')
            os.utime(source, (1600000000, 1600000000))
            engine = RenameEngine(); engine.set_rules([{"type": "replace_text"}])
            result = engine.execute_rename([str(source)])
            self.assertEqual(result['successful'], 1)
            self.assertEqual(Path(result['operations'][0]['new_path']).stat().st_mtime_ns, 1600000000000000000)

    @unittest.skipUnless(os.name == 'nt', 'Windows file attributes')
    def test_copy_preserves_read_only_attribute(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'input.txt'); source.write_bytes(b'normal content'); source.chmod(0o444)
            engine = RenameEngine(); engine.set_rules([{"type": "replace_text"}])
            result = engine.execute_rename([str(source)])
            self.assertEqual(result['successful'], 1)
            self.assertTrue(Path(result['operations'][0]['new_path']).stat().st_file_attributes & 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows file attributes')
    def test_whole_pdf_copy_preserves_read_only_metadata_and_publishes_output(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'input.pdf')
            writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
            with source.open('wb') as output:
                writer.write(output)
            os.utime(source, (1600000000, 1600000000)); source.chmod(0o444)
            outputs = write_pdf_output_jobs(str(source), output_dir=root, jobs=[PdfOutputJob('copy.pdf', [0])])
            target = Path(outputs[0])
            self.assertEqual(len(PdfReader(str(target)).pages), 1)
            self.assertEqual(target.stat().st_mtime_ns, 1600000000000000000)
            self.assertTrue(target.stat().st_file_attributes & 1)

    def test_overwrite_rejects_a_replacement_before_renaming_it(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'input.txt'); source.write_bytes(b'original')
            stat = source.stat()
            identity = {'dev': str(stat.st_dev & 0xFFFFFFFF if os.name == 'nt' else stat.st_dev),
                        'ino': str(stat.st_ino), 'size': str(stat.st_size), 'mtime_ns': str(stat.st_mtime_ns), 'canonical': str(source.resolve())}
            source.rename(source.with_suffix('.old')); source.write_bytes(b'private replacement')
            engine = RenameEngine(); engine.set_rules([{"type": "uniform_name", "base_name": "changed"}])
            with input_context({str(source): identity}):
                result = engine.execute_rename([str(source)], save_method='overwrite')
            self.assertEqual(result['successful'], 0)
            self.assertEqual(source.read_bytes(), b'private replacement')
            self.assertFalse(Path(root, 'changed.txt').exists())

    def test_overwrite_supports_normal_and_case_only_names_without_clobbering(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, 'input.txt'); source.write_bytes(b'normal')
            engine = RenameEngine(); engine.set_rules([{"type": "uniform_name", "base_name": "INPUT"}])
            result = engine.execute_rename([str(source)], save_method='overwrite')
            self.assertEqual(result['successful'], 1, result)
            renamed = Path(result['operations'][0]['new_path'])
            self.assertEqual(renamed.read_bytes(), b'normal')
            engine.set_rules([{"type": "uniform_name", "base_name": "changed"}])
            result = engine.execute_rename([str(renamed)], save_method='overwrite')
            self.assertEqual(result['successful'], 1, result)
            other = Path(root, 'other.txt'); other.write_bytes(b'other')
            result = engine.execute_rename([str(other)], save_method='overwrite')
            self.assertEqual(result['successful'], 0)
            self.assertEqual(Path(root, 'changed.txt').read_bytes(), b'normal')


if __name__ == '__main__':
    unittest.main()
