"""Exercise the frozen engine from an isolated directory, without source imports."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time

import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def identity(path):
    stat = path.stat()
    return {'dev': str(stat.st_dev & 0xFFFFFFFF if os.name == 'nt' else stat.st_dev),
            'ino': str(stat.st_ino), 'size': str(stat.st_size), 'mtime_ns': str(stat.st_mtime_ns),
            'canonical': str(path.resolve())}


class Engine:
    def __init__(self, executable, directory):
        self.messages, self.pending, self.request_id = queue.Queue(), [], 0
        self.stderr = (directory / 'engine-stderr.log').open('w', encoding='utf-8')
        environment = {**os.environ, 'APPDATA': str(directory), 'LOCALAPPDATA': str(directory),
                       'FILE_TOOLBOX_ENGINE_TOKEN': 'packaged-smoke-token', 'PYTHONIOENCODING': 'utf-8'}
        environment.pop('PYTHONPATH', None); environment.pop('PYTHONHOME', None)
        self.process = subprocess.Popen([str(executable)], cwd=directory, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True,
            encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        def read():
            for line in self.process.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: pass
        self.reader = threading.Thread(target=read, daemon=True); self.reader.start()

    def receive(self, predicate):
        for i, item in enumerate(self.pending):
            if predicate(item): return self.pending.pop(i)
        deadline = time.monotonic() + 120
        while True:
            item = self.messages.get(timeout=max(0.1, deadline - time.monotonic()))
            if predicate(item): return item
            self.pending.append(item)

    def call(self, method, params):
        self.request_id += 1
        request = self.request_id
        self.process.stdin.write(json.dumps({'id': request, 'method': method, 'params': params,
                                            'auth': 'packaged-smoke-token'}) + '\n')
        self.process.stdin.flush()
        reply = self.receive(lambda r: r.get('id') == request)
        if 'error' in reply: raise RuntimeError(f'{method}: {reply["error"]}')
        return reply['result']

    def task(self, method, params):
        task_id = f'smoke-{self.request_id + 1}'
        self.call(method, {**params, 'task_id': task_id})
        payload = self.receive(lambda r: r.get('method') == 'task.complete' and r['params']['task_id'] == task_id)['params']
        if not payload['ok']: raise RuntimeError(f'{method}: {payload}')
        return payload['result']

    def close(self):
        if self.process.poll() is None:
            try: self.call('shutdown', {}); self.process.wait(timeout=15)
            except Exception: self.process.kill(); self.process.wait(timeout=10)
        self.reader.join(timeout=2)
        self.process.stdin.close(); self.process.stdout.close(); self.stderr.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, default=ROOT / 'engine/dist/engine.exe')
    parser.add_argument('--report', type=Path, default=ROOT / 'acceptance-samples/packaged-engine.json')
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix='toolbox-frozen-smoke-') as temporary:
        directory = Path(temporary)
        engine = Engine(args.engine.resolve(), directory)
        try:
            engine.receive(lambda r: r.get('method') == 'ready')
            assert engine.call('ping', {})['pong']; checks.append('frozen engine startup and ping')
            samples = ROOT / 'acceptance-samples'
            front, back, chinese = [samples / name for name in ['01-front.pdf', '02-back-reversed.pdf', '05-chinese-scan.png']]
            def tool(action, files, **options):
                return engine.task('pdf_tools.run', {'action': action, 'files': [str(p) for p in files],
                    'options': {'output_dir': str(directory / 'out'), **options},
                    '_input_identities': {str(p): identity(p) for p in files}})
            inspected = tool('inspect', [front, back, chinese], compact_inspect=True)
            assert inspected['ocr']['available'] and len(inspected['sources']) == 3
            checks.append('packaged PDF/image dependencies and offline Chinese/English data')
            merged = tool('assemble', [front, back], filename='merged')
            with pymupdf.open(merged['output_files'][0]) as document: assert len(document) == 6
            assert len(tool('thumbnails', [front], pages=[{'source': 0, 'index': 0}])['thumbnails']) == 1
            checks.append('real PDF assembly and thumbnail rendering')
            recognized = tool('ocr', [chinese], filename='recognized', ocr_text=True)
            assert '文件工具箱' in ''.join(recognized['recognized_text']).replace(' ', '')
            assert '文件工具箱' in Path(recognized['text_files'][0]).read_text(encoding='utf-8-sig').replace(' ', '')
            with pymupdf.open(recognized['output_files'][0]) as document:
                assert '文件工具箱' in document[0].get_text().replace(' ', '')
            checks.append('offline Chinese OCR searchable PDF and complete TXT')
            compressed = tool('compress', [front], filename='raster', compression='raster', dpi=72, quality=60)
            image = tool('extract_images', [Path(compressed['output_files'][0])], filename='image')
            assert len(image['output_files']) == 3
            checks.append('raster compression and embedded image extraction')
            split = engine.task('pdf_split.execute_async', {'pdf_paths': [str(front)],
                'config': {'mode': 'by_page_count', 'page_count': 1, 'output_dir': str(directory / 'split')},
                '_input_identities': {str(front): identity(front)}})
            assert len(split['output_files']) == 3; checks.append('ordinary PDF split output')
            scan = samples / '03-scan-markers.pdf'
            scanned = engine.task('scan_split.scan_only', {'pdf_path': str(scan), 'reference_image_path': '',
                'options': {'detection_mode': 'stamp', 'dpi': 150}, 'page_limit': 0,
                '_input_identities': {str(scan): identity(scan)}})
            assert scanned['total_pages'] == 6; checks.append('scan engine with native image dependencies')
            renamed = engine.call('rename.execute', {'files': [str(front)], 'rules': [{'type': 'insert_text', 'text': 'copy_'}],
                'save_method': 'copy', 'output_dir': str(directory / 'renamed'), '_input_identities': {str(front): identity(front)}})
            assert renamed['successful'] == 1
            assert not engine.call('rename.undo', {'undo_token': renamed['undo_token']})['failed']
            checks.append('rename copy and undo')
            preset = engine.call('presets.save', {'scope': 'workbench', 'name': 'Smoke', 'settings': {'dpi': 150}})
            assert engine.call('presets.list', {'scope': 'workbench'})[0]['id'] == preset['id']
            assert engine.call('history.get', {'count': 100})['records']
            checks.append('preset persistence and operation history')
        finally: engine.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'passed': True, 'engine': str(args.engine.resolve()), 'checks': checks}, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'checks': checks}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
