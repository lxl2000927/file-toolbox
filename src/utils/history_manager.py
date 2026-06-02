import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict, fields
from enum import Enum
from collections import deque
import threading


class OperationType(Enum):
    RENAME = "rename"
    PDF_SPLIT = "pdf_split"
    SCAN_SPLIT = "scan_split"
    UPDATE_CHECK = "update_check"
    INTERNAL = "internal"

# 模块级常量：用于日志级别推断的关键词
_WARN_TOKENS = ("warning", "warn", "警告", "重试", "降级", "漏检", "未匹配", "未命中", "取消", "停止", "跳过")
_DEBUG_TOKENS = ("候选", "面积", "圆度", "内点", "比例", "样本", "解码", "debug")


@dataclass
class OperationRecord:
    id: str
    operation_type: OperationType
    timestamp: str
    description: str
    details: Dict[str, Any]
    session_id: str = ""
    success: bool = True
    error_message: Optional[str] = None
    level: str = "info"
    source: str = "system"
    message: str = ""

    def to_dict(self):
        return {
            **asdict(self),
            "operation_type": self.operation_type.value
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        data = dict(data or {})
        raw = data.get("operation_type")
        try:
            data["operation_type"] = OperationType(raw)
        except (ValueError, TypeError):
            data["operation_type"] = OperationType.INTERNAL
        data.setdefault("details", {})
        data.setdefault("session_id", "")
        data.setdefault("success", True)
        data.setdefault("error_message", None)
        data.setdefault("level", HistoryManager.infer_level(data.get("success", True), data.get("error_message"), data.get("details", {}), data.get("description", "")))
        data.setdefault("source", HistoryManager.infer_source(data.get("operation_type")))
        data.setdefault("message", data.get("description", ""))
        allowed = {field.name for field in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


class HistoryManager:
    # 模块级常量避免每调用重建
    _allowed_fields = {field.name for field in fields(OperationRecord)}

    def __init__(self, max_history_size: int = 100, storage_path: Optional[str] = None):
        import uuid

        if not isinstance(max_history_size, int) or max_history_size <= 0:
            max_history_size = 100

        self.max_history_size = max_history_size
        self.storage_path = storage_path
        self.history: List[OperationRecord] = []
        self._lock = threading.Lock()
        self.session_id = str(uuid.uuid4())[:8]
        self.last_error: Optional[str] = None

        if storage_path and os.path.exists(storage_path):
            self._load_from_file()

    @staticmethod
    def infer_source(operation_type) -> str:
        value = operation_type.value if isinstance(operation_type, OperationType) else str(operation_type or "")
        if value in {OperationType.RENAME.value, OperationType.PDF_SPLIT.value, OperationType.SCAN_SPLIT.value}:
            return value
        return "system"

    @staticmethod
    def infer_level(success: bool = True, error_message: Optional[str] = None, details: Optional[Dict[str, Any]] = None, description: str = "") -> str:
        text = f"{description or ''} {error_message or ''}".lower()
        # 确保 details 是 dict，否则返回 info
        try:
            detail = details if isinstance(details, dict) else {}
        except Exception:
            detail = {}
        if success is False or error_message:
            return "error"
        if success is True:
            if detail.get("cancelled") or detail.get("suspect_segments"):
                return "warning"
            return "success"
        if detail.get("cancelled") or detail.get("suspect_segments"):
            return "warning"
        if any(token in text for token in _WARN_TOKENS):
            return "warning"
        if any(token in text for token in _DEBUG_TOKENS):
            return "debug"
        return "info"

    def add_record(self, operation_type, description: str,
                   details: Dict[str, Any], success: bool = True,
                   error_message: Optional[str] = None,
                   level: Optional[str] = None,
                   source: Optional[str] = None,
                   message: Optional[str] = None) -> str:
        import uuid

        if isinstance(operation_type, str):
            try:
                operation_type = OperationType(operation_type)
            except (ValueError, TypeError):
                operation_type = OperationType.INTERNAL

        record_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()
        details = details if isinstance(details, dict) else {}
        normalized_source = source or self.infer_source(operation_type)
        normalized_level = level or self.infer_level(success, error_message, details, description)
        normalized_message = message or description

        record = OperationRecord(
            id=record_id,
            operation_type=operation_type,
            timestamp=timestamp,
            description=description,
            details=details,
            session_id=str(getattr(self, "session_id", "") or ""),
            success=bool(success),
            error_message=error_message,
            level=normalized_level,
            source=normalized_source,
            message=normalized_message,
        )

        with self._lock:
            self.history.insert(0, record)
            if len(self.history) > self.max_history_size:
                self.history = self.history[:self.max_history_size]
            try:
                self._save_to_file_unlocked(list(self.history))
            except Exception as e:
                self.last_error = str(e)

        return record_id

    def get_recent_records(self, count: int = 10,
                           operation_type: Optional[OperationType] = None,
                           session_id: Optional[str] = None) -> List[OperationRecord]:
        count = max(0, count)
        with self._lock:
            filtered = list(self.history)
        if session_id is not None:
            sid = str(session_id or "")
            filtered = [r for r in filtered if str(getattr(r, "session_id", "") or "") == sid]
        if operation_type:
            filtered = [r for r in filtered if r.operation_type == operation_type]
        return filtered[:count]

    def get_record_by_id(self, record_id: str) -> Optional[OperationRecord]:
        with self._lock:
            for record in self.history:
                if record.id == record_id:
                    return record
            return None

    def clear_history(self):
        with self._lock:
            self.history.clear()
            try:
                self._save_to_file_unlocked(list(self.history))
            except Exception as e:
                self.last_error = str(e)

    def _save_to_file(self, snapshot: Optional[list[OperationRecord]] = None) -> bool:
        if not self.storage_path:
            return False

        try:
            self.last_error = None
            parent = os.path.dirname(self.storage_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if snapshot is None:
                with self._lock:
                    snapshot = list(self.history)
            return self._save_to_file_unlocked(snapshot or [])
        except Exception as e:
            self.last_error = str(e)
            return False

    def _save_to_file_unlocked(self, snapshot: list[OperationRecord]) -> bool:
        if not self.storage_path:
            return False
        data = [record.to_dict() for record in snapshot]
        parent = os.path.dirname(self.storage_path) or "."
        try:
            # 使用唯一临时文件名避免并发覆盖
            fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp", prefix="hist_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.storage_path)
                return True
            except Exception:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                raise
        except Exception as e:
            self.last_error = str(e)
            return False

    def _load_from_file(self):
        if not self.storage_path or not os.path.exists(self.storage_path):
            return

        try:
            file_size = os.path.getsize(self.storage_path)
            if file_size > 10 * 1024 * 1024:
                self.last_error = f"历史记录文件过大 ({file_size} bytes)，跳过加载"
                return
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            records: list[OperationRecord] = []
            skipped = 0
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        skipped += 1
                        continue
                    try:
                        records.append(OperationRecord.from_dict(item))
                    except Exception:
                        skipped += 1
                        continue
            with self._lock:
                self.history = records
            if skipped > 0:
                self.last_error = f"跳过 {skipped} 条损坏的历史记录"
        except json.JSONDecodeError:
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                bad_path = f"{self.storage_path}.corrupt.{ts}"
                os.replace(self.storage_path, bad_path)
                self.last_error = f"历史记录文件损坏，已备份至 {bad_path}"
            except Exception:
                self.last_error = f"历史记录文件损坏，且无法备份: {self.storage_path}"
            return
        except Exception as e:
            self.last_error = f"加载历史记录失败: {e}"
            return

    def get_statistics(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = list(self.history)

        total = len(snapshot)
        successful = sum(1 for r in snapshot if r.success)
        failed = total - successful

        by_type = {}
        for record in snapshot:
            op_type = record.operation_type.value
            by_type[op_type] = by_type.get(op_type, 0) + 1

        return {
            "total_operations": total,
            "successful_operations": successful,
            "failed_operations": failed,
            "operations_by_type": by_type,
            "success_rate": successful / total if total > 0 else 0
        }
