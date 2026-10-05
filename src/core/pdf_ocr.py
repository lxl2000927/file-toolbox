"""Offline OCR: preserve original pages and overlay only the recognized text."""
from pathlib import Path
import sys
import threading

import pymupdf as fitz

_OCR_LOCK = threading.Lock()
LANGUAGES = {'chi_sim+eng', 'eng', 'chi_sim'}


def tessdata_dir():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    return root / 'assets' / 'tessdata'


def ocr_status():
    root = tessdata_dir()
    available = [lang for lang in ('chi_sim', 'eng') if (root / f'{lang}.traineddata').is_file()]
    return {'available': len(available) == 2, 'languages': available}


def searchable_pdf(source, language, dpi, check, notify, *, text_consumer=None, preview_limit=None):
    from src.core.pdf_tools_engine import render
    if language not in LANGUAGES:
        raise ValueError('请选择简体中文、英文或中英混合')
    if not all((tessdata_dir() / f'{part}.traineddata').is_file() for part in language.split('+')):
        raise ValueError('缺少离线 OCR 语言数据，请运行 scripts/setup_ocr.py 后重启引擎')
    target = fitz.open()
    texts = []
    try:
        target.insert_pdf(source, links=True, annots=True, widgets=True)
        target.set_metadata(source.metadata)
        if source.get_toc():
            target.set_toc(source.get_toc())
        for index, page in enumerate(source):
            check()
            text = page.get_text().strip()
            images = page.get_image_info()
            # A digital header or page number must not hide a scanned body.
            large_images = [image for image in images
                            if fitz.Rect(image['bbox']).get_area() >= page.rect.get_area() * 0.1
                            and min(image['width'], image['height']) > 1]
            if not text or large_images:
                render_dpi = max(150, dpi)
                # Upscaling an already scanned page invents no detail and can
                # worsen Tesseract's character sizing, especially in Chinese.
                for image in large_images:
                    box = fitz.Rect(image['bbox'])
                    if box.width > 0 and box.height > 0:
                        native_dpi = min(image['width'] * 72 / box.width, image['height'] * 72 / box.height)
                        render_dpi = min(render_dpi, max(72, native_dpi))
                pix = render(page, render_dpi)
                # Tessdata never comes from a renderer-supplied path.
                with _OCR_LOCK:
                    check()
                    data = pix.pdfocr_tobytes(language=language, tessdata=str(tessdata_dir()))
                check()
                with fitz.open(stream=data, filetype='pdf') as recognized:
                    ocr_page = recognized[0]
                    if text:
                        # Redact duplicates only from the temporary OCR layer,
                        # with transparent fill so original content stays intact.
                        transform = page.rotation_matrix * page.rect.torect(ocr_page.rect)
                        words = page.get_text('words')
                        for word in words:
                            ocr_page.add_redact_annot(fitz.Rect(word[:4]) * transform,
                                                      fill=False, cross_out=False)
                        if words:
                            ocr_page.apply_redactions(images=0, graphics=0)
                    # Remove only OCR's rendered image; keep the invisible,
                    # Unicode-mapped GlyphLessFont text on the original page.
                    for xref in {image[0] for image in ocr_page.get_images()}:
                        ocr_page.delete_image(xref)
                    destination = target[index]
                    destination.remove_rotation()
                    destination.show_pdf_page(destination.rect, recognized, 0, overlay=True)
                    text = destination.get_text().strip()
            if text_consumer is not None:
                text_consumer(index, text)
            if preview_limit is None or len(texts) < preview_limit:
                texts.append(text if preview_limit is None else text[:4000])
            notify('离线 OCR', index + 1, len(source))
        return target, texts
    except BaseException:
        target.close()
        raise
