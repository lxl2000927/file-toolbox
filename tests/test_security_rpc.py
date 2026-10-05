import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

from pypdf import PdfWriter


class SecurityRpcTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="toolbox-rpc-")
        self.root = Path(self.directory.name)
        self.source = self.root / 'selected.pdf'
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        with self.source.open('wb') as output:
            writer.write(output)
        # Capture exactly the host's JSON bigint representation, exercising
        # Node/Python Windows device-ID compatibility rather than assuming it.
        self.identity = json.loads(subprocess.check_output([
            'node', '-e', "const fs=require('fs'), s=fs.statSync(process.argv[1],{bigint:true}); console.log(JSON.stringify({dev:String(s.dev),ino:String(s.ino),size:String(s.size),mtime_ns:String(s.mtimeNs),canonical:fs.realpathSync(process.argv[1])}))", str(self.source)
        ], text=True))
        env = {**os.environ, 'FILE_TOOLBOX_ENGINE_TOKEN': 'regression-test-token',
               'APPDATA': str(self.root), 'LOCALAPPDATA': str(self.root), 'PYTHONIOENCODING': 'utf-8'}
        # A closed semaphore makes the real route queue deterministic. The
        # test-only route releases it; neither hook is present in production.
        script = """
import threading
from engine import server
server._TASK_SEMAPHORE = threading.Semaphore(0)
def release(params):
    server._TASK_SEMAPHORE.release()
    server._try_start_queued()
    return {}
server.ROUTES['test.release'] = release
server.main()
"""
        script = script.replace("server.main()", getattr(self, "server_setup", "") + "\nserver.main()")
        self.proc = subprocess.Popen([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        self.messages = queue.Queue()
        self.pending = []
        def read():
            for line in self.proc.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    pass
        self.reader = threading.Thread(target=read, daemon=True); self.reader.start()
        self.next_id = 0
        self.receive(lambda value: value.get('method') == 'ready')

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(timeout=10)
        self.reader.join(timeout=2)
        self.proc.stdin.close(); self.proc.stdout.close()
        self.directory.cleanup()

    def receive(self, predicate):
        for index, value in enumerate(self.pending):
            if predicate(value):
                return self.pending.pop(index)
        while True:
            value = self.messages.get(timeout=15)
            if predicate(value):
                return value
            self.pending.append(value)

    def call(self, method, params):
        self.next_id += 1
        self.proc.stdin.write(json.dumps({'id': self.next_id, 'method': method, 'params': params, 'auth': 'regression-test-token'}) + '\n')
        self.proc.stdin.flush()
        return self.receive(lambda value: value.get('id') == self.next_id)

    def params(self):
        return {'pdf_paths': [str(self.source)], 'config': {},
                '_input_identities': {str(self.source): self.identity}}

    def test_backend_rejects_path_coercions_and_missing_host_metadata(self):
        for malformed in [{str(self.source): True}, str(self.source), [[str(self.source)]], [False]]:
            params = self.params(); params['pdf_paths'] = malformed
            self.assertIn('error', self.call('pdf_split.preview_many', params))
        for output in [True, False, 0, 42, [], {}]:
            params = self.params(); params['config'] = {'output_dir': output}
            self.assertIn('error', self.call('pdf_split.preview_many', params))
        self.assertIn('error', self.call('pdf_split.preview_many', {'pdf_paths': [str(self.source)]}))
        self.assertTrue(self.call('pdf_split.preview_many', self.params())['result']['plans'][str(self.source)]['valid'])

    def test_queued_replaced_input_is_rejected_before_read_or_output(self):
        params = self.params(); params['task_id'] = 'replacement'
        self.assertTrue(self.call('pdf_split.execute_async', params)['result']['queued'])
        self.source.rename(self.source.with_suffix('.old'))
        self.source.write_bytes(b'PRIVATE FILE')
        self.call('test.release', {})
        event = self.receive(lambda value: value.get('method') == 'task.complete')['params']
        self.assertEqual(event['result']['successful'], 0)
        self.assertEqual(event['result']['output_files'], [])
        self.assertIn('发生变化', ' '.join(event['result']['errors']))

    def test_queued_original_input_still_produces_valid_output(self):
        params = self.params(); params['task_id'] = 'ordinary'
        self.assertTrue(self.call('pdf_split.execute_async', params)['result']['queued'])
        self.call('test.release', {})
        event = self.receive(lambda value: value.get('method') == 'task.complete')['params']
        self.assertEqual(event['result']['successful'], 1)
        output = event['result']['output_files'][0]
        self.assertTrue(Path(output).read_bytes().startswith(b'%PDF-'))

    def test_reference_pdf_rejection_returns_an_error_and_engine_stays_usable(self):
        huge = self.root / 'huge.pdf'
        writer = PdfWriter(); writer.add_blank_page(width=14000, height=14000)
        with huge.open('wb') as output:
            writer.write(output)
        stat = huge.stat()
        identity = {'dev': str(stat.st_dev & 0xFFFFFFFF if os.name == 'nt' else stat.st_dev),
                    'ino': str(stat.st_ino), 'size': str(stat.st_size), 'mtime_ns': str(stat.st_mtime_ns), 'canonical': str(huge.resolve())}
        rejected = self.call('scan_split.preview_reference', {'reference_image_path': str(huge), '_input_identities': {str(huge): identity}})
        self.assertFalse(rejected['result']['ok'])
        self.assertIn('尺寸', rejected['result']['error'])
        normal = self.call('scan_split.preview_reference', {'reference_image_path': str(self.source), '_input_identities': {str(self.source): self.identity}})
        self.assertTrue(normal['result']['ok'])
        self.assertTrue(normal['result']['data_url'].startswith('data:image/png;base64,'))


if __name__ == '__main__':
    unittest.main()
