"""Bounded OCR previews and an optional disk-backed complete text stream."""
from tempfile import SpooledTemporaryFile

PREVIEW_PAGES = 20
PREVIEW_CHARS = 4000


class OcrTextCapture:
    def __init__(self, full_text=False, preview_limit=PREVIEW_PAGES):
        self.preview_limit = max(0, preview_limit)
        self.pages, self.total = [], 0
        self.stream = SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b') if full_text else None
        if self.stream: self.stream.write(b'\xef\xbb\xbf')

    def __enter__(self): return self

    def __exit__(self, *args):
        if self.stream: self.stream.close()

    def accept(self, index, text):
        self.total += 1
        if len(self.pages) < self.preview_limit:
            self.pages.append({'page': index + 1, 'text': text[:PREVIEW_CHARS], 'truncated': len(text) > PREVIEW_CHARS})
        if self.stream:
            self.stream.write(f'\n===== 第 {index + 1} 页 =====\n{text}\n'.encode('utf-8'))

    def write_to(self, destination, check):
        self.stream.seek(0)
        while chunk := self.stream.read(1024 * 1024):
            check()
            destination.write(chunk)
