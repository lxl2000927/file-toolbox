from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextBrowser, QGroupBox, QPlainTextEdit, QHBoxLayout, QPushButton, QFileDialog, QLabel, QProgressBar, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from utils.style_manager import StyleManager
from utils.history_manager import HistoryManager, OperationType
from PyQt6.QtCore import QUrl, QTimer


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
        self._update_url = "https://www.bilibili.com/video/BV1uT4y1P7CX/?share_source=copy_web"
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
        if path.lower().endswith(".json"):
            try:
                import json
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
            except Exception:
                return
        else:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.logs_view.toPlainText())
            except Exception:
                return

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
        self._set_update_ui_state("loading", "正在检查网络并准备打开更新页面...")
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
        if err != QNetworkReply.NetworkError.NoError:
            self._handle_network_error(err, reply.errorString())
            reply.deleteLater()
            return

        reply.deleteLater()
        ok = QDesktopServices.openUrl(QUrl(self._update_url))
        if ok:
            self._set_update_ui_state("idle", "已在浏览器中打开更新页面。")
            return
        self._handle_open_failed()

    def _handle_open_failed(self):
        if self._attempt < self._max_attempts:
            self._set_update_ui_state("loading", f"打开失败，正在重试 ({self._attempt}/{self._max_attempts})...")
            QTimer.singleShot(1200, self._attempt_fetch)
            return
        self._set_update_ui_state("error", "无法打开链接。请点击“重试”或检查默认浏览器设置。")
        QMessageBox.critical(self, "打开失败", "无法打开链接。请检查默认浏览器设置或稍后重试。")

    def _handle_network_error(self, err, err_text: str):
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
            message = f"网络请求失败：{err_text}"

        if self._attempt < self._max_attempts:
            self._set_update_ui_state("loading", f"{message}\n正在重试 ({self._attempt}/{self._max_attempts})...")
            QTimer.singleShot(1200, self._attempt_fetch)
            return

        self._set_update_ui_state("error", f"{message}\n已达到最大重试次数，可点击“重试”再次尝试。")
        QMessageBox.warning(self, "检查更新失败", message)

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
        self._set_update_ui_state("idle", "已取消打开链接。")
