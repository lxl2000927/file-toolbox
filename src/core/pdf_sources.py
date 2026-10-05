"""One live source document per operation; metadata survives document eviction."""
import hashlib
import os
from contextlib import ExitStack


class PdfSources:
    def __init__(self, files, signatures, opener, reader, max_bytes, check, result):
        if signatures is not None and (not isinstance(signatures, list) or len(signatures) != len(files)):
            raise ValueError('文件快照无效，请重新载入')
        self.files, self.signatures = files, signatures
        self.opener, self.reader, self.max_bytes = opener, reader, max_bytes
        self.check, self.result = check, result
        self.info = {}
        self._page_bytes = {}
        self._stack, self._index, self._document = ExitStack(), None, None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stack.close()

    def __len__(self):
        return len(self.files)

    def __getitem__(self, index):
        if not 0 <= index < len(self):
            raise IndexError(index)
        self.check()
        if index == self._index:
            return self._document
        # Close before reading the next file: peak source memory never scales
        # with the number of imported files. Documents never cross workers.
        self._stack.close()
        self._stack = ExitStack()
        self._index, self._document = None, None
        path = self.files[index]
        data = self.reader(path, self.max_bytes)
        signature = hashlib.sha256(data).hexdigest()
        expected = self.signatures[index] if self.signatures is not None else self.info.get(index, {}).get('signature')
        if expected is not None and expected != signature:
            raise ValueError('输入文件已发生变化，请重新载入后复核')
        doc, kind = self.opener(path, data, self._stack, self.check)
        if index not in self.info:
            self.result['bytes_before'] += len(data)
        self.info[index] = {'path': path, 'name': os.path.basename(path), 'kind': kind,
                            'page_count': len(doc), 'signature': signature, 'size': len(data)}
        self._page_bytes[index] = getattr(doc, '_toolbox_page_bytes', None)
        self._index, self._document = index, doc
        return doc

    def page_count(self, index):
        if index not in self.info:
            self[index]
        return self.info[index]['page_count']

    def memory_size(self, index):
        return max(self.info[index]['size'], sum(self._page_bytes.get(index) or []))

    def selected_bytes(self, index, pages):
        page_bytes = self._page_bytes.get(index)
        if page_bytes is not None:
            return sum(page_bytes[page] for page in pages)
        return self.info[index]['size'] * len(pages) / self.info[index]['page_count']

    def describe(self, index, compact=False):
        doc = self[index]
        pages = []
        if not compact:
            for i, page in enumerate(doc):
                self.check()
                pages.append({'index': i, 'width': page.rect.width, 'height': page.rect.height, 'rotation': page.rotation})
        return {**self.info[index], 'pages': pages}
