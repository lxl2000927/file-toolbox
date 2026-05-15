import json
from datetime import datetime
from urllib.parse import urlparse

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextBrowser, QGroupBox, QPlainTextEdit,
    QHBoxLayout, QPushButton, QFileDialog, QLabel, QProgressBar, QMessageBox,
    QDialog, QDialogButtonBox, QTextEdit,
)
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from utils.style_manager import StyleManager
from utils.history_manager import HistoryManager, OperationType
from PyQt6.QtCore import QUrl, QTimer

CURRENT_VERSION = "1.1.2.0"
GITHUB_REPO = "LXL2000927/file-toolbox"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=1"
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"


class AboutPanel(QWidget):
    def __init__(self, history_manager: HistoryManager = None):
        super().__init__()
        self.history_manager = history_manager
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._attempt = 0
        self._max_attempts = 3
        self._rate_limit_reset_at = ""
        self._update_url = GITHUB_API_LATEST
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        group = QGroupBox("关于")
        group.setProperty("compact", True)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setReadOnly(True)
        browser.setMinimumHeight(260)
        browser.setStyleSheet(f"""
            QTextBrowser {{
                border: 1px solid {StyleManager.get_color("border")};
                border-radius: {StyleManager.SIZES["border_radius"]};
                padding: 12px;
                background-color: {StyleManager.get_color("white")};
            }}
        """)

        html = """
        <div style="line-height: 1.6;">
          <div style="font-size: 18px; font-weight: 700; margin-bottom: 10px;">
            [ PDF Split ] - 重新定义PDF拆分的轻巧与强大
          </div>

          <div style="font-size: 14px; font-weight: 700; margin: 10px 0 6px 0;">
            为什么选择我们？
          </div>

          <div style="margin: 6px 0;">
            <span style="font-weight: 700;">✨ 智能拆分模式</span>
            <ul style="margin: 6px 0 0 18px;">
              <li>均匀分割：自动将文档等分为N份。</li>
              <li>提取单页：快速提取特定页面为新文件。</li>
              <li>高级模式：自定义每一份的页码范围，灵活随心。</li>
            </ul>
          </div>

          <div style="margin: 10px 0;">
            <span style="font-weight: 700;">🛡️ 隐私与安全承诺</span>
            <div style="margin-top: 6px;">
              我们坚信，你的文档就是你的隐私。软件100%离线运行，绝无网络上传，给你十足的安全感。
            </div>
          </div>

          <div style="margin: 10px 0;">
            <span style="font-weight: 700;">⚡ 性能卓越</span>
            <div style="margin-top: 6px;">
              采用底层优化技术，处理百页文档仅需数秒，效率远超同类工具。
            </div>
          </div>

          <div style="margin: 10px 0;">
            <span style="font-weight: 700;">📁 格式完美保持</span>
            <div style="margin-top: 6px;">
              拆分后的PDF完整保留原始的文字、图像、排版和书签信息。
            </div>
          </div>

          <div style="margin-top: 14px; padding-top: 10px; border-top: 1px solid #dee2e6;">
            了解更多、获取支持或提出建议，请访问：
            <a href="https://chatgpt.com/">https://chatgpt.com/</a>
          </div>
        </div>
        """
        browser.setHtml(html)

        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.setProperty("variant", "primary")
        self.cancel_link_btn = QPushButton("取消链接")
        self.cancel_link_btn.setProperty("variant", "outline")
        self.cancel_link_btn.setVisible(False)
        self.retry_btn = QPushButton("重试")
        self.retry_btn.setProperty("variant", "outline")
        self.retry_btn.setVisible(False)

        self.update_status = QLabel("")
        self.update_status.setWordWrap(True)
        self.update_status.setStyleSheet(f"color: {StyleManager.get_color('gray_600')}; margin-bottom: 0px;")

        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 0)
        self.update_progress.setFixedHeight(10)
        self.update_progress.setTextVisible(False)
        self.update_progress.setVisible(False)

        group_layout.addWidget(browser, 1)
        layout.addWidget(group, 2)

        logs_group = QGroupBox("运行日志")
        logs_group.setProperty("compact", True)
        logs_layout = QHBoxLayout(logs_group)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        logs_layout.setSpacing(10)

        logs_left = QWidget()
        logs_left_layout = QVBoxLayout(logs_left)
        logs_left_layout.setContentsMargins(0, 0, 0, 0)
        logs_left_layout.setSpacing(8)

        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setPlaceholderText("这里显示本次运行期间的操作日志")
        self.logs_view.setMinimumHeight(180)
        logs_left_layout.addWidget(self.logs_view, 1)

        btn_row = QWidget()
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        btn_row_layout.setSpacing(8)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("variant", "outline")
        self.export_btn = QPushButton("导出日志")
        self.export_btn.setProperty("variant", "primary")

        btn_row_layout.addWidget(self.refresh_btn)
        btn_row_layout.addWidget(self.export_btn)
        btn_row_layout.addWidget(self.check_update_btn)
        btn_row_layout.addWidget(self.retry_btn)
        btn_row_layout.addWidget(self.cancel_link_btn)
        btn_row_layout.addStretch(1)

        logs_left_layout.addWidget(btn_row, 0)
        logs_left_layout.addWidget(self.update_status, 0)
        logs_left_layout.addWidget(self.update_progress, 0)

        logs_layout.addWidget(logs_left, 1)

        layout.addWidget(logs_group, 1)

        self.refresh_btn.clicked.connect(self._refresh_logs)
        self.export_btn.clicked.connect(self._export_logs)
        self._refresh_logs()

        self.check_update_btn.clicked.connect(self._on_check_update_clicked)
        self.cancel_link_btn.clicked.connect(self._cancel_update_check)
        self.retry_btn.clicked.connect(self._retry_update_check)

    def _refresh_logs(self):
        if not self.history_manager or not getattr(self.history_manager, "history", None):
            self.logs_view.setPlainText("")
            return
        lines = []
        sid = str(getattr(self.history_manager, "session_id", "") or "")
        for r in self.history_manager.get_recent_records(count=self.history_manager.max_history_size, session_id=sid):
            status = "成功" if r.success else "失败"
            op = r.operation_type.value
            desc = r.description
            ts = r.timestamp
            lines.append(f"[{ts}] [{op}] {desc} - {status}")
            if r.error_message:
                lines.append(f"  错误: {r.error_message}")
        self.logs_view.setPlainText("\n".join(lines))

    def _export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "logs.txt", "文本文件 (*.txt);;JSON文件 (*.json)")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                sid = str(getattr(self.history_manager, "session_id", "") or "")
                data = [
                    rec.to_dict()
                    for rec in (
                        self.history_manager.get_recent_records(count=self.history_manager.max_history_size, session_id=sid)
                        if self.history_manager
                        else []
                    )
                ]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.logs_view.toPlainText())
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"日志导出失败：\n{e}")
            self.update_status.setText(f"日志导出失败：{e}")
            return

        self.update_status.setText(f"日志已导出：{path}")

    def _set_update_ui_state(self, state: str, message: str = ""):
        loading = state == "loading"
        self.update_progress.setVisible(loading)
        self.cancel_link_btn.setVisible(loading)
        self.check_update_btn.setEnabled(not loading)
        self.retry_btn.setVisible(state == "error")
        self.update_status.setText(message or "")

    def _on_check_update_clicked(self):
        try:
            if self.history_manager:
                self.history_manager.add_record(
                    operation_type=OperationType.UPDATE_CHECK,
                    description="点击检查更新",
                    details={"url": self._update_url},
                    success=True,
                )
        except Exception:
            pass
        self._attempt = 0
        self._start_update_check()

    def _retry_update_check(self):
        self._attempt = 0
        self._start_update_check()

    def _start_update_check(self):
        self._set_update_ui_state("loading", "正在连接 GitHub 检查更新...")
        self._attempt_fetch()

    def _attempt_fetch(self):
        if self._reply is not None:
            old = self._reply
            try:
                old.abort()
            except Exception:
                pass
            self._reply = None
            try:
                old.deleteLater()
            except Exception:
                pass

        self._attempt += 1
        req = QNetworkRequest(QUrl(self._update_url))
        try:
            req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        except Exception:
            pass
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", b"FileToolbox-UpdateChecker")
        try:
            req.setTransferTimeout(8000)
        except Exception:
            self._timeout_timer.start(8000)

        self._reply = self._network.get(req)
        self._reply.finished.connect(self._on_fetch_finished)

    def _on_timeout(self):
        if self._reply is None:
            return
        try:
            self._reply.abort()
        except Exception:
            pass

    def _on_fetch_finished(self):
        if self._timeout_timer.isActive():
            self._timeout_timer.stop()
        reply = self._reply
        self._reply = None
        if reply is None:
            return

        err = reply.error()
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            status_code = int(status_code) if status_code is not None else 0
        except Exception:
            status_code = 0

        try:
            raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
        except Exception:
            raw = ""

        if err != QNetworkReply.NetworkError.NoError:
            self._handle_http_or_network_error(err, reply.errorString(), status_code, raw, reply)
            reply.deleteLater()
            return

        reply.deleteLater()

        if status_code and (status_code < 200 or status_code >= 300):
            self._handle_http_or_network_error(None, "", status_code, raw, reply)
            return

        try:
            data = json.loads(raw) if raw else None
        except Exception:
            self._set_update_ui_state("error", "解析 GitHub 响应失败。可点击\"重试\"再次尝试。")
            return

        release = None
        if isinstance(data, list):
            release = data[0] if data else None
        elif isinstance(data, dict):
            release = data

        if not release:
            try:
                if self.history_manager:
                    self.history_manager.add_record(
                        operation_type=OperationType.UPDATE_CHECK,
                        description=f"检查更新完成：仓库尚未发布任何 Release（当前 {CURRENT_VERSION}）",
                        details={"current_version": CURRENT_VERSION, "has_update": False},
                        success=True,
                    )
            except Exception:
                pass
            self._set_update_ui_state("idle", f"当前版本 {CURRENT_VERSION}。仓库尚未发布任何 Release。")
            QMessageBox.information(
                self,
                "检查更新",
                f"当前版本 {CURRENT_VERSION}。\n该仓库尚未发布任何 Release，无法判断是否有新版本。",
            )
            return

        latest_version = str(release.get("tag_name", "") or "").strip()
        release_name = str(release.get("name", "") or "").strip()
        release_body = str(release.get("body", "") or "").strip()
        html_url = self._safe_release_url(str(release.get("html_url", "") or "").strip())

        if not latest_version:
            self._set_update_ui_state("error", "未获取到版本信息。可点击\"重试\"再次尝试。")
            return

        has_update, _ = self._compare_versions(latest_version, CURRENT_VERSION)

        try:
            if self.history_manager:
                self.history_manager.add_record(
                    operation_type=OperationType.UPDATE_CHECK,
                    description=f"检查更新完成：当前 {CURRENT_VERSION}，最新 {latest_version}",
                    details={
                        "current_version": CURRENT_VERSION,
                        "latest_version": latest_version,
                        "has_update": has_update,
                    },
                    success=True,
                )
        except Exception:
            pass

        if has_update:
            self._set_update_ui_state("idle", f"发现新版本 {latest_version}（当前 {CURRENT_VERSION}）")
            self._show_update_dialog(latest_version, release_name, release_body, html_url)
        else:
            self._set_update_ui_state("idle", f"已是最新版本 {CURRENT_VERSION}。")
            QMessageBox.information(self, "检查更新", f"当前已是最新版本 {CURRENT_VERSION}。")

    def _parse_version(self, version_str: str):
        v = version_str.lstrip("v").strip()
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                break
        return tuple(parts) if parts else (0,)

    def _compare_versions(self, latest: str, current: str):
        a = self._parse_version(latest)
        b = self._parse_version(current)
        max_len = max(len(a), len(b))
        a_padded = a + (0,) * (max_len - len(a))
        b_padded = b + (0,) * (max_len - len(b))
        return a_padded > b_padded, (a_padded, b_padded)

    def _show_update_dialog(self, version: str, name: str, body: str, url: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("发现新版本")
        dialog.setMinimumSize(520, 420)
        dialog.setStyleSheet(f"""
            QDialog {{
                background-color: {StyleManager.get_color("white")};
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"新版本 {version}")
        title.setStyleSheet(f"""
            font-size: 18px;
            font-weight: 700;
            color: {StyleManager.get_color("gray_900")};
        """)
        layout.addWidget(title)

        if name and name != version:
            subtitle = QLabel(name)
            subtitle.setStyleSheet(f"color: {StyleManager.get_color('gray_600')}; font-size: 13px;")
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)

        body_edit = QTextEdit()
        body_edit.setReadOnly(True)
        body_edit.setMarkdown(body if body else "暂无更新说明。")
        body_edit.setStyleSheet(f"""
            QTextEdit {{
                border: 1px solid {StyleManager.get_color("border")};
                border-radius: {StyleManager.SIZES["border_radius"]};
                padding: 10px;
                background-color: {StyleManager.get_color("gray_50")};
            }}
        """)
        layout.addWidget(body_edit, 1)

        buttons = QDialogButtonBox()
        download_btn = QPushButton("前往下载")
        download_btn.setProperty("variant", "primary")
        safe_url = self._safe_release_url(url) or GITHUB_RELEASES_PAGE
        download_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(safe_url)))
        close_btn = QPushButton("稍后再说")
        close_btn.setProperty("variant", "outline")
        close_btn.clicked.connect(dialog.accept)

        buttons.addButton(download_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        dialog.exec()

    def _safe_release_url(self, url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme != "https":
            return ""
        if parsed.netloc.lower() != "github.com":
            return ""
        expected_prefix = f"/{GITHUB_REPO}/releases"
        if not parsed.path.startswith(expected_prefix):
            return ""
        return raw

    def _read_reply_header(self, reply, name: bytes) -> str:
        if reply is None:
            return ""
        try:
            value = bytes(reply.rawHeader(name)).decode("utf-8", errors="replace").strip()
            return value
        except Exception:
            return ""

    def _format_rate_limit_reset(self, reset_value: str) -> str:
        try:
            ts = int(str(reset_value or "").strip())
            if ts <= 0:
                return ""
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    def _is_rate_limited(self, status_code: int, api_message: str, remaining: str) -> bool:
        if status_code != 403:
            return False
        text = str(api_message or "").lower()
        if "rate limit" in text or "api rate limit" in text:
            return True
        try:
            return int(str(remaining or "").strip()) <= 0
        except Exception:
            return False

    def _handle_http_or_network_error(self, err, err_text: str, status_code: int, raw: str, reply=None):
        api_message = ""
        try:
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    api_message = str(payload.get("message", "") or "").strip()
        except Exception:
            api_message = ""

        rate_limit_remaining = self._read_reply_header(reply, b"X-RateLimit-Remaining")
        rate_limit_reset_raw = self._read_reply_header(reply, b"X-RateLimit-Reset")
        rate_limit_reset_at = self._format_rate_limit_reset(rate_limit_reset_raw)
        self._rate_limit_reset_at = rate_limit_reset_at

        rate_limited = False
        not_found = False
        if status_code == 403:
            rate_limited = self._is_rate_limited(status_code, api_message, rate_limit_remaining)
            message = (
                f"GitHub API 访问频率超限，请稍后再试。恢复时间约为：{rate_limit_reset_at}。\n（每个公网 IP 每小时未认证请求上限为 60 次）"
                if rate_limited and rate_limit_reset_at
                else "GitHub API 访问频率超限，请稍后再试。\n（每个公网 IP 每小时未认证请求上限为 60 次）"
                if rate_limited
                else f"GitHub 拒绝访问（403）：{api_message or '权限不足'}"
            )
        elif status_code == 404:
            not_found = True
            message = "未找到该仓库或对应的 Release。请确认仓库地址正确，并已发布 Release。"
        elif status_code and (status_code < 200 or status_code >= 300):
            message = f"GitHub 请求失败（HTTP {status_code}）：{api_message or err_text or '未知错误'}"
        else:
            offline_like = {
                QNetworkReply.NetworkError.HostNotFoundError,
                QNetworkReply.NetworkError.NetworkSessionFailedError,
                QNetworkReply.NetworkError.TimeoutError,
                QNetworkReply.NetworkError.TemporaryNetworkFailureError,
                QNetworkReply.NetworkError.ConnectionRefusedError,
                QNetworkReply.NetworkError.RemoteHostClosedError,
            }
            if err in offline_like:
                message = "网络不可用或连接失败。请检查网络连接后重试。"
            else:
                message = f"网络请求失败：{err_text or '未知错误'}"

        try:
            if self.history_manager:
                self.history_manager.add_record(
                    operation_type=OperationType.UPDATE_CHECK,
                    description="检查更新失败",
                    details={
                        "status_code": status_code,
                        "api_message": api_message,
                        "rate_limited": rate_limited,
                        "rate_limit_remaining": rate_limit_remaining,
                        "rate_limit_reset": rate_limit_reset_raw,
                        "rate_limit_reset_at": rate_limit_reset_at,
                        "not_found": not_found,
                    },
                    success=False,
                    error_message=message,
                )
        except Exception:
            pass

        retryable = not (rate_limited or not_found)
        if retryable and self._attempt < self._max_attempts:
            self._set_update_ui_state("loading", f"{message}\n正在重试 ({self._attempt}/{self._max_attempts})...")
            QTimer.singleShot(1200, self._attempt_fetch)
            return

        if rate_limited:
            state_msg = f"GitHub API 访问频率超限，约 {rate_limit_reset_at} 后恢复。" if rate_limit_reset_at else "GitHub API 访问频率超限，请稍后再试。"
            box_msg = (
                f"GitHub API 访问频率超限，请稍后再试。\n\n预计恢复时间：{rate_limit_reset_at}\n\n"
                if rate_limit_reset_at
                else "GitHub API 访问频率超限，请稍后再试。\n\n"
            )
            self._set_update_ui_state("idle", state_msg)
            QMessageBox.information(
                self,
                "暂时无法检查更新",
                box_msg
                + "原因通常是当前公网 IP 的未认证 GitHub API 请求额度已经用完。\n"
                + "如果你在公司/校园网、代理或共享网络下，其他人的请求也会消耗同一个 IP 的额度。\n\n"
                + "GitHub 对未登录请求的限额为每个公网 IP 每小时 60 次。",
            )
            return

        if not_found:
            self._set_update_ui_state("idle", "仓库或 Release 不存在。")
            QMessageBox.information(
                self,
                "暂未发布更新",
                "未找到该仓库或对应的 Release。\n请确认仓库地址正确，并已在 GitHub 上发布 Release。",
            )
            return

        self._set_update_ui_state("error", message)
        QMessageBox.warning(self, "检查更新失败", f"{message}\n已达到最大重试次数。")

    def _handle_network_error(self, err, err_text: str):
        self._handle_http_or_network_error(err, err_text, 0, "", None)

    def _cancel_update_check(self):
        if self._reply is not None:
            old = self._reply
            try:
                old.abort()
            except Exception:
                pass
            self._reply = None
            try:
                old.deleteLater()
            except Exception:
                pass
        if self._timeout_timer.isActive():
            self._timeout_timer.stop()
        self._set_update_ui_state("idle", "已取消检查更新。")