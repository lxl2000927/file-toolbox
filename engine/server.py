"""
engine/server.py — JSON-RPC 2.0 stdio 服务
与 Electron 主进程通过 stdin/stdout 逐行 JSON 通信。

启动：python engine/server.py
协议：每行一个 JSON 对象，\n 分隔
"""
from __future__ import annotations

import os
import sys
import json
import math
import numbers
import threading
import traceback
import time
import hmac
from collections import deque
from dataclasses import asdict, is_dataclass, replace
from typing import Any, Callable, Dict

ENGINE_AUTH_TOKEN = os.environ.get("FILE_TOOLBOX_ENGINE_TOKEN", "")

# ── 确保能 import src.*（server.py 在 engine/ 子目录） ──────
_ENGINE_DIR = __file__ if "__file__" in dir() else "."
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(_ENGINE_DIR)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_SRC_ROOT = os.path.join(_PROJECT_ROOT, "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

# ── 关键：Windows 上必须关掉 I/O 缓冲 ──────────────────────
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ── 导入现有引擎（零修改） ──────────────────────────────────
try:
    from src.core.rename_engine import RenameEngine
except Exception:
    RenameEngine = None  # type: ignore[assignment]

try:
    from src.core.pdf_split_engine import PdfSplitEngine
except Exception:
    PdfSplitEngine = None  # type: ignore[assignment]

PdfScanSplitEngine = None  # type: ignore[assignment]
PdfScanSplitOptions = None  # type: ignore[assignment]
_PDF_SCAN_IMPORT_ERROR = ""
_PDF_SCAN_IMPORT_ATTEMPTED = False
_PDF_SCAN_IMPORT_LOCK = threading.Lock()

# ── 全局 HistoryManager（所有引擎共享，操作自动记录） ────────
try:
    from src.utils.history_manager import HistoryManager
    _HISTORY_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "FileToolbox")
    _HISTORY_PATH = os.path.join(_HISTORY_DIR, "history.json")
    _HISTORY_MANAGER = HistoryManager(storage_path=_HISTORY_PATH)
except Exception:
    _HISTORY_MANAGER = None  # type: ignore[assignment]

# [Bug#2 Fix] 优雅关闭标志：handle_shutdown 置位后，主循环在发送响应后干净退出，
# 触发 atexit 回调刷盘，避免 Windows 上 process.kill() 硬杀导致最近 5s 历史丢失
_SHUTDOWN_REQUESTED = False

try:
    from src.utils.path_utils import make_unique_output_path
except Exception:
    make_unique_output_path = None  # type: ignore[assignment]


def _make_rename_engine() -> RenameEngine:
    if RenameEngine is None:
        raise RuntimeError("RenameEngine 不可用（缺少依赖）")
    return RenameEngine(history_manager=_HISTORY_MANAGER)


def _make_pdf_split_engine() -> PdfSplitEngine:
    if PdfSplitEngine is None:
        raise RuntimeError("PdfSplitEngine 不可用（缺少依赖：pypdf）")
    return PdfSplitEngine(history_manager=_HISTORY_MANAGER)


def _ensure_pdf_scan_engine() -> None:
    global PdfScanSplitEngine, PdfScanSplitOptions, _PDF_SCAN_IMPORT_ERROR, _PDF_SCAN_IMPORT_ATTEMPTED
    if _PDF_SCAN_IMPORT_ATTEMPTED and PdfScanSplitEngine is not None and PdfScanSplitOptions is not None:
        return
    with _PDF_SCAN_IMPORT_LOCK:
        if _PDF_SCAN_IMPORT_ATTEMPTED and PdfScanSplitEngine is not None and PdfScanSplitOptions is not None:
            return
        try:
            from src.core.pdf_scan_split_engine import PdfScanSplitEngine as Engine, PdfScanSplitOptions as Options
            PdfScanSplitEngine = Engine
            PdfScanSplitOptions = Options
            _PDF_SCAN_IMPORT_ERROR = ""
        except Exception:
            PdfScanSplitEngine = None
            PdfScanSplitOptions = None
            _PDF_SCAN_IMPORT_ERROR = traceback.format_exc()
        finally:
            _PDF_SCAN_IMPORT_ATTEMPTED = True


def _pdf_scan_unavailable_message() -> str:
    if _PDF_SCAN_IMPORT_ERROR:
        last_line = _PDF_SCAN_IMPORT_ERROR.strip().splitlines()[-1]
        return f"PdfScanSplitEngine 不可用：{last_line}"
    return "PdfScanSplitEngine 不可用"


# ── 输出锁（多线程任务共用 stdout） ──────────────────────────
_STDOUT_LOCK = threading.Lock()

# ── 全局任务取消注册表 ─────────────────────────────────────
_CANCEL_FLAGS: Dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()
_UNDO_RECORDS: Dict[str, list[dict]] = {}
_UNDO_LOCK = threading.Lock()
_MAX_UNDO_RECORDS = 50  # cap to prevent unbounded memory growth
_MAX_UNDO_OPERATIONS = 50000  # [P2 #26] 所有令牌的 operation 总数上限，超过 LRU 淘汰
_MAX_ACTIVE_TASKS = 3
_MAX_QUEUED_TASKS = 50
_TASK_SEMAPHORE = threading.Semaphore(_MAX_ACTIVE_TASKS)
_TASK_QUEUE: deque[tuple[str, Any, dict, threading.Event]] = deque()
# [Bug#6 Fix] 连续线程启动失败计数（模块级，跨 _try_start_queued 调用累积）
_CONSECUTIVE_THREAD_FAILURES = 0


def _new_task_id(prefix: str) -> str:
    return f"{prefix}_{os.urandom(8).hex()}"


def _task_id_from_params(params: dict, prefix: str) -> str:
    raw = str(params.get("task_id") or "")
    if raw and len(raw) <= 80 and all(ch.isalnum() or ch in "_-" for ch in raw):
        return raw
    return _new_task_id(prefix)


def _reserve_task(task_id: str, method: str, params: dict, runner: Any) -> tuple[threading.Event, bool, int]:
    with _CANCEL_LOCK:
        if task_id in _CANCEL_FLAGS:
            raise RuntimeError(f"任务ID已存在: {task_id}")

    acquired = _TASK_SEMAPHORE.acquire(blocking=False)
    if acquired:
        flag = threading.Event()
        with _CANCEL_LOCK:
            if task_id in _CANCEL_FLAGS:
                _TASK_SEMAPHORE.release()
                raise RuntimeError(f"任务ID已存在: {task_id}")
            _CANCEL_FLAGS[task_id] = flag
        return (flag, False, 0)

    flag = threading.Event()
    with _CANCEL_LOCK:
        if task_id in _CANCEL_FLAGS:
            raise RuntimeError(f"任务ID已存在: {task_id}")
        if len(_TASK_QUEUE) >= _MAX_QUEUED_TASKS:
            raise RuntimeError(f"等待队列已满（最多{_MAX_QUEUED_TASKS}个），请稍后再试")
        _CANCEL_FLAGS[task_id] = flag
        _TASK_QUEUE.append((task_id, runner, params, flag))
        position = len(_TASK_QUEUE)
    return (flag, True, position)


def _release_task(task_id: str, *, reserved: bool = True) -> None:
    with _CANCEL_LOCK:
        _CANCEL_FLAGS.pop(task_id, None)
    if reserved:
        _TASK_SEMAPHORE.release()
    _try_start_queued()


def _try_start_queued() -> None:
    global _CONSECUTIVE_THREAD_FAILURES
    while True:
        with _CANCEL_LOCK:
            if not _TASK_QUEUE:
                return
            task_id, runner, params, flag = _TASK_QUEUE.popleft()

        acquired = _TASK_SEMAPHORE.acquire(blocking=False)
        if not acquired:
            with _CANCEL_LOCK:
                _TASK_QUEUE.appendleft((task_id, runner, params, flag))
            return

        with _CANCEL_LOCK:
            if flag.is_set() or task_id not in _CANCEL_FLAGS:
                _TASK_SEMAPHORE.release()
                # [Bug1 Fix] Race condition: flag set by _cancel_task AFTER popleft but BEFORE
                # we re-acquired _CANCEL_LOCK. In this window _cancel_task found
                # removed_queued=False, set the flag, but did NOT clean _CANCEL_FLAGS
                # or send task.complete -> frontend hangs + memory leak.
                # [P1 #8] 删除原 198-203 行的 elif/elif fallback 分支：
                # flag 未 set 却发 cancelled:True 会让正常任务收到错误通知。
                if flag.is_set() and task_id in _CANCEL_FLAGS:
                    _CANCEL_FLAGS.pop(task_id, None)
                    send_notification("task.complete", {
                        "task_id": task_id,
                        "ok": False,
                        "cancelled": True,
                        "error": "已取消",
                    })
                continue

        # [P1 #10] task.queued 通知必须在 thread.start() 之前发送，
        # 否则线程内可能先于本通知发出 task.complete，前端状态错乱。
        send_notification("task.queued", {"task_id": task_id, "queued": False, "position": 0})
        try:
            thread = threading.Thread(target=runner, args=(task_id, params, flag), daemon=True)
            thread.start()
            with _CANCEL_LOCK:  # [Bug#6 Fix] 计数器需加锁：_try_start_queued 会被 worker 线程并发调用
                _CONSECUTIVE_THREAD_FAILURES = 0  # 启动成功，重置计数
            return
        except Exception as exc:
            with _CANCEL_LOCK:
                _CANCEL_FLAGS.pop(task_id, None)
                _CONSECUTIVE_THREAD_FAILURES += 1
                fail_count = _CONSECUTIVE_THREAD_FAILURES
            _TASK_SEMAPHORE.release()
            send_notification("task.complete", {
                "task_id": task_id,
                "ok": False,
                "cancelled": False,
                "error": f"无法启动后台线程: {exc}",
            })
            # [P1 #12][Bug#6 Fix] 连续失败超过 3 次：保留剩余队列项，停止雪崩，发 warning 通知
            # （原为局部变量，每次调用从 0 开始，无法跨调用检测连续失败；
            #  现为模块级变量且加锁，可跨调用安全累积）
            if fail_count >= 3:
                remaining = []
                with _CANCEL_LOCK:
                    while _TASK_QUEUE:
                        remaining.append(_TASK_QUEUE.popleft())
                    for queued_id, _runner, _params, _flag in remaining:
                        _CANCEL_FLAGS.pop(queued_id, None)
                send_notification("task.warning", {
                    "message": f"连续 {fail_count} 次启动后台线程失败，已暂停派发队列任务",
                    "remaining_queued": len(remaining),
                })
                for queued_id, _runner, _params, _flag in remaining:
                    send_notification("task.complete", {
                        "task_id": queued_id,
                        "ok": False,
                        "cancelled": False,
                        "error": "后台线程连续启动失败，队列任务已终止",
                    })
                break
            continue


def _cancel_task(task_id: str) -> bool:
    removed_queued = False
    with _CANCEL_LOCK:
        flag = _CANCEL_FLAGS.get(task_id)
        if flag is not None:
            for index, (queued_id, _runner, _params, _flag) in enumerate(_TASK_QUEUE):
                if queued_id == task_id:
                    del _TASK_QUEUE[index]
                    _CANCEL_FLAGS.pop(task_id, None)
                    removed_queued = True
                    break
    if flag is None:
        return False
    flag.set()
    if removed_queued:
        send_notification("task.complete", {
            "task_id": task_id,
            "ok": False,
            "cancelled": True,
            "error": "已取消",
        })
        _try_start_queued()
    return True


# ── 辅助函数 ───────────────────────────────────────────────

def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    # [P1 #5] 先处理 numpy 标量（float32/float64/int* 等）→ 走 numbers.Real 分支，
    # 避免 allow_nan=False 对 numpy.float32('nan') 抛 ValueError
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        try:
            f = float(value)
        except Exception:
            return None
        return f if math.isfinite(f) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)

def send(data: dict) -> None:
    # [P1 #5] send 序列化失败兜底：写 stderr 日志，不发垃圾到 stdout
    try:
        line = json.dumps(_json_safe(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except Exception:
        try:
            sys.stderr.write("send() serialize failed: " + traceback.format_exc() + "\n")
        except Exception:
            pass
        return
    with _STDOUT_LOCK:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            pass


def send_notification(method: str, params: dict) -> None:
    send({"jsonrpc": "2.0", "method": method, "params": params})


def error_response(req_id: Any, code: int, message: str, data: str = "") -> dict:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message, "data": data},
        "id": req_id,
    }


def success_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


# ── ping ──────────────────────────────────────────────────

def handle_ping(params: dict) -> dict:
    return {"pong": True}


# ── 重命名 ─────────────────────────────────────────────────

def handle_rename_preview(params: dict) -> list[dict]:
    engine = _make_rename_engine()
    engine.set_rules(params.get("rules", []))
    results = engine.batch_generate_filenames(params.get("files", []))
    return [{"old": old, "new": new} for old, new in results]


def handle_rename_execute(params: dict) -> dict:
    engine = _make_rename_engine()
    engine.set_rules(params.get("rules", []))
    result = engine.execute_rename(
        params.get("files", []),
        save_method=params.get("save_method", "copy"),
        output_dir=params.get("output_dir", ""),
    )
    operations = [op for op in result.get("operations", []) if isinstance(op, dict) and op.get("success")]
    if operations:
        undo_token = os.urandom(16).hex()
        with _UNDO_LOCK:
            _UNDO_RECORDS[undo_token] = operations
            # evict oldest entries when cap is exceeded
            while len(_UNDO_RECORDS) > _MAX_UNDO_RECORDS:
                _UNDO_RECORDS.pop(next(iter(_UNDO_RECORDS)), None)
            # [P2 #26] 限制所有令牌的 operation 总数，LRU 淘汰最早 token
            total_ops = sum(len(ops) for ops in _UNDO_RECORDS.values())
            while total_ops > _MAX_UNDO_OPERATIONS and len(_UNDO_RECORDS) > 1:
                oldest_token = next(iter(_UNDO_RECORDS))
                total_ops -= len(_UNDO_RECORDS[oldest_token])
                _UNDO_RECORDS.pop(oldest_token, None)
        result["undo_token"] = undo_token
    return result


def handle_rename_undo(params: dict) -> dict:
    """撤销重命名：覆盖模式改回原路径，副本模式删除副本。"""
    undo_token = str(params.get("undo_token") or "")
    if not undo_token:
        return {"restored": [], "failed": [{"path": "", "error": "缺少撤销令牌"}]}
    # [P1 #9] 改为非破坏性 get：执行后仅在全部成功时才 pop，便于失败重试
    with _UNDO_LOCK:
        operations = _UNDO_RECORDS.get(undo_token, None)
    if not operations:
        return {"restored": [], "failed": [{"path": "", "error": "撤销令牌无效或已使用"}]}
    restored: list[dict] = []
    failed: list[dict] = []
    for op in operations:
        try:
            if not isinstance(op, dict):
                continue
            if not op.get("success"):
                continue
            original = str(op.get("original_path") or "")
            new_path = str(op.get("new_path") or "")
            operation = str(op.get("operation") or op.get("operation_type") or "").lower()
            # [Bug#9 Fix] 删除死代码：FileOperationRecord.to_dict() 不含 output_dir 字段，
            # op.get("output_dir") 恒为空，原 if operation not in (...) and output_dir 分支永不执行。
            # 无法确定操作类型时默认按副本处理（只删副本文件，不尝试还原路径），
            # 避免依赖文件系统状态推断导致的边缘情况错误
            if operation not in ("copy", "overwrite"):
                operation = "copy"
            if not original or not new_path:
                continue
            if not os.path.exists(new_path):
                failed.append({"path": new_path, "error": "目标不存在"})
                continue
            if operation == "copy":
                os.remove(new_path)
                restored.append({"from": new_path, "to": original, "operation": "copy"})
                continue
            if os.path.normcase(os.path.abspath(original)) == os.path.normcase(os.path.abspath(new_path)):
                continue
            if os.path.exists(original):
                failed.append({"path": original, "error": "原路径已存在，跳过避免覆盖"})
                continue
            os.rename(new_path, original)
            restored.append({"from": new_path, "to": original})
        except Exception as exc:
            failed.append({"path": str(op.get("new_path") or ""), "error": str(exc)})
    # [P1 #9] 仅当没有失败项时才销毁令牌；保留令牌以便用户重试
    if not failed:
        with _UNDO_LOCK:
            _UNDO_RECORDS.pop(undo_token, None)
    return {"restored": restored, "failed": failed}


# ── PDF 拆分 ───────────────────────────────────────────────

def handle_pdf_split_validate(params: dict) -> dict:
    engine = _make_pdf_split_engine()
    pdf_path = params.get("pdf_path", "") if "pdf_path" in params else ""
    valid, message, page_count = engine.validate_pdf_file(str(pdf_path))
    return {"valid": valid, "message": message, "page_count": page_count}


def handle_pdf_split_preview(params: dict) -> dict:
    engine = _make_pdf_split_engine()
    pdf_path = str(params.get("pdf_path", "") or "")
    planned = engine.plan_outputs_for_file(pdf_path, params.get("config", {}))
    return _serialize_pdf_split_plan(planned)


def _serialize_pdf_split_plan(planned: dict) -> dict:
    result = dict(planned or {})
    if "outputs" in result:
        result["outputs"] = [
            {"filename": o.filename, "page_range": list(o.page_range) if o.page_range else None}
            for o in result["outputs"]
        ]
    return result


def handle_pdf_split_preview_many(params: dict) -> dict:
    engine = _make_pdf_split_engine()
    pdf_paths = list(params.get("pdf_paths", []) or [])
    config = params.get("config", {}) or {}
    lines: list[str] = []
    plans: dict[str, dict] = {}
    used_paths: set[str] = set()

    for pdf_path in pdf_paths:
        base = os.path.basename(pdf_path)
        planned = engine.plan_outputs_for_file(pdf_path, config)
        plans[pdf_path] = _serialize_pdf_split_plan(planned)
        if not planned.get("valid"):
            lines.append(f"{base}  [FAIL] {planned.get('message') or '文件无效'}")
            continue

        page_count = int(planned.get("page_count") or 0)
        target_dir = str(planned.get("output_dir") or os.path.dirname(pdf_path))
        lines.append(f"{base}  ({page_count} 页)")
        lines.append(f"  输出目录: {target_dir}")

        for output in planned.get("outputs") or []:
            name = getattr(output, "filename", "") or ""
            page_range = getattr(output, "page_range", None)
            if make_unique_output_path is not None:
                unique_path = make_unique_output_path(target_dir, name, used_paths)
                unique_name = os.path.basename(unique_path)
            else:
                unique_name = name
            if page_range and len(page_range) == 2:
                lines.append(f"  - {unique_name}  ({page_range[0]}-{page_range[1]})")
            else:
                lines.append(f"  - {unique_name}")

        lines.append("")

    if not lines:
        lines = ["（无预览内容）"]
    elif lines[-1] == "":
        lines.pop()
    return {"lines": lines, "plans": plans}


def handle_pdf_split_execute_removed(params: dict) -> dict:
    # [P0 #4] 已移除同步 handle_pdf_split_execute 路由：
    # 大 PDF 同步执行会阻塞主循环所有请求（含 task.cancel）。
    # 前端请改用 pdf_split.execute_async 走后台线程 + 取消令牌。
    raise RuntimeError(
        "pdf_split.execute 已下线（同步执行会阻塞主循环），请改用 pdf_split.execute_async"
    )


def _run_pdf_split_async(task_id: str, params: dict, cancel_flag: threading.Event) -> None:
    pdf_paths = list(params.get("pdf_paths", []) or [])
    config = params.get("config", {}) or {}
    started_at = time.perf_counter()
    try:
        send_notification("task.progress", {
            "task_id": task_id, "phase": "start", "current": 0, "total": len(pdf_paths),
        })
        engine = _make_pdf_split_engine()
        results = engine.execute_split(pdf_paths, config, cancel_check=lambda: cancel_flag.is_set())
        cancelled = cancel_flag.is_set()
        if cancelled and "已取消" not in results.get("errors", []):
            results.setdefault("errors", []).append("已取消")
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        results["elapsed_ms"] = elapsed_ms
        results["cancelled"] = cancelled

        send_notification("task.progress", {
            "task_id": task_id, "phase": "done",
            "current": len(pdf_paths), "total": len(pdf_paths),
        })
        send_notification("task.complete", {
            "task_id": task_id,
            "ok": not cancelled,
            "task_type": "pdf_split",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancelled,
            "result": results,
            **({"error": "已取消"} if cancelled else {}),
        })
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        send_notification("task.complete", {
            "task_id": task_id, "ok": False,
            "task_type": "pdf_split",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancel_flag.is_set(),
            "error": str(exc), "trace": traceback.format_exc(),
        })
    finally:
        _release_task(task_id)


def handle_pdf_split_execute_async(params: dict) -> dict:
    task_id = _task_id_from_params(params, "pdf_split")
    cancel_flag, queued, position = _reserve_task(task_id, "pdf_split.execute_async", params, _run_pdf_split_async)
    if queued:
        return {"task_id": task_id, "queued": True, "position": position}
    try:
        thread = threading.Thread(target=_run_pdf_split_async, args=(task_id, params, cancel_flag), daemon=True)
        thread.start()
    except Exception as exc:
        _release_task(task_id)
        raise RuntimeError(f"无法启动后台线程: {exc}") from exc
    return {"task_id": task_id}


# ── 扫描拆分 ───────────────────────────────────────────────

def _build_scan_options(raw: dict) -> "PdfScanSplitOptions":
    _ensure_pdf_scan_engine()
    if PdfScanSplitOptions is None:
        raise RuntimeError(_pdf_scan_unavailable_message())
    raw = dict(raw or {})
    roi = raw.get("reference_roi")
    if isinstance(roi, (list, tuple)) and len(roi) == 4:
        raw["reference_roi"] = tuple(int(x) for x in roi)
    else:
        raw["reference_roi"] = None
    if "use_roi" not in raw and "qrcode_use_roi" in raw:
        raw["use_roi"] = raw.get("qrcode_use_roi")
    if "qrcode_use_roi" not in raw and "use_roi" in raw:
        raw["qrcode_use_roi"] = raw.get("use_roi")
    allowed = {f for f in PdfScanSplitOptions.__dataclass_fields__.keys()}
    cleaned = {k: v for k, v in raw.items() if k in allowed}
    return PdfScanSplitOptions(**cleaned)


def _serialize_scan_result(result) -> dict:
    if is_dataclass(result):
        return asdict(result)
    return dict(result or {})


def _fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _format_probe_log_lines(result: dict, options=None) -> list[str]:
    page_number = int(result.get("page_number") or int(result.get("page_index") or 0) + 1)
    total_pages = int(result.get("total_pages") or 0)
    marked = bool(result.get("marked"))
    reason = str(result.get("reason") or ("命中" if marked else "未命中"))
    lines = [
        f"第 {page_number} 页测试完成：{'命中标记页' if marked else '未命中'}（{reason}）",
    ]
    if total_pages:
        lines.append(f"PDF 总页数：{total_pages}，本次测试：第 {page_number} 页，模式：{result.get('detection_mode') or '-'}")

    qrcode = result.get("qrcode") if isinstance(result.get("qrcode"), dict) else {}
    if qrcode.get("present") or qrcode.get("infos") or qrcode.get("stats"):
        infos = qrcode.get("infos") if isinstance(qrcode.get("infos"), list) else []
        stats = qrcode.get("stats") if isinstance(qrcode.get("stats"), dict) else {}
        detail = f"二维码：{'有候选' if qrcode.get('present') or infos else '无'}，解码 {len(infos)} 个"
        if stats:
            detail += f"，面积 {_fmt_float(stats.get('area'), 0)}，形状 {_fmt_float(stats.get('aspect'))}"
        lines.append(detail)
        if qrcode.get("present") and not infos and not bool(getattr(options, "qrcode_no_decode", False)):
            lines.append("检测到疑似二维码，但未能解析内容。内容匹配仅适用于可被通用二维码解码器识别的码图；如只需判断标记页，请勾选“不解码内容”。")

    stamp = result.get("stamp") if isinstance(result.get("stamp"), dict) else {}
    if stamp.get("present") or stamp.get("candidates") is not None:
        lines.append(
            "印章："
            f"{'命中' if stamp.get('present') else '未命中'}，候选 {int(stamp.get('candidates') or 0)}，"
            f"面积占比 {_fmt_float(stamp.get('area_ratio'), 4)}，圆度 {_fmt_float(stamp.get('circularity'))}"
        )

    feature = result.get("feature") if isinstance(result.get("feature"), dict) else {}
    if feature.get("good_matches") or feature.get("inliers") or result.get("detection_mode") in ("feature", "auto"):
        lines.append(
            f"特征点：匹配 {int(feature.get('good_matches') or 0)}，"
            f"内点 {int(feature.get('inliers') or 0)}，比例 {_fmt_float(feature.get('inlier_ratio'))}"
        )
    return lines


def _run_scan_split_async(task_id: str, params: dict, cancel_flag: threading.Event) -> None:
    started_at = time.perf_counter()
    log_tail: deque[str] = deque(maxlen=200)
    try:
        _ensure_pdf_scan_engine()
        if PdfScanSplitEngine is None:
            raise RuntimeError(_pdf_scan_unavailable_message())

        options = _build_scan_options(params.get("options", {}))

        def progress(current: int, total: int) -> None:
            send_notification("task.progress", {
                "task_id": task_id, "phase": "scanning",
                "current": int(current), "total": int(total),
            })

        def log(msg: str) -> None:
            message = str(msg)
            log_tail.append(message)
            send_notification("task.log", {"task_id": task_id, "message": message})

        def cancel_check() -> bool:
            return cancel_flag.is_set()

        send_notification("task.progress", {"task_id": task_id, "phase": "start", "current": 0, "total": 0})

        result = PdfScanSplitEngine.execute(
            params.get("pdf_path", ""),
            params.get("reference_image_path", ""),
            output_dir=params.get("output_dir", "") or "",
            prefix=params.get("prefix", "") or "",
            options=options,
            progress=progress,
            log=log,
            cancel_check=cancel_check,
        )

        serialized = _serialize_scan_result(result)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        cancelled = cancel_flag.is_set()
        serialized.setdefault("elapsed_ms", elapsed_ms)
        serialized.setdefault("cancelled", cancelled)
        serialized.setdefault("log_tail", list(log_tail))
        has_outputs = bool(serialized.get("output_files"))
        success = (not cancelled) and has_outputs
        error = "已取消" if cancelled else (None if has_outputs else "未生成输出文件")
        _record_scan_history(params, serialized, success, error)
        send_notification("task.complete", {
            "task_id": task_id, "ok": success,
            "task_type": "scan_split",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancelled,
            "result": serialized,
            **({"error": error} if error else {}),
        })
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        _record_scan_history(params, {"elapsed_ms": elapsed_ms, "log_tail": list(log_tail), "cancelled": cancel_flag.is_set()}, False, str(exc))
        send_notification("task.complete", {
            "task_id": task_id, "ok": False,
            "task_type": "scan_split",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancel_flag.is_set(),
            "error": str(exc), "trace": traceback.format_exc(),
        })
    finally:
        _release_task(task_id)


def _record_scan_history(params: dict, result: dict, success: bool, error: str | None, *, description_prefix: str = "扫描拆分") -> None:
    if _HISTORY_MANAGER is None:
        return
    pdf_path = str(params.get("pdf_path", "") or "")
    details = {
        "pdf_path": pdf_path,
        "reference_image_path": params.get("reference_image_path", "") or "",
        "output_dir": params.get("output_dir", "") or "",
        "prefix": params.get("prefix", "") or "",
        "options": params.get("options", {}) or {},
        "output_files": result.get("output_files") or result.get("outputs") or [],
        "marker_pages": result.get("marker_pages") or [],
        "suspect_segments": result.get("suspect_segments") or [],
        "elapsed_ms": result.get("elapsed_ms"),
        "cancelled": bool(result.get("cancelled")),
        "log_tail": result.get("log_tail") or [],
        "performance_stats": result.get("performance_stats") or {},
    }
    description = f"{description_prefix} {os.path.basename(pdf_path) or 'PDF'}"
    level = "error" if not success or error else "warning" if details.get("cancelled") or details.get("suspect_segments") else "success"
    _HISTORY_MANAGER.add_record(
        "scan_split",
        description,
        details,
        success=success,
        error_message=error,
        level=level,
        source="scan_split",
        message=description,
    )


def handle_scan_split_execute_async(params: dict) -> dict:
    task_id = _task_id_from_params(params, "scan_split")
    cancel_flag, queued, position = _reserve_task(task_id, "scan_split.execute_async", params, _run_scan_split_async)
    if queued:
        return {"task_id": task_id, "queued": True, "position": position}
    try:
        thread = threading.Thread(target=_run_scan_split_async, args=(task_id, params, cancel_flag), daemon=True)
        thread.start()
    except Exception as exc:
        _release_task(task_id)
        raise RuntimeError(f"无法启动后台线程: {exc}") from exc
    return {"task_id": task_id}


def _run_probe_page(task_id: str, params: dict, cancel_flag: threading.Event) -> None:
    started_at = time.perf_counter()
    log_tail: deque[str] = deque(maxlen=200)
    try:
        _ensure_pdf_scan_engine()
        if PdfScanSplitEngine is None:
            raise RuntimeError(_pdf_scan_unavailable_message())
        options = _build_scan_options(params.get("options", {}))
        raw_page_index = params.get("page_index", 0)
        page_index = 0 if raw_page_index in (None, "") else int(raw_page_index)
        display_page = page_index + 1
        def task_log(msg: str) -> None:
            message = str(msg)
            log_tail.append(message)
            send_notification("task.log", {"task_id": task_id, "message": message})

        send_notification("task.progress", {"task_id": task_id, "phase": "testing", "current": 0, "total": 1})
        task_log(f"正在测试第 {display_page} 页…")
        result = PdfScanSplitEngine.probe_page(
            params.get("pdf_path", ""),
            params.get("reference_image_path", ""),
            options,
            page_index=page_index,
            cancel_check=lambda: cancel_flag.is_set(),
        )
        for line in _format_probe_log_lines(result, options):
            task_log(line)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        cancelled = cancel_flag.is_set()
        serialized = dict(result or {})
        serialized.setdefault("elapsed_ms", elapsed_ms)
        serialized.setdefault("cancelled", cancelled)
        serialized.setdefault("log_tail", list(log_tail))
        _record_scan_history(params, serialized, not cancelled, "已取消" if cancelled else None, description_prefix="单页测试")
        send_notification("task.progress", {"task_id": task_id, "phase": "done", "current": 1, "total": 1})
        send_notification("task.complete", {
            "task_id": task_id,
            "ok": not cancelled,
            "task_type": "scan_probe",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancelled,
            "result": serialized,
            **({"error": "已取消"} if cancelled else {}),
        })
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        _record_scan_history(params, {"elapsed_ms": elapsed_ms, "log_tail": list(log_tail), "cancelled": cancel_flag.is_set()}, False, str(exc), description_prefix="单页测试")
        send_notification("task.complete", {
            "task_id": task_id,
            "ok": False,
            "task_type": "scan_probe",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancel_flag.is_set(),
            "error": str(exc),
            "trace": traceback.format_exc(),
        })
    finally:
        _release_task(task_id)


def handle_scan_probe_page(params: dict) -> dict:
    task_id = _task_id_from_params(params, "probe")
    cancel_flag, queued, position = _reserve_task(task_id, "scan_split.probe_page", params, _run_probe_page)
    if queued:
        return {"task_id": task_id, "queued": True, "position": position}
    try:
        thread = threading.Thread(target=_run_probe_page, args=(task_id, params, cancel_flag), daemon=True)
        thread.start()
    except Exception as exc:
        _release_task(task_id)
        raise RuntimeError(f"无法启动后台线程: {exc}") from exc
    return {"task_id": task_id}


def _run_scan_only(task_id: str, params: dict, cancel_flag: threading.Event) -> None:
    started_at = time.perf_counter()
    log_tail: deque[str] = deque(maxlen=200)
    try:
        _ensure_pdf_scan_engine()
        if PdfScanSplitEngine is None:
            raise RuntimeError(_pdf_scan_unavailable_message())
        options = _build_scan_options(params.get("options", {}))
        # [Bug #30] 移除 scan_only 模式对 qrcode_max_attempts 的强制 48 上限：
        # 前端显示用户配置的值（如 180），后端也应使用该值，避免行为不一致。
        # 仅通过 log 通知前端当前使用的最大尝试次数。
        page_limit = int(params.get("page_limit", 0) or 0)
        send_notification("task.progress", {"task_id": task_id, "phase": "start", "current": 0, "total": 1})

        def progress(current: int, total: int) -> None:
            send_notification("task.progress", {
                "task_id": task_id, "phase": "scanning",
                "current": int(current), "total": int(total),
            })

        def log(msg: str) -> None:
            message = str(msg)
            log_tail.append(message)
            send_notification("task.log", {"task_id": task_id, "message": message})

        # [Bug #30] 通知前端当前使用的用户配置最大尝试次数
        log(f"快速扫描模式：使用用户配置的最大尝试次数 {getattr(options, 'qrcode_max_attempts', 180)}")

        result = PdfScanSplitEngine.scan_only(
            params.get("pdf_path", ""),
            params.get("reference_image_path", ""),
            options,
            page_limit=page_limit,
            progress=progress,
            log=log,
            cancel_check=lambda: cancel_flag.is_set(),
        )
        serialized = _serialize_scan_result(result)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        cancelled = cancel_flag.is_set()
        serialized.setdefault("elapsed_ms", elapsed_ms)
        serialized.setdefault("cancelled", cancelled)
        serialized.setdefault("log_tail", list(log_tail))
        _record_scan_history(params, serialized, not cancelled, "已取消" if cancelled else None, description_prefix="快速扫描")
        send_notification("task.progress", {"task_id": task_id, "phase": "done", "current": 1, "total": 1})
        send_notification("task.complete", {
            "task_id": task_id,
            "ok": not cancelled,
            "task_type": "scan_only",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancelled,
            "result": serialized,
            **({"error": "已取消"} if cancelled else {}),
        })
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        _record_scan_history(params, {"elapsed_ms": elapsed_ms, "log_tail": list(log_tail), "cancelled": cancel_flag.is_set()}, False, str(exc), description_prefix="快速扫描")
        send_notification("task.complete", {
            "task_id": task_id,
            "ok": False,
            "task_type": "scan_only",
            "elapsed_ms": elapsed_ms,
            "cancelled": cancel_flag.is_set(),
            "error": str(exc),
            "trace": traceback.format_exc(),
        })
    finally:
        _release_task(task_id)


def handle_scan_only(params: dict) -> dict:
    task_id = _task_id_from_params(params, "scan_only")
    cancel_flag, queued, position = _reserve_task(task_id, "scan_split.scan_only", params, _run_scan_only)
    if queued:
        return {"task_id": task_id, "queued": True, "position": position}
    try:
        thread = threading.Thread(target=_run_scan_only, args=(task_id, params, cancel_flag), daemon=True)
        thread.start()
    except Exception as exc:
        _release_task(task_id)
        raise RuntimeError(f"无法启动后台线程: {exc}") from exc
    return {"task_id": task_id}


def handle_scan_preview_reference(params: dict) -> dict:
    """渲染参考文件（图片或 PDF 首页）为 base64 PNG，带特征点可视化"""
    import base64
    _ensure_pdf_scan_engine()
    if PdfScanSplitEngine is None:
        return {"ok": False, "error": _pdf_scan_unavailable_message(), "trace": _PDF_SCAN_IMPORT_ERROR}
    path = str(params.get("reference_image_path", "") or "")
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "参考文件不存在"}
    try:
        import cv2
        bgr = PdfScanSplitEngine._read_reference_bgr(path)
        try:
            nfeatures = int(params.get("nfeatures", 1200) or 1200)
        except Exception:
            nfeatures = 1200
        nfeatures = max(100, min(10000, nfeatures))
        roi = params.get("roi")

        orb = cv2.ORB_create(nfeatures=nfeatures)
        kps, _ = orb.detectAndCompute(bgr, None)
        kps = kps or []
        vis = cv2.drawKeypoints(bgr, kps, None, color=(0, 255, 0))

        keypoints_in_roi = 0
        if roi and len(roi) == 4:
            height, width = bgr.shape[:2]
            try:
                rx, ry, rw, rh = int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])
            except Exception:
                rx, ry, rw, rh = 0, 0, 0, 0
            rx = max(0, min(width, rx))
            ry = max(0, min(height, ry))
            rw = max(0, min(width - rx, rw))
            rh = max(0, min(height - ry, rh))
            cv2.rectangle(vis, (rx, ry), (rx + rw, ry + rh), (255, 0, 0), 2)
            keypoints_in_roi = sum(
                1 for kp in kps
                if rx <= kp.pt[0] <= rx + rw and ry <= kp.pt[1] <= ry + rh
            )

        ok, buf = cv2.imencode(".png", vis)
        if not ok:
            return {"ok": False, "error": "图像编码失败"}
        data_url = "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
        height, width = bgr.shape[:2]
        return {
            "ok": True,
            "data_url": data_url,
            "width": int(width),
            "height": int(height),
            "keypoints_total": len(kps),
            "keypoints_in_roi": keypoints_in_roi,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def handle_task_cancel(params: dict) -> dict:
    task_id = str(params.get("task_id") or "")
    return {"cancelled": _cancel_task(task_id), "task_id": task_id}


# ── 历史记录查询 ──────────────────────────────────────────

def handle_history_get(params: dict) -> dict:
    if _HISTORY_MANAGER is None:
        return {"records": [], "session_id": ""}
    try:
        count = int(params.get("count", 50) or 50)
    except Exception:
        count = 50
    count = max(0, min(500, count))
    operation_type = params.get("operation_type")
    session_id = params.get("session_id")
    if session_id is None and params.get("current_session", True):
        session_id = str(getattr(_HISTORY_MANAGER, "session_id", "") or "")
    records = _HISTORY_MANAGER.get_recent_records(
        count=count,
        operation_type=_normalize_operation_type(operation_type),
        session_id=session_id,
    )
    return {
        "records": [r.to_dict() for r in records],
        "session_id": _HISTORY_MANAGER.session_id,
    }


def handle_history_clear(params: dict) -> dict:
    if _HISTORY_MANAGER is None:
        return {"cleared": False}
    _HISTORY_MANAGER.clear_history()
    return {"cleared": True, "session_id": _HISTORY_MANAGER.session_id}


def _normalize_operation_type(value: Any) -> Any:
    if not value:
        return None
    try:
        from src.utils.history_manager import OperationType
        return OperationType(str(value))
    except Exception:
        return None


def handle_shutdown(params: dict) -> dict:
    """[Bug#2 Fix] 优雅关闭：立即刷盘历史记录，置位退出标志。
    主循环会在发送本响应后 sys.exit(0)，触发 atexit 的 _flush_exit 兜底。"""
    global _SHUTDOWN_REQUESTED
    if _HISTORY_MANAGER is not None:
        try:
            _HISTORY_MANAGER._flush_to_disk()
        except Exception:
            pass
    _SHUTDOWN_REQUESTED = True
    return {"ok": True}


# ── 路由表 ─────────────────────────────────────────────────

ROUTES: Dict[str, Callable] = {
    "ping":                       handle_ping,
    "rename.preview":             handle_rename_preview,
    "rename.execute":             handle_rename_execute,
    "rename.undo":                handle_rename_undo,
    "pdf_split.validate":         handle_pdf_split_validate,
    "pdf_split.preview":          handle_pdf_split_preview,
    "pdf_split.preview_many":     handle_pdf_split_preview_many,
    # [P0 #4] 移除 pdf_split.execute 同步路由，避免大 PDF 阻塞主循环；保留 execute_async
    "pdf_split.execute_async":    handle_pdf_split_execute_async,
    "scan_split.execute_async":   handle_scan_split_execute_async,
    "scan_split.preview_reference": handle_scan_preview_reference,
    "scan_split.probe_page":     handle_scan_probe_page,
    "scan_split.scan_only":      handle_scan_only,
    "task.cancel":                handle_task_cancel,
    "history.get":                handle_history_get,
    "history.clear":              handle_history_clear,
    "shutdown":                   handle_shutdown,  # [Bug#2 Fix] 优雅关闭路由
}


# ── 主循环 ─────────────────────────────────────────────────

def main() -> None:
    # 重定向 stderr → null，避免异步线程写入导致管道阻塞
    try:
        _log_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "FileToolbox", "logs"
        )
        os.makedirs(_log_dir, exist_ok=True)
        sys.stderr = open(os.path.join(_log_dir, "engine.log"), "a", encoding="utf-8", buffering=1)
        # [P1 #11] 仅重定向 Python sys.stderr 不够，OpenCV 等 C 库仍写 fd 2；
        # 用 os.dup2 把底层 fd 2 也指向同一日志文件
        try:
            _log_fd = os.open(
                os.path.join(_log_dir, "engine.log"),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            )
            os.dup2(_log_fd, 2)
            os.close(_log_fd)
        except Exception:
            pass
    except Exception:
        pass  # Imp1: last-resort fallback only if log dir creation fails

    # [P2 #24] ENGINE_AUTH_TOKEN 未设置时写 warning 到日志，便于排查；
    # 不拒启以兼顾本地开发体验
    if not ENGINE_AUTH_TOKEN:
        try:
            sys.stderr.write("WARNING: ENGINE_AUTH_TOKEN 未设置，引擎无鉴权\n")
        except Exception:
            pass

    send({"jsonrpc": "2.0", "method": "ready", "params": {}})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            send(error_response(None, -32700, "Parse error"))
            continue

        # 非 dict 类型的 JSON（list/str/int 等）会导致 .get() 抛出 AttributeError，崩溃整个进程
        if not isinstance(request, dict):
            send(error_response(None, -32600, "Invalid Request"))
            continue

        req_id = request.get("id")
        # [P0 #1] 鉴权移入主循环 try 防护范围之外但仍需防御 TypeError：
        # compare_digest 收到非 ASCII str 会崩溃进程，统一转 utf-8 bytes 比较
        if ENGINE_AUTH_TOKEN:
            try:
                supplied_token = str(request.get("auth", "") or "")
                if not hmac.compare_digest(
                    supplied_token.encode("utf-8", "ignore"),
                    ENGINE_AUTH_TOKEN.encode("utf-8", "ignore"),
                ):
                    send(error_response(req_id, -32010, "Unauthorized"))
                    continue
            except Exception:
                send(error_response(req_id, -32010, "Unauthorized"))
                continue

        method = str(request.get("method", "") or "")
        params = request.get("params", {})
        if not isinstance(params, dict):
            params = {}

        # 路由级参数校验（防御极端输入）
        if method in ("rename.preview", "rename.execute"):
            if not isinstance(params.get("files"), (list, tuple)):
                params["files"] = []
            if len(params.get("files", [])) > 5000:
                send(error_response(req_id, -32002, "文件列表过长（最多5000个）"))
                continue

        handler = ROUTES.get(method)
        if not handler:
            send(error_response(req_id, -32601, f"Method not found: {method}"))
            continue

        try:
            result = handler(params)
            send(success_response(req_id, result))
        except Exception as exc:
            send(error_response(req_id, -32000, str(exc), traceback.format_exc()))

        # [Bug#2 Fix] shutdown 响应已发出，干净退出（atexit 会再刷一次盘兜底）
        if _SHUTDOWN_REQUESTED:
            break


if __name__ == "__main__":
    main()
