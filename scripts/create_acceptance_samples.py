"""Reproducible, synthetic acceptance documents; never reads user documents."""
from pathlib import Path
import io
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
from src.core.pdf_tools_engine import run_pdf_tool
from src.core.pdf_scan_split_engine import PdfScanSplitEngine, PdfScanSplitOptions


def create_samples(root):
    root.mkdir(parents=True, exist_ok=True)
    for filename, labels in [('01-front.pdf', ['FRONT ONE', 'FRONT TWO', 'FRONT THREE']),
                             ('02-back-reversed.pdf', ['BACK THREE', 'BACK TWO', 'BACK ONE'])]:
        with fitz.open() as doc:
            for index, label in enumerate(labels):
                page = doc.new_page(width=420, height=595)
                page.draw_rect(fitz.Rect(0, 0, 420, 105), fill=(0.87, 0.93, 0.99), color=None)
                page.insert_text((34, 55), label, fontsize=24, color=(0.1, 0.3, 0.5))
                page.insert_text((34, 135), 'File Toolbox / Acceptance 2026-10-05', fontsize=12)
                page.insert_text((34, 185), f'Example document {index + 1}', fontsize=18)
                for y in range(235, 420, 26):
                    page.draw_line((34, y), (375, y), color=(0.85, 0.88, 0.92), width=1)
            doc.set_toc([[1, 'Part 1', 1], [1, 'Part 3', 3]])
            doc[0].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(20, 20, 80, 70), 'page': 2})
            doc.save(root / filename)
    with fitz.open() as doc:
        for n in range(6):
            page = doc.new_page(width=420, height=595)
            if n in (0, 2, 4):
                page.draw_circle((315, 85), 34, color=(0.85, 0, 0), width=5)
            page.insert_text((30, 60), f'SCANNED BATCH / PAGE {n + 1}', fontsize=16)
            page.insert_text((30, 175), 'Review marker pages, merge or add boundaries.', fontsize=12)
        doc.save(root / '03-scan-markers.pdf')
    with fitz.open() as doc:
        doc.new_page(width=420, height=595).insert_text((30, 70), 'KEEP THIS PAGE', fontsize=22)
        doc.new_page(width=420, height=595)
        page = doc.new_page(width=420, height=595)
        page.draw_rect(page.rect, fill=(0, 0, 0), color=None)
        doc.new_page(width=420, height=595).insert_text((30, 70), 'Small text must remain', fontsize=9)
        doc.save(root / '04-blank-and-black.pdf')
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    if not font_path.exists():
        raise RuntimeError('Chinese acceptance fixture requires Microsoft YaHei font on Windows')
    image = Image.new('RGB', (1400, 650), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 50)
    for y, line in [(80, '文件工具箱 测试发票'), (200, 'Invoice 2026 12345678'), (320, '日期 2026 年 10 月 5 日')]:
        draw.text((80, y), line, font=font, fill='black')
    image.save(root / '05-chinese-scan.png')
    with fitz.open() as doc:
        page = doc.new_page(width=700, height=325)
        page.insert_image(page.rect, filename=str(root / '05-chinese-scan.png'))
        doc.save(root / '06-chinese-scan.pdf')
    with fitz.open() as doc:
        page = doc.new_page(width=700, height=400)
        page.draw_rect(fitz.Rect(0, 0, 700, 60), color=None, fill=(0.87, 0.93, 0.99))
        page.insert_text((40, 38), 'ARCHIVE HEADER / PAGE 1', fontsize=18)
        page.insert_image(fitz.Rect(0, 70, 700, 395), filename=str(root / '05-chinese-scan.png'))
        doc.save(root / '07-hybrid-scan.pdf')


def validate(root):
    output = root / 'automated-results'
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file() and p.suffix.lower() in ('.pdf', '.png')}
    checks = []
    def run(label, action, names, **options):
        result = run_pdf_tool(action, [str(root / name) for name in names], {'output_dir': str(output), 'filename': label, **options})
        assert not result['errors'] and not result['cancelled'], result
        checks.append({'check': label, 'outputs': result['output_files'], 'before': result['bytes_before'], 'after': result['bytes_after']})
        return result
    r = run('01-reorder-rotate', 'assemble', ['01-front.pdf'], pages=[{'source': 0, 'index': 2, 'rotation': 90}, {'source': 0, 'index': 0}])
    with fitz.open(r['output_files'][0]) as doc:
        assert len(doc) == 2 and doc[0].rotation == 90 and 'THREE' in doc[0].get_text()
    r = run('02-interleave', 'interleave', ['01-front.pdf', '02-back-reversed.pdf'], reverse_back=True)
    with fitz.open(r['output_files'][0]) as doc:
        assert len(doc) == 6 and 'BACK ONE' in doc[1].get_text() and 'BACK THREE' in doc[5].get_text()
    r = run('03-blank-review', 'detect_blank', ['04-blank-and-black.pdf'])
    assert [p['index'] for p in r['candidates']] == [1]
    run('04-image-to-pdf', 'assemble', ['05-chinese-scan.png'])
    run('05-pdf-to-image', 'export_images', ['01-front.pdf'], dpi=96)
    run('06-extract-original', 'extract_images', ['06-chinese-scan.pdf'])
    run('07-lossless', 'compress', ['01-front.pdf'], compression='lossless')
    run('08-raster', 'compress', ['06-chinese-scan.pdf'], compression='raster', dpi=100, quality=55)
    r = run('09-offline-ocr', 'ocr', ['06-chinese-scan.pdf'], language='chi_sim+eng', dpi=200)
    with fitz.open(r['output_files'][0]) as doc:
        text = ''.join(doc[0].get_text().split())
        assert '文件工具箱' in text and '12345678' in text, text
    scan_options = PdfScanSplitOptions(detection_mode='stamp', dpi=180)
    scan = PdfScanSplitEngine.scan_only(str(root / '03-scan-markers.pdf'), '', scan_options)
    assert scan.marker_pages == [0, 2, 4], scan
    segments = PdfScanSplitEngine.build_segments(scan.total_pages, scan.marker_pages, scan_options)
    # Simulate a reviewer merging first two detected segments before exporting.
    run('10-reviewed-segments', 'segments', ['03-scan-markers.pdf'], segments=[segments[0] + segments[1], segments[2]], compression='lossless')
    r = run('11-hybrid-ocr', 'ocr', ['07-hybrid-scan.pdf'], language='chi_sim+eng', dpi=200)
    with fitz.open(r['output_files'][0]) as doc, fitz.open(root / '07-hybrid-scan.pdf') as source:
        text = ''.join(doc[0].get_text().split())
        assert text.count('ARCHIVEHEADER') == 1 and '文件工具箱' in text, text
        assert doc[0].get_pixmap().samples == source[0].get_pixmap().samples
    assert all((root / name).read_bytes() == data for name, data in before.items())
    (root / 'automated-report.json').write_text(json.dumps({'checks': checks, 'source_files_unchanged': True}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS: {len(checks)} acceptance operations; original files unchanged. {root}')


if __name__ == '__main__':
    root = ROOT / 'acceptance-samples'
    create_samples(root)
    validate(root)
