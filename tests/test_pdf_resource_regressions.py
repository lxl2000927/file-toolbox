import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image

from src.core import pdf_tools_engine as engine
from src.utils import output_resources


class PdfResourceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def frames(self, sizes):
        path = self.root / 'frames.tiff'
        pictures = [Image.new('RGB', size, (index * 31 % 256, 40, 80)) for index, size in enumerate(sizes)]
        try:
            pictures[0].save(path, save_all=True, append_images=pictures[1:], compression='tiff_deflate')
        finally:
            for picture in pictures:
                picture.close()
        return str(path)

    def test_compressed_multiframe_budget_is_checked_before_conversion_even_for_inspect(self):
        path = self.frames([(1200, 1200)] * 12)
        converted = []
        original = engine.ImageOps.exif_transpose

        def observe(picture, *args, **kwargs):
            converted.append(picture.tell())
            return original(picture, *args, **kwargs)

        # The larger budget admits one frame but cannot hold all twelve.
        for available in (38, 170):
            for action in ('inspect', 'assemble'):
                with self.subTest(action=action, available=available), patch.object(output_resources, 'available_memory_bytes', return_value=available * 1024**2), patch.object(engine.ImageOps, 'exif_transpose', observe):
                    with self.assertRaisesRegex(ValueError, '图片导入.*内存.*缩小图片.*拆分多帧'):
                        engine.run_pdf_tool(action, [path], {'output_dir': str(self.root / 'out')})
                    self.assertEqual(converted, [], 'resource rejection must precede image conversion')
        self.assertFalse(list(self.root.glob('out/*.pdf')))

    def test_image_output_budget_counts_decoded_selected_occurrences(self):
        path = self.frames([(600, 600)] * 4)
        with patch.object(output_resources, 'available_memory_bytes', return_value=96 * 1024**2):
            inspected = engine.run_pdf_tool('inspect', [path])
            self.assertEqual(inspected['sources'][0]['page_count'], 4)
            with self.assertRaisesRegex(ValueError, '本输出单元.*内存'):
                engine.run_pdf_tool('assemble', [path], {'output_dir': str(self.root / 'out'),
                    'pages': [{'source': 0, 'index': 0}] * 12})
        self.assertFalse(list(self.root.glob('out/*.pdf')))

    def test_later_image_frame_obeys_pixel_limit_before_any_conversion(self):
        path = self.frames([(50, 50), (200, 200)])
        with patch.object(engine, 'MAX_RENDER_PIXELS', 20000):
            with self.assertRaisesRegex(ValueError, '像素'):
                engine.run_pdf_tool('inspect', [path])

    def test_image_import_honors_cancellation_between_frames(self):
        path = self.frames([(160, 120)] * 12)
        converted, stop = [], False
        original = engine.ImageOps.exif_transpose

        def observe(picture, *args, **kwargs):
            nonlocal stop
            converted.append(picture.tell())
            answer = original(picture, *args, **kwargs)
            stop = True
            return answer

        with patch.object(engine.ImageOps, 'exif_transpose', observe):
            result = engine.run_pdf_tool('inspect', [path], cancel_check=lambda: stop)
        self.assertTrue(result['cancelled'])
        self.assertEqual(set(converted), {0})
        self.assertEqual(result['sources'], [])

    def test_sparse_selection_budget_and_import_are_based_on_selected_pages(self):
        path = self.root / 'many.pdf'
        with fitz.open() as document:
            for index in range(1000):
                document.new_page(width=120, height=160).insert_text((10, 20), f'PAGE-{index}')
            document.save(path)
        imported = []
        original = fitz.Document.insert_pdf

        def observe(document, source, **kwargs):
            before = len(document)
            answer = original(document, source, **kwargs)
            imported.append(len(document) - before)
            return answer

        with patch.object(output_resources, 'available_memory_bytes', return_value=38 * 1024**2), patch.object(fitz.Document, 'insert_pdf', observe):
            result = engine.run_pdf_tool('assemble', [str(path)], {
                'output_dir': str(self.root / 'out'), 'pages': [{'source': 0, 'index': 0}, {'source': 0, 'index': 999}]})
        with fitz.open(result['output_files'][0]) as document:
            self.assertEqual([page.get_text().strip() for page in document], ['PAGE-0', 'PAGE-999'])
        self.assertEqual(sum(imported), 2)

    def test_sparse_reordering_keeps_links_forms_duplicates_and_independent_rotations(self):
        path = self.root / 'links.pdf'
        with fitz.open() as document:
            for index in range(6):
                document.new_page(width=240, height=320).insert_text((20, 50), f'PAGE-{index}')
            document[0].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(20, 70, 100, 90), 'page': 5, 'to': fitz.Point(20, 50)})
            document[0].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(20, 100, 100, 120), 'page': 2})
            document[0].insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(20, 130, 100, 150), 'uri': 'https://example.org/'})
            document[5].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(20, 70, 100, 90), 'page': 0})
            widget = fitz.Widget()
            widget.field_name, widget.field_value = 'quantity', 'confirmed'
            widget.field_type, widget.rect = fitz.PDF_WIDGET_TYPE_TEXT, fitz.Rect(20, 180, 160, 210)
            document[0].add_widget(widget)
            document.set_toc([[1, 'Last page', 6]])
            document.save(path)
        result = engine.run_pdf_tool('assemble', [str(path)], {'output_dir': str(self.root / 'out'), 'pages': [
            {'source': 0, 'index': 5, 'rotation': 90}, {'source': 0, 'index': 0},
            {'source': 0, 'index': 0, 'rotation': 180}, {'source': 0, 'index': 5, 'rotation': 270}]})
        with fitz.open(result['output_files'][0]) as document:
            self.assertEqual([page.rotation for page in document], [90, 0, 180, 270])
            self.assertEqual([page.get_text().splitlines()[0] for page in document], ['PAGE-5', 'PAGE-0', 'PAGE-0', 'PAGE-5'])
            self.assertEqual(document.get_toc(), [[1, 'Last page', 1]])
            for page_index in (1, 2):
                links = document[page_index].get_links()
                self.assertEqual([link['page'] for link in links if link['kind'] == fitz.LINK_GOTO], [0])
                self.assertEqual([link['uri'] for link in links if link['kind'] == fitz.LINK_URI], ['https://example.org/'])
                self.assertEqual([field.field_value for field in document[page_index].widgets()], ['confirmed'])
            for page_index in (0, 3):
                self.assertEqual(document[page_index].get_links()[0]['page'], 1)

    def test_sparse_links_keep_coordinates_on_rotated_cropped_sources(self):
        path = self.root / 'rotated-links.pdf'
        with fitz.open() as document:
            for index in range(6):
                page = document.new_page(width=400, height=500)
                page.set_cropbox(fitz.Rect(40, 60, 360, 450))
                page.insert_text((20, 50), f'PAGE-{index}')
            document[0].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(20, 70, 100, 90),
                                     'page': 5, 'to': fitz.Point(30, 110)})
            document[0].set_rotation(90)
            document[5].set_rotation(180)
            document.save(path)
        result = engine.run_pdf_tool('assemble', [str(path)], {'output_dir': str(self.root / 'out'), 'pages': [
            {'source': 0, 'index': 5, 'rotation': 90}, {'source': 0, 'index': 0, 'rotation': 180}]})
        with fitz.open(result['output_files'][0]) as document:
            link = document[1].get_links()[0]
            self.assertEqual(link['page'], 0)
            self.assertEqual(link['from'] * document[1].derotation_matrix, fitz.Rect(20, 70, 100, 90))
            self.assertEqual(link['to'] * document[0].derotation_matrix, fitz.Point(30, 110))

    def test_incremental_checkpoint_callback_receives_only_committed_unit(self):
        path = self.root / 'output.pdf'
        path.write_bytes(b'complete')
        events = []

        class Callback:
            def commit_unit(self, operation, key, unit):
                events.append(('commit', operation, key, unit))

            def update_unit(self, operation, key, unit):
                events.append(('update', operation, key, unit))

        result = {}
        journal = output_resources.OutputCheckpoint('test', {}, [], self.root, None, Callback(), lambda: None, result)
        journal.commit('pdf:0', 'pdf', str(path), False)
        journal.commit('pdf:0', 'text', str(path), True)
        journal.commit('pdf:1', 'pdf', str(path), True)
        self.assertEqual([event[0:1] + event[2:3] for event in events], [('commit', 'pdf:0'), ('update', 'pdf:0'), ('commit', 'pdf:1')])
        self.assertEqual(set(events[0][3]['files']), {'pdf'})
        self.assertFalse(events[0][3]['complete'])
        events[0][3]['files'].clear()
        self.assertEqual(set(result['checkpoint']['units']['pdf:0']['files']), {'pdf', 'text'})
        self.assertEqual(result['checkpoint']['version'], 1)
        self.assertEqual(set(result['checkpoint']['units']), {'pdf:0', 'pdf:1'})

    def test_checkpoint_without_callback_does_not_copy_historical_units(self):
        path = self.root / 'output.pdf'
        path.write_bytes(b'complete')
        journal = output_resources.OutputCheckpoint('test', {}, [], self.root, None, None, lambda: None, {})
        journal.commit('pdf:0', 'pdf', str(path), True)

        class ImmutablePriorUnit(dict):
            def __deepcopy__(self, memo):
                raise AssertionError('commit traversed an unrelated completed unit')

        journal.state['units']['pdf:0'] = ImmutablePriorUnit(journal.state['units']['pdf:0'])
        journal.commit('pdf:1', 'pdf', str(path), True)
        self.assertEqual(set(journal.state['units']), {'pdf:0', 'pdf:1'})

    def test_plain_checkpoint_callbacks_keep_independent_v1_snapshots(self):
        path = self.root / 'output.pdf'
        path.write_bytes(b'complete')
        snapshots = []
        journal = output_resources.OutputCheckpoint('test', {}, [], self.root, None, snapshots.append, lambda: None, {})
        journal.commit('pdf:0', 'pdf', str(path), False)
        journal.commit('pdf:0', 'text', str(path), True)
        self.assertEqual(set(snapshots[0]['units']['pdf:0']['files']), {'pdf'})
        self.assertEqual(set(snapshots[1]['units']['pdf:0']['files']), {'pdf', 'text'})
        self.assertEqual(snapshots[1], copy.deepcopy(journal.state))


if __name__ == '__main__':
    unittest.main()
