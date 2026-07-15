import json
import os
import atexit
import copy
import tempfile
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict, fields
from enum import Enum
from collections import deque
import threading
import uuid  # Imp5: moved to top level


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
    _MAX_DETAILS_BYTES = 64 * 1024
    _MAX_DETAIL_STRING_CHARS = 4096
    _DETAIL_LIST_LIMITS = {
        "log_tail": 80,
        "operations": 20,
        "errors": 20,
        "output_files": 100,
        "marker_pages": 100,
        "suspect_segments": 100,
    }

    def __init__(self, max_history_size: int = 100, storage_path: Optional[str] = None):
        if not isinstance(max_history_size, int) or max_history_size <= 0:
            max_history_size = 100

        self.max_history_size = max_history_size
        self._storage_path = storage_path
        self.history: List[OperationRecord] = []
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.session_id = str(uuid.uuid4())[:8]
        self.last_error: Optional[str] = None

        # 异步写入：后台线程每 5 秒批量刷盘，避免同步 I/O 阻塞调用方
        self._dirty = threading.Event()
        self._writer_stop = threading.Event()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()
        atexit.register(self._flush_exit)

        if storage_path and os.path.exists(storage_path):
            self._load_from_file()

    @property
    def storage_path(self) -> Optional[str]:
        with self._lock:
            return self._storage_path

    @storage_path.setter
    def storage_path(self, value: Optional[str]) -> None:
        with self._lock:
            self._storage_path = value

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
        if detail.get("cancelled"):
            return "warning"
        if success is False or error_message:
            return "error"
        if success is True:
            if detail.get("suspect_segments"):
                return "warning"
            return "success"
        if detail.get("suspect_segments"):
            return "warning"
        if any(token in text for token in _WARN_TOKENS):
            return "warning"
        if any(token in text for token in _DEBUG_TOKENS):
            return "debug"
        return "info"

    @staticmethod
    def _json_size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    @classmethod
    def _truncate_detail_value(cls, value: Any, key: str = "", depth: int = 0) -> Any:
        if isinstance(value, str):
            if len(value) <= cls._MAX_DETAIL_STRING_CHARS:
                return value
            return value[:cls._MAX_DETAIL_STRING_CHARS] + "…（已截断）"
        if isinstance(value, list):
            limit = cls._DETAIL_LIST_LIMITS.get(key, 100)
            selected = value[-limit:] if key == "log_tail" else value[:limit]
            return [cls._truncate_detail_value(item, depth=depth + 1) for item in selected]
        if isinstance(value, dict):
            if depth >= 8:
                return {"truncated": True}
            return {
                str(item_key): cls._truncate_detail_value(item_value, str(item_key), depth + 1)
                for item_key, item_value in value.items()
            }
        return value

    @classmethod
    def _shrink_largest_detail(cls, details: Dict[str, Any]) -> bool:
        candidates = []
        for key, value in details.items():
            if key in {"details_truncated", "details_original_bytes"} or key.endswith("_truncated") or key.endswith("_original_count"):
                continue
            if isinstance(value, list) and not value:
                continue
            if isinstance(value, str) and not value:
                continue
            if isinstance(value, dict) and (not value or value == {"truncated": True}):
                continue
            try:
                candidates.append((cls._json_size(value), key, value))
            except (TypeError, ValueError):
                continue
        if not candidates:
            return False

        _, key, value = max(candidates, key=lambda item: item[0])
        if isinstance(value, list):
            details.setdefault(f"{key}_original_count", len(value))
            details[f"{key}_truncated"] = True
            if len(value) > 1:
                keep = max(1, len(value) // 2)
                details[key] = value[-keep:] if key == "log_tail" else value[:keep]
            else:
                details[key] = []
        elif isinstance(value, str):
            details[f"{key}_truncated"] = True
            details[key] = value[:max(0, len(value) // 2)] if len(value) > 32 else ""
        elif isinstance(value, dict):
            details[f"{key}_truncated"] = True
            details[key] = {"truncated": True}
        else:
            details.pop(key, None)
        return True

    @classmethod
    def _limit_details(cls, details: Dict[str, Any]) -> Dict[str, Any]:
        try:
            original_size = cls._json_size(details)
        except (TypeError, ValueError):
            return details
        if original_size <= cls._MAX_DETAILS_BYTES:
            return details

        compact = cls._truncate_detail_value(copy.deepcopy(details))
        compact["details_truncated"] = True
        compact["details_original_bytes"] = original_size
        for key, limit in cls._DETAIL_LIST_LIMITS.items():
            original = details.get(key)
            if isinstance(original, list) and len(original) > limit:
                compact[f"{key}_truncated"] = True
                compact[f"{key}_original_count"] = len(original)

        for _ in range(256):
            if cls._json_size(compact) <= cls._MAX_DETAILS_BYTES:
                break
            if not cls._shrink_largest_detail(compact):
                compact = {
                    "details_truncated": True,
                    "details_original_bytes": original_size,
                }
                break
        if cls._json_size(compact) > cls._MAX_DETAILS_BYTES:
            compact = {
                "details_truncated": True,
                "details_original_bytes": original_size,
            }
        return compact

    def add_record(self, operation_type, description: str,
                   details: Dict[str, Any], success: bool = True,
                   error_message: Optional[str] = None,
                   level: Optional[str] = None,
                   source: Optional[str] = None,
                   message: Optional[str] = None) -> str:
        if isinstance(operation_type, str):
            try:
                operation_type = OperationType(operation_type)
            except (ValueError, TypeError):
                operation_type = OperationType.INTERNAL

        record_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()
        details = details if isinstance(details, dict) else {}
        details = self._limit_details(details)
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
            self._dirty.set()

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

    def clear_history(self) -> bool:
        with self._write_lock:
            with self._lock:
                previous = list(self.history)
                self.history.clear()
                storage_path = self._storage_path
                if storage_path and not self._save_to_file_unlocked([], storage_path):
                    self.history = previous
                    return False
                self.last_error = None
                self._dirty.clear()
        return True

    def _save_to_file(self, snapshot: Optional[list[OperationRecord]] = None) -> bool:
        with self._write_lock:
            storage_path = self.storage_path
            if not storage_path:
                return False

            try:
                self.last_error = None
                parent = os.path.dirname(storage_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                if snapshot is None:
                    with self._lock:
                        snapshot = list(self.history)
                return self._save_to_file_unlocked(snapshot or [], storage_path)
            except Exception as e:
                self.last_error = str(e)
                return False

    def _save_to_file_unlocked(self, snapshot: list[OperationRecord], storage_path: Optional[str] = None) -> bool:
        storage_path = storage_path or self.storage_path
        if not storage_path:
            return False
        data = [record.to_dict() for record in snapshot]
        parent = os.path.dirname(storage_path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
            # 使用唯一临时文件名避免并发覆盖
            fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp", prefix="hist_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, storage_path)
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
        storage_path = self.storage_path
        if not storage_path or not os.path.exists(storage_path):
            return

        try:
            file_size = os.path.getsize(storage_path)
            if file_size > 10 * 1024 * 1024:
                self.last_error = f"历史记录文件过大 ({file_size} bytes)，跳过加载"
                return
            with open(storage_path, 'r', encoding='utf-8') as f:
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
                bad_path = f"{storage_path}.corrupt.{ts}"
                os.replace(storage_path, bad_path)
                self.last_error = f"历史记录文件损坏，已备份至 {bad_path}"
            except Exception:
                self.last_error = f"历史记录文件损坏，且无法备份: {storage_path}"
            return
        except Exception as e:
            self.last_error = f"加载历史记录失败: {e}"
            return

    def _writer_loop(self) -> None:
        retry_delay = 5.0
        while not self._writer_stop.is_set():
            try:
                if self._writer_stop.wait(retry_delay):
                    break
                if not self._dirty.is_set():
                    retry_delay = 5.0
                    continue
                self._dirty.clear()
                if self._flush_to_disk():
                    retry_delay = 5.0
                else:
                    self._dirty.set()
                    retry_delay = min(retry_delay * 2.0, 60.0)
            except Exception as e:
                self.last_error = f"历史记录后台写入线程异常: {e}"
                self._dirty.set()
                retry_delay = min(retry_delay * 2.0, 60.0)

    def _flush_to_disk(self) -> bool:
        with self._write_lock:
            with self._lock:
                storage_path = self._storage_path
                snapshot = list(self.history)
            if not storage_path:
                return True
            try:
                return self._save_to_file_unlocked(snapshot, storage_path)
            except Exception as e:
                self.last_error = str(e)
                return False

    def _flush_exit(self) -> None:
        writer_stop = getattr(self, "_writer_stop", None)
        writer_thread = getattr(self, "_writer_thread", None)
        if writer_stop is not None:
            writer_stop.set()
        dirty = getattr(self, "_dirty", None)
        if dirty is not None:
            dirty.set()
        if writer_thread is not None and writer_thread.is_alive():
            writer_thread.join(timeout=5)  # 等 _writer_loop 自然退出，避免双写
        self._flush_to_disk()             # 补刷一次，确保退出前数据落盘

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
