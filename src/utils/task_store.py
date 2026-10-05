"""Atomic, bounded task journal. Stored file identities are evidence, not grants."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})
STATES = TERMINAL | {'queued', 'running', 'pausing', 'paused', 'cancelling'}
_PRIVATE = {'task_id', '_resume_task_id', '_input_identities', '_output_identities'}
_SECRETS = {'auth', 'authorization', 'auth_token', 'engine_token', 'token', 'access_token'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def public_params(value):
    if isinstance(value, dict):
        return {key: public_params(item) for key, item in value.items()
                if key not in _PRIVATE and key.lower() not in _SECRETS and not key.startswith('_')}
    if isinstance(value, list):
        return [public_params(item) for item in value]
    return value


def _directories(identities):
    return {path: {key: value for key, value in identity.items() if key in ('canonical', 'dev', 'ino')}
            for path, identity in identities.items()}


def _diff(old, new, path=()):
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        changes = [['remove', list(path + (key,))] for key in old.keys() - new.keys()]
        for key, value in new.items():
            changes.extend(_diff(old[key], value, path + (key,)) if key in old
                           else [['set', list(path + (key,)), value]])
        return changes
    if isinstance(old, list) and isinstance(new, list) and len(new) > len(old) and new[:len(old)] == old:
        return [['append', list(path), new[len(old):]]]
    return [['set', list(path), new]]


def _apply_changes(record, changes):
    result = dict(record)
    for change in changes:
        operation, path = change[:2]
        if operation not in ('set', 'append', 'remove') or not isinstance(path, list) or not path:
            raise ValueError('任务日志增量无效')
        target = result
        for key in path[:-1]:
            target[key] = dict(target[key])
            target = target[key]
        key = path[-1]
        if operation == 'remove':
            target.pop(key, None)
        elif operation == 'append':
            target[key] = list(target[key]) + copy.deepcopy(change[2])
        else:
            target[key] = copy.deepcopy(change[2])
    return result


def output_identity(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ValueError('已完成输出在校验期间发生变化')
    return {'path': os.path.abspath(path), 'size': after.st_size, 'sha256': digest.hexdigest()}


def verify_outputs(identities):
    if not isinstance(identities, list):
        raise ValueError('任务检查点损坏')
    for expected in identities:
        if not isinstance(expected, dict) or not isinstance(expected.get('path'), str):
            raise ValueError('任务检查点损坏')
        try:
            actual = output_identity(expected['path'])
        except (OSError, ValueError) as exc:
            raise ValueError('已完成输出缺失或发生变化，无法安全续做') from exc
        if actual != expected:
            raise ValueError('已完成输出发生变化，无法安全续做')


def prepare_checkpoint(kind, inputs, options, checkpoint=None):
    sources = []
    for path in inputs:
        try:
            stat = os.stat(path)
            sources.append({'path': os.path.realpath(path), 'dev': stat.st_dev, 'ino': stat.st_ino,
                            'size': stat.st_size, 'mtime': stat.st_mtime_ns})
        except OSError:
            sources.append({'path': path, 'missing': True})
    operation = hashlib.sha256(json.dumps([kind, sources, options], sort_keys=True,
                                         ensure_ascii=False).encode('utf-8')).hexdigest()
    if checkpoint is not None:
        if (not isinstance(checkpoint, dict) or checkpoint.get('version') != 1
                or checkpoint.get('operation') != operation or not isinstance(checkpoint.get('units'), dict)):
            raise ValueError('任务检查点与来源或设置不一致，无法安全续做')
        return copy.deepcopy(checkpoint)
    return {'version': 1, 'operation': operation, 'units': {}, 'completed_inputs': {}}


def write_checkpointed_jobs(pdf_path, *, output_dir, jobs, checkpoint, namespace='',
                            on_checkpoint=None, on_output=None, **kwargs):
    """Keep atomic jobs as the retry unit; never reuse unverified output bytes."""
    from src.utils.pdf_output import write_pdf_output_jobs
    ordered = {}
    pending = []
    pending_indexes = []
    output_root = os.path.normcase(os.path.realpath(output_dir))
    for index, job in enumerate(jobs):
        key = f'{namespace}{index}'
        unit = checkpoint['units'].get(key)
        if unit is not None:
            if not isinstance(unit, dict) or unit.get('pages') != list(job.page_indexes):
                raise ValueError('任务检查点页序损坏')
            artifact = unit.get('files', {}).get('pdf')
            if not isinstance(artifact, dict) or os.path.normcase(os.path.realpath(os.path.dirname(artifact.get('path', '')))) != output_root:
                raise ValueError('任务检查点输出目录无效')
            verify_outputs([artifact])
            ordered[index] = artifact['path']
            if on_output:
                on_output(artifact['path'], list(job.page_indexes), 0)
        else:
            pending.append(job)
            pending_indexes.append(index)
    completed_count = 0
    def committed(path, pages, elapsed):
        nonlocal completed_count
        index = pending_indexes[completed_count]
        completed_count += 1
        ordered[index] = path
        if on_output:
            on_output(path, pages, elapsed)
        checkpoint['units'][f'{namespace}{index}'] = {
            'pages': pages, 'files': {'pdf': output_identity(path)}, 'complete': True}
        if on_checkpoint:
            on_checkpoint(copy.deepcopy(checkpoint))
    write_pdf_output_jobs(pdf_path, output_dir=output_dir, jobs=pending,
                          on_output=committed, cleanup_outputs_on_cancel=False, **kwargs)
    return [ordered[index] for index in sorted(ordered)]


class TaskStore:
    def __init__(self, path, *, max_records=200, max_bytes=128 * 1024 * 1024):
        self.path = Path(path)
        self.journal_path = self.path.with_suffix('.events.jsonl')
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.last_error = None
        self._blocked = False
        self._lock = threading.RLock()
        self._records = {}
        self._last_progress = {}
        self._sequence = 0
        try:
            if not self.path.exists() and self.journal_path.exists() and self.journal_path.stat().st_size:
                raise ValueError('任务日志缺少主快照')
            if self.path.exists():
                if self.path.stat().st_size > max_bytes:
                    raise ValueError('任务日志超过容量限制')
                data = json.loads(self.path.read_text('utf-8'))
                if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('tasks'), list):
                    raise ValueError('任务日志格式无效')
                for record in data['tasks']:
                    if (not isinstance(record, dict) or not isinstance(record.get('task_id'), str)
                            or record.get('state') not in STATES or not isinstance(record.get('params'), dict)
                            or record['task_id'] in self._records):
                        raise ValueError('任务日志记录无效')
                    self._records[record['task_id']] = record
                self._sequence = int(data.get('sequence', 0))
                recovered_tail = False
                if self.journal_path.exists():
                    if self.journal_path.stat().st_size > max_bytes:
                        raise ValueError('任务增量日志超过容量限制')
                    with self.journal_path.open('rb') as stream:
                        for line in stream:
                            # A committed event includes its final newline. A
                            # killed write may end mid-UTF8; discard only that
                            # unterminated tail, never a malformed full record.
                            if not line.endswith(b'\n'):
                                recovered_tail = True
                                break
                            entry = json.loads(line.decode('utf-8'))
                            if entry['sequence'] <= self._sequence:
                                continue
                            if entry['sequence'] != self._sequence + 1 or entry['task_id'] not in self._records:
                                raise ValueError('任务增量日志顺序无效')
                            self._records[entry['task_id']] = _apply_changes(self._records[entry['task_id']], entry['changes'])
                            self._sequence = entry['sequence']
                changed = False
                for record in self._records.values():
                    if record['state'] not in TERMINAL:
                        record.update(state='interrupted', error='引擎已重启，等待手动续做', updated_at=_now())
                        changed = True
                if changed or recovered_tail:
                    self._write(self._records)
                if recovered_tail:
                    with self.journal_path.open('wb') as stream:
                        stream.flush()
                        os.fsync(stream.fileno())
                    self.last_error = '已恢复任务日志：忽略了退出时未完整提交的末尾记录；最后一个处理中单元可能需要重新执行'
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            self.last_error = f'任务日志不可用：{exc}'
            self._blocked = True

    def _write(self, records):
        if self._blocked:
            raise RuntimeError(self.last_error)
        encoded = json.dumps({'version': 1, 'sequence': self._sequence, 'tasks': list(records.values())},
                             ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > self.max_bytes:
            raise ValueError('任务日志容量已满，请减少任务规模')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.tasks-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.last_error = None
        except OSError as exc:
            self.last_error = f'任务日志写入失败：{exc}'
            raise
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _append(self, task_id, changes):
        entry = {'sequence': self._sequence + 1, 'task_id': task_id, 'changes': changes}
        encoded = (json.dumps(entry, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        if len(encoded) > self.max_bytes:
            raise ValueError('任务检查点超过容量限制')
        try:
            journal_size = self.journal_path.stat().st_size if self.journal_path.exists() else 0
            if journal_size + len(encoded) > min(8 * 1024 * 1024, self.max_bytes):
                # A sequence-bearing snapshot is committed before truncating
                # deltas, so interruption at either step remains replayable.
                self._write(self._records)
                with self.journal_path.open('wb') as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                journal_size = 0
            with self.journal_path.open('ab', buffering=0) as stream:
                try:
                    if stream.write(encoded) != len(encoded):
                        raise OSError('任务日志未完整写入')
                    stream.flush()
                    os.fsync(stream.fileno())
                except OSError:
                    try:
                        stream.truncate(journal_size)
                        os.fsync(stream.fileno())
                    except OSError:
                        self._blocked = True
                    raise
            self._sequence += 1
            self.last_error = None
        except OSError as exc:
            self.last_error = f'任务日志写入失败：{exc}'
            raise

    def _change(self, task_id, change):
        with self._lock:
            if self._blocked:
                raise RuntimeError(self.last_error)
            if task_id not in self._records:
                return None
            records = dict(self._records)
            previous = records[task_id]
            record = dict(previous)
            if change(record) is False:
                return {'task_id': task_id, 'state': record['state']}
            record['updated_at'] = _now()
            changes = _diff(previous, record)
            self._append(task_id, changes)
            records[task_id] = _apply_changes(previous, changes)
            self._records = records
            return {'task_id': task_id, 'state': record['state']}

    def create(self, task_id, method, params, *, state='queued'):
        with self._lock:
            if self._blocked:
                raise RuntimeError(self.last_error)
            if task_id in self._records:
                raise ValueError('任务ID已存在，请使用新任务ID')
            cleaned = public_params(params)
            checkpoint = None
            previous_id = params.get('_resume_task_id')
            if previous_id:
                previous = self.retry_spec(previous_id)
                if previous['method'] != method or previous['params'] != cleaned:
                    raise ValueError('续做参数与原任务不一致')
                if previous['input_identities'] != params.get('_input_identities', {}):
                    raise ValueError('续做输入身份与原任务不一致')
                if _directories(previous['output_identities']) != _directories(params.get('_output_identities', {})):
                    raise ValueError('续做输出目录身份与原任务不一致')
                checkpoint = self.get_checkpoint(previous_id)
            record = {'task_id': task_id, 'method': method, 'state': state,
                      'params': cleaned, 'input_identities': copy.deepcopy(params.get('_input_identities', {})),
                      'output_identities': copy.deepcopy(params.get('_output_identities', {})),
                      'checkpoint': checkpoint, 'created_at': _now(), 'updated_at': _now(),
                      'phase': '', 'current': 0, 'total': 0, 'file': '', 'error': '', 'output_files': []}
            if previous_id:
                record['output_files'] = list(self._records[previous_id].get('output_files', []))
            records = dict(self._records)
            if previous_id:
                records[previous_id] = {**records[previous_id], 'retry_task_id': task_id, 'updated_at': _now()}
            records[task_id] = record
            while len(records) > self.max_records:
                removable = next((key for key, item in records.items() if item['state'] in TERMINAL), None)
                if removable is None:
                    raise ValueError('任务日志已满，等待现有任务完成')
                del records[removable]
            self._write(records)
            self._records = records
            self._last_progress = {key: value for key, value in self._last_progress.items() if key in records}
            return copy.deepcopy(record)

    def transition(self, task_id, state):
        if state not in STATES:
            raise ValueError('无效任务状态')
        def change(record):
            if record['state'] in TERMINAL:
                return False
            record['state'] = state
        return self._change(task_id, change)

    def progress(self, task_id, progress):
        with self._lock:
            record = self._records.get(task_id)
            if not record or record['state'] in TERMINAL:
                return
            now = time.monotonic()
            if record.get('phase') == progress.get('phase') and now - self._last_progress.get(task_id, 0) < 0.5:
                return
            self._last_progress[task_id] = now
            def change(item):
                for field in ('phase', 'current', 'total', 'file'):
                    if field in progress:
                        item[field] = progress[field]
            self._change(task_id, change)

    def checkpoint(self, task_id, checkpoint):
        def change(record):
            if record['state'] in TERMINAL:
                return False
            record['checkpoint'] = checkpoint
            paths = []
            for unit in checkpoint.get('units', {}).values():
                artifacts = unit.get('files', {})
                for artifact in artifacts.values() if isinstance(artifacts, dict) else artifacts:
                    if isinstance(artifact, dict) and artifact.get('path'):
                        paths.append(artifact['path'])
            record['output_files'] = list(dict.fromkeys(paths))
        return self._change(task_id, change)

    def get_checkpoint(self, task_id):
        with self._lock:
            return copy.deepcopy(self._records.get(task_id, {}).get('checkpoint'))

    def finish(self, task_id, event):
        def change(record):
            if record['state'] in TERMINAL:
                return False
            result = event.get('result') or {}
            record['state'] = 'cancelled' if event.get('cancelled') else 'completed' if event.get('ok') else 'failed'
            if record['state'] == 'completed':
                record['phase'] = 'done'
                record['current'] = record['total']
            record['error'] = str(event.get('error') or '')[:8192]
            record['output_files'] = list(dict.fromkeys(record['output_files'] + list(result.get('output_files') or [])))
        return self._change(task_id, change)

    def retry_spec(self, task_id):
        with self._lock:
            if self._blocked:
                raise RuntimeError(self.last_error)
            record = self._records.get(task_id)
            if record and record.get('retry_task_id'):
                raise ValueError('此任务已经续做，请使用最新续做任务')
            if not record or record['state'] not in TERMINAL or record['state'] == 'completed':
                raise ValueError('仅失败、取消或中断的任务可续做；已完成或运行中的任务不可重试')
            return copy.deepcopy({key: record[key] for key in ('method', 'params', 'input_identities', 'output_identities')})

    def list(self, limit=100):
        with self._lock:
            result = []
            for record in reversed(list(self._records.values())):
                item = {key: copy.deepcopy(record.get(key)) for key in (
                    'task_id', 'method', 'state', 'phase', 'current', 'total', 'file', 'error', 'created_at', 'updated_at')}
                item['output_files'] = record.get('output_files', [])[:50]
                item['output_count'] = len(record.get('output_files', []))
                item['can_retry'] = record['state'] in TERMINAL and record['state'] != 'completed' and not self._blocked and not record.get('retry_task_id')
                result.append(item)
                if len(result) >= max(1, min(200, int(limit))):
                    break
            return result

    def output_spec(self, task_id):
        with self._lock:
            if self._blocked:
                raise RuntimeError(self.last_error)
            record = self._records.get(task_id)
            if not record:
                raise ValueError('任务不存在')
            return copy.deepcopy({'output_files': record.get('output_files', [])[:50],
                                  'output_identities': record.get('output_identities', {})})
