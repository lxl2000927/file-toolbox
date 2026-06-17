import os
import shutil
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from enum import Enum
import re

# 模块级预编译正则（避免每文件/每调用重新编译）
_RE_LETTERS = re.compile(r"[A-Za-z]")
_RE_DIGITS = re.compile(r"[0-9]")
_RE_CHINESE = re.compile(r"[\u4e00-\u9fff]")
_RE_SYMBOLS = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_RE_INVOICE_NO = re.compile(r"(发票号码|发票号码：|发票号码:)\s*([0-9]{8})")
_RE_INVOICE_CODE = re.compile(r"(发票代码|发票代码：|发票代码:)\s*([0-9]{10,12})")
_RE_INVOICE_DATE = re.compile(r"(开票日期|开票日期：|开票日期:)\s*([0-9]{4}[-年/.][0-9]{1,2}[-月/.][0-9]{1,2})")
_RE_WHITESPACE_MULTI = re.compile(r"\s+")
_RE_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")
_RE_VALIDATE_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}


class RenameOperation(Enum):
    INSERT_TEXT = "insert_text"
    INSERT_NUMBER = "insert_number"
    DELETE_CHARS = "delete_chars"
    REPLACE_TEXT = "replace_text"
    CHANGE_EXTENSION = "change_extension"
    UNIFORM_NAME = "uniform_name"
    SMART_RECOGNIZE = "smart_recognize"
    KEEP_CHARS = "keep_chars"


class RenameRule:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        raw = str(config.get("type", "") or "").strip()
        try:
            self.type = RenameOperation(raw)
        except Exception:
            self.type = None
    
    def apply(self, filename: str, file_index: int = 0, filepath: Optional[str] = None) -> str:
        if self.type is None:
            return filename
        name, ext = os.path.splitext(filename)
        
        if self.type == RenameOperation.INSERT_TEXT:
            return self._apply_insert_text(name, ext)
        elif self.type == RenameOperation.INSERT_NUMBER:
            return self._apply_insert_number(name, ext, file_index)
        elif self.type == RenameOperation.DELETE_CHARS:
            return self._apply_delete_chars(name, ext)
        elif self.type == RenameOperation.REPLACE_TEXT:
            return self._apply_replace_text(name, ext)
        elif self.type == RenameOperation.CHANGE_EXTENSION:
            return self._apply_change_extension(name, ext)
        elif self.type == RenameOperation.UNIFORM_NAME:
            return self._apply_uniform_name(name, ext)
        elif self.type == RenameOperation.SMART_RECOGNIZE:
            return self._apply_smart_recognize(name, ext, file_index, filepath)
        elif self.type == RenameOperation.KEEP_CHARS:
            return self._apply_keep_chars(name, ext)
        
        return filename
    
    def _apply_insert_text(self, name: str, ext: str) -> str:
        text = self.config.get("text", "")
        position = self.config.get("position", "后缀")
        
        if position == "前缀":
            name = text + name
        elif position == "后缀":
            name = name + text
        elif position == "指定位置":
            index = self.config.get("index", 1) - 1
            index = max(0, min(index, len(name)))
            name = name[:index] + text + name[index:]
        
        return name + ext
    
    def _apply_insert_number(self, name: str, ext: str, file_index: int) -> str:
        prefix = self.config.get("prefix", "")
        start = self.config.get("start", 1)
        step = self.config.get("step", 1)
        digits = self.config.get("digits", 3)
        position = self.config.get("position", "后缀")
        
        number = start + file_index * step
        number_str = f"{number:0{digits}d}"
        
        text = prefix + number_str
        
        if position == "前缀":
            name = text + name
        else:
            name = name + text
        
        return name + ext
    
    def _apply_delete_chars(self, name: str, ext: str) -> str:
        delete_type = self.config.get("delete_type", "")
        
        if delete_type == "删除指定字符":
            chars = self.config.get("chars", "")
            name = name.replace(chars, "")
        elif delete_type == "删除前N个字符":
            count = self.config.get("count", 1)
            name = name[count:]
        elif delete_type == "删除后N个字符":
            count = self.config.get("count", 1)
            if count <= 0:
                pass  # Bug fix: count=0 causes name[:-0]="" in Python
            else:
                name = name[:-count] if count < len(name) else ""
        elif delete_type == "delete_patterns":
            targets = self.config.get("targets", []) or []
            custom_chars = self.config.get("custom_chars", "") or ""

            if "letters" in targets:
                name = _RE_LETTERS.sub("", name)
            if "digits" in targets:
                name = _RE_DIGITS.sub("", name)
            if "chinese" in targets:
                name = _RE_CHINESE.sub("", name)
            if "symbols" in targets:
                name = _RE_SYMBOLS.sub("", name)
            if custom_chars:
                pattern = "[" + re.escape(custom_chars) + "]"
                name = re.sub(pattern, "", name)
        
        return name + ext
    
    def _apply_replace_text(self, name: str, ext: str) -> str:
        find = self.config.get("find", "")
        replace = self.config.get("replace", "")
        case_sensitive = bool(self.config.get("case_sensitive", True))
        
        if find:
            if case_sensitive:
                name = name.replace(find, replace)
            else:
                name = re.sub(re.escape(find), lambda m: replace, name, flags=re.IGNORECASE)
        
        return name + ext
    
    def _apply_change_extension(self, name: str, ext: str) -> str:
        new_ext = self.config.get("new_ext", "")
        
        if new_ext:
            if not new_ext.startswith("."):
                new_ext = "." + new_ext
            ext = new_ext
        
        return name + ext

    def _apply_uniform_name(self, name: str, ext: str) -> str:
        base_name = self.config.get("base_name", "").strip()
        if not base_name:
            return name + ext
        return base_name + ext

    @staticmethod
    def _sanitize_filename_component(value: str, max_len: int = 80) -> str:
        if not value:
            return ""
        value = value.strip()
        value = _RE_WHITESPACE_MULTI.sub(" ", value)
        value = _RE_INVALID_CHARS.sub("_", value)
        value = value.strip(" .")
        if len(value) > max_len:
            value = value[:max_len].rstrip(" .")
        return value

    def _extract_title_from_pdf(self, filepath: str) -> str:
        try:
            import PyPDF2
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                meta = getattr(reader, "metadata", None)
                if meta and getattr(meta, "title", None):
                    title = str(meta.title).strip()
                    if title:
                        return title
        except Exception:
            return ""
        return ""

    def _extract_invoice_info_from_pdf(self, filepath: str) -> str:
        try:
            import PyPDF2
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                if not reader.pages:
                    return ""
                text = reader.pages[0].extract_text() or ""
        except Exception:
            return ""

        text = _RE_WHITESPACE_MULTI.sub(" ", text)
        invoice_no = ""
        invoice_code = ""
        date = ""

        m = _RE_INVOICE_NO.search(text)
        if m:
            invoice_no = m.group(2)

        m = _RE_INVOICE_CODE.search(text)
        if m:
            invoice_code = m.group(2)

        m = _RE_INVOICE_DATE.search(text)
        if m:
            date = m.group(2)
            date = date.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
            _dp=date.split("-")
            if len(_dp)==3: date=f"{_dp[0]}-{_dp[1].zfill(2)}-{_dp[2].zfill(2)}"

        parts = []
        if date:
            parts.append(date)
        if invoice_no:
            parts.append(invoice_no)
        if invoice_code:
            parts.append(invoice_code[-4:])

        return "_".join(parts)

    def _apply_smart_recognize(self, name: str, ext: str, file_index: int, filepath: Optional[str]) -> str:
        mode = self.config.get("mode", "content_title")
        position = self.config.get("position", "覆盖原名")
        index = int(self.config.get("index", 1) or 1)

        recognized = ""
        try:
            if filepath and os.path.isfile(filepath):
                lower = filepath.lower()
                if mode == "invoice_info" and lower.endswith(".pdf"):
                    recognized = self._extract_invoice_info_from_pdf(filepath)
                elif mode == "content_title":
                    if lower.endswith(".pdf"):
                        recognized = self._extract_title_from_pdf(filepath)
                    else:
                        try:
                            with open(filepath, "rb") as f:
                                data = f.read(4096)
                            text = data.decode("utf-8-sig", errors="ignore").strip()
                            if text:
                                recognized = text.splitlines()[0].strip()
                        except Exception:
                            recognized = ""
        except Exception:
            recognized = ""

        recognized = self._sanitize_filename_component(recognized)
        if not recognized:
            return name + ext

        if position == "覆盖原名":
            return recognized + ext
        if position == "首位":
            return recognized + name + ext
        if position == "末位":
            return name + recognized + ext

        index = max(1, index)
        insert_at = min(index - 1, len(name))
        return name[:insert_at] + recognized + name[insert_at:] + ext

    def _apply_keep_chars(self, name: str, ext: str) -> str:
        mode = self.config.get("mode", "range")
        if mode == "specified":
            chars = self.config.get("chars", "")
            if not chars:
                return name + ext
            allowed = set(chars)
            kept = "".join([c for c in name if c in allowed])
            return kept + ext

        range_text = str(self.config.get("range", "") or "").strip()
        direction = self.config.get("direction", "从右往左")

        if not range_text:
            return name + ext

        m = _RE_RANGE.match(range_text)
        if not m:
            return name + ext

        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start <= 0 or end <= 0:
            return name + ext
        if start > end:
            start, end = end, start

        s = name
        if direction == "从右往左":
            s = s[::-1]

        start0 = max(0, start - 1)
        end0 = min(len(s), end)
        kept = s[start0:end0]
        if direction == "从右往左":
            kept = kept[::-1]
        return kept + ext


class FileOperationRecord:
    def __init__(self, original_path: str, new_path: str, operation_type: str, 
                 timestamp: Optional[datetime] = None):
        self.original_path = original_path
        self.new_path = new_path
        self.operation_type = operation_type
        self.timestamp = timestamp or datetime.now()
        self.success = False
        self.error_message = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_path": self.original_path,
            "new_path": self.new_path,
            "operation_type": self.operation_type,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "error_message": self.error_message
        }


def _normalized_abs_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _make_unique_copy_path(target_path: str, used_paths: set[str]) -> str:
    # TOCTOU 警告（已知债务）：
    # 当前 engine 为单请求串行模型，不存在并发覆盖问题。
    # 若未来引入多请求并发，需在 open(candidate, "x") 成功到 shutil.copy2 之间
    # 加文件锁（例如 portalocker）或原子 rename 代替 copy2。
    base, ext = os.path.splitext(target_path)
    candidate = target_path
    counter = 1
    while counter <= 10000:
        norm = _normalized_abs_path(candidate)
        if norm not in used_paths:
            # 原子检查：尝试以独占创建模式打开
            try:
                with open(candidate, "x"):
                    pass
                os.remove(candidate)
            except FileExistsError:
                pass
            except OSError:
                pass
            else:
                used_paths.add(norm)
                return candidate
        candidate = f"{base}_副本{counter}{ext}"
        counter += 1
    raise RuntimeError(f"无法生成唯一文件名，已尝试 10000 个副本仍然冲突: {target_path}")


class RenameEngine:
    def __init__(self, history_manager=None):
        self.history_manager = history_manager
        self.rules: List[RenameRule] = []
        self.operation_records: List[FileOperationRecord] = []
    
    def set_rules(self, rule_configs: List[Dict[str, Any]]):
        self.rules = [RenameRule(config) for config in (rule_configs or []) if isinstance(config, dict)]
    
    def clear_rules(self):
        self.rules.clear()
    
    def generate_new_filename(self, original_filename: str, file_index: int = 0, filepath: Optional[str] = None) -> str:
        if not self.rules:
            return original_filename
        
        current_name = original_filename
        
        for rule in self.rules:
            current_name = rule.apply(current_name, file_index, filepath)
        
        return current_name
    
    def batch_generate_filenames(self, filepaths: List[str]) -> List[Tuple[str, str]]:
        results = []

        for i, filepath in enumerate(filepaths):
            original_filename = os.path.basename(filepath)
            new_filename = self.generate_new_filename(original_filename, i, filepath)
            results.append((filepath, new_filename))

        return results

    def execute_rename(self, filepaths: List[str], save_method: str = "copy",
                          output_dir: Optional[str] = None) -> Dict[str, Any]:
        # 输入校验：规范化 filepaths 类型
        if not isinstance(filepaths, (list, tuple)):
            filepaths = [filepaths] if filepaths else []
        if not isinstance(save_method, str):
            save_method = "copy"
        if output_dir is not None and not isinstance(output_dir, str):
            output_dir = str(output_dir) if output_dir else None

        results = {
            "total": len(filepaths),
            "successful": 0,
            "failed": 0,
            "errors": [],
            "operations": []
        }

        self.operation_records.clear()

        if not filepaths:
            results["errors"].append("没有需要处理的文件")
            return results

        if not self.rules:
            results["errors"].append("没有设置重命名规则")
            return results

        used_copy_paths: set[str] = set()

        for i, filepath in enumerate(filepaths):
            if not os.path.exists(filepath):
                record = FileOperationRecord(filepath, "", "rename")
                record.success = False
                record.error_message = "文件不存在"
                self.operation_records.append(record)
                results["failed"] += 1
                results["errors"].append(f"文件不存在: {filepath}")
                continue

            original_filename = os.path.basename(filepath)
            new_filename = self.generate_new_filename(original_filename, i, filepath)
            valid, message = self.validate_filename(new_filename)
            if not valid:
                record = FileOperationRecord(filepath, "", "rename")
                record.success = False
                record.error_message = message
                self.operation_records.append(record)
                results["failed"] += 1
                results["errors"].append(f"文件名无效 {original_filename} -> {new_filename}: {message}")
                results["operations"].append(record.to_dict())
                continue

            original_dir = os.path.dirname(filepath)

            if output_dir:
                target_dir = output_dir
                if not os.path.exists(target_dir):
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                    except Exception as e:
                        record = FileOperationRecord(filepath, "", "rename")
                        record.success = False
                        record.error_message = f"创建输出目录失败: {str(e)}"
                        self.operation_records.append(record)
                        results["failed"] += 1
                        results["errors"].append(f"创建输出目录失败: {target_dir}")
                        continue
            else:
                target_dir = original_dir

            new_filepath = os.path.join(target_dir, new_filename)

            record = FileOperationRecord(filepath, new_filepath, str(save_method or "rename"))

            try:
                if save_method == "overwrite":
                    if os.path.normcase(os.path.abspath(filepath)) == os.path.normcase(os.path.abspath(new_filepath)):
                        if filepath == new_filepath:
                            record.success = True
                            record.error_message = "文件名未改变"
                            results["successful"] += 1
                        else:
                            temp_filepath = f"{new_filepath}.rename_tmp_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                            try:
                                os.rename(filepath, temp_filepath)
                                os.rename(temp_filepath, new_filepath)
                            except Exception:
                                if os.path.exists(temp_filepath) and not os.path.exists(filepath):
                                    try:
                                        os.rename(temp_filepath, filepath)
                                    except Exception:
                                        pass
                                raise
                            record.success = True
                            results["successful"] += 1
                    else:
                        if os.path.exists(new_filepath):
                            raise FileExistsError(f"目标文件已存在，为避免覆盖丢失已停止: {new_filepath}")
                        os.rename(filepath, new_filepath)
                        record.success = True
                        results["successful"] += 1

                elif save_method == "copy":
                    new_filepath = _make_unique_copy_path(new_filepath, used_copy_paths)

                    shutil.copy2(filepath, new_filepath)
                    record.new_path = new_filepath
                    record.success = True
                    results["successful"] += 1

                else:
                    raise ValueError(f"不支持的保存方式: {save_method}")

            except Exception as e:
                record.success = False
                record.error_message = str(e)
                results["failed"] += 1
                results["errors"].append(f"处理文件失败 {original_filename}: {str(e)}")

            self.operation_records.append(record)
            results["operations"].append(record.to_dict())

        self._record_to_history(results)

        return results

    def _record_to_history(self, results: Dict[str, Any]):
        if not self.history_manager:
            return

        from src.utils.history_manager import OperationType
        
        description = f"批量重命名 {results['successful']}/{results['total']} 个文件"
        
        details = {
            "total_files": results["total"],
            "successful_files": results["successful"],
            "failed_files": results["failed"],
            "errors": results["errors"][:20],
            "operations": results["operations"][:20] if results["operations"] else []
        }
        
        success = results["failed"] == 0
        
        self.history_manager.add_record(
            operation_type=OperationType.RENAME,
            description=description,
            details=details,
            success=success,
            error_message=f"失败 {results['failed']} 个文件" if results["failed"] > 0 else None
        )
    
    def undo_last_operation(self) -> Dict[str, Any]:
        if not self.operation_records:
            return {"success": False, "message": "没有可撤销的操作"}
        
        undo_results = {
            "total": 0,
            "successful": 0,
            "failed": 0,
            "errors": []
        }
        
        for record in reversed(self.operation_records):
            if not record.success:
                continue
            
            try:
                op = str(record.operation_type or "rename")
                if op == "copy":
                    if os.path.exists(record.new_path):
                        os.remove(record.new_path)
                        undo_results["successful"] += 1
                    else:
                        undo_results["failed"] += 1
                        undo_results["errors"].append(f"文件不存在: {record.new_path}")
                elif op in ("overwrite", "rename"):
                    if os.path.exists(record.new_path):
                        if os.path.exists(record.original_path):
                            undo_results["failed"] += 1
                            undo_results["errors"].append(f"原路径已存在，无法撤销: {record.original_path}")
                        else:
                            os.rename(record.new_path, record.original_path)
                            undo_results["successful"] += 1
                    else:
                        undo_results["failed"] += 1
                        undo_results["errors"].append(f"文件不存在: {record.new_path}")
                else:
                    undo_results["failed"] += 1
                    undo_results["errors"].append(f"不支持撤销的操作类型: {op}")
            except Exception as e:
                undo_results["failed"] += 1
                undo_results["errors"].append(f"撤销失败 {record.new_path}: {str(e)}")
            
            undo_results["total"] += 1
        
        self.operation_records.clear()
        
        return undo_results
    
    def validate_filename(self, filename: str) -> Tuple[bool, str]:
        if not isinstance(filename, str) or not filename.strip():
            return False, "文件名为空"

        if len(filename) > 255:
            return False, "文件名过长（超过255个字符）"

        if _RE_VALIDATE_INVALID_CHARS.search(filename):
            return False, "文件名包含无效字符"

        name_without_ext = os.path.splitext(filename)[0].upper()
        if name_without_ext in _RESERVED_NAMES:
            return False, f"文件名是系统保留名称: {name_without_ext}"
        
        if filename.endswith('.') or filename.endswith(' '):
            return False, "文件名不能以点或空格结尾"
        
        return True, "文件名有效"
