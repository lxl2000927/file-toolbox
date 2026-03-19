import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import threading


class OperationType(Enum):
    RENAME = "rename"
    PDF_SPLIT = "pdf_split"
    SCAN_SPLIT = "scan_split"
    UPDATE_CHECK = "update_check"
    INTERNAL = "internal"


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
    
    def to_dict(self):
        return {
            **asdict(self),
            "operation_type": self.operation_type.value
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        raw = data.get("operation_type")
        try:
            data["operation_type"] = OperationType(raw)
        except Exception:
            data["operation_type"] = OperationType.INTERNAL
        return cls(**data)


class HistoryManager:
    def __init__(self, max_history_size: int = 100, storage_path: Optional[str] = None):
        import uuid

        self.max_history_size = max_history_size
        self.storage_path = storage_path
        self.history: List[OperationRecord] = []
        self._lock = threading.Lock()
        self.session_id = str(uuid.uuid4())[:8]
        
        if storage_path and os.path.exists(storage_path):
            self._load_from_file()
    
    def add_record(self, operation_type, description: str, 
                   details: Dict[str, Any], success: bool = True, 
                   error_message: Optional[str] = None) -> str:
        import uuid

        if isinstance(operation_type, str):
            try:
                operation_type = OperationType(operation_type)
            except Exception:
                operation_type = OperationType.INTERNAL
        
        record_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()
        
        record = OperationRecord(
            id=record_id,
            operation_type=operation_type,
            timestamp=timestamp,
            description=description,
            details=details,
            session_id=str(getattr(self, "session_id", "") or ""),
            success=success,
            error_message=error_message
        )

        snapshot: list[OperationRecord]
        with self._lock:
            self.history.insert(0, record)
            if len(self.history) > self.max_history_size:
                self.history = self.history[:self.max_history_size]
            snapshot = list(self.history)

        self._save_to_file(snapshot)
        
        return record_id
    
    def get_recent_records(self, count: int = 10, 
                          operation_type: Optional[OperationType] = None,
                          session_id: Optional[str] = None) -> List[OperationRecord]:
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
        snapshot: list[OperationRecord]
        with self._lock:
            self.history.clear()
            snapshot = list(self.history)
        self._save_to_file(snapshot)
    
    def _save_to_file(self, snapshot: Optional[list[OperationRecord]] = None) -> bool:
        if not self.storage_path:
            return False
        
        try:
            parent = os.path.dirname(self.storage_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if snapshot is None:
                with self._lock:
                    snapshot = list(self.history)
            data = [record.to_dict() for record in (snapshot or [])]
            tmp_path = f"{self.storage_path}.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.storage_path)
                return True
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
        except Exception:
            return False
    
    def _load_from_file(self):
        if not self.storage_path or not os.path.exists(self.storage_path):
            return
        
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                with self._lock:
                    records: list[OperationRecord] = []
                    if isinstance(data, list):
                        for item in data:
                            if not isinstance(item, dict):
                                continue
                            try:
                                records.append(OperationRecord.from_dict(item))
                            except Exception:
                                continue
                    self.history = records
        except Exception:
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                bad_path = f"{self.storage_path}.corrupt.{ts}"
                os.replace(self.storage_path, bad_path)
            except Exception:
                pass
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
