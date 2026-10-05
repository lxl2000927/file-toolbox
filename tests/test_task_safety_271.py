import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from engine import server
from src.core.pdf_scan_split_engine import PdfScanSplitEngine
from src.core.pdf_split_engine import PdfSplitEngine
from src.utils import output_resources, pdf_output, task_store


class TaskSafety271Tests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='toolbox-safety-271-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def pdf(self, pages=2):
        path = self.root / 'input.pdf'
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=100, height=100)
        writer.write(path)
        return path

    def copied(self):
        source = self.root / 'input.txt'
        source.write_text('original', encoding='utf-8')
        result = server.handle_rename_execute({'files': [str(source)], 'save_method': 'copy',
            'output_dir': str(self.root), 'rules': [{'type': 'insert_text', 'position': '后缀', 'text': '_copy'}]})
        return result, Path(result['operations'][0]['new_path'])

    def test_copy_undo_rejects_atomic_replacement_and_keeps_retry_token(self):
        result, output = self.copied()
        replacement = self.root / 'replacement'
        replacement.write_text('later work', encoding='utf-8')
        replacement.replace(output)
        undone = server.handle_rename_undo({'undo_token': result['undo_token']})
        self.assertTrue(output.exists(), 'undo must preserve later editor saves')
        self.assertEqual(output.read_text(), 'later work')
        self.assertEqual(undone['restored'], [])
        self.assertTrue(undone['failed'])
        self.assertIn(result['undo_token'], server._UNDO_RECORDS)

    def test_copy_undo_rejects_in_place_content_change_even_with_original_mtime(self):
        result, output = self.copied()
        before = output.stat()
        output.write_text('modified', encoding='utf-8')
        os.utime(output, ns=(before.st_atime_ns, before.st_mtime_ns))
        undone = server.handle_rename_undo({'undo_token': result['undo_token']})
        self.assertTrue(output.exists())
        self.assertTrue(undone['failed'])

    def test_copy_undo_still_removes_unchanged_copy(self):
        result, output = self.copied()
        undone = server.handle_rename_undo({'undo_token': result['undo_token']})
        self.assertFalse(output.exists())
        self.assertEqual(undone['failed'], [])

    def test_changed_copy_can_retry_undo_after_restoring_original_content_and_metadata(self):
        result, output = self.copied()
        before = output.stat()
        output.write_text('changed!', encoding='utf-8')
        self.assertTrue(server.handle_rename_undo({'undo_token': result['undo_token']})['failed'])
        output.write_text('original', encoding='utf-8')
        os.utime(output, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(server.handle_rename_undo({'undo_token': result['undo_token']})['failed'], [])
        self.assertFalse(output.exists())

    def test_copy_undo_rejects_different_file_object_even_with_identical_bytes(self):
        result, output = self.copied()
        replacement = self.root / 'replacement'
        replacement.write_bytes(output.read_bytes())
        before = output.stat()
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(output)
        self.assertTrue(server.handle_rename_undo({'undo_token': result['undo_token']})['failed'])
        self.assertTrue(output.exists())

    def test_published_output_checkpoint_precedes_pause_boundary_in_both_writers(self):
        for full_copy in (False, True):
            with self.subTest(full_copy=full_copy):
                source = self.pdf()
                destination = self.root / str(full_copy)
                saved = []
                observed = []
                def check():
                    if destination.exists() and list(destination.glob('*.pdf')):
                        observed.append(bool(saved and saved[-1]['units']))
                    return False
                jobs = [pdf_output.PdfOutputJob('a.pdf', [0, 1] if full_copy else [0])]
                checkpoint = task_store.prepare_checkpoint('test', [str(source)], {})
                task_store.write_checkpointed_jobs(str(source), output_dir=str(destination), jobs=jobs,
                    checkpoint=checkpoint, on_checkpoint=lambda value: saved.append(copy.deepcopy(value)), cancel_check=check)
                self.assertTrue(observed)
                self.assertTrue(all(observed), 'a blocking pause may occur only after durable checkpoint commit')

    def test_legacy_pdf_writer_checks_disk_before_publish_for_both_paths(self):
        source = self.pdf()
        for pages in ([0], [0, 1]):
            with self.subTest(pages=pages), patch.object(output_resources.shutil, 'disk_usage', return_value=(100, 100, 0)):
                destination = self.root / str(len(pages))
                with self.assertRaisesRegex(OSError, '空间'):
                    pdf_output.write_pdf_output_jobs(str(source), output_dir=str(destination),
                        jobs=[pdf_output.PdfOutputJob('result.pdf', pages)])
                self.assertEqual(list(destination.iterdir()), [])

    def test_aggregate_capacity_is_rejected_before_accepting_checkpoint(self):
        path = self.root / 'tasks.json'
        store = task_store.TaskStore(path, max_bytes=4096)
        store.create('one', 'pdf_split.execute_async', {'padding': 'x' * 2400}, state='running')
        with self.assertRaisesRegex(ValueError, '容量'):
            store.checkpoint('one', {'version': 1, 'units': {}, 'saved_result': 'y' * 1600})
        self.assertIsNone(store.get_checkpoint('one'))
        restarted = task_store.TaskStore(path, max_bytes=4096)
        self.assertFalse(restarted._blocked)
        self.assertEqual(restarted.retry_spec('one')['params']['padding'], 'x' * 2400)
        restarted.create('two', 'pdf_split.execute_async', {})

    def test_record_count_limit_evicts_old_failed_or_cancelled_tasks_but_not_active(self):
        store = task_store.TaskStore(self.root / 'count-limit.json', max_records=3)
        store.create('active', 'pdf_tools.run', {}, state='running')
        for index in range(5):
            store.create(str(index), 'pdf_tools.run', {}, state='running')
            store.finish(str(index), {'ok': False, 'cancelled': index % 2 == 0, 'error': 'cancel or failure'})
        self.assertEqual({record['task_id'] for record in store.list()}, {'active', '3', '4'})
        self.assertEqual(next(record for record in store.list() if record['task_id'] == 'active')['state'], 'running')

    def test_legacy_accepted_oversize_journal_remains_readable_and_retryable(self):
        path = self.root / 'tasks.json'
        store = task_store.TaskStore(path, max_bytes=8192)
        store.create('one', 'pdf_split.execute_async', {'padding': 'x' * 2400}, state='running')
        value = {'version': 1, 'units': {}, 'saved_result': 'y' * 1600}
        store.checkpoint('one', value)
        restarted = task_store.TaskStore(path, max_bytes=4096)
        self.assertFalse(restarted._blocked, 'capacity pressure is recoverable and must not poison the whole journal')
        self.assertEqual(restarted.get_checkpoint('one'), value)
        self.assertEqual(restarted.retry_spec('one')['params']['padding'], 'x' * 2400)
        self.assertTrue(restarted.list()[0]['can_retry'])
        again = task_store.TaskStore(path, max_bytes=4096)
        self.assertFalse(again._blocked)
        self.assertEqual(again.get_checkpoint('one'), value)

    def test_legacy_oversize_retry_can_start_and_reuse_checkpoint_but_reject_growth(self):
        path = self.root / 'legacy-start.json'
        store = task_store.TaskStore(path, max_bytes=8192)
        store.create('one', 'pdf_tools.run', {'padding': 'x' * 2400}, state='running')
        checkpoint = {'version': 1, 'operation': 'op', 'units': {}, 'saved_result': 'y' * 1600}
        store.checkpoint('one', checkpoint)
        store = task_store.TaskStore(path, max_bytes=4096)
        store.create('retry', 'pdf_tools.run', {'padding': 'x' * 2400, '_resume_task_id': 'one'})
        store.transition('retry', 'running')
        store.checkpoint('retry', checkpoint)
        self.assertEqual(store.list()[0]['state'], 'running')
        with self.assertRaisesRegex(ValueError, '容量'):
            store.checkpoint_unit('retry', 'op', 'new', {'files': {'pdf': {'path': 'new.pdf'}}})
        self.assertEqual(store.get_checkpoint('retry'), checkpoint)
        store.finish('retry', {'ok': False, 'error': '任务日志容量已满，请减少任务规模'})
        self.assertEqual(store.list()[0]['state'], 'failed')

    def test_scan_resume_checks_saved_page_count_against_source(self):
        source = self.pdf(3)
        saved = []
        destination = str(self.root / 'scan')
        with patch.object(PdfScanSplitEngine, '_scan_markers', return_value=([0], 3)):
            PdfScanSplitEngine.execute(str(source), '', output_dir=destination,
                on_checkpoint=lambda value: saved.append(copy.deepcopy(value)))
        checkpoint = saved[0]
        checkpoint['scan']['total_pages'] = 2
        with self.assertRaisesRegex(ValueError, '检查点'):
            PdfScanSplitEngine.execute(str(source), '', output_dir=destination, checkpoint=checkpoint)

    def test_unit_patch_persistence_does_not_copy_or_serialize_accumulated_history(self):
        path = self.root / 'tasks.json'
        store = task_store.TaskStore(path)
        store.create('one', 'pdf_tools.run', {}, state='running')
        for index in range(20):
            store.checkpoint_unit('one', 'operation', str(index),
                {'files': {'pdf': {'path': f'{index}.pdf', 'size': 1, 'sha256': 'abc'}}, 'complete': True})
        real_copy, real_dumps = copy.deepcopy, json.dumps
        def bounded_copy(value, *args, **kwargs):
            if isinstance(value, dict) and ('units' in value or len(value) >= 20):
                raise AssertionError('copied complete unit history')
            return real_copy(value, *args, **kwargs)
        def bounded_json(value, *args, **kwargs):
            if isinstance(value, dict) and (value.get('tasks') or 'units' in value):
                raise AssertionError('serialized complete history')
            return real_dumps(value, *args, **kwargs)
        with patch.object(task_store.copy, 'deepcopy', side_effect=bounded_copy), patch.object(task_store.json, 'dumps', side_effect=bounded_json):
            store.checkpoint_unit('one', 'operation', '20',
                {'files': {'pdf': {'path': '20.pdf', 'size': 1, 'sha256': 'abc'}}, 'complete': True})
        restored = task_store.TaskStore(path)
        self.assertEqual(len(restored.get_checkpoint('one')['units']), 21)
        self.assertEqual(restored.list()[0]['output_count'], 21)

    def test_failed_unit_append_leaves_previous_checkpoint_and_capacity_unchanged(self):
        store = task_store.TaskStore(self.root / 'tasks.json')
        store.create('one', 'pdf_tools.run', {}, state='running')
        store.checkpoint_unit('one', 'op', '0', {'files': {'pdf': {'path': 'first.pdf'}}})
        before, size = store.get_checkpoint('one'), store._snapshot_size()
        with patch.object(store, '_append', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                store.checkpoint_unit('one', 'op', '1', {'files': {'pdf': {'path': 'second.pdf'}}})
        self.assertEqual(store.get_checkpoint('one'), before)
        self.assertEqual(store._snapshot_size(), size)
        self.assertEqual(store.list()[0]['output_files'], ['first.pdf'])

    def test_scan_server_incremental_callback_replays_nested_checkpoints(self):
        source = self.pdf(3)
        destination = str(self.root / 'scan')
        store = task_store.TaskStore(self.root / 'tasks.json')
        store.create('one', 'pdf_scan_split.execute_async', {}, state='running')
        with patch.object(server, '_TASK_STORE', store), patch.object(PdfScanSplitEngine, '_scan_markers', return_value=([0, 1, 2], 3)):
            result = PdfScanSplitEngine.execute(str(source), '', output_dir=destination,
                on_checkpoint=server._CheckpointCallback('one'))
        self.assertEqual(len(result.output_files), 3)
        store = task_store.TaskStore(self.root / 'tasks.json')
        saved = store.get_checkpoint('one')
        self.assertEqual(saved['units'], saved['segments']['units'])
        with patch.object(PdfScanSplitEngine, '_scan_markers', side_effect=AssertionError('unexpected scan')):
            resumed = PdfScanSplitEngine.execute(str(source), '', output_dir=destination, checkpoint=saved)
        self.assertEqual(resumed.output_files, result.output_files)
        self.assertEqual(len(list(Path(destination).glob('*.pdf'))), 3)

    def test_incremental_byte_accounting_matches_materialized_snapshot(self):
        store = task_store.TaskStore(self.root / 'tasks.json')
        store.create('one', 'pdf_tools.run', {}, state='running')
        for index in range(12):
            store.checkpoint_unit('one', 'op', str(index), {'files': {'pdf': {'path': f'{index}中文.pdf'}}})
            store.checkpoint_meta('one', 'op', {'completed_inputs': {str(index): {'path': '中文'}}})
            actual = task_store._json_size({'version': 1, 'sequence': store._sequence, 'tasks': list(store._records.values())})
            self.assertEqual(store._snapshot_size(), actual)

    def test_byte_delta_matches_nested_set_append_remove_and_unicode(self):
        samples = [
            ({'nested': {}}, {'nested': {'甲': '中', '乙': [1, '文']}}),
            ({'nested': {'甲': '中', '乙': [1, '文']}}, {'nested': {}}),
            ({'list': []}, {'list': ['中', {'文': '\n'}]}),
            ({'list': ['中']}, {'list': ['中', {'文': '😀'}]}),
            ({'nested': {'甲': {'乙': [1, 2]}}}, {'nested': {'甲': {'乙': [1, 2, 3], '丙': None}}}),
        ]
        for old, new in samples:
            with self.subTest(old=old, new=new):
                changes = task_store._diff(old, new)
                self.assertEqual(task_store._json_size(old) + task_store._change_size(old, changes), task_store._json_size(new))
                self.assertEqual(task_store._apply_changes(old, changes), new)

    def test_near_capacity_acceptance_leaves_room_for_pause_and_restart(self):
        path = self.root / 'tasks.json'
        store = task_store.TaskStore(path, max_bytes=4096)
        store.create('one', 'pdf_split.execute_async', {}, state='running')
        # Advance close to a sequence digit boundary, then use every available byte.
        for index in range(8):
            store.checkpoint('one', {'version': 1, 'units': {}, 'padding': 'x' * index})
        for size in range(4096, 0, -1):
            try:
                store.checkpoint('one', {'version': 1, 'units': {}, 'padding': 'x' * size})
                break
            except ValueError:
                pass
        self.assertEqual(store._sequence, 9)
        store.transition('one', 'paused')
        restored = task_store.TaskStore(path, max_bytes=4096)
        self.assertFalse(restored._blocked)
        self.assertEqual(restored.list()[0]['state'], 'interrupted')

    def test_full_checkpoint_can_finish_failed_with_capacity_error_or_long_details(self):
        for error in ('任务日志容量已满，请减少任务规模', '磁盘写入失败：' + '中文\\\n😀' * 2000):
            with self.subTest(error=error[:20]):
                path = self.root / f'terminal-{len(error)}.json'
                store = task_store.TaskStore(path, max_bytes=4096)
                store.create('one', 'pdf_tools.run', {}, state='running')
                unit = {'files': {'pdf': {'path': 'already-complete.pdf', 'size': 1, 'sha256': 'abc'}}}
                for size in range(4096, 0, -1):
                    checkpoint = {'version': 1, 'operation': 'op', 'units': {'0': unit}, 'padding': 'x' * size}
                    try:
                        store.checkpoint('one', checkpoint)
                        break
                    except ValueError:
                        pass
                store.finish('one', {'ok': False, 'error': error})
                self.assertEqual(store.list()[0]['state'], 'failed')
                self.assertTrue(store.list()[0]['can_retry'])
                self.assertTrue(store.list()[0]['error'])
                self.assertEqual(store.get_checkpoint('one'), checkpoint)
                restarted = task_store.TaskStore(path, max_bytes=4096)
                self.assertFalse(restarted._blocked)
                self.assertEqual(restarted.list()[0]['state'], 'failed')
                self.assertEqual(restarted.get_checkpoint('one'), checkpoint)

    def test_full_checkpoint_can_finish_complete_with_large_progress_total(self):
        path = self.root / 'completed-at-capacity.json'
        store = task_store.TaskStore(path, max_bytes=4096)
        store.create('one', 'pdf_tools.run', {}, state='running')
        store.progress('one', {'current': 0, 'total': 10 ** 100, 'phase': ''})
        for size in range(4096, 0, -1):
            try:
                store.checkpoint('one', {'version': 1, 'units': {}, 'padding': 'x' * size})
                break
            except ValueError:
                pass
        store.finish('one', {'ok': True})
        self.assertEqual(store.list()[0]['state'], 'completed')
        self.assertEqual(store.list()[0]['current'], 10 ** 100)

    def test_capacity_failure_after_publish_preserves_checkpoint_and_finishes_task(self):
        path = self.root / 'published-capacity.json'
        store = task_store.TaskStore(path, max_bytes=4096)
        store.create('one', 'pdf_tools.run', {}, state='running')
        store.create('other', 'pdf_tools.run', {}, state='running')
        unit = {'files': {'pdf': {'path': 'already-complete.pdf', 'size': 1, 'sha256': 'abc'}}}
        for size in range(4096, 0, -1):
            checkpoint = {'version': 1, 'operation': 'op', 'units': {'0': unit}, 'padding': 'x' * size}
            try:
                store.checkpoint('one', checkpoint)
                break
            except ValueError:
                pass
        output = self.root / ('published-but-journal-full-' + 'a' * 90 + '.pdf')
        output.write_bytes(b'complete output')
        with self.assertRaisesRegex(ValueError, '容量'):
            store.checkpoint_unit('one', 'op', '1', {'files': {'pdf': {'path': str(output), 'size': 15, 'sha256': 'abc'}}})
        store.finish('one', {'ok': False, 'error': '任务日志容量已满，请减少任务规模',
                            'result': {'output_files': ['already-complete.pdf', str(output)]}})
        self.assertTrue(output.exists())
        with self.assertRaisesRegex(ValueError, '容量'):
            store.checkpoint_unit('other', 'different-operation', '0', {'files': {'pdf': {'path': str(output)}}})
        restored = task_store.TaskStore(path, max_bytes=4096)
        self.assertFalse(restored._blocked)
        record = next(item for item in restored.list() if item['task_id'] == 'one')
        self.assertEqual(record['state'], 'failed')
        self.assertTrue(record['can_retry'])
        self.assertTrue(record['error'])
        self.assertEqual(restored.get_checkpoint('one'), checkpoint)
        self.assertEqual(record['output_files'], ['already-complete.pdf', str(output)])
        restored.create('retry', 'pdf_tools.run', {'_resume_task_id': 'one'}, state='running')
        self.assertEqual(restored.get_checkpoint('retry'), checkpoint)

    def test_oversize_recovery_can_resume_without_unbounded_new_task_growth(self):
        path = self.root / 'tasks.json'
        store = task_store.TaskStore(path, max_bytes=8192)
        store.create('one', 'pdf_split.execute_async', {'padding': 'x' * 2400}, state='running')
        checkpoint = {'version': 1, 'units': {}, 'saved_result': 'y' * 1600}
        store.checkpoint('one', checkpoint)
        store = task_store.TaskStore(path, max_bytes=4096)
        for index in range(4):
            parent = 'one' if index == 0 else f'retry{index - 1}'
            spec = store.retry_spec(parent)
            store.create(f'retry{index}', spec['method'], {**spec['params'], '_resume_task_id': parent}, state='running')
            store.finish(f'retry{index}', {'ok': False})
            store = task_store.TaskStore(path, max_bytes=4096)
            self.assertFalse(store._blocked)
            self.assertEqual(store.get_checkpoint(f'retry{index}'), checkpoint)
        with self.assertRaisesRegex(ValueError, '容量'):
            store.create('unrelated', 'pdf_split.execute_async', {})
