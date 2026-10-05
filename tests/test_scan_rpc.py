import json
import subprocess
import unittest
from pathlib import Path
import fitz
from pypdf import PdfReader
import test_security_rpc as rpc_fixture


class ScanRpcTests(unittest.TestCase):
    setUp = rpc_fixture.SecurityRpcTests.setUp
    tearDown = rpc_fixture.SecurityRpcTests.tearDown
    receive = rpc_fixture.SecurityRpcTests.receive
    call = rpc_fixture.SecurityRpcTests.call
    server_setup = """
release_preview = threading.Event()
original_preview = server.ROUTES['scan_split.preview_reference']
def held_preview(params):
    if params.get('hold'):
        server.send_notification('test.preview_started', {})
        release_preview.wait(5)
    return original_preview(params)
server.ROUTES['scan_split.preview_reference'] = held_preview
def release_reference(params):
    release_preview.set()
    return {}
server.ROUTES['test.release_preview'] = release_reference
def fail_second_output(params):
    from pypdf import PdfWriter
    original_write = PdfWriter.write
    writes = 0
    def failing_write(writer, stream):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError('simulated disk failure')
        return original_write(writer, stream)
    PdfWriter.write = failing_write
    return {}
server.ROUTES['test.fail_second_output'] = fail_second_output
"""

    def make_stamp_pdf(self):
        document = fitz.open()
        for _ in range(3):
            page = document.new_page(width=200, height=200)
            page.draw_circle(fitz.Point(100, 100), 25, color=(1, 0, 0), width=3)
        document.save(self.source)
        document.close()
        self.identity = json.loads(subprocess.check_output([
            'node', '-e', "const fs=require('fs'), s=fs.statSync(process.argv[1],{bigint:true}); console.log(JSON.stringify({dev:String(s.dev),ino:String(s.ino),size:String(s.size),mtime_ns:String(s.mtimeNs),canonical:fs.realpathSync(process.argv[1])}))", str(self.source)
        ], text=True))

    def scan_params(self):
        return {'pdf_path': str(self.source), 'reference_image_path': '', 'task_id': 'scan-regression',
                'options': {'detection_mode': 'stamp', 'dpi': 180},
                '_input_identities': {str(self.source): self.identity}}

    def test_ping_is_answered_while_reference_preview_is_still_running(self):
        self.next_id += 1
        preview_id = self.next_id
        self.proc.stdin.write(json.dumps({'id': preview_id, 'method': 'scan_split.preview_reference',
                                         'auth': 'regression-test-token', 'params': {
                                             'reference_image_path': str(self.source), 'hold': True,
                                             '_input_identities': {str(self.source): self.identity}}}) + '\n')
        self.proc.stdin.flush()
        self.receive(lambda value: value.get('method') == 'test.preview_started')
        self.assertTrue(self.call('ping', {})['result']['pong'])
        self.assertFalse(any(value.get('id') == preview_id for value in self.pending))
        self.call('test.release_preview', {})
        self.assertTrue(self.receive(lambda value: value.get('id') == preview_id)['result']['ok'])

    def test_zero_markers_is_a_warning_result_and_creates_no_copy(self):
        self.call('scan_split.execute_async', self.scan_params())
        self.call('test.release', {})
        result = self.receive(lambda value: value.get('method') == 'task.complete')['params']
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['output_files'], [])
        self.assertTrue(result['result']['warnings'])
        self.assertFalse(list(self.root.glob('*_scan*.pdf')))
        history = self.call('history.get', {})['result']['records']
        self.assertEqual(history[0]['level'], 'warning')

    def test_writing_progress_is_delivered_for_each_real_output(self):
        self.make_stamp_pdf()
        self.call('scan_split.execute_async', self.scan_params())
        self.call('test.release', {})
        completed = self.receive(lambda value: value.get('method') == 'task.complete')['params']
        self.assertTrue(completed['ok'])
        self.assertEqual(len(completed['result']['output_files']), 3)
        writing = [value['params'] for value in self.pending
                   if value.get('method') == 'task.progress' and value['params'].get('phase') == 'writing']
        self.assertEqual([(value['current'], value['total']) for value in writing],
                         [(0, 3), (1, 3), (2, 3), (3, 3)])

    def test_partial_write_failure_reaches_client_and_history(self):
        self.make_stamp_pdf()
        self.call('test.fail_second_output', {})
        self.call('scan_split.execute_async', self.scan_params())
        self.call('test.release', {})
        completed = self.receive(lambda value: value.get('method') == 'task.complete')['params']
        self.assertFalse(completed['ok'])
        result = completed['result']
        self.assertIn('simulated disk failure', completed['error'])
        self.assertEqual(len(result['output_files']), 1)
        self.assertEqual(len(PdfReader(result['output_files'][0]).pages), 1)
        self.assertEqual(result['failed_segments'][0]['index'], 2)
        self.assertEqual(result['pending_segments'][0]['index'], 3)
        history = self.call('history.get', {})['result']['records'][0]
        self.assertEqual(history['level'], 'error')
        self.assertEqual(history['details']['failed_segments'], result['failed_segments'])
        self.assertEqual(history['details']['pending_segments'], result['pending_segments'])
