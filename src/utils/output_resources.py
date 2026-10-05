"""Conservative output budgets and verified, JSON-only output checkpoints.

Budgets are estimates, not reservations. The atomic writer also checks free
space while writing, so a concurrently filling disk cannot publish a partial
output. A single MuPDF output still grows with its final document size.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

MIB = 1024 * 1024
DISK_RESERVE = 8 * MIB
MAX_OUTPUT_MEMORY = 2 * 1024 * MIB


def available_memory_bytes():
    try:
        if os.name == 'nt':
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total',
                    'page_available', 'virtual_total', 'virtual_available', 'extended')]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.available
        else:
            return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (AttributeError, OSError, ValueError):
        pass
    return 1024 * MIB


def memory_budget():
    return min(MAX_OUTPUT_MEMORY, int(available_memory_bytes() * 0.55))


def check_memory(required, budget=None):
    budget = memory_budget() if budget is None else budget
    if required > budget:
        raise ValueError(f'本输出单元预计需要 {required / MIB:.0f} MB 内存，当前预算 {budget / MIB:.0f} MB；'
                         '请选择按页数分卷并减小每卷页数，或减少页面、降低 DPI。完整单文件合并会随文档增长。')


def check_disk(directory, required=0):
    ancestor = Path(directory)
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor)[2]
    if free < required + DISK_RESERVE:
        raise OSError(f'输出磁盘空间不足：预计还需 {(required + DISK_RESERVE) / MIB:.1f} MB，'
                      f'可用 {free / MIB:.1f} MB；请释放空间、更换输出目录或减少本次页面。')


class CheckedOutputStream:
    """Check disk capacity before large writes and before atomic publication."""
    def __init__(self, stream, directory):
        self.stream, self.directory = stream, directory
        self.seek, self.tell, self.flush = stream.seek, stream.tell, stream.flush
        self.since_check = 8 * MIB

    def write(self, data):
        if self.since_check + len(data) >= 8 * MIB:
            check_disk(self.directory, len(data))
            self.since_check = 0
        count = self.stream.write(data)
        self.since_check += count
        return count

    def finish(self):
        self.stream.flush()
        check_disk(self.directory)


def checked_writer(directory, writer):
    def write(stream):
        checked = CheckedOutputStream(stream, directory)
        writer(checked)
        checked.finish()
    return write


def file_identity(path, check=lambda: None):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('输出检查点不是普通文件')
        while chunk := stream.read(MIB):
            check()
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('输出文件在校验时发生变化')
    return {'path': str(path), 'size': after.st_size, 'sha256': digest.hexdigest()}


class OutputCheckpoint:
    """Schema v1: {version, operation, units:{id:{files:{kind:identity},complete}}}.

    operation hashes action, options and actual authorized source signatures.
    identity is {path,size,sha256}. Only files inside the chosen output directory
    with matching size and SHA256 can be reused. `complete` is informational;
    callers require every artifact needed by the unit before skipping it.
    """
    def __init__(self, action, options, sources, directory, checkpoint, callback, check, result):
        payload = json.dumps({'action': action, 'options': options, 'sources': sources},
                             sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        operation = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        self.state = {'version': 1, 'operation': operation, 'units': {}}
        self.directory = Path(directory).resolve()
        self.callback, self.check, self.result = callback, check, result
        self.verified = {}
        if checkpoint is not None:
            if (isinstance(checkpoint, dict) and checkpoint.get('version') == 1
                    and checkpoint.get('operation') == operation and isinstance(checkpoint.get('units'), dict)):
                self.state['units'] = copy.deepcopy(checkpoint['units'])
            else:
                raise ValueError('输出检查点损坏，或与当前参数、来源文件不一致；请重新载入并复核来源后新建任务。已有输出已保留。')

    def get(self, unit, kind):
        key = (unit, kind)
        if key in self.verified:
            return self.verified[key]
        entry = self.state['units'].get(unit)
        record = entry.get('files', {}).get(kind) if isinstance(entry, dict) and isinstance(entry.get('files'), dict) else None
        valid = None
        if isinstance(record, dict):
            try:
                path = Path(record['path'])
                if (path.is_absolute() and not path.is_symlink() and path.resolve().parent == self.directory
                        and stat.S_ISREG(path.stat().st_mode) and path.stat().st_size == record['size']):
                    actual = file_identity(path, self.check)
                    if actual == record:
                        valid = actual
            except InterruptedError:
                raise
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if record is not None and valid is None:
            self.result.setdefault('warnings', []).append('已完成输出缺失或内容变化，已重新处理对应单元并保留已有文件。')
        self.verified[key] = valid
        return valid

    def commit(self, unit, kind, path, complete):
        # Once the atomic file exists, record it even if cancellation arrives;
        # cancellation is honored at the next processing boundary.
        record = file_identity(path)
        self.verified[(unit, kind)] = record
        entry = self.state['units'].get(unit)
        updating = isinstance(entry, dict) and isinstance(entry.get('files'), dict)
        if not isinstance(entry, dict) or not isinstance(entry.get('files'), dict):
            entry = {'files': {}}
            self.state['units'][unit] = entry
        entry['files'][kind], entry['complete'] = record, complete
        # The operation owns this state until it returns. Copy only at an
        # external callback boundary; walking every previous unit at each
        # commit makes a many-output job quadratic even without persistence.
        self.result['checkpoint'] = self.state
        if self.callback is not None:
            callback = getattr(self.callback, 'update_unit' if updating else 'commit_unit', None)
            if callable(callback):
                callback(self.state['operation'], unit, copy.deepcopy(entry))
            else:
                self.callback(copy.deepcopy(self.state))
        return record
