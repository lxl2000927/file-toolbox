from __future__ import annotations

import os
import time

from PyQt6.QtCore import Qt, QEvent, QPoint, QTimer, QSettings, QDateTime
from PyQt6.QtGui import QImage, QPixmap, QTextCharFormat, QColor, QTextCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QSplitter,
    QGroupBox,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QStackedWidget,
    QStyle,
)
from PyQt6.QtWidgets import QDialog

from core.pdf_scan_split_engine import PdfScanSplitOptions, PdfScanSplitResult
from ui.pdf_scan_split_worker import PdfScanSplitWorker
from ui.roi_select_dialog import RoiSelectDialog
from ui.widgets import AutoPopupComboBox
from ui.widgets import EmptyStateWidget, StatusBanner
from utils.style_manager import StyleManager
from utils.history_manager import HistoryManager, OperationType


class PdfScanSplitPanel(QWidget):
    def __init__(self, history_manager: HistoryManager = None, parent=None):
        super().__init__(parent)
        self.history_manager = history_manager
        self._pdf_path: str = ""
        self._reference_image_path: str = ""
        self._worker: PdfScanSplitWorker | None = None
        self._worker_task: str = "scan_split"
        self._zoom_step = 1.15
        self._reference_pixmap_original: QPixmap | None = None
        self._reference_zoom = 1.0
        self._reference_zoom_custom = False
        self._reference_loaded_path = ""
        self._reference_roi: tuple[int, int, int, int] | None = None
        self._pdf_total_pages: int = 0
        self._restoring_settings = False
        self._save_settings_timer: QTimer | None = None
        self._drag_active_ref = False
        self._drag_start_pos = QPoint()
        self._drag_start_h = 0
        self._drag_start_v = 0
        self._run_started_at: float | None = None
        self._run_log_lines: list[str] = []
        self._run_context: dict | None = None
        self._main_splitter: QSplitter | None = None
        self._splitter_adjusting = False

        self._setup_ui()
        self._connect_signals()
        self._restore_settings()
        self._refresh_pdf_page_count()

    def set_pdf_path(self, pdf_path: str):
        self._pdf_path = pdf_path or ""
        self.pdf_path_input.setText(self._pdf_path)
        self._refresh_pdf_page_count()
        self._sync_image_state()
        if self._pdf_path:
            self.status_banner.set_message("已选择PDF", os.path.basename(self._pdf_path))
        self._schedule_save_settings()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.status_banner = StatusBanner()
        self.status_banner.set_message("提示", "先选择PDF文件，再选择识别方式并开始扫描。")
        root.addWidget(self.status_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._main_splitter = splitter

        self.function_group = QGroupBox("功能区")
        self.function_group.setProperty("compact", True)
        function_layout = QVBoxLayout(self.function_group)
        function_layout.setContentsMargins(0, 0, 0, 0)
        function_layout.setSpacing(10)

        pdf_row = QWidget()
        pdf_row_layout = QHBoxLayout(pdf_row)
        pdf_row_layout.setContentsMargins(0, 0, 0, 0)
        pdf_row_layout.setSpacing(8)

        self.pdf_path_input = QLineEdit()
        self.pdf_path_input.setPlaceholderText("选择要拆分的PDF")
        self.pdf_path_input.setReadOnly(True)
        self.pdf_path_input.setProperty("compact", True)

        self.select_pdf_button = QPushButton("选择PDF")
        self.select_pdf_button.setFixedHeight(38)
        self.select_pdf_button.setProperty("variant", "primary")
        self.select_pdf_button.setAutoDefault(False)
        self.select_pdf_button.setDefault(False)

        pdf_row_layout.addWidget(self.pdf_path_input, 1)
        pdf_row_layout.addWidget(self.select_pdf_button)
        function_layout.addWidget(pdf_row)

        out_row = QWidget()
        out_row_layout = QHBoxLayout(out_row)
        out_row_layout.setContentsMargins(0, 0, 0, 0)
        out_row_layout.setSpacing(8)

        self.output_dir_input = QLineEdit()
        self.output_dir_input.setPlaceholderText("输出目录（留空则与PDF同目录）")
        self.output_dir_input.setReadOnly(True)
        self.output_dir_input.setProperty("compact", True)

        self.select_output_button = QPushButton("选择输出目录")
        self.select_output_button.setFixedHeight(38)
        self.select_output_button.setProperty("variant", "outline")
        self.select_output_button.setAutoDefault(False)
        self.select_output_button.setDefault(False)

        out_row_layout.addWidget(self.output_dir_input, 1)
        out_row_layout.addWidget(self.select_output_button)
        function_layout.addWidget(out_row)

        prefix_row = QWidget()
        prefix_row_layout = QHBoxLayout(prefix_row)
        prefix_row_layout.setContentsMargins(0, 0, 0, 0)
        prefix_row_layout.setSpacing(8)

        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText("输出文件名前缀（可选，例如: split_）")
        self.prefix_input.setProperty("compact", True)

        prefix_row_layout.addWidget(self.prefix_input, 1)
        function_layout.addWidget(prefix_row)

        ref_row = QWidget()
        ref_row_layout = QHBoxLayout(ref_row)
        ref_row_layout.setContentsMargins(0, 0, 0, 0)
        ref_row_layout.setSpacing(8)

        self.reference_image_input = QLineEdit()
        self.reference_image_input.setPlaceholderText("选择参考图像（用于特征匹配）")
        self.reference_image_input.setReadOnly(True)
        self.reference_image_input.setProperty("compact", True)

        self.select_reference_button = QPushButton("选择图像")
        self.select_reference_button.setFixedHeight(38)
        self.select_reference_button.setProperty("variant", "primary")
        self.select_reference_button.setAutoDefault(False)
        self.select_reference_button.setDefault(False)

        ref_row_layout.addWidget(self.reference_image_input, 1)
        ref_row_layout.addWidget(self.select_reference_button)
        function_layout.addWidget(ref_row)

        mode_row = QWidget()
        mode_row_layout = QHBoxLayout(mode_row)
        mode_row_layout.setContentsMargins(0, 0, 0, 0)
        mode_row_layout.setSpacing(8)

        mode_label = QLabel("识别方式:")
        mode_label.setFont(StyleManager.get_font("small"))
        mode_label.setStyleSheet(f"color: {StyleManager.get_color('gray_700')};")

        self.detect_mode_combo = AutoPopupComboBox()
        self.detect_mode_combo.addItems(["自动(二维码/印章/特征点)", "二维码识别", "印章识别", "特征匹配(参考图像)"])
        self.detect_mode_combo.setProperty("compact", True)

        self.qrcode_text_input = QLineEdit()
        self.qrcode_text_input.setPlaceholderText("二维码内容包含（可选）")
        self.qrcode_text_input.setProperty("compact", True)

        mode_row_layout.addWidget(mode_label)
        mode_row_layout.addWidget(self.detect_mode_combo, 1)
        function_layout.addWidget(mode_row)

        qr_opts = QWidget()
        qr_opts_layout = QHBoxLayout(qr_opts)
        qr_opts_layout.setContentsMargins(0, 0, 0, 0)
        qr_opts_layout.setSpacing(10)

        self.qrcode_no_decode_checkbox = QCheckBox("二维码不解码内容")
        self.qrcode_no_decode_checkbox.setToolTip("仅检测二维码存在，不读取内容（更快；将忽略“二维码内容包含”筛选）")
        self.qrcode_use_roi_checkbox = QCheckBox("框选特征点")
        self.qrcode_use_roi_checkbox.setChecked(True)
        self.qrcode_use_roi_checkbox.setToolTip("使用框选区域(ROI)缩小识别范围，提升速度与稳定性")

        self.qrcode_skip_checkbox = QCheckBox("命中后跳过")
        self.qrcode_skip_pages_spin = QSpinBox()
        self.qrcode_skip_pages_spin.setRange(0, 200)
        self.qrcode_skip_pages_spin.setValue(0)
        self.qrcode_skip_pages_spin.setProperty("compact", True)
        skip_label = QLabel("页")
        skip_label.setStyleSheet(f"color: {StyleManager.get_color('gray_700')};")

        qr_opts_layout.addWidget(self.qrcode_no_decode_checkbox)
        qr_opts_layout.addWidget(self.qrcode_use_roi_checkbox)
        qr_opts_layout.addStretch(1)
        qr_opts_layout.addWidget(self.qrcode_skip_checkbox)
        qr_opts_layout.addWidget(self.qrcode_skip_pages_spin)
        qr_opts_layout.addWidget(skip_label)

        function_layout.addWidget(self.qrcode_text_input)
        function_layout.addWidget(qr_opts)

        params_row = QWidget()
        params_row_layout = QGridLayout(params_row)
        params_row_layout.setContentsMargins(0, 0, 0, 0)
        params_row_layout.setHorizontalSpacing(10)
        params_row_layout.setVerticalSpacing(8)

        self.nfeatures_spin = QSpinBox()
        self.nfeatures_spin.setRange(100, 5000)
        self.nfeatures_spin.setValue(1200)
        self.nfeatures_spin.setProperty("compact", True)
        self.nfeatures_spin.setToolTip("每页提取的ORB特征点上限。值越大越容易匹配到标记，但速度更慢。")

        self.min_matches_spin = QSpinBox()
        self.min_matches_spin.setRange(5, 300)
        self.min_matches_spin.setValue(25)
        self.min_matches_spin.setProperty("compact", True)
        self.min_matches_spin.setToolTip("判定为标记页所需的最少“有效匹配”数量。值越大越严格，误报更少但更易漏检。")

        self.ratio_spin = QDoubleSpinBox()
        self.ratio_spin.setRange(0.5, 0.95)
        self.ratio_spin.setSingleStep(0.05)
        self.ratio_spin.setValue(0.75)
        self.ratio_spin.setProperty("compact", True)
        self.ratio_spin.setToolTip("KNN匹配的比例阈值（Lowe ratio test）。越小越严格，误匹配更少但可能漏检。")

        self.ransac_spin = QDoubleSpinBox()
        self.ransac_spin.setRange(1.0, 12.0)
        self.ransac_spin.setSingleStep(0.5)
        self.ransac_spin.setValue(5.0)
        self.ransac_spin.setProperty("compact", True)
        self.ransac_spin.setToolTip("RANSAC重投影阈值（像素）。越大越宽松，内点可能变多但误报风险增加。")

        self.min_inlier_ratio_spin = QDoubleSpinBox()
        self.min_inlier_ratio_spin.setRange(0.1, 0.9)
        self.min_inlier_ratio_spin.setSingleStep(0.05)
        self.min_inlier_ratio_spin.setValue(0.45)
        self.min_inlier_ratio_spin.setProperty("compact", True)
        self.min_inlier_ratio_spin.setToolTip("内点比例阈值。用于兜底判定：比例越高越严格。")

        self.preset_combo = AutoPopupComboBox()
        self.preset_combo.addItems(["预设：均衡(默认)", "预设：更严格(少误报)", "预设：更宽松(少漏检)"])
        self.preset_combo.setProperty("compact", True)

        self.marker_as_first_page_checkbox = QCheckBox("标记页作为新文件第一页")
        self.marker_as_first_page_checkbox.setChecked(True)

        self.exclude_marker_page_checkbox = QCheckBox("标记页不输出")
        self.exclude_marker_page_checkbox.setChecked(False)

        self.enable_multithread_checkbox = QCheckBox("启用多线程优化")
        self.enable_multithread_checkbox.setChecked(False)
        self.enable_multithread_checkbox.setToolTip("启用OpenCV多线程与优化（CPU占用更高，但通常更快）")

        self.enable_gpu_checkbox = QCheckBox("启用GPU加速")
        self.enable_gpu_checkbox.setChecked(False)
        self.enable_gpu_checkbox.setToolTip("优先尝试OpenCL/CUDA加速；若当前环境不可用会自动回退到CPU")

        params_row_layout.addWidget(QLabel("特征点数量:"), 0, 0)
        params_row_layout.addWidget(self.nfeatures_spin, 0, 1)
        params_row_layout.addWidget(QLabel("最小匹配数:"), 1, 0)
        params_row_layout.addWidget(self.min_matches_spin, 1, 1)
        params_row_layout.addWidget(QLabel("比例阈值:"), 2, 0)
        params_row_layout.addWidget(self.ratio_spin, 2, 1)
        params_row_layout.addWidget(QLabel("RANSAC阈值:"), 3, 0)
        params_row_layout.addWidget(self.ransac_spin, 3, 1)
        params_row_layout.addWidget(QLabel("内点比例阈值:"), 4, 0)
        params_row_layout.addWidget(self.min_inlier_ratio_spin, 4, 1)
        params_row_layout.addWidget(QLabel("参数预设:"), 5, 0)
        params_row_layout.addWidget(self.preset_combo, 5, 1)
        params_row_layout.addWidget(self.marker_as_first_page_checkbox, 6, 0, 1, 2)
        params_row_layout.addWidget(self.exclude_marker_page_checkbox, 7, 0, 1, 2)
        params_row_layout.addWidget(self.enable_multithread_checkbox, 8, 0, 1, 2)
        params_row_layout.addWidget(self.enable_gpu_checkbox, 9, 0, 1, 2)

        self.params_help_label = QLabel(
            "说明：特征点数量越大越容易匹配到标记但会变慢；最小匹配数越大判定越严格（误报更少、漏检更可能）；"
            "比例阈值越小越严格（误匹配更少、漏检更可能）；RANSAC阈值与内点比例阈值影响“内点”判定。"
        )
        self.params_help_label.setWordWrap(True)
        self.params_help_label.setFont(StyleManager.get_font("small"))
        self.params_help_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        params_row_layout.addWidget(self.params_help_label, 10, 0, 1, 2)
        function_layout.addWidget(params_row)

        tune_group = QGroupBox("调参工具（不输出文件）")
        tune_group.setProperty("compact", True)
        tune_layout = QHBoxLayout(tune_group)
        tune_layout.setContentsMargins(0, 0, 0, 0)
        tune_layout.setSpacing(8)

        self.test_page_spin = QSpinBox()
        self.test_page_spin.setRange(1, 1)
        self.test_page_spin.setValue(1)
        self.test_page_spin.setProperty("compact", True)
        self.test_page_spin.setToolTip("对指定页进行一次识别测试，并输出匹配/内点统计")

        self.probe_button = QPushButton("测试单页")
        self.probe_button.setFixedHeight(32)
        self.probe_button.setProperty("variant", "outline")
        self.probe_button.setAutoDefault(False)
        self.probe_button.setDefault(False)

        self.quick_scan_pages_spin = QSpinBox()
        self.quick_scan_pages_spin.setRange(1, 800)
        self.quick_scan_pages_spin.setValue(30)
        self.quick_scan_pages_spin.setProperty("compact", True)
        self.quick_scan_pages_spin.setToolTip("只扫描前N页，便于快速调参")

        self.quick_scan_button = QPushButton("快速扫描前N页")
        self.quick_scan_button.setFixedHeight(32)
        self.quick_scan_button.setProperty("variant", "outline")
        self.quick_scan_button.setAutoDefault(False)
        self.quick_scan_button.setDefault(False)

        tune_layout.addWidget(QLabel("页码:"))
        tune_layout.addWidget(self.test_page_spin)
        tune_layout.addWidget(self.probe_button)
        tune_layout.addStretch(1)
        tune_layout.addWidget(QLabel("前N页:"))
        tune_layout.addWidget(self.quick_scan_pages_spin)
        tune_layout.addWidget(self.quick_scan_button)

        function_layout.addWidget(tune_group)

        action_row = QWidget()
        action_row_layout = QHBoxLayout(action_row)
        action_row_layout.setContentsMargins(0, 0, 0, 0)
        action_row_layout.setSpacing(8)

        self.start_button = QPushButton("开始扫描拆分")
        self.start_button.setFixedHeight(38)
        self.start_button.setProperty("variant", "primary")
        self.start_button.setAutoDefault(False)
        self.start_button.setDefault(False)

        self.stop_button = QPushButton("停止")
        self.stop_button.setFixedHeight(38)
        self.stop_button.setProperty("variant", "outline")
        self.stop_button.setAutoDefault(False)
        self.stop_button.setDefault(False)
        self.stop_button.setEnabled(False)

        action_row_layout.addWidget(self.start_button, 1)
        action_row_layout.addWidget(self.stop_button)
        function_layout.addWidget(action_row)
        function_layout.addStretch()

        self.progress_group = QGroupBox("进度 / 日志")
        self.progress_group.setProperty("compact", True)
        progress_layout = QVBoxLayout(self.progress_group)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("这里会实时显示进度与错误信息")
        self.log_text.setMinimumHeight(120)
        progress_layout.addWidget(self.log_text, 1)

        self.progress_group.setMinimumHeight(170)

        self.image_group = QGroupBox("图像输入（预览 / 特征点）")
        self.image_group.setProperty("compact", True)
        image_layout = QVBoxLayout(self.image_group)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(8)

        self.image_empty = EmptyStateWidget(
            title="未选择PDF",
            subtitle="选择PDF后可预览页面并进行框选ROI / 调参测试。",
            action_text="选择PDF",
            action_callback=self._on_select_pdf,
            icon=QStyle.StandardPixmap.SP_FileDialogStart,
        )

        image_body = QWidget()
        image_body_layout = QVBoxLayout(image_body)
        image_body_layout.setContentsMargins(0, 0, 0, 0)
        image_body_layout.setSpacing(8)

        roi_row = QWidget()
        roi_row_layout = QHBoxLayout(roi_row)
        roi_row_layout.setContentsMargins(0, 0, 0, 0)
        roi_row_layout.setSpacing(8)

        self.roi_select_button = QPushButton("框选区域")
        self.roi_select_button.setProperty("variant", "outline")
        self.roi_select_button.setFixedHeight(32)
        self.roi_select_button.setAutoDefault(False)
        self.roi_select_button.setDefault(False)

        self.roi_clear_button = QPushButton("清除区域")
        self.roi_clear_button.setProperty("variant", "outline")
        self.roi_clear_button.setFixedHeight(32)
        self.roi_clear_button.setAutoDefault(False)
        self.roi_clear_button.setDefault(False)

        self.roi_summary_label = QLabel("")
        self.roi_summary_label.setFont(StyleManager.get_font("small"))
        self.roi_summary_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        self.roi_summary_label.setText("未框选区域")

        roi_row_layout.addWidget(self.roi_select_button)
        roi_row_layout.addWidget(self.roi_summary_label, 1)
        roi_row_layout.addStretch()
        roi_row_layout.addWidget(self.roi_clear_button)
        image_body_layout.addWidget(roi_row)

        self.keypoint_info_label = QLabel("未加载图像")
        self.keypoint_info_label.setStyleSheet(f"color: {StyleManager.get_color('gray_600')};")
        image_body_layout.addWidget(self.keypoint_info_label)

        self.image_view = QLabel()
        self.image_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_view.setMinimumHeight(320)
        self.image_view.setStyleSheet(f"border: 1px solid {StyleManager.get_color('border')}; border-radius: 8px;")

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.image_view)
        self.image_scroll = scroll
        image_body_layout.addWidget(scroll, 1)

        self.image_stack = QStackedWidget()
        self.image_stack.addWidget(self.image_empty)
        self.image_stack.addWidget(image_body)
        image_layout.addWidget(self.image_stack, 1)

        function_scroll = QScrollArea()
        function_scroll.setWidgetResizable(True)
        function_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        function_scroll.setWidget(self.function_group)
        self.function_scroll = function_scroll

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_layout.addWidget(self.image_group, 3)
        left_layout.addWidget(self.progress_group, 1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.function_scroll, 1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([650, 650])

        root.addWidget(splitter, 1)
        self._sync_image_state()

    def _connect_signals(self):
        self.select_pdf_button.clicked.connect(self._on_select_pdf)
        self.select_output_button.clicked.connect(self._on_select_output_dir)
        self.select_reference_button.clicked.connect(self._on_select_reference_image)

        self.nfeatures_spin.valueChanged.connect(self._refresh_reference_keypoints)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.ransac_spin.valueChanged.connect(self._schedule_save_settings)
        self.min_inlier_ratio_spin.valueChanged.connect(self._schedule_save_settings)
        self.min_matches_spin.valueChanged.connect(self._schedule_save_settings)
        self.ratio_spin.valueChanged.connect(self._schedule_save_settings)
        self.marker_as_first_page_checkbox.stateChanged.connect(self._schedule_save_settings)
        self.exclude_marker_page_checkbox.stateChanged.connect(self._schedule_save_settings)
        self.enable_multithread_checkbox.stateChanged.connect(self._schedule_save_settings)
        self.enable_gpu_checkbox.stateChanged.connect(self._schedule_save_settings)
        self.detect_mode_combo.currentIndexChanged.connect(self._on_detection_mode_changed)
        self.qrcode_no_decode_checkbox.stateChanged.connect(self._on_detection_mode_changed)
        self.qrcode_use_roi_checkbox.stateChanged.connect(self._on_detection_mode_changed)
        self.qrcode_skip_checkbox.stateChanged.connect(self._on_detection_mode_changed)
        self.qrcode_skip_pages_spin.valueChanged.connect(self._schedule_save_settings)
        self.qrcode_text_input.textChanged.connect(self._schedule_save_settings)
        self.quick_scan_pages_spin.valueChanged.connect(self._schedule_save_settings)
        self.test_page_spin.valueChanged.connect(self._schedule_save_settings)
        self.start_button.clicked.connect(self._on_start)
        self.stop_button.clicked.connect(self._on_stop)
        self.probe_button.clicked.connect(self._on_probe_page)
        self.quick_scan_button.clicked.connect(self._on_quick_scan)

        self.image_scroll.viewport().installEventFilter(self)
        self.roi_clear_button.clicked.connect(self._clear_reference_roi)
        self.roi_select_button.clicked.connect(self._open_roi_dialog)
        if self._main_splitter is not None:
            self._main_splitter.splitterMoved.connect(lambda *_: self._clamp_main_splitter())
        self._on_detection_mode_changed()

    def _on_select_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择PDF文件", "", "PDF Files (*.pdf)")
        if not path:
            return
        self.set_pdf_path(path)

    def _on_select_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", "")
        if not path:
            return
        self.output_dir_input.setText(path)

    def _on_select_reference_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择参考图像", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        self._reference_image_path = path
        self.reference_image_input.setText(path)
        self._on_detection_mode_changed()
        self._schedule_save_settings()

    def _append_log(self, text: str, level: str | None = None):
        if text is None:
            return
        try:
            text = str(text)
        except Exception:
            return
        if not text.strip():
            return
        # 推断日志级别
        if level is None:
            lower = text.lower()
            if any(word in lower for word in ["错误", "失败", "异常", "未找到", "不存在", "已取消"]):
                level = "error"
            elif any(word in lower for word in ["警告", "注意", "提示"]):
                level = "warn"
            elif any(word in lower for word in ["成功", "完成", "已生成", "耗时"]):
                level = "success"
            elif any(word in lower for word in ["调试", "trace"]):
                level = "debug"
            else:
                level = "info"
        # 时间戳
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        # 格式化输出
        prefix = f"[{timestamp}] [{level.upper()}]"
        full_text = f"{prefix} {text}"
        # 设置颜色
        cursor = self.log_text.textCursor()
        format = QTextCharFormat()
        if level == "error":
            format.setForeground(QColor("red"))
        elif level == "warn":
            format.setForeground(QColor("orange"))
        elif level == "success":
            format.setForeground(QColor("green"))
        elif level == "debug":
            format.setForeground(QColor("gray"))
        else:
            format.setForeground(QColor("black"))
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(full_text + "\n", format)
        # 自动滚动
        bar = self.log_text.verticalScrollBar()
        bar.setValue(bar.maximum())
        # 记录原始日志行（不带格式）
        if self._run_started_at is not None:
            self._run_log_lines.append(str(text))
            if len(self._run_log_lines) > 200:
                self._run_log_lines = self._run_log_lines[-200:]

    def _apply_reference_zoom(self):
        if self._reference_pixmap_original is None:
            return
        scale = max(0.2, min(5.0, float(self._reference_zoom)))
        pix = self._reference_pixmap_original
        scaled = pix.scaled(
            int(pix.width() * scale),
            int(pix.height() * scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_view.setPixmap(scaled)
        self.image_view.resize(scaled.size())

    def _set_reference_fit(self):
        if self._reference_pixmap_original is None:
            return
        if self._reference_zoom_custom:
            return
        viewport = self.image_scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        pix = self._reference_pixmap_original
        if pix.width() <= 0 or pix.height() <= 0:
            return
        scale = min(viewport.width() / pix.width(), viewport.height() / pix.height())
        scale = min(1.0, max(0.05, float(scale)))
        self._reference_zoom = scale
        self._apply_reference_zoom()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                if obj is self.image_scroll.viewport() and self._reference_pixmap_original is not None:
                    self._drag_active_ref = True
                    self._drag_start_pos = event.globalPosition().toPoint()
                    self._drag_start_h = self.image_scroll.horizontalScrollBar().value()
                    self._drag_start_v = self.image_scroll.verticalScrollBar().value()
                    self.image_scroll.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True

        if event.type() == QEvent.Type.MouseMove:
            if self._drag_active_ref and obj is self.image_scroll.viewport():
                pos = event.globalPosition().toPoint()
                delta = pos - self._drag_start_pos
                self.image_scroll.horizontalScrollBar().setValue(self._drag_start_h - delta.x())
                self.image_scroll.verticalScrollBar().setValue(self._drag_start_v - delta.y())
                return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._drag_active_ref:
                    self._drag_active_ref = False
                    self.image_scroll.viewport().unsetCursor()
                    return True

        if event.type() == QEvent.Type.Wheel:
            try:
                modifiers = event.modifiers()
            except Exception:
                modifiers = Qt.KeyboardModifier.NoModifier

            if modifiers == Qt.KeyboardModifier.ControlModifier:
                try:
                    delta = int(event.angleDelta().y())
                except Exception:
                    delta = 0
                if delta == 0:
                    return super().eventFilter(obj, event)

                if obj is self.image_scroll.viewport() and self._reference_pixmap_original is not None:
                    factor = 1.0 / self._zoom_step if delta < 0 else self._zoom_step
                    self._reference_zoom_custom = True
                    self._reference_zoom *= factor
                    self._apply_reference_zoom()
                    return True

        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._set_reference_fit)
        QTimer.singleShot(0, self._clamp_main_splitter)

    def _clamp_main_splitter(self):
        splitter = self._main_splitter
        if splitter is None or self._splitter_adjusting:
            return
        sizes = splitter.sizes()
        if not sizes or len(sizes) < 2:
            return
        total = int(sum(int(s) for s in sizes))
        if total <= 0:
            return
        min_side = 360
        min_ratio = 0.25
        max_ratio = 0.75
        min_left = max(min_side, int(total * min_ratio))
        max_left = min(total - min_side, int(total * max_ratio))
        if max_left < min_left:
            return
        left = int(sizes[0])
        if left < min_left:
            left = min_left
        elif left > max_left:
            left = max_left
        right = total - left
        if right < min_side:
            return
        self._splitter_adjusting = True
        try:
            splitter.setSizes([left, right])
        finally:
            self._splitter_adjusting = False

    def _refresh_reference_keypoints(self):
        mode = self._get_detection_mode()
        if mode in ("qrcode", "stamp"):
            if self._reference_image_path:
                label = "二维码模式：不计算特征点，可用于框选特征点" if mode == "qrcode" else "印章模式：不计算特征点，可用于框选特征点"
                self.keypoint_info_label.setText(label)
            else:
                label = "二维码模式：可选加载图像并框选特征点" if mode == "qrcode" else "印章模式：可选加载图像并框选特征点"
                self.keypoint_info_label.setText(label)
                self.image_view.setPixmap(QPixmap())
                self._reference_pixmap_original = None
                self._reference_zoom = 1.0
                self._reference_zoom_custom = False
                self._reference_loaded_path = ""
                self._sync_roi_summary()
                return
        if not self._reference_image_path:
            self.keypoint_info_label.setText("未加载图像")
            self.image_view.setPixmap(QPixmap())
            self._reference_pixmap_original = None
            self._reference_zoom = 1.0
            self._reference_zoom_custom = False
            self._reference_loaded_path = ""
            self._reference_roi = None
            self._sync_roi_summary()
            return
        new_image = self._reference_loaded_path != self._reference_image_path

        try:
            import numpy as np
            import cv2
        except Exception:
            self.keypoint_info_label.setText("缺少依赖：numpy / opencv-python")
            return

        try:
            data = np.fromfile(self._reference_image_path, dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("无法读取图像")
            if mode in ("qrcode", "stamp"):
                vis = img.copy()
                if self._reference_roi:
                    x, y, w, h = self._reference_roi
                    cv2.rectangle(vis, (x, y), (x + w, y + h), (255, 0, 0), 2)
                    label = "二维码模式：已加载图像（已框选特征点）" if mode == "qrcode" else "印章模式：已加载图像（已框选特征点）"
                    self.keypoint_info_label.setText(label)
                else:
                    label = "二维码模式：已加载图像（未框选特征点）" if mode == "qrcode" else "印章模式：已加载图像（未框选特征点）"
                    self.keypoint_info_label.setText(label)
            else:
                orb = cv2.ORB_create(nfeatures=int(self.nfeatures_spin.value()))
                kps, des = orb.detectAndCompute(img, None)
                kps = kps or []
                vis = cv2.drawKeypoints(img, kps, None, color=(0, 255, 0))

                if self._reference_roi:
                    x, y, w, h = self._reference_roi
                    in_roi = [
                        kp
                        for kp in kps
                        if x <= kp.pt[0] <= x + w and y <= kp.pt[1] <= y + h
                    ]
                    cv2.rectangle(vis, (x, y), (x + w, y + h), (255, 0, 0), 2)
                    self.keypoint_info_label.setText(
                        f"已加载图像：检测到 {len(kps)} 个特征点（框选区域内 {len(in_roi)} 个用于匹配）"
                    )
                else:
                    self.keypoint_info_label.setText(f"已加载图像：检测到 {len(kps)} 个特征点")

            rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            pix = QPixmap.fromImage(qimg)
            self._reference_pixmap_original = pix
            if new_image:
                self._reference_roi = None
                self._reference_zoom_custom = False
                self._reference_zoom = 1.0
            self._apply_reference_zoom()
            self._reference_loaded_path = self._reference_image_path
            QTimer.singleShot(0, self._set_reference_fit)
            self._sync_roi_summary()
        except Exception as e:
            self.keypoint_info_label.setText(f"图像处理失败：{str(e)}")

    def _clear_reference_roi(self):
        self._reference_roi = None
        self._sync_roi_summary()
        self._refresh_reference_keypoints()
        self._schedule_save_settings()

    def _sync_roi_summary(self):
        if not self._reference_roi:
            self.roi_summary_label.setText("未框选区域")
            return
        x, y, w, h = self._reference_roi
        self.roi_summary_label.setText(f"区域：x={x}, y={y}, w={w}, h={h}")

    def _open_roi_dialog(self):
        if not self._reference_image_path:
            QMessageBox.information(self, "提示", "请先选择参考图像")
            return
        try:
            dialog = RoiSelectDialog(
                image_path=self._reference_image_path,
                initial_roi=self._reference_roi,
                parent=self,
            )
        except Exception as e:
            QMessageBox.warning(self, "警告", str(e))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        roi = dialog.selected_roi()
        if roi:
            self._reference_roi = roi
            self._sync_roi_summary()
            self._refresh_reference_keypoints()
            self._schedule_save_settings()

    def _refresh_pdf_page_count(self):
        path = self.pdf_path_input.text().strip()
        total = 0
        if path and os.path.exists(path):
            try:
                import PyPDF2

                with open(path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    total = int(len(reader.pages) or 0)
            except Exception:
                total = 0
        self._pdf_total_pages = max(0, int(total))
        if hasattr(self, "test_page_spin"):
            if self._pdf_total_pages > 0:
                current = int(self.test_page_spin.value() or 1)
                self.test_page_spin.setRange(1, self._pdf_total_pages)
                self.test_page_spin.setValue(min(max(1, current), self._pdf_total_pages))
            else:
                self.test_page_spin.setRange(1, 1)
                self.test_page_spin.setValue(1)

    def _sync_image_state(self):
        has_pdf = bool(self.pdf_path_input.text().strip())
        if hasattr(self, "image_stack"):
            self.image_stack.setCurrentIndex(1 if has_pdf else 0)
        if not has_pdf:
            self._reference_pixmap_original = None
            self.image_view.clear()
            self.keypoint_info_label.setText("未加载图像")
            self.roi_summary_label.setText("未框选区域")

    def _build_options(self) -> PdfScanSplitOptions:
        mode = self._get_detection_mode()
        dpi = 180
        if mode == "qrcode":
            dpi = 200
        if mode == "stamp":
            dpi = 220
        if mode == "auto":
            dpi = 220
        roi_ready = bool(self.reference_image_input.text().strip()) and bool(self._reference_roi)
        return PdfScanSplitOptions(
            dpi=dpi,
            nfeatures=int(self.nfeatures_spin.value()),
            ratio=float(self.ratio_spin.value()),
            min_matches=int(self.min_matches_spin.value()),
            ransac_reproj_threshold=float(self.ransac_spin.value()),
            min_inlier_ratio=float(self.min_inlier_ratio_spin.value()),
            marker_as_first_page=bool(self.marker_as_first_page_checkbox.isChecked()),
            exclude_marker_page=bool(self.exclude_marker_page_checkbox.isChecked()),
            enable_multithread=bool(self.enable_multithread_checkbox.isChecked()),
            enable_gpu=bool(self.enable_gpu_checkbox.isChecked()),
            reference_roi=self._reference_roi,
            detection_mode=mode,
            qrcode_text_contains=self.qrcode_text_input.text().strip(),
            qrcode_no_decode=bool(self.qrcode_no_decode_checkbox.isChecked()),
            qrcode_skip_pages=int(self.qrcode_skip_pages_spin.value() if self.qrcode_skip_checkbox.isChecked() else 0),
            qrcode_use_roi=bool(self.qrcode_use_roi_checkbox.isChecked()) and roi_ready,
            qrcode_max_attempts=180,
        )

    def _get_detection_mode(self) -> str:
        text = self.detect_mode_combo.currentText().strip()
        if text.startswith("自动"):
            return "auto"
        if text.startswith("二维码"):
            return "qrcode"
        if text.startswith("印章"):
            return "stamp"
        if text.startswith("特征"):
            return "feature"
        return "qrcode"

    def _on_detection_mode_changed(self):
        mode = self._get_detection_mode()
        use_roi_for_qr = bool(self.qrcode_use_roi_checkbox.isChecked()) if hasattr(self, "qrcode_use_roi_checkbox") else False
        needs_ref = (mode in ("feature", "auto")) or (mode in ("qrcode", "stamp") and use_roi_for_qr)

        self.reference_image_input.setEnabled(needs_ref)
        self.select_reference_button.setEnabled(needs_ref)
        self.roi_select_button.setEnabled(needs_ref and bool(self._reference_image_path))
        self.roi_clear_button.setEnabled(needs_ref)

        no_decode = bool(self.qrcode_no_decode_checkbox.isChecked()) if hasattr(self, "qrcode_no_decode_checkbox") else False
        self.qrcode_no_decode_checkbox.setEnabled(mode in ("qrcode", "auto"))
        self.qrcode_use_roi_checkbox.setEnabled(mode in ("qrcode", "stamp", "auto"))
        self.qrcode_skip_checkbox.setEnabled(mode in ("qrcode", "stamp", "auto"))
        self.qrcode_skip_pages_spin.setEnabled(mode in ("qrcode", "stamp", "auto") and bool(self.qrcode_skip_checkbox.isChecked()))

        self.qrcode_text_input.setEnabled(mode in ("qrcode", "auto") and (not no_decode))

        feature_related = mode in ("feature", "auto")
        self.nfeatures_spin.setEnabled(feature_related)
        self.min_matches_spin.setEnabled(feature_related)
        self.ratio_spin.setEnabled(feature_related)
        self.ransac_spin.setEnabled(feature_related)
        self.min_inlier_ratio_spin.setEnabled(feature_related)
        self.preset_combo.setEnabled(feature_related)

        if mode == "auto":
            self.reference_image_input.setPlaceholderText("选择参考图像（可选，用于特征匹配兜底）")
            self.qrcode_use_roi_checkbox.setToolTip("使用框选区域(ROI)缩小识别范围，提升速度与稳定性")
        elif mode == "qrcode":
            if use_roi_for_qr:
                self.reference_image_input.setPlaceholderText("选择图像并框选特征点（可选）")
            else:
                self.reference_image_input.setPlaceholderText("二维码模式不需要参考图像")
            self.qrcode_use_roi_checkbox.setToolTip("使用框选区域(ROI)缩小识别范围，提升速度与稳定性")
        elif mode == "stamp":
            if use_roi_for_qr:
                self.reference_image_input.setPlaceholderText("选择图像并框选特征点（可选）")
            else:
                self.reference_image_input.setPlaceholderText("印章模式不需要参考图像")
            self.qrcode_use_roi_checkbox.setToolTip("使用框选区域(ROI)缩小识别范围，提升速度与稳定性")
        else:
            self.reference_image_input.setPlaceholderText("选择参考图像（用于特征匹配）")
            self.qrcode_use_roi_checkbox.setToolTip("使用框选区域(ROI)缩小识别范围，提升速度与稳定性")

        self._refresh_reference_keypoints()
        self._schedule_save_settings()

    def _on_preset_changed(self):
        if self._restoring_settings:
            return
        name = self.preset_combo.currentText().strip()
        if "更严格" in name:
            self.nfeatures_spin.setValue(1000)
            self.ratio_spin.setValue(0.70)
            self.min_matches_spin.setValue(35)
            self.ransac_spin.setValue(4.0)
            self.min_inlier_ratio_spin.setValue(0.55)
        elif "更宽松" in name:
            self.nfeatures_spin.setValue(2000)
            self.ratio_spin.setValue(0.85)
            self.min_matches_spin.setValue(18)
            self.ransac_spin.setValue(6.0)
            self.min_inlier_ratio_spin.setValue(0.35)
        else:
            self.nfeatures_spin.setValue(1200)
            self.ratio_spin.setValue(0.75)
            self.min_matches_spin.setValue(25)
            self.ransac_spin.setValue(5.0)
            self.min_inlier_ratio_spin.setValue(0.45)
        self._refresh_reference_keypoints()
        self._schedule_save_settings()

    def _on_start(self):
        if not self.pdf_path_input.text().strip():
            self.status_banner.set_message("提示", "请先选择PDF文件。")
            return
        mode = self._get_detection_mode()
        roi_requested = bool(self.qrcode_use_roi_checkbox.isChecked()) and mode in ("qrcode", "stamp", "auto")
        roi_ready = bool(self.reference_image_input.text().strip()) and bool(self._reference_roi)
        roi_fallback = roi_requested and (not roi_ready)
        roi_tip = "已勾选“框选特征点”，但未选择图像或未框选区域，已按全页识别"
        if mode == "feature" and not self.reference_image_input.text().strip():
            self.status_banner.set_message("提示", "特征匹配模式需要先选择参考图像。")
            return
        if self._worker and self._worker.isRunning():
            self.status_banner.set_message("提示", "任务正在执行中。")
            return

        options = self._build_options()

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._run_started_at = time.perf_counter()
        self._run_log_lines = []
        self._worker_task = "scan_split"
        pdf_path = self.pdf_path_input.text().strip()
        base = os.path.basename(pdf_path)
        if roi_fallback:
            self.status_banner.set_message("正在处理", f"正在扫描：{base}（ROI未设置，按全页识别）")
        else:
            self.status_banner.set_message("正在处理", f"正在扫描：{base}")
        options_dict = {
            "dpi": int(options.dpi),
            "nfeatures": int(options.nfeatures),
            "ratio": float(options.ratio),
            "min_matches": int(options.min_matches),
            "ransac_reproj_threshold": float(options.ransac_reproj_threshold),
            "min_inlier_ratio": float(options.min_inlier_ratio),
            "marker_as_first_page": bool(options.marker_as_first_page),
            "exclude_marker_page": bool(options.exclude_marker_page),
            "enable_multithread": bool(options.enable_multithread),
            "enable_gpu": bool(options.enable_gpu),
            "reference_roi": options.reference_roi,
            "detection_mode": str(options.detection_mode),
            "qrcode_text_contains": str(options.qrcode_text_contains or ""),
            "qrcode_no_decode": bool(options.qrcode_no_decode),
            "qrcode_skip_pages": int(options.qrcode_skip_pages or 0),
            "qrcode_use_roi": bool(options.qrcode_use_roi),
            "qrcode_max_attempts": int(options.qrcode_max_attempts or 180),
        }
        self._run_context = {
            "pdf_path": pdf_path,
            "pdf_name": base,
            "reference_image_path": self.reference_image_input.text().strip(),
            "output_dir": self.output_dir_input.text().strip(),
            "prefix": self.prefix_input.text().strip(),
            "options": options_dict,
        }
        if self.history_manager:
            try:
                self.history_manager.add_record(
                    operation_type=OperationType.SCAN_SPLIT,
                    description=f"开始扫描拆分：{base}",
                    details=self._run_context,
                    success=True,
                )
            except Exception:
                pass
        self._append_log("开始扫描拆分…")
        if roi_fallback:
            self._append_log(roi_tip)
        self._save_settings()

        self._worker = PdfScanSplitWorker(
            pdf_path=self.pdf_path_input.text().strip(),
            reference_image_path=self.reference_image_input.text().strip(),
            output_dir=self.output_dir_input.text().strip(),
            prefix=self.prefix_input.text().strip(),
            options=options,
            task="scan_split",
            parent=self,
        )
        self._worker.progressChanged.connect(self._on_worker_progress)
        self._worker.logAppended.connect(self._append_log)
        self._worker.finishedWithResult.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_probe_page(self):
        if not self.pdf_path_input.text().strip():
            QMessageBox.warning(self, "警告", "请先选择PDF文件")
            return
        mode = self._get_detection_mode()
        roi_requested = bool(self.qrcode_use_roi_checkbox.isChecked()) and mode in ("qrcode", "stamp", "auto")
        roi_ready = bool(self.reference_image_input.text().strip()) and bool(self._reference_roi)
        roi_fallback = roi_requested and (not roi_ready)
        roi_tip = "已勾选“框选特征点”，但未选择图像或未框选区域，已按全页识别"
        if mode == "feature" and not self.reference_image_input.text().strip():
            QMessageBox.warning(self, "警告", "请先选择参考图像")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "任务正在执行中")
            return

        options = self._build_options()
        page_index = int(self.test_page_spin.value()) - 1
        pdf_path = self.pdf_path_input.text().strip()
        base = os.path.basename(pdf_path)
        self._run_context = {
            "task": "probe_page",
            "pdf_path": pdf_path,
            "pdf_name": base,
            "reference_image_path": self.reference_image_input.text().strip(),
            "page_index": int(page_index),
        }

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._run_started_at = time.perf_counter()
        self._run_log_lines = []
        self._worker_task = "probe_page"
        self._append_log(f"开始单页测试：第 {page_index + 1} 页…")
        if roi_fallback:
            self._append_log(roi_tip)
        self._save_settings()

        self._worker = PdfScanSplitWorker(
            pdf_path=self.pdf_path_input.text().strip(),
            reference_image_path=self.reference_image_input.text().strip(),
            output_dir="",
            prefix="",
            options=options,
            task="probe_page",
            probe_page_index=page_index,
            parent=self,
        )
        self._worker.progressChanged.connect(self._on_worker_progress)
        self._worker.logAppended.connect(self._append_log)
        self._worker.finishedWithResult.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_quick_scan(self):
        if not self.pdf_path_input.text().strip():
            QMessageBox.warning(self, "警告", "请先选择PDF文件")
            return
        mode = self._get_detection_mode()
        roi_requested = bool(self.qrcode_use_roi_checkbox.isChecked()) and mode in ("qrcode", "stamp", "auto")
        roi_ready = bool(self.reference_image_input.text().strip()) and bool(self._reference_roi)
        roi_fallback = roi_requested and (not roi_ready)
        roi_tip = "已勾选“框选特征点”，但未选择图像或未框选区域，已按全页识别"
        if mode == "feature" and not self.reference_image_input.text().strip():
            QMessageBox.warning(self, "警告", "请先选择参考图像")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "任务正在执行中")
            return

        options = self._build_options()
        limit = int(self.quick_scan_pages_spin.value())
        pdf_path = self.pdf_path_input.text().strip()
        base = os.path.basename(pdf_path)
        self._run_context = {
            "task": "scan_only",
            "pdf_path": pdf_path,
            "pdf_name": base,
            "reference_image_path": self.reference_image_input.text().strip(),
            "page_limit": int(limit),
        }

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._run_started_at = time.perf_counter()
        self._run_log_lines = []
        self._worker_task = "scan_only"
        self._append_log(f"开始快速扫描前 {limit} 页（不输出文件）…")
        if roi_fallback:
            self._append_log(roi_tip)
        self._save_settings()

        self._worker = PdfScanSplitWorker(
            pdf_path=self.pdf_path_input.text().strip(),
            reference_image_path=self.reference_image_input.text().strip(),
            output_dir="",
            prefix="",
            options=options,
            task="scan_only",
            page_limit=limit,
            parent=self,
        )
        self._worker.progressChanged.connect(self._on_worker_progress)
        self._worker.logAppended.connect(self._append_log)
        self._worker.finishedWithResult.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_stop(self):
        if not self._worker or not self._worker.isRunning():
            return
        self._append_log("已请求停止…")
        self.status_banner.set_message("正在取消", "已请求停止任务，请稍候…")
        self._worker.cancel()
        self.stop_button.setEnabled(False)

    def _cleanup_worker(self):
        w = self._worker
        self._worker = None
        if w is None:
            return
        try:
            w.deleteLater()
        except Exception:
            pass

    def _on_worker_progress(self, current: int, total: int):
        self.progress_bar.setRange(0, max(1, int(total)))
        self.progress_bar.setValue(int(current))
        if self._worker_task in ("scan_split", "scan_only"):
            self.status_banner.set_message("正在处理", f"进度 {int(current)}/{int(total)}")

    def _on_worker_finished(self, result: object):
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(True)
        self.progress_bar.setVisible(False)

        if isinstance(result, dict):
            try:
                elapsed_ms = int((time.perf_counter() - self._run_started_at) * 1000) if self._run_started_at else None
                marked = bool(result.get("marked"))
                page_number = int(result.get("page_number") or 0)
                reason = str(result.get("reason") or "")
                qr = result.get("qrcode") or {}
                stamp = result.get("stamp") or {}
                feat = result.get("feature") or {}
                if qr.get("infos"):
                    sample = str(qr.get("infos")[0] or "")
                    sample = (sample[:60] + "…") if len(sample) > 60 else sample
                    self._append_log(f"单页测试：第 {page_number} 页 -> {'命中' if marked else '未命中'}，{reason}，二维码示例：{sample}")
                else:
                    self._append_log(f"单页测试：第 {page_number} 页 -> {'命中' if marked else '未命中'}，{reason}")
                if isinstance(stamp, dict) and ("present" in stamp or "area_ratio" in stamp):
                    try:
                        present = bool(stamp.get("present"))
                        ar = float(stamp.get("area_ratio") or 0.0)
                        cand = int(stamp.get("candidates") or 0)
                        self._append_log(f"印章检测：{'命中' if present else '未命中'}，候选 {cand}，面积占比 {ar:.4f}")
                    except Exception:
                        pass
                if feat:
                    self._append_log(
                        f"特征统计：匹配 {int(feat.get('good_matches') or 0)} / 内点 {int(feat.get('inliers') or 0)} / 比例 {float(feat.get('inlier_ratio') or 0.0):.2f}"
                    )
                if elapsed_ms is not None:
                    if elapsed_ms < 1000:
                        self._append_log(f"单页测试耗时：{elapsed_ms}ms")
                    else:
                        self._append_log(f"单页测试耗时：{elapsed_ms / 1000.0:.2f}s")
            except Exception:
                self._append_log("单页测试完成，但解析结果失败")
            self._run_started_at = None
            return

        if not isinstance(result, PdfScanSplitResult):
            self._append_log("任务完成，但返回结果类型异常")
            self._run_started_at = None
            return
        pdf_name = (self._run_context or {}).get("pdf_name") if self._run_context else ""
        elapsed_ms = int((time.perf_counter() - self._run_started_at) * 1000) if self._run_started_at else None

        if self._worker_task == "scan_only":
            if result.marker_pages:
                self._append_log(f"快速扫描结束：识别到标记页：{', '.join(str(p + 1) for p in result.marker_pages)}")
            else:
                self._append_log("快速扫描结束：未识别到标记页")
            if elapsed_ms is not None:
                if elapsed_ms < 1000:
                    self._append_log(f"快速扫描耗时：{elapsed_ms}ms")
                else:
                    self._append_log(f"快速扫描耗时：{elapsed_ms / 1000.0:.2f}s")
            if self.history_manager:
                try:
                    details = {
                        "pdf_path": self.pdf_path_input.text().strip(),
                        "reference_image_path": self.reference_image_input.text().strip(),
                        "page_limit": int(self.quick_scan_pages_spin.value()),
                        "marker_pages": [int(p) for p in (result.marker_pages or [])],
                        "elapsed_ms": elapsed_ms,
                        "log_tail": self._run_log_lines[-40:],
                    }
                    self.history_manager.add_record(
                        operation_type=OperationType.SCAN_SPLIT,
                        description=f"快速扫描：{pdf_name}",
                        details=details,
                        success=True,
                    )
                except Exception:
                    pass
            self._run_started_at = None
            return

        if not result.output_files:
            if result.marker_pages:
                self._append_log("任务结束：已识别标记页，但未生成输出（可能已取消）")
            else:
                self._append_log("任务结束：未识别到标记页，未生成输出")
            if elapsed_ms is not None:
                if elapsed_ms < 1000:
                    self._append_log(f"总耗时：{elapsed_ms}ms")
                else:
                    self._append_log(f"总耗时：{elapsed_ms / 1000.0:.2f}s")
            if self.history_manager:
                try:
                    details = dict(self._run_context or {})
                    details.update(
                        {
                            "success": False,
                            "total_pages": int(getattr(result, "total_pages", 0) or 0),
                            "marker_pages": [int(p) for p in (result.marker_pages or [])],
                            "output_files": [],
                            "elapsed_ms": elapsed_ms,
                            "log_tail": self._run_log_lines[-40:],
                        }
                    )
                    self.history_manager.add_record(
                        operation_type=OperationType.SCAN_SPLIT,
                        description=f"扫描拆分未生成输出：{pdf_name}",
                        details=details,
                        success=False,
                        error_message="未生成输出文件（可能已取消或未识别到标记页）",
                    )
                except Exception:
                    pass
            self._run_started_at = None
            return

        self._append_log(f"任务完成：生成 {len(result.output_files)} 个文件")
        if elapsed_ms is not None:
            if elapsed_ms < 1000:
                self._append_log(f"总耗时：{elapsed_ms}ms")
            else:
                self._append_log(f"总耗时：{elapsed_ms / 1000.0:.2f}s")
        if self.history_manager:
            try:
                details = dict(self._run_context or {})
                details.update(
                    {
                        "success": True,
                        "total_pages": int(getattr(result, "total_pages", 0) or 0),
                        "marker_pages": [int(p) for p in (result.marker_pages or [])],
                        "output_count": int(len(result.output_files)),
                        "output_files": list(result.output_files[:20]),
                        "elapsed_ms": elapsed_ms,
                        "log_tail": self._run_log_lines[-40:],
                    }
                )
                self.history_manager.add_record(
                    operation_type=OperationType.SCAN_SPLIT,
                    description=f"扫描拆分完成：{pdf_name} -> {len(result.output_files)} 个文件",
                    details=details,
                    success=True,
                )
            except Exception:
                pass
        self._run_started_at = None
        QMessageBox.information(self, "完成", f"扫描拆分完成，共生成 {len(result.output_files)} 个文件")

    def _on_worker_failed(self, message: str):
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        cancelled = str(message or "").strip() == "已取消"
        if cancelled:
            self._append_log("任务已取消")
            self.status_banner.set_message("已取消", "任务已取消。")
        else:
            self._append_log(f"任务失败：{message}")
            self.status_banner.set_message("处理失败", str(message or "任务失败"))
        pdf_name = (self._run_context or {}).get("pdf_name") if self._run_context else ""
        elapsed_ms = int((time.perf_counter() - self._run_started_at) * 1000) if self._run_started_at else None
        if self.history_manager:
            try:
                details = dict(self._run_context or {})
                details.update(
                    {
                        "success": False,
                        "cancelled": bool(cancelled),
                        "elapsed_ms": elapsed_ms,
                        "log_tail": self._run_log_lines[-40:],
                    }
                )
                desc = f"扫描拆分取消：{pdf_name}" if cancelled else f"扫描拆分失败：{pdf_name}"
                self.history_manager.add_record(
                    operation_type=OperationType.SCAN_SPLIT,
                    description=desc,
                    details=details,
                    success=False,
                    error_message=str(message or ""),
                )
            except Exception:
                pass
        self._run_started_at = None
        if cancelled:
            QMessageBox.information(self, "已取消", "任务已取消")
        else:
            QMessageBox.critical(self, "错误", message)

    def _settings(self) -> QSettings:
        return QSettings("FileToolbox", "PdfScanSplitPanel")

    def _restore_settings(self):
        self._restoring_settings = True
        try:
            s = self._settings()
            mode_value = str(s.value("detectModeValue", "") or "").strip()
            if mode_value:
                target_idx = -1
                for i in range(int(self.detect_mode_combo.count() or 0)):
                    if self._get_detection_mode_for_text(str(self.detect_mode_combo.itemText(i) or "")) == mode_value:
                        target_idx = i
                        break
                if target_idx >= 0:
                    self.detect_mode_combo.setCurrentIndex(int(target_idx))
            else:
                mode_index = s.value("detectModeIndex", None)
                if mode_index is not None:
                    try:
                        idx = int(mode_index)
                        if int(self.detect_mode_combo.count() or 0) == 4 and 0 <= idx <= 2:
                            idx = idx + 1
                        self.detect_mode_combo.setCurrentIndex(int(idx))
                    except Exception:
                        pass

            def _set_int(key: str, widget: QSpinBox, *, default: int):
                v = s.value(key, None)
                if v is None:
                    widget.setValue(int(default))
                    return
                try:
                    widget.setValue(int(v))
                except Exception:
                    widget.setValue(int(default))

            def _set_float(key: str, widget: QDoubleSpinBox, *, default: float):
                v = s.value(key, None)
                if v is None:
                    widget.setValue(float(default))
                    return
                try:
                    widget.setValue(float(v))
                except Exception:
                    widget.setValue(float(default))

            _set_int("nfeatures", self.nfeatures_spin, default=1200)
            _set_int("minMatches", self.min_matches_spin, default=25)
            _set_float("ratio", self.ratio_spin, default=0.75)
            _set_float("ransacReprojThreshold", self.ransac_spin, default=5.0)
            _set_float("minInlierRatio", self.min_inlier_ratio_spin, default=0.45)

            _set_int("quickScanPages", self.quick_scan_pages_spin, default=30)
            _set_int("testPage", self.test_page_spin, default=1)

            def _to_bool(v, default: bool = False) -> bool:
                if v is None:
                    return bool(default)
                if isinstance(v, bool):
                    return bool(v)
                try:
                    return bool(int(v))
                except Exception:
                    s2 = str(v).strip().lower()
                    if s2 in ("1", "true", "yes", "y", "on"):
                        return True
                    if s2 in ("0", "false", "no", "n", "off", ""):
                        return False
                    return bool(default)

            self.marker_as_first_page_checkbox.setChecked(_to_bool(s.value("markerAsFirst", 1), True))
            self.exclude_marker_page_checkbox.setChecked(_to_bool(s.value("excludeMarker", 0), False))
            self.enable_multithread_checkbox.setChecked(_to_bool(s.value("enableMultithread", 0), False))
            self.enable_gpu_checkbox.setChecked(_to_bool(s.value("enableGpu", 0), False))

            self.qrcode_no_decode_checkbox.setChecked(_to_bool(s.value("qrNoDecode", 0), False))
            self.qrcode_use_roi_checkbox.setChecked(_to_bool(s.value("qrUseRoi", 1), True))
            self.qrcode_skip_checkbox.setChecked(_to_bool(s.value("qrSkipEnabled", 0), False))
            _set_int("qrSkipPages", self.qrcode_skip_pages_spin, default=0)
            self.qrcode_text_input.setText(str(s.value("qrTextContains", "") or ""))

            preset_index = s.value("presetIndex", None)
            if preset_index is not None:
                try:
                    self.preset_combo.setCurrentIndex(int(preset_index))
                except Exception:
                    pass

            pdf_path = str(s.value("pdfPath", "") or "")
            if pdf_path and os.path.exists(pdf_path):
                self._pdf_path = pdf_path
                self.pdf_path_input.setText(pdf_path)
                self._sync_image_state()

            ref_path = str(s.value("refImagePath", "") or "")
            if ref_path and os.path.exists(ref_path):
                self._reference_image_path = ref_path
                self.reference_image_input.setText(ref_path)

            output_dir = str(s.value("outputDir", "") or "")
            if output_dir and os.path.exists(output_dir):
                self.output_dir_input.setText(output_dir)

            self.prefix_input.setText(str(s.value("prefix", "") or ""))

            roi = s.value("referenceRoi", None)
            if roi:
                try:
                    if isinstance(roi, str):
                        parts = [int(x) for x in roi.split(",") if x.strip()]
                        if len(parts) == 4:
                            self._reference_roi = (parts[0], parts[1], parts[2], parts[3])
                    else:
                        parts = list(roi)
                        if len(parts) == 4:
                            self._reference_roi = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
                except Exception:
                    self._reference_roi = None
            self._sync_roi_summary()
            self._on_detection_mode_changed()
        finally:
            self._restoring_settings = False

    def _save_settings(self):
        try:
            s = self._settings()
            s.setValue("detectModeIndex", int(self.detect_mode_combo.currentIndex()))
            s.setValue("detectModeValue", str(self._get_detection_mode() or "qrcode"))
            s.setValue("nfeatures", int(self.nfeatures_spin.value()))
            s.setValue("minMatches", int(self.min_matches_spin.value()))
            s.setValue("ratio", float(self.ratio_spin.value()))
            s.setValue("ransacReprojThreshold", float(self.ransac_spin.value()))
            s.setValue("minInlierRatio", float(self.min_inlier_ratio_spin.value()))
            s.setValue("presetIndex", int(self.preset_combo.currentIndex()))

            s.setValue("markerAsFirst", 1 if self.marker_as_first_page_checkbox.isChecked() else 0)
            s.setValue("excludeMarker", 1 if self.exclude_marker_page_checkbox.isChecked() else 0)
            s.setValue("enableMultithread", 1 if self.enable_multithread_checkbox.isChecked() else 0)
            s.setValue("enableGpu", 1 if self.enable_gpu_checkbox.isChecked() else 0)

            s.setValue("qrNoDecode", 1 if self.qrcode_no_decode_checkbox.isChecked() else 0)
            s.setValue("qrUseRoi", 1 if self.qrcode_use_roi_checkbox.isChecked() else 0)
            s.setValue("qrSkipEnabled", 1 if self.qrcode_skip_checkbox.isChecked() else 0)
            s.setValue("qrSkipPages", int(self.qrcode_skip_pages_spin.value()))
            s.setValue("qrTextContains", str(self.qrcode_text_input.text().strip()))

            s.setValue("quickScanPages", int(self.quick_scan_pages_spin.value()))
            s.setValue("testPage", int(self.test_page_spin.value()))

            s.setValue("pdfPath", str(self.pdf_path_input.text().strip()))
            s.setValue("refImagePath", str(self.reference_image_input.text().strip()))
            s.setValue("outputDir", str(self.output_dir_input.text().strip()))
            s.setValue("prefix", str(self.prefix_input.text().strip()))

            if self._reference_roi:
                x, y, w, h = self._reference_roi
                s.setValue("referenceRoi", f"{int(x)},{int(y)},{int(w)},{int(h)}")
            else:
                s.remove("referenceRoi")
            s.sync()
        except Exception:
            return

    def _get_detection_mode_for_text(self, text: str) -> str:
        text = str(text or "").strip()
        if text.startswith("自动"):
            return "auto"
        if text.startswith("二维码"):
            return "qrcode"
        if text.startswith("印章"):
            return "stamp"
        if text.startswith("特征"):
            return "feature"
        return "qrcode"

    def _schedule_save_settings(self):
        if self._restoring_settings:
            return
        if self._save_settings_timer is None:
            self._save_settings_timer = QTimer(self)
            self._save_settings_timer.setSingleShot(True)
            self._save_settings_timer.timeout.connect(self._save_settings)
        self._save_settings_timer.start(350)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            if not self._worker.wait(10000):
                QMessageBox.information(self, "提示", "任务仍在运行，请稍后再关闭")
                event.ignore()
                return
        self._save_settings()
        super().closeEvent(event)
