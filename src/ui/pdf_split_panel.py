from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QGroupBox, QComboBox, QLineEdit, QSpinBox, QStyle,
    QRadioButton, QButtonGroup, QFormLayout, QSizePolicy,
    QMessageBox, QFileDialog, QProgressBar, QStackedWidget, QCheckBox, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QEvent, pyqtSignal, QSize, QThreadPool
from PyQt6.QtGui import QFontDatabase
from ui.widgets import AutoPopupComboBox
from ui.widgets import EmptyStateWidget, StatusBanner
from utils.file_picker import FilePicker
from utils.style_manager import StyleManager
from utils.history_manager import HistoryManager
from utils.async_worker import Worker
from core.pdf_split_engine import PdfSplitEngine, SplitMode
import os


class PdfSplitPanel(QWidget):
    filesAdded = pyqtSignal(list)
    
    def __init__(self, history_manager: HistoryManager = None):
        super().__init__()
        
        self.pdf_files = []
        self.history_manager = history_manager
        self.engine = PdfSplitEngine(history_manager)
        self._thread_pool = QThreadPool.globalInstance()
        self._active_workers: set[Worker] = set()
        self._executing = False
        self._previewing = False
        self._preview_token = 0
        self.setAcceptDrops(True)
        
        self._setup_ui()
        self._connect_signals()
        self.filesAdded.connect(self._on_files_added)
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.status_banner = StatusBanner()
        self.status_banner.set_message("提示", "可将 PDF 拖拽到左侧列表，或点击“添加PDF文件”。")
        main_layout.addWidget(self.status_banner)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
        file_group = QGroupBox("PDF文件选择")
        file_group.setProperty("compact", True)
        file_layout = QVBoxLayout(file_group)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(8)
        
        button_layout = QHBoxLayout()
        
        self.add_file_button = QPushButton("添加PDF文件")
        self.add_file_button.setIconSize(QSize(20, 20))
        
        self.clear_files_button = QPushButton("清空列表")
        
        button_layout.addWidget(self.add_file_button)
        button_layout.addWidget(self.clear_files_button)
        button_layout.addStretch()
        
        file_layout.addLayout(button_layout)
        
        self.file_list = QListWidget()
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setAcceptDrops(True)
        self.file_list.installEventFilter(self)

        self.empty_files = EmptyStateWidget(
            title="还没有添加PDF",
            subtitle="支持拖拽添加，或点击下方按钮选择文件。",
            action_text="添加PDF文件",
            action_callback=self._on_add_files,
            icon=QStyle.StandardPixmap.SP_FileDialogStart,
        )

        self.file_stack = QStackedWidget()
        self.file_stack.addWidget(self.empty_files)
        self.file_stack.addWidget(self.file_list)
        file_layout.addWidget(self.file_stack, 1)
        
        left_layout.addWidget(file_group, 1)
        
        splitter.addWidget(left_panel)
        
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        split_mode_group = QGroupBox("拆分模式")
        split_mode_group.setProperty("compact", True)
        split_mode_layout = QVBoxLayout(split_mode_group)
        split_mode_layout.setContentsMargins(0, 0, 0, 0)
        split_mode_layout.setSpacing(8)
        
        self.mode_combo = AutoPopupComboBox()
        self.mode_combo.addItems(["按页数拆分", "按文件大小拆分", "按页码范围拆分", "按书签拆分"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.mode_combo.setProperty("compact", True)
        self.mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        split_mode_layout.addWidget(self.mode_combo)
        
        self.mode_stack = QStackedWidget()
        
        self.page_mode_widget = self._create_page_mode_widget()
        self.size_mode_widget = self._create_size_mode_widget()
        self.range_mode_widget = self._create_range_mode_widget()
        self.bookmark_mode_widget = self._create_bookmark_mode_widget()
        
        self.mode_stack.addWidget(self.page_mode_widget)
        self.mode_stack.addWidget(self.size_mode_widget)
        self.mode_stack.addWidget(self.range_mode_widget)
        self.mode_stack.addWidget(self.bookmark_mode_widget)
        
        self.mode_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        split_mode_layout.addWidget(self.mode_stack)
        
        right_layout.addWidget(split_mode_group)
        
        output_group = QGroupBox("输出设置")
        output_group.setProperty("compact", True)
        output_layout = QFormLayout(output_group)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(8)
        output_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.use_custom_output_dir_checkbox = QCheckBox()
        self.use_custom_output_dir_checkbox.setChecked(False)
        self.use_custom_output_dir_checkbox.setToolTip("指定输出目录（不勾选则与源文件同目录）")

        self.output_dir_label = QLabel("与源文件同目录")
        self.output_dir_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        self.output_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.select_output_button = QPushButton("选择输出目录")
        self.select_output_button.setFixedHeight(30)
        self.select_output_button.setProperty("variant", "primary")
        self.select_output_button.setAutoDefault(False)
        self.select_output_button.setDefault(False)
        self.select_output_button.setEnabled(False)

        output_dir_row = QWidget()
        output_dir_row_layout = QHBoxLayout(output_dir_row)
        output_dir_row_layout.setContentsMargins(0, 0, 0, 0)
        output_dir_row_layout.setSpacing(8)
        output_dir_row_layout.addWidget(self.use_custom_output_dir_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        output_dir_row_layout.addWidget(self.output_dir_label, 1)
        output_dir_row_layout.addWidget(self.select_output_button, 0, Qt.AlignmentFlag.AlignVCenter)
        
        output_layout.addRow("输出目录:", output_dir_row)
        
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText("例如: split_")
        self.prefix_input.setProperty("compact", True)
        output_layout.addRow("文件名前缀:", self.prefix_input)
        
        right_layout.addWidget(output_group)
        
        action_group = QGroupBox("操作")
        action_group.setProperty("compact", True)
        action_layout = QVBoxLayout(action_group)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        action_layout.addWidget(self.progress_bar)
        
        self.preview_button = QPushButton("预览拆分结果")
        self.preview_button.setFixedHeight(32)
        self.preview_button.setProperty("variant", "primary")

        self.copy_preview_button = QPushButton("复制预览")
        self.copy_preview_button.setFixedHeight(32)
        self.copy_preview_button.setProperty("variant", "outline")

        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setPlaceholderText("点击“预览拆分结果”生成预览")
        self.preview_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.preview_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_text.setMinimumHeight(140)
        
        self.execute_button = QPushButton("开始拆分")
        self.execute_button.setFixedHeight(36)
        self.execute_button.setProperty("variant", "primary")

        preview_row = QWidget()
        preview_row_layout = QHBoxLayout(preview_row)
        preview_row_layout.setContentsMargins(0, 0, 0, 0)
        preview_row_layout.setSpacing(8)
        preview_row_layout.addWidget(self.preview_button, 1)
        preview_row_layout.addWidget(self.copy_preview_button, 0)

        action_layout.addWidget(preview_row)
        action_layout.addWidget(self.preview_text, 1)
        action_layout.addWidget(self.execute_button)
        
        right_layout.addWidget(action_group, 1)
        
        splitter.addWidget(right_panel)
        
        splitter.setSizes([650, 550])
    
    def _create_page_mode_widget(self):
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.page_count_spin = QSpinBox()
        self.page_count_spin.setRange(1, 1000)
        self.page_count_spin.setValue(1)
        self.page_count_spin.setProperty("compact", True)
        layout.addRow("每份页数:", self.page_count_spin)
        
        return widget
    
    def _create_size_mode_widget(self):
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 100)
        self.size_spin.setValue(10)
        self.size_spin.setProperty("compact", True)
        
        self.size_unit_combo = AutoPopupComboBox()
        self.size_unit_combo.addItems(["MB", "KB"])
        self.size_unit_combo.setProperty("compact", True)
        self.size_unit_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        
        size_layout = QHBoxLayout()
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(6)
        size_layout.addWidget(self.size_spin, 1)
        size_layout.addWidget(self.size_unit_combo)
        
        layout.addRow("最大文件大小:", size_layout)
        
        return widget
    
    def _create_range_mode_widget(self):
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.range_input = QLineEdit()
        self.range_input.setPlaceholderText("例如: 1-5, 8, 10-15")
        self.range_input.setProperty("compact", True)
        layout.addRow("页码范围:", self.range_input)
        
        hint_label = QLabel("提示: 使用逗号分隔多个范围，例如: 1-3, 5, 7-9")
        hint_label.setFont(StyleManager.get_font("small"))
        hint_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        layout.addRow("", hint_label)
        
        return widget
    
    def _create_bookmark_mode_widget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        self.bookmark_level_spin = QSpinBox()
        self.bookmark_level_spin.setRange(1, 10)
        self.bookmark_level_spin.setValue(1)
        self.bookmark_level_spin.setProperty("compact", True)
        
        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form_layout.addRow("书签级别:", self.bookmark_level_spin)
        
        layout.addLayout(form_layout)
        
        info_label = QLabel("根据PDF书签结构拆分，每个指定级别的书签对应一个文件")
        info_label.setFont(StyleManager.get_font("small"))
        info_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        layout.addStretch()
        
        return widget
    
    def _connect_signals(self):
        self.add_file_button.clicked.connect(self._on_add_files)
        self.clear_files_button.clicked.connect(self._on_clear_files)
        self.select_output_button.clicked.connect(self._on_select_output)
        self.use_custom_output_dir_checkbox.stateChanged.connect(self._on_use_custom_output_dir_changed)
        self.preview_button.clicked.connect(self._on_preview)
        self.copy_preview_button.clicked.connect(self._on_copy_preview)
        self.execute_button.clicked.connect(self._on_execute)
        self._sync_file_list_state()
    
    def _on_add_files(self):
        files = FilePicker.get_pdf_files(self)
        self._add_files(files)
    
    def _on_clear_files(self):
        self.pdf_files.clear()
        self.file_list.clear()
        self._sync_file_list_state()
        self.status_banner.set_message("提示", "列表已清空。")
    
    def _on_select_output(self):
        if not self.use_custom_output_dir_checkbox.isChecked():
            self.use_custom_output_dir_checkbox.setChecked(True)
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录", "")
        if dir_path:
            self.output_dir_label.setText(dir_path)
            self.output_dir_label.setStyleSheet(f"color: {StyleManager.get_color('success')};")

    def _on_use_custom_output_dir_changed(self, state):
        checked = state == Qt.CheckState.Checked.value
        self.select_output_button.setEnabled(checked)
        if checked:
            if not self.output_dir_label.text() or self.output_dir_label.text() == "与源文件同目录":
                self.output_dir_label.setText("未选择")
                self.output_dir_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        else:
            self.output_dir_label.setText("与源文件同目录")
            self.output_dir_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
    
    def _on_mode_changed(self, mode_text):
        if mode_text == "按页数拆分":
            self.mode_stack.setCurrentWidget(self.page_mode_widget)
        elif mode_text == "按文件大小拆分":
            self.mode_stack.setCurrentWidget(self.size_mode_widget)
        elif mode_text == "按页码范围拆分":
            self.mode_stack.setCurrentWidget(self.range_mode_widget)
        elif mode_text == "按书签拆分":
            self.mode_stack.setCurrentWidget(self.bookmark_mode_widget)
    
    def _build_config(self) -> dict:
        mode_text = self.mode_combo.currentText()
        
        if mode_text == "按页数拆分":
            mode = SplitMode.BY_PAGE_COUNT
            page_count = self.page_count_spin.value()
            config = {
                "mode": mode.value,
                "page_count": page_count,
                "max_size": 10,
                "size_unit": "MB",
                "page_ranges": "",
                "bookmark_level": 1
            }
        elif mode_text == "按文件大小拆分":
            mode = SplitMode.BY_FILE_SIZE
            max_size = self.size_spin.value()
            size_unit = self.size_unit_combo.currentText()
            config = {
                "mode": mode.value,
                "page_count": 10,
                "max_size": max_size,
                "size_unit": size_unit,
                "page_ranges": "",
                "bookmark_level": 1
            }
        elif mode_text == "按页码范围拆分":
            mode = SplitMode.BY_PAGE_RANGE
            page_ranges = self.range_input.text().strip()
            config = {
                "mode": mode.value,
                "page_count": 10,
                "max_size": 10,
                "size_unit": "MB",
                "page_ranges": page_ranges,
                "bookmark_level": 1
            }
        elif mode_text == "按书签拆分":
            mode = SplitMode.BY_BOOKMARK
            bookmark_level = self.bookmark_level_spin.value()
            config = {
                "mode": mode.value,
                "page_count": 10,
                "max_size": 10,
                "size_unit": "MB",
                "page_ranges": "",
                "bookmark_level": bookmark_level
            }
        else:
            config = {
                "mode": SplitMode.BY_PAGE_COUNT.value,
                "page_count": 10,
                "max_size": 10,
                "size_unit": "MB",
                "page_ranges": "",
                "bookmark_level": 1
            }
        
        if self.use_custom_output_dir_checkbox.isChecked() and self.output_dir_label.text() != "未选择":
            config["output_dir"] = self.output_dir_label.text()
        else:
            config["output_dir"] = ""
        config["file_prefix"] = self.prefix_input.text().strip()
        
        return config
    
    def _on_preview(self):
        if not self.pdf_files:
            self.status_banner.set_message("提示", "请先添加PDF文件后再预览。")
            return
        if self._previewing:
            self.status_banner.set_message("正在预览", "正在生成预览，请稍候…")
            return
        config = self._build_config()
        files = list(self.pdf_files)
        self._previewing = True
        self._preview_token += 1
        token = self._preview_token
        self.preview_button.setEnabled(False)
        self.copy_preview_button.setEnabled(False)
        if not self._executing:
            self.execute_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.preview_text.setPlainText("正在生成预览，请稍候…")
        self.status_banner.set_message("正在预览", "正在读取PDF并生成拆分计划…")

        worker = Worker(self._generate_preview_lines_for_files, files, config)
        self._active_workers.add(worker)

        def _finish_preview():
            self._previewing = False
            self.preview_button.setEnabled(not self._executing)
            self.copy_preview_button.setEnabled(not self._executing)
            self.execute_button.setEnabled(not self._executing)
            if not self._executing:
                self.progress_bar.setVisible(False)
            self._active_workers.discard(worker)

        def _on_finished(result):
            if token != self._preview_token:
                self._active_workers.discard(worker)
                return
            lines = result if isinstance(result, list) else ["（无预览内容）"]
            self.preview_text.setPlainText("\n".join(lines))
            self.status_banner.set_message("预览已生成", f"共 {len(files)} 个文件。")
            _finish_preview()

        def _on_error(err):
            if token != self._preview_token:
                self._active_workers.discard(worker)
                return
            message = getattr(err, "message", None) or str(err)
            exc_type = getattr(err, "exc_type", None)
            if exc_type:
                message = f"{exc_type}: {message}"
            self.preview_text.setPlainText(f"预览生成失败：{message}")
            self.status_banner.set_message("预览失败", "生成预览时发生错误，请检查文件或拆分设置。")
            _finish_preview()

        worker.signals.finished.connect(_on_finished)
        worker.signals.error.connect(_on_error)
        self._thread_pool.start(worker)
    
    def _on_execute(self):
        if not self.pdf_files:
            self.status_banner.set_message("提示", "请先添加PDF文件后再开始拆分。")
            return
        
        if self.use_custom_output_dir_checkbox.isChecked():
            if not self.output_dir_label.text() or self.output_dir_label.text() == "未选择":
                self.status_banner.set_message("提示", "已勾选自定义输出目录，请先选择目录。")
                return
        
        config = self._build_config()
        
        self._executing = True
        self._preview_token += 1
        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.copy_preview_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.status_banner.set_message("正在处理", "正在拆分，请稍候…")

        worker = Worker(self.engine.execute_split, list(self.pdf_files), config)
        self._active_workers.add(worker)

        def _on_finished(result):
            self._executing = False
            self.progress_bar.setRange(0, max(1, len(self.pdf_files)))
            self.progress_bar.setValue(len(self.pdf_files))
            if result.get("failed", 0) == 0:
                total_outputs = 0
                for op in (result.get("operations") or []):
                    try:
                        total_outputs += int(op.get("output_count") or 0)
                    except Exception:
                        total_outputs += len(op.get("output_files", []) or [])
                QMessageBox.information(self, "成功", f"成功拆分 {result.get('successful', 0)} 个PDF文件\n共生成 {total_outputs} 个输出文件")
            else:
                error_msg = f"成功 {result.get('successful', 0)} 个，失败 {result.get('failed', 0)} 个\n"
                errs = result.get("errors") or []
                if errs:
                    error_msg += "\n错误信息:\n" + "\n".join(errs[:3])
                QMessageBox.warning(self, "部分失败", error_msg)
            self.execute_button.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.copy_preview_button.setEnabled(True)
            self.progress_bar.setVisible(False)
            self._active_workers.discard(worker)

        def _on_error(err):
            self._executing = False
            message = getattr(err, "message", None) or str(err)
            exc_type = getattr(err, "exc_type", None)
            if exc_type:
                message = f"{exc_type}: {message}"
            QMessageBox.critical(self, "错误", f"执行拆分时发生错误:\n{message}")
            self.execute_button.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.copy_preview_button.setEnabled(True)
            self.progress_bar.setVisible(False)
            self._active_workers.discard(worker)

        worker.signals.finished.connect(_on_finished)
        worker.signals.error.connect(_on_error)
        self._thread_pool.start(worker)

    def _generate_preview_lines_for_files(self, files: list[str], config: dict) -> list[str]:
        lines: list[str] = []
        used_paths: set[str] = set()

        for pdf_path in files:
            base = os.path.basename(pdf_path)
            plan = self.engine.plan_outputs_for_file(pdf_path, config)
            if not plan.get("valid"):
                lines.append(f"{base}  [FAIL] {plan.get('message') or '文件无效'}")
                continue

            page_count = int(plan.get("page_count") or 0)
            target_dir = str(plan.get("output_dir") or os.path.dirname(pdf_path))
            lines.append(f"{base}  ({page_count} 页)")
            lines.append(f"  输出目录: {target_dir}")

            outputs = plan.get("outputs") or []
            for out in outputs:
                name = getattr(out, "filename", "") or ""
                page_range = getattr(out, "page_range", None)
                unique_path = self.engine.make_unique_output_path(target_dir, name, used_paths)
                unique_name = os.path.basename(unique_path)
                if page_range and isinstance(page_range, tuple) and len(page_range) == 2:
                    lines.append(f"  - {unique_name}  ({page_range[0]}-{page_range[1]})")
                else:
                    lines.append(f"  - {unique_name}")

            lines.append("")

        if not lines:
            return ["（无预览内容）"]
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def _generate_preview_lines(self, config: dict) -> list[str]:
        return self._generate_preview_lines_for_files(list(self.pdf_files), config)
    
    def _on_files_added(self, files):
        for file_path in files:
            file_name = os.path.basename(file_path)
            item = QListWidgetItem(file_name)
            item.setData(Qt.ItemDataRole.UserRole, file_path)
            self.file_list.addItem(item)
        self._sync_file_list_state()
        if files:
            self.status_banner.set_message("已添加文件", f"新增 {len(files)} 个，当前共 {len(self.pdf_files)} 个。")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self._add_files(self._extract_paths_from_drop_event(event))
        event.acceptProposedAction()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.DragEnter:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True
        if event.type() == QEvent.Type.Drop:
            self._add_files(self._extract_paths_from_drop_event(event))
            event.acceptProposedAction()
            return True
        return super().eventFilter(obj, event)

    def _extract_paths_from_drop_event(self, event):
        urls = event.mimeData().urls()
        paths = []
        for u in urls:
            p = u.toLocalFile()
            if p and os.path.isfile(p) and p.lower().endswith(".pdf"):
                paths.append(p)
        return paths

    def _add_files(self, files):
        if not files:
            return
        existing = set(self.pdf_files)
        new_files = []
        for f in files:
            if f and os.path.isfile(f) and f.lower().endswith(".pdf") and f not in existing:
                new_files.append(f)
                existing.add(f)
        if not new_files:
            return
        self.pdf_files.extend(new_files)
        self.filesAdded.emit(new_files)

    def _sync_file_list_state(self):
        has_files = bool(self.pdf_files)
        self.file_stack.setCurrentIndex(1 if has_files else 0)
        self.clear_files_button.setEnabled(has_files)

    def _on_copy_preview(self):
        text = self.preview_text.toPlainText()
        if not text.strip():
            self.status_banner.set_message("提示", "预览为空，先点击“预览拆分结果”。")
            return
        try:
            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(text)
            self.status_banner.set_message("已复制", "预览内容已复制到剪贴板。")
        except Exception:
            self.status_banner.set_message("提示", "复制失败，请稍后重试。")
