"""Bounded local PDF operations shared by the workbench and scan review.

Page references contain only indexes into the authorized input list. No action
reads a path from its options or writes back to an input. Preview signatures are
checked against the exact bytes opened through the existing input guard.
"""
from contextlib import ExitStack
import base64
import io
import math
import os

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageOps

from src.utils.atomic_output import write_new_output
from src.utils.input_guard import read_input_bytes
from src.utils.path_utils import _safe_output_name
from src.utils.pdf_native_lock import PDF_NATIVE_LOCK
from src.core.pdf_sources import PdfSources
from src.core.pdf_text import OcrTextCapture, PREVIEW_PAGES, PREVIEW_CHARS

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILES = 3000
MAX_PAGES = 100000
MAX_SOURCE_PAGES = 10000
MAX_RENDER_PIXELS = 32_000_000
_DOCUMENT_LOCK = PDF_NATIVE_LOCK
ACTIONS = {'inspect', 'thumbnails', 'assemble', 'interleave', 'detect_blank',
           'export_images', 'extract_images', 'compress', 'ocr', 'segments'}


class _PdfOutputStream:
    """Expose file callbacks without .name (PyMuPDF otherwise reopens it)."""
    def __init__(self, stream):
        self.write = stream.write
        self.seek = stream.seek
        self.tell = stream.tell
        self.flush = stream.flush


def integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not minimum <= value <= maximum:
        raise ValueError(f'{label}必须为 {minimum}–{maximum} 的整数')
    return int(value)


def page_refs(options, docs):
    raw = options.get('pages')
    if raw is None:
        raw = []
        for s in range(len(docs)):
            count = docs.page_count(s)
            if len(raw) + count > MAX_PAGES:
                raise ValueError('本次输入总页数超过 100000，请分批处理')
            raw.extend({'source': s, 'index': i, 'rotation': 0} for i in range(count))
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PAGES:
        raise ValueError('请至少保留一页，最多 100000 页')
    result = []
    for page in raw:
        if not isinstance(page, dict):
            raise ValueError('页面引用无效')
        source = integer(page.get('source'), 0, len(docs) - 1, '来源')
        index = integer(page.get('index'), 0, docs.page_count(source) - 1, '页码')
        rotation = integer(page.get('rotation', 0), 0, 270, '旋转角度')
        if rotation % 90:
            raise ValueError('旋转角度必须为 90 的倍数')
        result.append({'source': source, 'index': index, 'rotation': rotation})
    return result


def render(page, dpi=100, *, max_edge=None, grayscale=False):
    scale = dpi / 72
    width, height = page.rect.width, page.rect.height
    if min(width, height) <= 0 or not math.isfinite(width * height):
        raise ValueError('页面尺寸无效')
    if max_edge:
        scale = min(scale, max_edge / max(width, height))
    scale = min(scale, math.sqrt(MAX_RENDER_PIXELS / (width * height)))
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY if grayscale else fitz.csRGB, alpha=False)


def _open_source(path, data, stack):
    if os.path.splitext(path)[1].lower() == '.pdf':
        doc = stack.enter_context(fitz.open(stream=data, filetype='pdf'))
        if doc.needs_pass:
            raise ValueError(f'PDF 已加密，请先解密：{os.path.basename(path)}')
        kind = 'pdf'
    else:
        with Image.open(io.BytesIO(data)) as original:
            if original.width * original.height > MAX_RENDER_PIXELS:
                raise ValueError('图片超过 3200 万像素，请先缩小')
            # All frames of TIFF/GIF become pages; EXIF orientation is applied.
            frame_count = getattr(original, 'n_frames', 1)
            if frame_count > 1000:
                raise ValueError('单张图片文件帧数超过 1000')
            doc = stack.enter_context(fitz.open())
            for frame in range(frame_count):
                original.seek(frame)
                img = ImageOps.exif_transpose(original).convert('RGBA')
                background = Image.new('RGB', img.size, 'white')
                background.paste(img, mask=img.getchannel('A'))
                stream = io.BytesIO()
                background.save(stream, format='PNG')
                page = doc.new_page(width=img.width, height=img.height)
                page.insert_image(page.rect, stream=stream.getvalue())
        kind = 'image'
    if not len(doc) or len(doc) > MAX_SOURCE_PAGES:
        raise ValueError('文件页数须在 1–10000 之间')
    return doc, kind


def run_pdf_tool(action, files, options=None, cancel_check=None, progress=None):
    if action not in ACTIONS:
        raise ValueError('未知 PDF 操作')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES or any(not isinstance(p, str) or not os.path.isabs(p) for p in files):
        raise ValueError('请选择 1–3000 个文件')
    options = options or {}
    if not isinstance(options, dict):
        raise ValueError('选项必须是对象')
    result = {'action': action, 'output_files': [], 'errors': [], 'cancelled': False, 'bytes_before': 0, 'bytes_after': 0}

    def check():
        if cancel_check and cancel_check():
            raise InterruptedError('已取消')

    def notify(phase, current, total):
        if progress:
            progress(phase, current, total)

    # MuPDF documents are confined to one worker. Poll while waiting so queued
    # thumbnail/OCR work can be cancelled without waiting for another document.
    acquired = False
    try:
        while not acquired:
            check()
            acquired = _DOCUMENT_LOCK.acquire(timeout=0.1)
        with PdfSources(files, options.get('signatures'), _open_source, read_input_bytes, MAX_FILE_BYTES, check, result) as docs:
            if action == 'inspect':
                from src.core.pdf_ocr import ocr_status
                result.update(sources=[], ocr=ocr_status())
                total_pages = 0
                for n, path in enumerate(files):
                    check()
                    notify('读取文件', n, len(files))
                    try:
                        source = docs.describe(n, options.get('compact_inspect') is True)
                        if total_pages + source['page_count'] > MAX_PAGES:
                            raise ValueError('本次输入总页数超过 100000，请分批处理')
                        total_pages += source['page_count']
                        result['sources'].append(source)
                    except InterruptedError:
                        raise
                    except Exception as error:
                        if not options.get('partial_inspect'):
                            raise
                        result['errors'].append(f'{os.path.basename(path)}：{error}')
                    notify('已导入文件', n + 1, len(files))
                return result
            refs = page_refs(options, docs)
            input_pages = sum(docs.page_count(i) for i in {ref['source'] for ref in refs})
            result.update(input_pages=input_pages, output_pages=len(refs),
                comparison_eligible=action in {'assemble', 'compress', 'ocr', 'interleave'}
                    and len(refs) == input_pages and len({(ref['source'], ref['index']) for ref in refs}) == input_pages)
            if action == 'thumbnails':
                if len(refs) > 24:
                    raise ValueError('每批缩略图最多 24 页')
                thumbnail_size = integer(options.get('thumbnail_size', 360), 120, 1400, '预览尺寸')
                if thumbnail_size > 360 and len(refs) > 1:
                    raise ValueError('高清预览每次只处理一页')
                thumbnails = []
                for n, ref in enumerate(refs):
                    check()
                    page = docs[ref['source']][ref['index']]
                    pix = render(page, 200 if thumbnail_size > 360 else 100, max_edge=thumbnail_size)
                    thumbnails.append({**ref, 'data_url': 'data:image/jpeg;base64,' + base64.b64encode(pix.tobytes('jpeg', jpg_quality=72)).decode('ascii')})
                    notify('生成缩略图', n + 1, len(refs))
                return {**result, 'thumbnails': thumbnails}
            if action == 'detect_blank':
                candidates = []
                for n, ref in enumerate(refs):
                    check()
                    page = docs[ref['source']][ref['index']]
                    pix = render(page, 100, max_edge=1200, grayscale=True)
                    gray = np.frombuffer(pix.samples, dtype=np.uint8)
                    ink = float(np.mean(gray < 220))
                    mean = float(np.mean(gray))
                    # Absolute brightness rejects constant black/gray pages.
                    # Existing text/annotations are conservative vetoes.
                    if mean > 242 and ink < 0.0008 and not page.get_text().strip() and not page.first_annot and not page.first_widget:
                        candidates.append({**ref, 'ink_ratio': round(ink, 6), 'brightness': round(mean, 2)})
                    notify('检查空白页', n + 1, len(refs))
                return {**result, 'candidates': candidates, 'checked': len(refs)}

            output_dir = options.get('output_dir')
            if not isinstance(output_dir, str) or not os.path.isabs(output_dir):
                raise ValueError('请选择输出文件夹')
            name = _safe_output_name(options.get('filename', '处理结果'), '处理结果', require_pdf=False)
            if name.lower().endswith('.pdf'):
                name = name[:-4]
            name = name[:100]
            dpi = integer(options.get('dpi', 150), 72, 400, 'DPI')
            quality = integer(options.get('quality', 80), 30, 100, '图片质量')
            compression = options.get('compression', 'lossless' if action == 'compress' else 'none')
            if compression not in ('none', 'lossless', 'raster'):
                raise ValueError('未知压缩模式')

            def publish(filename, data, total):
                check()
                path = write_new_output(output_dir, filename, lambda f: f.write(data), cancel_check)
                result['output_files'].append(path)
                result['bytes_after'] += len(data)
                notify('已输出', len(result['output_files']), total)

            def finish_pdf(doc, filename, total):
                with ExitStack() as processing_stack:
                    check()
                    processed = doc
                    capture = processing_stack.enter_context(OcrTextCapture(options.get('ocr_text') is True,
                        PREVIEW_PAGES - len(result.get('ocr_pages', []))))
                    wants_ocr = options.get('ocr') or action == 'ocr'
                    def recognize(document):
                        from src.core.pdf_ocr import searchable_pdf
                        recognized, _ = searchable_pdf(document, options.get('language', 'chi_sim+eng'), dpi, check, notify,
                            text_consumer=capture.accept, preview_limit=0)
                        return processing_stack.enter_context(recognized)
                    if compression != 'raster' and (options.get('ocr') or action == 'ocr'):
                        processed = recognize(doc)
                    if compression == 'raster':
                        raster = processing_stack.enter_context(fitz.open())
                        for i, page in enumerate(processed):
                            check()
                            pix = render(page, dpi)
                            target = raster.new_page(width=page.rect.width, height=page.rect.height)
                            target.insert_image(target.rect, stream=pix.tobytes('jpeg', jpg_quality=quality))
                            notify('压缩页面', i + 1, len(processed))
                        if options.get('ocr') or action == 'ocr':
                            # OCR must run after rasterization; never silently drop
                            # the searchable layer selected by the user.
                            raster = recognize(raster)
                        processed = raster
                    # Write directly to the atomic output stream; avoid holding
                    # a second, serialized copy of a large merged PDF in RAM.
                    path = write_new_output(output_dir, filename + '.pdf',
                        lambda stream: processed.save(_PdfOutputStream(stream), garbage=4 if compression != 'none' else 2, deflate=True), cancel_check)
                    result['output_files'].append(path)
                    result['bytes_after'] += os.path.getsize(path)
                    if wants_ocr:
                        result.setdefault('ocr_pages', []).extend({**page, 'output_file': path} for page in capture.pages)
                        result.setdefault('recognized_text', []).extend(page['text'] for page in capture.pages)
                        result['ocr_total_pages'] = result.get('ocr_total_pages', 0) + capture.total
                        result.update(ocr_preview_limit=PREVIEW_PAGES, ocr_preview_chars=PREVIEW_CHARS)
                        if capture.stream:
                            text_name = os.path.splitext(os.path.basename(path))[0] + '_文字.txt'
                            text_path = write_new_output(output_dir, text_name, lambda stream: capture.write_to(stream, check), cancel_check)
                            result.setdefault('text_files', []).append(text_path)
                    notify('已输出', len(result['output_files']), total)

            if action == 'export_images':
                image_format = options.get('image_format', 'png')
                if image_format not in ('png', 'jpeg'):
                    raise ValueError('图片格式须为 PNG 或 JPEG')
                for n, ref in enumerate(refs):
                    check()
                    page = docs[ref['source']][ref['index']]
                    old_rotation = page.rotation
                    try:
                        page.set_rotation((old_rotation + ref['rotation']) % 360)
                        pix = render(page, dpi)
                    finally:
                        page.set_rotation(old_rotation)
                    data = pix.tobytes('jpeg', jpg_quality=quality) if image_format == 'jpeg' else pix.tobytes('png')
                    publish(f'{name}_{n + 1:04d}.{"jpg" if image_format == "jpeg" else "png"}', data, len(refs))
            elif action == 'extract_images':
                seen = set()
                for n, ref in enumerate(refs):
                    check()
                    doc = docs[ref['source']]
                    for info in doc[ref['index']].get_images(full=True):
                        check()
                        key = (ref['source'], info[0])
                        if key in seen:
                            continue
                        seen.add(key)
                        extracted = doc.extract_image(info[0])
                        if extracted:
                            publish(f'{name}_s{ref["source"] + 1}_p{ref["index"] + 1}_{info[0]}.{extracted["ext"]}', extracted['image'], len(refs))
                    notify('提取原始图片', n + 1, len(refs))
                if not result['output_files']:
                    result['warnings'] = ['所选页面没有嵌入图片；矢量内容请使用“导出页面图片”。']
            else:
                groups = [refs]
                if action == 'interleave':
                    if len(docs) != 2:
                        raise ValueError('正反面交错合并需要恰好两个文件')
                    front = [r for r in refs if r['source'] == 0]
                    back = [r for r in refs if r['source'] == 1]
                    if options.get('reverse_back'):
                        back.reverse()
                    groups = [[r for i in range(max(len(front), len(back))) for r in (front[i:i + 1] + back[i:i + 1])]]
                    if len(front) != len(back):
                        result['warnings'] = ['正反面页数不同，多出的页面已保留在末尾。']
                elif action == 'segments':
                    raw_groups = options.get('segments')
                    if len(docs) != 1 or not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= MAX_PAGES:
                        raise ValueError('复核分段无效')
                    groups = [page_refs({'pages': [{'source': 0, 'index': i} for i in group]}, docs) for group in raw_groups if isinstance(group, list)]
                    if len(groups) != len(raw_groups) or sum(len(g) for g in groups) > MAX_PAGES:
                        raise ValueError('复核页码无效')
                    all_indexes = [r['index'] for g in groups for r in g]
                    if len(all_indexes) != len(set(all_indexes)):
                        raise ValueError('分段页码重复，请重新复核')
                for n, group in enumerate(groups):
                    check()
                    with fitz.open() as output:
                        # Copy whole source page trees first so internal links
                        # and form relationships survive. select() remaps page
                        # references after sorting/deleting/extracting pages.
                        offsets = {}
                        source_indexes = {}
                        for ref in group:
                            source_indexes.setdefault(ref['source'], []).append(ref['index'])
                        source_metadata, source_bookmarks = {}, {}
                        for source_id, indices in source_indexes.items():
                            check()
                            first_page, last_page = min(indices), max(indices)
                            offsets[source_id] = len(output) - first_page
                            source = docs[source_id]
                            source_metadata[source_id] = source.metadata
                            source_bookmarks[source_id] = source.get_toc()
                            output.insert_pdf(source, from_page=first_page, to_page=last_page, links=True, annots=True, widgets=True)
                            notify('合并来源文件', len(offsets), len(source_indexes))
                        chosen = [offsets[ref['source']] + ref['index'] for ref in group]
                        if chosen != list(range(len(output))):
                            output.select(chosen)
                        for i, ref in enumerate(group):
                            check()
                            page = output[i]
                            page.set_rotation((page.rotation + ref['rotation']) % 360)
                            notify('整理页面', i + 1, len(group))
                        output.set_metadata(source_metadata[group[0]['source']] if len(offsets) == 1 else {'producer': 'File Toolbox', 'title': name})
                        positions = {}
                        for i, ref in enumerate(group):
                            positions.setdefault((ref['source'], ref['index']), i + 1)
                        bookmarks = []
                        for source_id in offsets:
                            for level, title, old_page in source_bookmarks[source_id]:
                                new_page = positions.get((source_id, old_page - 1))
                                if new_page:
                                    level = min(level, bookmarks[-1][0] + 1 if bookmarks else 1)
                                    bookmarks.append([level, title, new_page])
                        if bookmarks:
                            output.set_toc(bookmarks)
                        finish_pdf(output, f'{name}_{n + 1:03d}' if action == 'segments' else name, len(groups))
            check()
            return result
    except InterruptedError:
        return {**result, 'cancelled': True}
    except Exception as error:
        if not result['output_files']:
            raise
        return {**result, 'errors': [str(error)]}
    finally:
        if acquired:
            _DOCUMENT_LOCK.release()
