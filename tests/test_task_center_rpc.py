import unittest
import json
import os
import subprocess
import sys
from pathlib import Path

import test_security_rpc as fixture


class TaskCenterRpcTests(unittest.TestCase):
    setUp = fixture.SecurityRpcTests.setUp
    tearDown = fixture.SecurityRpcTests.tearDown
    receive = fixture.SecurityRpcTests.receive
    call = fixture.SecurityRpcTests.call

    def params(self, task_id='persistent-split'):
        return {'task_id': task_id, 'pdf_paths': [str(self.source)],
                'config': {'output_dir': str(self.root / 'outputs')},
                '_input_identities': {str(self.source): self.identity}}

    def test_queue_pause_allows_other_tasks_and_cancel_unblocks_paused(self):
        self.call('pdf_split.execute_async', self.params())
        paused = self.call('task.pause', {'task_id': 'persistent-split'})
        self.assertIn('result', paused, 'task.pause route must exist')
        self.assertEqual(paused['result']['state'], 'paused')
        self.call('pdf_split.execute_async', self.params('second'))
        self.call('test.release', {})
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertEqual(complete['task_id'], 'second')
        tasks = self.call('tasks.list', {})['result']['tasks']
        self.assertEqual(next(t for t in tasks if t['task_id'] == 'persistent-split')['state'], 'paused')
        self.assertTrue(self.call('task.cancel', {'task_id': 'persistent-split'})['result']['cancelled'])
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['cancelled'])
        retry = self.call('tasks.retry_spec', {'task_id': 'persistent-split'})['result']
        self.assertEqual(retry['method'], 'pdf_split.execute_async')
        self.assertNotIn('_input_identities', retry['params'])
        self.assertEqual(retry['input_identities'][str(self.source)], self.identity)

    def test_queued_resume_runs_and_persists_completed_output(self):
        self.call('pdf_split.execute_async', self.params())
        self.assertIn('result', self.call('task.pause', {'task_id': 'persistent-split'}))
        self.call('test.release', {})
        self.assertTrue(self.call('task.resume', {'task_id': 'persistent-split'})['result']['resumed'])
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['ok'])
        tasks = self.call('tasks.list', {})['result']['tasks']
        self.assertEqual(tasks[0]['state'], 'completed')
        self.assertTrue(Path(tasks[0]['output_files'][0]).exists())

    def test_preview_process_never_changes_primary_journal_and_restart_interrupts(self):
        self.call('pdf_split.execute_async', self.params())
        journal = self.root / 'FileToolbox' / 'tasks.json'
        before = journal.read_bytes()
        script = "import json; from engine import server; print(json.dumps(server.handle_tasks_list({})))"
        env = {**os.environ, 'APPDATA': str(self.root), 'FILE_TOOLBOX_PREVIEW_ENGINE': '1'}
        preview = json.loads(subprocess.check_output([sys.executable, '-c', script], env=env, text=True))
        self.assertEqual(preview['tasks'], [])
        self.assertEqual(journal.read_bytes(), before)
        self.proc.terminate(); self.proc.wait(10)
        env.pop('FILE_TOOLBOX_PREVIEW_ENGINE')
        restarted = json.loads(subprocess.check_output([sys.executable, '-c', script], env=env, text=True))
        self.assertEqual(restarted['tasks'][0]['state'], 'interrupted')
        self.assertTrue(restarted['tasks'][0]['can_retry'])
        self.assertFalse((self.root / 'outputs').exists())


class RunningTaskRpcTests(unittest.TestCase):
    setUp = fixture.SecurityRpcTests.setUp
    tearDown = fixture.SecurityRpcTests.tearDown
    receive = fixture.SecurityRpcTests.receive
    call = fixture.SecurityRpcTests.call
    params = TaskCenterRpcTests.params
    server_setup = """
from pypdf import PdfWriter
release_native = threading.Event()
real_write = PdfWriter.write
def held_write(writer, stream):
    server.send_notification('test.native_started', {})
    release_native.wait(5)
    return real_write(writer, stream)
PdfWriter.write = held_write
server.ROUTES['test.release_native'] = lambda p: release_native.set()
def fail_thread_once(params):
    real_start = threading.Thread.start
    def fail(thread):
        threading.Thread.start = real_start
        raise RuntimeError('thread unavailable')
    threading.Thread.start = fail
    return {}
server.ROUTES['test.fail_thread_once'] = fail_thread_once
"""

    def native_params(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        for _ in range(2):
            writer.add_blank_page(width=100, height=100)
        writer.write(self.source)
        self.identity = json.loads(subprocess.check_output([
            'node', '-e', "const fs=require('fs'),s=fs.statSync(process.argv[1],{bigint:true});console.log(JSON.stringify({canonical:fs.realpathSync(process.argv[1]),dev:String(s.dev),ino:String(s.ino),size:String(s.size),mtime_ns:String(s.mtimeNs)}))", str(self.source)
        ], text=True))
        params = self.params()
        params['config'].update(mode='by_page_count', page_count=1)
        return params

    def hold_and_pause(self):
        self.call('pdf_split.execute_async', self.native_params())
        self.call('test.release', {})
        self.receive(lambda r: r.get('method') == 'test.native_started')
        paused = self.call('task.pause', {'task_id': 'persistent-split'})['result']
        self.assertEqual(paused['state'], 'pausing')
        self.call('test.release_native', {})
        self.receive(lambda r: r.get('method') == 'task.state' and r['params']['state'] == 'paused')
        self.assertTrue(self.call('ping', {})['result']['pong'])

    def test_running_pause_is_honest_until_safe_boundary_then_resume(self):
        self.hold_and_pause()
        self.assertEqual(self.call('task.pause', {'task_id': 'persistent-split'})['result']['state'], 'paused')
        self.assertTrue(self.call('task.resume', {'task_id': 'persistent-split'})['result']['resumed'])
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['ok'])
        self.assertFalse(self.call('task.pause', {'task_id': 'persistent-split'})['result']['paused'])
        self.assertEqual(self.call('tasks.list', {})['result']['tasks'][0]['state'], 'completed')

    def test_running_paused_cancel_unblocks_worker(self):
        self.hold_and_pause()
        self.assertTrue(self.call('task.cancel', {'task_id': 'persistent-split'})['result']['cancelled'])
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['cancelled'])
        self.assertEqual(self.call('tasks.list', {})['result']['tasks'][0]['state'], 'cancelled')

    def test_retry_after_cancel_reuses_committed_output_through_task_journal(self):
        self.hold_and_pause()
        self.call('task.cancel', {'task_id': 'persistent-split'})
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['cancelled'])
        existing = list((self.root / 'outputs').glob('*.pdf'))
        self.assertEqual(len(existing), 1)
        original_time = existing[0].stat().st_mtime_ns
        spec = self.call('tasks.retry_spec', {'task_id': 'persistent-split'})['result']
        params = {**spec['params'], 'task_id': 'resumed-split', '_resume_task_id': 'persistent-split',
                  '_input_identities': spec['input_identities'], '_output_identities': spec['output_identities']}
        self.assertIn('result', self.call(spec['method'], params))
        retried = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(retried['ok'])
        self.assertEqual(len(list((self.root / 'outputs').glob('*.pdf'))), 2)
        self.assertEqual(existing[0].stat().st_mtime_ns, original_time)


    def test_thread_start_failure_is_persisted_as_failed(self):
        self.call('test.release', {})
        self.call('test.fail_thread_once', {})
        self.assertIn('error', self.call('pdf_split.execute_async', self.params()))
        self.assertEqual(self.call('tasks.list', {})['result']['tasks'][0]['state'], 'failed')
