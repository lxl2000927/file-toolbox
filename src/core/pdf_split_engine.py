import os
import shutil
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
try:
    import PyPDF2
except Exception:
    PyPDF2 = None
import re


class SplitMode(Enum):
    BY_PAGE_COUNT = "by_page_count"
    BY_FILE_SIZE = "by_file_size"
    BY_PAGE_RANGE = "by_page_range"
    BY_BOOKMARK = "by_bookmark"


class PdfSplitConfig:
    def __init__(self, config_dict: Dict[str, Any]):
        raw_mode = config_dict.get("mode", "by_page_count")
        try:
            self.mode = SplitMode(raw_mode)
        except Exception:
            self.mode = SplitMode.BY_PAGE_COUNT
        self.page_count = config_dict.get("page_count", 10)
        self.max_size = config_dict.get("max_size", 10)
        self.size_unit = config_dict.get("size_unit", "MB")
        self.page_ranges = config_dict.get("page_ranges", "")
        self.bookmark_level = config_dict.get("bookmark_level", 1)
        self.output_dir = config_dict.get("output_dir", "")
        self.file_prefix = config_dict.get("file_prefix", "")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "page_count": self.page_count,
            "max_size": self.max_size,
            "size_unit": self.size_unit,
            "page_ranges": self.page_ranges,
            "bookmark_level": self.bookmark_level,
            "output_dir": self.output_dir,
            "file_prefix": self.file_prefix
        }


class SplitOperationRecord:
    def __init__(self, original_path: str, output_files: List[str], operation_type: str,
                 timestamp: Optional[datetime] = None):
        self.original_path = original_path
        self.output_files = output_files
        self.operation_type = operation_type
        self.timestamp = timestamp or datetime.now()
        self.success = False
        self.error_message = ""
        self.total_pages = 0
        self.output_count = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_path": self.original_path,
            "output_files": self.output_files[:5],
            "operation_type": self.operation_type,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "error_message": self.error_message,
            "total_pages": self.total_pages,
            "output_count": self.output_count
        }


@dataclass(frozen=True)
class PlannedOutput:
    filename: str
    page_range: Optional[Tuple[int, int]] = None


class PdfSplitEngine:
    def __init__(self, history_manager=None):
        self.history_manager = history_manager
        self.config: Optional[PdfSplitConfig] = None
        self.operation_records: List[SplitOperationRecord] = []

    def _execute_single(self, pdf_path: str, config_dict: Dict[str, Any]) -> List[str]:
        result = self.execute_split([pdf_path], config_dict)
        if not isinstance(result, dict):
            raise RuntimeError("拆分执行失败")
        if int(result.get("successful") or 0) <= 0:
            errors = result.get("errors") or []
            msg = errors[0] if errors else "拆分执行失败"
            raise RuntimeError(str(msg))
        ops = result.get("operations") or []
        if not ops:
            return []
        first = ops[0] or {}
        files = first.get("output_files") or []
        return [str(p) for p in files if p]

    def split_by_page_count(self, pdf_path: str, output_dir: str, prefix: str, page_count: int) -> List[str]:
        config = {
            "mode": SplitMode.BY_PAGE_COUNT.value,
            "output_dir": str(output_dir or ""),
            "file_prefix": str(prefix or ""),
            "page_count": int(page_count or 1),
        }
        return self._execute_single(pdf_path, config)

    def split_by_page_ranges(self, pdf_path: str, output_dir: str, prefix: str, page_ranges: str) -> List[str]:
        config = {
            "mode": SplitMode.BY_PAGE_RANGE.value,
            "output_dir": str(output_dir or ""),
            "file_prefix": str(prefix or ""),
            "page_ranges": str(page_ranges or ""),
        }
        return self._execute_single(pdf_path, config)

    def split_by_file_size(
        self,
        pdf_path: str,
        output_dir: str,
        prefix: str,
        max_size: float,
        *,
        size_unit: str = "MB",
    ) -> List[str]:
        config = {
            "mode": SplitMode.BY_FILE_SIZE.value,
            "output_dir": str(output_dir or ""),
            "file_prefix": str(prefix or ""),
            "max_size": float(max_size or 0.0),
            "size_unit": str(size_unit or "MB"),
        }
        return self._execute_single(pdf_path, config)

    def split_by_bookmark(self, pdf_path: str, output_dir: str, prefix: str, bookmark_level: int) -> List[str]:
        config = {
            "mode": SplitMode.BY_BOOKMARK.value,
            "output_dir": str(output_dir or ""),
            "file_prefix": str(prefix or ""),
            "bookmark_level": int(bookmark_level or 1),
        }
        return self._execute_single(pdf_path, config)

    @staticmethod
    def make_unique_output_path(output_dir: str, filename: str, used_paths: set[str]) -> str:
        name = filename or "output.pdf"
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        base, ext = os.path.splitext(name)
        candidate = os.path.join(output_dir, name)
        if candidate not in used_paths and not os.path.exists(candidate):
            used_paths.add(candidate)
            return candidate
        counter = 2
        while True:
            new_name = f"{base}_{counter}{ext}"
            candidate = os.path.join(output_dir, new_name)
            if candidate not in used_paths and not os.path.exists(candidate):
                used_paths.add(candidate)
                return candidate
            counter += 1
    
    def set_config(self, config_dict: Dict[str, Any]):
        self.config = PdfSplitConfig(config_dict)
    
    def validate_pdf_file(self, filepath: str) -> Tuple[bool, str, Optional[int]]:
        if not os.path.exists(filepath):
            return False, "文件不存在", None
        
        if not filepath.lower().endswith('.pdf'):
            return False, "文件不是PDF格式", None

        if PyPDF2 is None:
            return False, "缺少依赖：PyPDF2", None
        
        try:
            with open(filepath, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                page_count = len(pdf_reader.pages)
                
                if page_count == 0:
                    return False, "PDF文件没有页面", 0
                
                return True, "文件有效", page_count
        except Exception as e:
            return False, f"读取PDF文件失败: {str(e)}", None
    
    def parse_page_ranges(self, range_str: str, total_pages: int) -> List[Tuple[int, int]]:
        ranges = []
        
        if not range_str.strip():
            return [(1, total_pages)]
        
        parts = [part.strip() for part in range_str.split(',')]
        
        for part in parts:
            if '-' in part:
                start_end = part.split('-')
                if len(start_end) != 2:
                    continue
                
                try:
                    start = int(start_end[0].strip())
                    end = int(start_end[1].strip())
                    
                    start = max(1, min(start, total_pages))
                    end = max(start, min(end, total_pages))
                    
                    ranges.append((start, end))
                except ValueError:
                    continue
            else:
                try:
                    page = int(part.strip())
                    page = max(1, min(page, total_pages))
                    ranges.append((page, page))
                except ValueError:
                    continue
        
        if not ranges:
            ranges = [(1, total_pages)]
        
        return ranges

    @staticmethod
    def _sanitize_title(title: str) -> str:
        safe = re.sub(r"[^\w\-_\. ]", "_", str(title or "未命名"))
        safe = safe.strip() or "未命名"
        return safe[:50]

    def plan_outputs_for_file(self, pdf_path: str, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        config = PdfSplitConfig(config_dict)
        prefix = config.file_prefix or ""
        base = os.path.basename(pdf_path)
        stem = os.path.splitext(base)[0]
        output_dir = config.output_dir or os.path.dirname(pdf_path)

        if PyPDF2 is None:
            return {
                "valid": False,
                "message": "缺少依赖：PyPDF2",
                "page_count": None,
                "output_dir": output_dir,
                "outputs": [],
            }

        if not os.path.exists(pdf_path):
            return {
                "valid": False,
                "message": "文件不存在",
                "page_count": None,
                "output_dir": output_dir,
                "outputs": [],
            }

        if not pdf_path.lower().endswith(".pdf"):
            return {
                "valid": False,
                "message": "文件不是PDF格式",
                "page_count": None,
                "output_dir": output_dir,
                "outputs": [],
            }

        try:
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                total_pages = int(len(reader.pages))
                if total_pages <= 0:
                    return {
                        "valid": False,
                        "message": "PDF文件没有页面",
                        "page_count": 0,
                        "output_dir": output_dir,
                        "outputs": [],
                    }

                outputs: list[PlannedOutput] = []

                if config.mode == SplitMode.BY_PAGE_COUNT:
                    per = max(1, int(config.page_count or 1))
                    if total_pages <= per:
                        outputs.append(PlannedOutput(f"{prefix}{base}", (1, total_pages)))
                    else:
                        num_chunks = (total_pages + per - 1) // per
                        for i in range(num_chunks):
                            start = i * per + 1
                            end = min((i + 1) * per, total_pages)
                            outputs.append(PlannedOutput(f"{prefix}{stem}_part{i + 1}.pdf", (start, end)))

                elif config.mode == SplitMode.BY_PAGE_RANGE:
                    ranges = self.parse_page_ranges(str(config.page_ranges or ""), total_pages)
                    for i, (start, end) in enumerate(ranges):
                        outputs.append(PlannedOutput(f"{prefix}{stem}_range{i + 1}.pdf", (start, end)))

                elif config.mode == SplitMode.BY_FILE_SIZE:
                    max_size = float(config.max_size or 10)
                    max_size_mb = (max_size / 1024.0) if str(config.size_unit or "MB") == "KB" else max_size

                    try:
                        file_size_bytes = os.path.getsize(pdf_path)
                        file_size_mb = file_size_bytes / (1024 * 1024)
                    except Exception:
                        file_size_mb = 0.0

                    if file_size_mb <= 0:
                        return {
                            "valid": False,
                            "message": "无法读取文件大小，无法按大小拆分",
                            "page_count": total_pages,
                            "output_dir": output_dir,
                            "outputs": [],
                        }
                    if file_size_mb <= max_size_mb:
                        outputs.append(PlannedOutput(f"{prefix}{base}", (1, total_pages)))
                    else:
                        avg_page_size_mb = file_size_mb / total_pages
                        pages_per_part = max(1, int(max_size_mb / avg_page_size_mb)) if avg_page_size_mb > 0 else 1
                        num_chunks = (total_pages + pages_per_part - 1) // pages_per_part
                        for i in range(num_chunks):
                            start = i * pages_per_part + 1
                            end = min((i + 1) * pages_per_part, total_pages)
                            outputs.append(PlannedOutput(f"{prefix}{stem}_part{i + 1}.pdf", (start, end)))

                elif config.mode == SplitMode.BY_BOOKMARK:
                    level = max(1, int(config.bookmark_level or 1))
                    outline = getattr(reader, "outline", None)
                    if not outline:
                        outputs.append(PlannedOutput(f"{prefix}{base}", (1, total_pages)))
                    else:
                        bookmarks = self._extract_bookmarks(reader, outline, level, total_pages)
                        if not bookmarks:
                            outputs.append(PlannedOutput(f"{prefix}{base}", (1, total_pages)))
                        else:
                            for i, (title, start, end) in enumerate(bookmarks):
                                safe_title = self._sanitize_title(title)
                                s = max(1, int(start or 1))
                                e = int(end or start or s)
                                if e < s:
                                    e = s
                                outputs.append(PlannedOutput(f"{prefix}{safe_title}_{i + 1}.pdf", (s, e)))

                else:
                    return {
                        "valid": False,
                        "message": "不支持的拆分模式",
                        "page_count": total_pages,
                        "output_dir": output_dir,
                        "outputs": [],
                    }

                return {
                    "valid": True,
                    "message": "OK",
                    "page_count": total_pages,
                    "output_dir": output_dir,
                    "outputs": outputs,
                }
        except Exception as e:
            return {
                "valid": False,
                "message": f"读取PDF文件失败: {str(e)}",
                "page_count": None,
                "output_dir": output_dir,
                "outputs": [],
            }

    def _write_planned_outputs(
        self,
        pdf_path: str,
        *,
        output_dir: str,
        outputs: list[PlannedOutput],
        used_paths: Optional[set[str]] = None,
    ) -> list[str]:
        if PyPDF2 is None:
            raise RuntimeError("缺少依赖：PyPDF2")
        if not outputs:
            return []

        os.makedirs(output_dir, exist_ok=True)
        used_paths = used_paths or set()

        with open(pdf_path, "rb") as src_f:
            reader = PyPDF2.PdfReader(src_f)
            total_pages = len(reader.pages)
            if total_pages <= 0:
                raise RuntimeError("PDF文件没有页面")

            if (
                len(outputs) == 1
                and outputs[0].page_range
                and outputs[0].page_range == (1, total_pages)
            ):
                out_path = self.make_unique_output_path(output_dir, outputs[0].filename, used_paths)
                shutil.copy2(pdf_path, out_path)
                return [out_path]

            output_paths: list[str] = []
            for planned in outputs:
                if not planned.page_range or len(planned.page_range) != 2:
                    raise ValueError("输出计划缺少页码范围，无法执行拆分")

                start, end = planned.page_range
                start = int(start)
                end = int(end)
                start = max(1, min(start, total_pages))
                end = max(start, min(end, total_pages))

                out_path = self.make_unique_output_path(output_dir, planned.filename, used_paths)
                tmp_path = f"{out_path}.tmp"
                try:
                    with open(tmp_path, "wb") as out_f:
                        writer = PyPDF2.PdfWriter()
                        for page_num in range(start - 1, end):
                            writer.add_page(reader.pages[page_num])
                        writer.write(out_f)
                    os.replace(tmp_path, out_path)
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    raise
                output_paths.append(out_path)

            return output_paths
    
    def _destination_page_1based(self, pdf_reader, item) -> Optional[int]:
        if item is None:
            return None
        try:
            page_num = getattr(item, "page", None)
            if page_num is not None and isinstance(page_num, int):
                return max(1, int(page_num) + 1)
        except Exception:
            pass
        try:
            idx = pdf_reader.get_destination_page_number(item)
            return max(1, int(idx) + 1)
        except Exception:
            return None

    def _collect_bookmark_starts(
        self,
        pdf_reader,
        outline,
        target_level: int,
        *,
        current_level: int = 1,
        starts: Optional[list[Tuple[str, int]]] = None,
    ) -> list[Tuple[str, int]]:
        starts = starts or []
        if not outline:
            return starts

        for item in outline:
            if isinstance(item, list):
                self._collect_bookmark_starts(
                    pdf_reader, item, target_level, current_level=current_level + 1, starts=starts
                )
                continue

            title = getattr(item, "title", "未命名")
            if current_level == target_level:
                page_1based = self._destination_page_1based(pdf_reader, item)
                if page_1based is not None:
                    starts.append((str(title), int(page_1based)))

            children = getattr(item, "children", None)
            if children and current_level < target_level:
                self._collect_bookmark_starts(
                    pdf_reader, children, target_level, current_level=current_level + 1, starts=starts
                )

        return starts

    def _extract_bookmarks(
        self, pdf_reader, outline, target_level: int, total_pages: int
    ) -> List[Tuple[str, int, int]]:
        starts = self._collect_bookmark_starts(pdf_reader, outline, target_level)
        total_pages = max(0, int(total_pages or 0))
        if not starts or total_pages <= 0:
            return []

        normalized: list[Tuple[str, int]] = []
        for title, start_page in starts:
            s = max(1, min(int(start_page), total_pages))
            normalized.append((title, s))
        normalized.sort(key=lambda x: x[1])

        bookmarks: list[Tuple[str, int, int]] = []
        for i, (title, start) in enumerate(normalized):
            next_start = normalized[i + 1][1] if i + 1 < len(normalized) else (total_pages + 1)
            end = max(start, min(total_pages, next_start - 1))
            bookmarks.append((title, start, end))

        return bookmarks
    
    def execute_split(self, pdf_paths: List[str], config_dict: Dict[str, Any]) -> Dict[str, Any]:
        results = {
            "total": len(pdf_paths),
            "successful": 0,
            "failed": 0,
            "errors": [],
            "operations": []
        }
        
        self.operation_records.clear()
        self.set_config(config_dict)
        
        if not pdf_paths:
            results["errors"].append("没有需要处理的PDF文件")
            return results
        
        if not self.config:
            results["errors"].append("没有设置拆分配置")
            return results

        configured_output_dir = self.config.output_dir or ""
        if configured_output_dir and not os.path.exists(configured_output_dir):
            try:
                os.makedirs(configured_output_dir, exist_ok=True)
            except Exception as e:
                results["errors"].append(f"创建输出目录失败: {str(e)}")
                return results

        used_paths: set[str] = set()
        
        for pdf_path in pdf_paths:
            record = SplitOperationRecord(pdf_path, [], "split")
            
            try:
                planned = self.plan_outputs_for_file(pdf_path, config_dict)
                if not planned.get("valid"):
                    raise RuntimeError(str(planned.get("message") or "无法生成拆分计划"))
                record.total_pages = int(planned.get("page_count") or 0)
                output_dir = configured_output_dir or str(planned.get("output_dir") or os.path.dirname(pdf_path))
                outputs = planned.get("outputs") or []
                if any(getattr(o, "page_range", None) is None for o in outputs):
                    raise RuntimeError("输出计划不完整，无法执行拆分")

                output_files = self._write_planned_outputs(
                    pdf_path,
                    output_dir=output_dir,
                    outputs=list(outputs),
                    used_paths=used_paths,
                )
                
                record.output_files = output_files
                record.output_count = len(output_files)
                record.success = True
                results["successful"] += 1
            
            except Exception as e:
                record.success = False
                record.error_message = str(e)
                results["failed"] += 1
                results["errors"].append(f"处理文件失败 {os.path.basename(pdf_path)}: {str(e)}")
            
            self.operation_records.append(record)
            results["operations"].append(record.to_dict())
        
        self._record_to_history(results)
        
        return results
    
    def _record_to_history(self, results: Dict[str, Any]):
        if not self.history_manager:
            return

        from utils.history_manager import OperationType
        
        description = f"PDF拆分 {results['successful']}/{results['total']} 个文件"
        
        details = {
            "total_files": results["total"],
            "successful_files": results["successful"],
            "failed_files": results["failed"],
            "total_outputs": sum(len(record.get("output_files", [])) for record in results["operations"]),
            "errors": results["errors"][:5],
            "operations": results["operations"][:5] if results["operations"] else []
        }
        
        success = results["failed"] == 0
        
        self.history_manager.add_record(
            operation_type=OperationType.PDF_SPLIT,
            description=description,
            details=details,
            success=success,
            error_message=f"失败 {results['failed']} 个文件" if results["failed"] > 0 else None
        )
