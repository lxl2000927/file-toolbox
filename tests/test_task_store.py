import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('src.utils.task_store'), 'persistent task store is missing')
        self.module = importlib.import_module('src.utils.task_store')
        self.directory = tempfile.TemporaryDirectory(prefix='toolbox-tasks-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'tasks.json'
        self.store = self.module.TaskStore(self.path, max_records=3)

    def create(self, task_id='one', state='running'):
        return self.store.create(task_id, 'pdf_split.execute_async', {
            'task_id': task_id, 'pdf_paths': ['source.pdf'], 'config': {},
            '_input_identities': {'source.pdf': {'size': '123'}},
            '_output_identities': {'output': {'ino': '321'}},
            'auth': 'secret-token', '_resume_task_id': '',
        }, state=state)

    def test_restart_marks_unfinished_interrupted_and_keeps_private_retry_spec(self):
        self.create()
        self.store.checkpoint('one', {'version': 1, 'completed': ['source.pdf']})
        restarted = self.module.TaskStore(self.path)
        public = restarted.list()[0]
        self.assertEqual(public['state'], 'interrupted')
        self.assertTrue(public['can_retry'])
        self.assertNotIn('params', public)
        self.assertNotIn('checkpoint', public)
        spec = restarted.retry_spec('one')
        self.assertEqual(spec['input_identities'], {'source.pdf': {'size': '123'}})
        self.assertEqual(spec['output_identities'], {'output': {'ino': '321'}})
        self.assertEqual(spec['params'], {'pdf_paths': ['source.pdf'], 'config': {}})
        self.assertEqual(restarted.get_checkpoint('one')['completed'], ['source.pdf'])
        self.assertNotIn('secret-token', self.path.read_text('utf-8'))

    def test_atomic_write_failure_preserves_previous_state_and_journal(self):
        self.create()
        original = self.path.read_bytes()
        with patch.object(self.module.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                self.create('two')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.list()[0]['state'], 'running')
        self.assertEqual(len(self.store.list()), 1)

    def test_corrupt_journal_is_reported_and_never_overwritten(self):
        self.path.write_text('{broken', 'utf-8')
        corrupted = self.module.TaskStore(self.path)
        self.assertTrue(corrupted.last_error)
        with self.assertRaisesRegex(RuntimeError, '日志'):
            corrupted.create('x', 'pdf_tools.run', {'action': 'assemble'})
        self.assertEqual(self.path.read_text('utf-8'), '{broken')

    def test_terminal_is_monotonic_and_only_old_terminal_records_are_evicted(self):
        self.create('active')
        for task_id in ['old', 'middle', 'new']:
            self.create(task_id)
            self.store.finish(task_id, {'ok': True, 'result': {'output_files': ['done.pdf']}})
        self.assertEqual({task['task_id'] for task in self.store.list()}, {'active', 'middle', 'new'})
        self.store.transition('new', 'paused')
        self.store.progress('new', {'current': 9, 'total': 10})
        self.assertEqual(self.store.list()[0]['state'], 'completed')
        with self.assertRaisesRegex(ValueError, '完成'):
            self.store.retry_spec('new')

    def test_retry_keeps_directory_identity_but_ignores_directory_mtime_changes(self):
        original = {'pdf_paths': ['a.pdf'], '_output_identities': {
            'output': {'canonical': 'output', 'dev': '1', 'ino': '2', 'mtime_ns': '10', 'size': '0'}}}
        self.store.create('original', 'pdf_split.execute_async', original)
        self.store.finish('original', {'ok': False})
        fresh = {**original, '_resume_task_id': 'original', '_output_identities': {
            'output': {'canonical': 'output', 'dev': '1', 'ino': '2', 'mtime_ns': '99', 'size': '8'}}}
        self.store.create('retry', 'pdf_split.execute_async', fresh)
        self.assertEqual(self.store.list()[0]['task_id'], 'retry')

    def test_progress_and_checkpoint_journal_do_not_rewrite_large_original_request(self):
        self.store.create('scale', 'pdf_tools.run', {'action': 'assemble', 'options': {'pages': list(range(100000))}})
        original = self.path.read_bytes()
        checkpoint = {'version': 1, 'units': {}}
        for index in range(30):
            checkpoint['units'][str(index)] = {'files': {'pdf': {'path': f'out{index}.pdf', 'size': 1, 'sha256': 'abc'}}}
            self.store.checkpoint('scale', checkpoint)
        self.assertEqual(self.path.read_bytes(), original, 'unit commits must not rewrite original100k-page request')
        journal = self.path.with_suffix('.events.jsonl')
        self.assertLess(journal.stat().st_size, 20000)
        restored = self.module.TaskStore(self.path)
        self.assertEqual(restored.list()[0]['output_count'], 30)
        self.assertEqual(len(restored.get_checkpoint('scale')['units']), 30)

    def test_corrupt_increment_is_reported_without_discarding_or_rewriting(self):
        self.create()
        journal = self.path.with_suffix('.events.jsonl')
        journal.write_text('{broken\n', 'utf-8')
        original = self.path.read_bytes()
        restored = self.module.TaskStore(self.path)
        self.assertTrue(restored.last_error)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(journal.read_text('utf-8'), '{broken\n')
        with self.assertRaisesRegex(RuntimeError, '日志'):
            restored.create('new', 'pdf_split.execute_async', {})

    def test_retry_lineage_prevents_duplicate_children_and_stale_checkpoint_reuse(self):
        self.create()
        self.store.checkpoint('one', {'version': 1, 'units': {'0': {'files': {'pdf': {'path': 'done.pdf'}}}}})
        self.store.finish('one', {'ok': False})
        spec = self.store.retry_spec('one')
        params = {**spec['params'], '_resume_task_id': 'one', '_input_identities': spec['input_identities'],
                  '_output_identities': spec['output_identities']}
        self.store.create('child', spec['method'], params)
        self.assertEqual(self.store.list()[0]['output_count'], 1)
        with self.assertRaisesRegex(ValueError, '续做'):
            self.store.create('duplicate', spec['method'], params)
        self.store.finish('child', {'ok': False})
        self.assertFalse(next(task for task in self.store.list() if task['task_id'] == 'one')['can_retry'])
        self.assertTrue(next(task for task in self.store.list() if task['task_id'] == 'child')['can_retry'])
        self.assertEqual(self.module.TaskStore(self.path).retry_spec('child')['method'], spec['method'])
        self.store.finish('child', {'ok': True})
        with self.assertRaisesRegex(ValueError, '续做'):
            self.store.retry_spec('one')

    def test_success_reports_complete_progress_even_when_last_progress_was_throttled(self):
        self.create()
        self.store.progress('one', {'phase': 'writing', 'current': 0, 'total': 3})
        self.store.progress('one', {'phase': 'writing', 'current': 3, 'total': 3})
        self.store.finish('one', {'ok': True})
        self.assertEqual(self.store.list()[0]['current'], 3)

    def test_simultaneous_retry_can_only_create_one_child(self):
        self.create()
        self.store.finish('one', {'ok': False})
        spec = self.store.retry_spec('one')
        params = {**spec['params'], '_resume_task_id': 'one', '_input_identities': spec['input_identities'],
                  '_output_identities': spec['output_identities']}
        barrier = threading.Barrier(2)
        results = []
        def create(task_id):
            barrier.wait()
            try:
                self.store.create(task_id, spec['method'], params)
                results.append('created')
            except ValueError:
                results.append('rejected')
        threads = [threading.Thread(target=create, args=(task_id,)) for task_id in ('child1', 'child2')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(results), ['created', 'rejected'])

    def test_truncated_utf8_tail_recovers_last_durable_event_with_warning(self):
        self.create()
        self.store.checkpoint('one', {'version': 1, 'units': {'0': {'files': {'pdf': {'path': 'done.pdf'}}}}})
        journal = self.path.with_suffix('.events.jsonl')
        with journal.open('ab') as stream:
            stream.write(b'{"sequence":2,"data":"\xe4\xb8')
        restored = self.module.TaskStore(self.path)
        self.assertEqual(restored.list()[0]['state'], 'interrupted')
        self.assertEqual(restored.list()[0]['output_files'], ['done.pdf'])
        self.assertTrue(restored.last_error)
        self.assertTrue(restored.list()[0]['can_retry'])
        self.assertEqual(self.module.TaskStore(self.path).list()[0]['output_files'], ['done.pdf'])

    def test_valid_json_without_newline_is_not_treated_as_durable_event(self):
        self.create()
        journal = self.path.with_suffix('.events.jsonl')
        journal.write_text(json.dumps({'sequence': 1, 'task_id': 'one',
                                      'changes': [['set', ['state'], 'completed']]}), 'utf-8')
        restored = self.module.TaskStore(self.path)
        self.assertEqual(restored.list()[0]['state'], 'interrupted')
        self.assertTrue(restored.last_error)
