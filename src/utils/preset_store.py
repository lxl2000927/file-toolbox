"""Small versioned settings store, independent of transient renderer state."""
import copy
import json
import os
from pathlib import Path
import threading
import uuid


class PresetStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    @staticmethod
    def _scope(scope):
        if scope not in ('scan', 'workbench'):
            raise ValueError('方案类别无效')

    def _read(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 1024 * 1024:
            raise ValueError('方案文件过大，请检查应用数据目录')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('presets'), list):
            raise ValueError('方案文件格式无效，原文件已保留')
        return data['presets']

    def _write(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.presets-{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({'version': 1, 'presets': records}, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def list(self, scope):
        self._scope(scope)
        with self.lock:
            return copy.deepcopy([item for item in self._read() if item.get('scope') == scope])

    def save(self, scope, name, settings):
        self._scope(scope)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 50:
            raise ValueError('方案名须为 1–50 个字符')
        if not isinstance(settings, dict) or len(json.dumps(settings, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 16000:
            raise ValueError('方案内容无效或超过 16 KB')
        with self.lock:
            records = self._read()
            existing = next((item for item in records if item.get('scope') == scope and item.get('name') == name.strip()), None)
            if len(records) >= 50 and not existing:
                raise ValueError('最多保存 50 个方案，请先删除不需要的方案')
            record = {'id': existing['id'] if existing else uuid.uuid4().hex, 'scope': scope, 'name': name.strip(), 'settings': copy.deepcopy(settings)}
            records = [item for item in records if item.get('id') != record['id']]
            records.append(record)
            self._write(records)
            return copy.deepcopy(record)

    def delete(self, scope, preset_id):
        self._scope(scope)
        with self.lock:
            records = self._read()
            self._write([item for item in records if not (item.get('scope') == scope and item.get('id') == preset_id)])
        return {'deleted': True}
