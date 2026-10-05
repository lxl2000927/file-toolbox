"""Generate reproducible, synthetic 3000-file / 9000-page acceptance input."""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'acceptance-samples' / 'scale-inputs'


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    # A textured, scanned page makes preview decode and source bytes realistic;
    # every third PDF embeds it once and references it on three pages.
    rng = np.random.default_rng(20261004)
    image = Image.fromarray(rng.integers(150, 256, (1700, 1200, 3), dtype=np.uint8))
    draw = ImageDraw.Draw(image)
    for y in range(90, 1650, 70):
        draw.text((70, y), f'FILE TOOLBOX SYNTHETIC SCAN - ROW {y}', fill='black', font_size=24)
    stream = io.BytesIO(); image.save(stream, 'JPEG', quality=85)
    scan = stream.getvalue()
    sizes = []
    for i in range(3000):
        path = TARGET / f'document-{i + 1:04d}.pdf'
        if not path.exists():
            with fitz.open() as doc:
                xref = 0
                for page_id in range(3):
                    page = doc.new_page(width=595, height=842)
                    if i % 3 == 0:
                        xref = page.insert_image(page.rect, stream=scan, xref=xref)
                    page.insert_text((35, 35), f'DOC {i + 1:04d} PAGE {page_id + 1}', fontsize=12)
                doc.save(path, deflate=True)
        sizes.append(path.stat().st_size)
    report = {'files': 3000, 'pages': 9000, 'scanned_files': 1000, 'bytes_total': sum(sizes),
              'bytes_1000': sum(sizes[:1000]), 'largest_file_bytes': max(sizes), 'synthetic': True}
    (TARGET.parent / 'scale-inputs.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
