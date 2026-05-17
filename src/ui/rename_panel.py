from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QGroupBox, QComboBox, QLineEdit,
    QRadioButton, QButtonGroup, QTabWidget, QGridLayout,
    QMessageBox, QCheckBox, QToolButton, QStyle, QMenu, QSizePolicy, QHeaderView, QStyleOptionButton, QStyleOptionHeader
)
from PyQt6.QtCore import Qt, QEvent, QRect, QPoint, pyqtSignal, QTimer, QThreadPool
from PyQt6.QtGui import QColor, QPen
from ui.widgets import AutoPopupComboBox
from ui.widgets import StyledSpinBox
from utils.file_picker import FilePicker
from utils.style_manager import StyleManager
from core.rename_engine import RenameEngine
from utils.history_manager import HistoryManager
from utils.async_worker import Worker
import os
import locale
import re


class FileListHeaderView(QHeaderView):
    checkStateChanged = pyqtSignal(int)
    sortRequested = pyqtSignal(int)
    arrowMenuRequested = pyqtSignal(object)

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._check_state = Qt.CheckState.Unchecked
        self._label_text = "原文件名(0/0)"
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.setSectionsClickable(True)

    def setCheckState(self, state: Qt.CheckState):
        if self._check_state == state:
            return
        self._check_state = state
        self.viewport().update()

    def checkState(self) -> Qt.CheckState:
        return self._check_state

    def setLabelText(self, text: str):
        self._label_text = text or ""
        self.viewport().update()

    def setSortOrder(self, order: Qt.SortOrder):
        self._sort_order = order
        self.viewport().update()

    def minimumFirstSectionWidth(self) -> int:
        margin = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderMargin, None, self)
        cb_w = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self)
        arrow_w = 22
        label_w = self.fontMetrics().horizontalAdvance(self._label_text)
        return int(margin + cb_w + 8 + label_w + 14 + arrow_w + 12)

    def _checkbox_rect(self, rect: QRect) -> QRect:
        w = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self)
        h = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, None, self)
        margin = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderMargin, None, self)
        x = rect.left() + max(0, margin)
        y = rect.top() + (rect.height() - h) // 2
        return QRect(x, y, w, h)

    def _arrow_rect(self, rect: QRect) -> QRect:
        size = min(18, rect.height() - 6)
        x = rect.right() - size - 8
        y = rect.top() + (rect.height() - size) // 2
        return QRect(x, y, size, size)

    def _draw_sort_arrow(self, painter, rect: QRect):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)

        active = QColor(StyleManager.get_color("primary"))
        inactive = QColor(StyleManager.get_color("gray_500"))

        cx = rect.center().x()
        w = min(10, max(8, rect.width() - 6))
        half = w // 2
        gap = 2

        up_cy = rect.top() + rect.height() // 2 - (gap + 2)
        down_cy = rect.top() + rect.height() // 2 + (gap + 2)
        dy = max(3, w // 3)

        def _pen(c: QColor):
            p = QPen(c, 2)
            p.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            return p

        painter.setPen(_pen(active if self._sort_order == Qt.SortOrder.AscendingOrder else inactive))
        painter.drawLine(QPoint(cx - half, up_cy + dy), QPoint(cx, up_cy - dy))
        painter.drawLine(QPoint(cx + half, up_cy + dy), QPoint(cx, up_cy - dy))

        painter.setPen(_pen(active if self._sort_order == Qt.SortOrder.DescendingOrder else inactive))
        painter.drawLine(QPoint(cx - half, down_cy - dy), QPoint(cx, down_cy + dy))
        painter.drawLine(QPoint(cx + half, down_cy - dy), QPoint(cx, down_cy + dy))
        painter.restore()

    def paintSection(self, painter, rect, logicalIndex):
        if logicalIndex != 0:
            return super().paintSection(painter, rect, logicalIndex)

        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logicalIndex
        opt.text = ""
        self.style().drawControl(QStyle.ControlElement.CE_HeaderSection, opt, painter, self)

        cb_rect = self._checkbox_rect(rect)
        cb_opt = QStyleOptionButton()
        cb_opt.rect = cb_rect
        cb_opt.state = QStyle.StateFlag.State_Enabled
        if self._check_state == Qt.CheckState.Checked:
            cb_opt.state |= QStyle.StateFlag.State_On
        elif self._check_state == Qt.CheckState.PartiallyChecked:
            cb_opt.state |= QStyle.StateFlag.State_NoChange
        else:
            cb_opt.state |= QStyle.StateFlag.State_Off
        self.style().drawControl(QStyle.ControlElement.CE_CheckBox, cb_opt, painter, self)

        arrow_rect = self._arrow_rect(rect)
        text_rect = QRect(cb_rect.right() + 8, rect.top(), arrow_rect.left() - (cb_rect.right() + 14), rect.height())
        painter.save()
        painter.setPen(QColor(StyleManager.get_color("gray_800")))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._label_text)
        painter.restore()
        self._draw_sort_arrow(painter, arrow_rect)

    def _handle_checkbox_click(self, event) -> bool:
        try:
            pos = event.position().toPoint()
        except Exception:
            pos = event.pos()
        index = self.logicalIndexAt(pos)
        if index == 0:
            if self.count() <= 0:
                return False
            w = self.sectionSize(0)
            rect_a = QRect(self.sectionViewportPosition(0), 0, w, self.viewport().height())
            rect_b = QRect(self.sectionPosition(0), 0, w, self.height())
            if self._checkbox_rect(rect_a).contains(pos) or self._checkbox_rect(rect_b).contains(pos):
                next_state = Qt.CheckState.Unchecked if self._check_state == Qt.CheckState.Checked else Qt.CheckState.Checked
                self.setCheckState(next_state)
                self.checkStateChanged.emit(int(next_state.value))
                event.accept()
                return True
        return False

    def _handle_arrow_click(self, event) -> bool:
        try:
            pos = event.position().toPoint()
        except Exception:
            pos = event.pos()
        index = self.logicalIndexAt(pos)
        if index != 0:
            return False
        w = self.sectionSize(0)
        rect = QRect(self.sectionViewportPosition(0), 0, w, self.viewport().height())
        if self._arrow_rect(rect).contains(pos):
            try:
                gp = event.globalPosition().toPoint()
            except Exception:
                gp = self.mapToGlobal(pos)
            self.arrowMenuRequested.emit(gp)
            event.accept()
            return True
        return False

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if self._handle_arrow_click(event):
                return True
            if self._handle_checkbox_click(event):
                return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        if self._handle_arrow_click(event):
            return
        if self._handle_checkbox_click(event):
            return
        try:
            pos = event.position().toPoint()
        except Exception:
            pos = event.pos()
        if self.logicalIndexAt(pos) == 0 and event.button() == Qt.MouseButton.LeftButton:
            self.sortRequested.emit(0)
        super().mousePressEvent(event)


class FileListItem(QTreeWidgetItem):
    def __init__(self, values, file_path: str, file_size: int):
        super().__init__(values)
        self._file_path = file_path
        self._file_size = int(file_size or 0)
        self._insert_index = 0

    def __lt__(self, other):
        tree = self.treeWidget()
        if tree is None:
            return super().__lt__(other)
        col = tree.sortColumn()
        if col == 0 and isinstance(other, FileListItem):
            mode = getattr(tree, "_sort_mode", "name")
            if mode == "size":
                if self._file_size != other._file_size:
                    return self._file_size < other._file_size
                return self._insert_index < other._insert_index
            key_func = getattr(tree, "_name_sort_key_func", None)
            key_a = key_func(self.text(0), self._insert_index) if key_func else (self.text(0), self._insert_index)
            key_b = key_func(other.text(0), other._insert_index) if key_func else (other.text(0), other._insert_index)
            return key_a < key_b
        return self.text(col) < other.text(col)


class RenamePanel(QWidget):
    filesAdded = pyqtSignal(list)
    
    def __init__(self, history_manager: HistoryManager = None):
        super().__init__()
        
        self.files = []
        self.rules = []
        self.engine = RenameEngine()
        self.history_manager = history_manager or HistoryManager()
        self.engine.history_manager = self.history_manager
        self.setAcceptDrops(True)
        self._updating_preview = False
        self._bulk_check_update = False
        self._executing = False
        self._sort_mode = "name"
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._thread_pool = QThreadPool.globalInstance()
        self._active_workers: set[Worker] = set()
        self._closing = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._update_preview)
        try:
            locale.setlocale(locale.LC_COLLATE, "")
        except Exception:
            pass
        
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)
        
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([650, 550])
    
    def _create_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        files_group = QGroupBox("原文件名 / 新文件名")
        files_layout = QVBoxLayout(files_group)
        
        files_button_layout = QHBoxLayout()
        
        self.add_files_button = QPushButton("+ 添加文件")
        self.add_files_button.clicked.connect(self._on_add_files)
        
        self.clear_files_button = QPushButton("清空列表")
        self.clear_files_button.clicked.connect(self._on_clear_files)
        
        files_button_layout.addWidget(self.add_files_button)
        files_button_layout.addWidget(self.clear_files_button)
        files_button_layout.addStretch()
        
        files_layout.addLayout(files_button_layout)
        
        self.file_list = QTreeWidget()
        self.file_list.setColumnCount(2)
        self.file_list.setHeaderLabels(["原文件名", "新文件名"])
        self.file_list.setStyleSheet("QTreeWidget::item { padding: 4px 4px; }")
        self.file_list_header = FileListHeaderView(Qt.Orientation.Horizontal, self.file_list)
        self.file_list_header.checkStateChanged.connect(self._on_header_check_state_changed)
        self.file_list_header.sortRequested.connect(self._on_header_sort_requested)
        self.file_list_header.arrowMenuRequested.connect(self._on_header_arrow_menu_requested)
        self.file_list.setHeader(self.file_list_header)
        self.file_list_header.setFixedHeight(28)
        self.file_list.setRootIsDecorated(False)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setSortingEnabled(True)
        self.file_list.header().setSortIndicatorShown(False)
        self.file_list._sort_mode = self._sort_mode
        self.file_list._name_sort_key_func = self._build_name_sort_key
        self.file_list.setUniformRowHeights(True)
        self.file_list.setAcceptDrops(True)
        self.file_list.installEventFilter(self)
        if self.file_list.viewport():
            self.file_list.viewport().setAcceptDrops(True)
            self.file_list.viewport().installEventFilter(self)
        self.file_list.itemChanged.connect(self._on_file_item_changed)
        self._update_header_summary()
        files_layout.addWidget(self.file_list, 1)
        
        layout.addWidget(files_group, 1)
        
        return panel
    
    def _create_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        tab_nav = QWidget()
        tab_nav_layout = QHBoxLayout(tab_nav)
        tab_nav_layout.setContentsMargins(0, 0, 0, 0)
        tab_nav_layout.setSpacing(0)

        self.tab_button_group = QButtonGroup(self)
        self.tab_button_group.setExclusive(True)

        tab_texts = ["插入", "替换", "删除", "智能识别", "自定义"]
        self.tab_buttons = []
        for idx, text in enumerate(tab_texts):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("variant", "tab")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(32)
            self.tab_button_group.addButton(btn, idx)
            btn.clicked.connect(lambda _=False, i=idx: self.tab_widget.setCurrentIndex(i))
            tab_nav_layout.addWidget(btn, 1)
            self.tab_buttons.append(btn)

        layout.addWidget(tab_nav, 0)

        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget, 1)
        self.tab_widget.tabBar().hide()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.currentChanged.connect(self._sync_tab_buttons)
        self.tab_buttons[0].setChecked(True)

        insert_tab = QWidget()
        insert_layout = QVBoxLayout(insert_tab)
        insert_layout.setContentsMargins(0, 0, 0, 0)
        insert_layout.setSpacing(3)

        rules_group = QGroupBox("规则列表")
        rules_group.setProperty("compact", True)
        rules_layout = QVBoxLayout(rules_group)
        rules_layout.setContentsMargins(4, 3, 4, 3)
        rules_layout.setSpacing(6)

        header_style = """
            QToolButton {
                padding: 2px 2px;
                font-size: 12px;
                font-weight: 600;
                color: #212529;
                text-align: left;
            }
        """

        def _bind_toggle(header_button: QToolButton, content_widget: QWidget):
            def _on_toggled(checked: bool):
                content_widget.setVisible(checked)
                header_button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            header_button.toggled.connect(_on_toggled)
            _on_toggled(header_button.isChecked())

        insert_text_header = QToolButton()
        insert_text_header.setText("① 插入字符")
        insert_text_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        insert_text_header.setCheckable(True)
        insert_text_header.setChecked(True)
        insert_text_header.setArrowType(Qt.ArrowType.DownArrow)
        insert_text_header.setAutoRaise(True)
        insert_text_header.setStyleSheet(header_style)

        insert_text_content = QWidget()
        insert_text_content_layout = QVBoxLayout(insert_text_content)
        insert_text_content_layout.setContentsMargins(0, 0, 0, 0)
        insert_text_content_layout.setSpacing(6)

        self.insert_text_input = QLineEdit()
        self.insert_text_input.setPlaceholderText("插入字符")
        self.insert_text_input.textChanged.connect(self._on_rules_changed)
        self.insert_text_input.setProperty("compact", True)

        insert_text_info = QToolButton()
        insert_text_info.setAutoRaise(True)
        insert_text_info.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        insert_text_info.setFixedSize(22, 22)
        insert_text_info.setProperty("variant", "icon")

        insert_text_row = QWidget()
        insert_text_row_layout = QHBoxLayout(insert_text_row)
        insert_text_row_layout.setContentsMargins(0, 0, 0, 0)
        insert_text_row_layout.setSpacing(6)
        insert_text_label = QLabel("插入字符")
        insert_text_label.setProperty("compact", True)
        insert_text_label.setFixedWidth(56)
        insert_text_row_layout.addWidget(insert_text_label)
        insert_text_row_layout.addWidget(self.insert_text_input, 1)
        insert_text_row_layout.addWidget(insert_text_info, 0, Qt.AlignmentFlag.AlignVCenter)
        insert_text_content_layout.addWidget(insert_text_row)

        self.insert_position_combo = AutoPopupComboBox()
        self.insert_position_combo.addItems(["末位", "首位", "指定位置"])
        self.insert_position_combo.setCurrentText("末位")
        self.insert_position_combo.setProperty("compact", True)
        self.insert_position_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)

        self.insert_index_spin = StyledSpinBox()
        self.insert_index_spin.setRange(1, 9999)
        self.insert_index_spin.setValue(1)
        self.insert_index_spin.valueChanged.connect(self._on_rules_changed)
        self.insert_index_spin.setProperty("compact", True)
        self.insert_index_spin.setMaximumWidth(90)

        insert_position_field = QWidget()
        insert_position_field_layout = QHBoxLayout(insert_position_field)
        insert_position_field_layout.setContentsMargins(0, 0, 0, 0)
        insert_position_field_layout.setSpacing(6)
        insert_position_field_layout.addWidget(self.insert_position_combo, 1)
        insert_position_field_layout.addWidget(self.insert_index_spin, 0)

        insert_position_row = QWidget()
        insert_position_row_layout = QHBoxLayout(insert_position_row)
        insert_position_row_layout.setContentsMargins(0, 0, 0, 0)
        insert_position_row_layout.setSpacing(6)
        insert_position_label = QLabel("插入位置")
        insert_position_label.setProperty("compact", True)
        insert_position_label.setFixedWidth(56)
        insert_position_row_layout.addWidget(insert_position_label)
        insert_position_row_layout.addWidget(insert_position_field, 1)
        insert_text_content_layout.addWidget(insert_position_row)

        def _sync_insert_index_visibility(_=None):
            show_index = self.insert_position_combo.currentText() == "指定位置"
            self.insert_index_spin.setVisible(show_index)

        self.insert_position_combo.currentTextChanged.connect(_sync_insert_index_visibility)
        self.insert_position_combo.currentTextChanged.connect(self._on_rules_changed)
        _sync_insert_index_visibility()

        insert_text_container = QWidget()
        insert_text_container_layout = QVBoxLayout(insert_text_container)
        insert_text_container_layout.setContentsMargins(0, 0, 0, 0)
        insert_text_container_layout.setSpacing(2)
        insert_text_container_layout.addWidget(insert_text_header)
        insert_text_container_layout.addWidget(insert_text_content)
        rules_layout.addWidget(insert_text_container)
        _bind_toggle(insert_text_header, insert_text_content)

        insert_number_header = QToolButton()
        insert_number_header.setText("② 插入编号")
        insert_number_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        insert_number_header.setCheckable(True)
        insert_number_header.setChecked(True)
        insert_number_header.setArrowType(Qt.ArrowType.DownArrow)
        insert_number_header.setAutoRaise(True)
        insert_number_header.setStyleSheet(header_style)

        insert_number_content = QWidget()
        insert_number_layout = QGridLayout(insert_number_content)
        insert_number_layout.setContentsMargins(0, 0, 0, 0)
        insert_number_layout.setHorizontalSpacing(10)
        insert_number_layout.setVerticalSpacing(6)
        insert_number_layout.setColumnStretch(0, 0)
        insert_number_layout.setColumnStretch(1, 1)
        insert_number_layout.setColumnStretch(2, 0)
        insert_number_layout.setColumnStretch(3, 1)

        self.number_start_input = StyledSpinBox()
        self.number_start_input.setRange(1, 999999)
        self.number_start_input.setValue(1)
        self.number_start_input.valueChanged.connect(self._on_rules_changed)
        self.number_start_input.setProperty("compact", True)

        self.number_step_input = StyledSpinBox()
        self.number_step_input.setRange(1, 999999)
        self.number_step_input.setValue(1)
        self.number_step_input.valueChanged.connect(self._on_rules_changed)
        self.number_step_input.setProperty("compact", True)

        self.number_digits_input = StyledSpinBox()
        self.number_digits_input.setRange(1, 6)
        self.number_digits_input.setValue(1)
        self.number_digits_input.valueChanged.connect(self._on_rules_changed)
        self.number_digits_input.setProperty("compact", True)

        self.number_position_combo = AutoPopupComboBox()
        self.number_position_combo.addItems(["末位", "首位"])
        self.number_position_combo.setCurrentText("末位")
        self.number_position_combo.currentTextChanged.connect(self._on_rules_changed)
        self.number_position_combo.setProperty("compact", True)
        self.number_position_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)

        insert_number_label_start = QLabel("初始值")
        insert_number_label_start.setProperty("compact", True)
        insert_number_layout.addWidget(insert_number_label_start, 0, 0)
        insert_number_layout.addWidget(self.number_start_input, 0, 1)

        insert_number_label_digits = QLabel("位数")
        insert_number_label_digits.setProperty("compact", True)
        insert_number_layout.addWidget(insert_number_label_digits, 0, 2)
        insert_number_layout.addWidget(self.number_digits_input, 0, 3)

        insert_number_label_step = QLabel("递增量")
        insert_number_label_step.setProperty("compact", True)
        insert_number_layout.addWidget(insert_number_label_step, 1, 0)
        insert_number_layout.addWidget(self.number_step_input, 1, 1)

        insert_number_label_pos = QLabel("位置")
        insert_number_label_pos.setProperty("compact", True)
        insert_number_layout.addWidget(insert_number_label_pos, 1, 2)
        insert_number_layout.addWidget(self.number_position_combo, 1, 3)

        insert_number_container = QWidget()
        insert_number_container_layout = QVBoxLayout(insert_number_container)
        insert_number_container_layout.setContentsMargins(0, 0, 0, 0)
        insert_number_container_layout.setSpacing(2)
        insert_number_container_layout.addWidget(insert_number_header)
        insert_number_container_layout.addWidget(insert_number_content)
        rules_layout.addWidget(insert_number_container)
        _bind_toggle(insert_number_header, insert_number_content)

        rules_layout.addStretch(1)

        insert_layout.addWidget(rules_group, 1)
        self.tab_widget.addTab(insert_tab, "插入")

        replace_tab = QWidget()
        replace_layout = QVBoxLayout(replace_tab)
        replace_layout.setContentsMargins(0, 0, 0, 0)
        replace_layout.setSpacing(3)

        replace_rules_group = QGroupBox("规则列表")
        replace_rules_group.setProperty("compact", True)
        replace_rules_layout = QVBoxLayout(replace_rules_group)
        replace_rules_layout.setContentsMargins(4, 3, 4, 3)
        replace_rules_layout.setSpacing(6)

        replace_header = QToolButton()
        replace_header.setText("① 替换字符")
        replace_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        replace_header.setCheckable(True)
        replace_header.setChecked(True)
        replace_header.setArrowType(Qt.ArrowType.DownArrow)
        replace_header.setAutoRaise(True)
        replace_header.setStyleSheet(header_style)

        replace_content = QWidget()
        replace_content_layout = QVBoxLayout(replace_content)
        replace_content_layout.setContentsMargins(0, 0, 0, 0)
        replace_content_layout.setSpacing(6)

        replace_find_row = QWidget()
        replace_find_row_layout = QHBoxLayout(replace_find_row)
        replace_find_row_layout.setContentsMargins(0, 0, 0, 0)
        replace_find_row_layout.setSpacing(6)
        replace_find_label = QLabel("查找字符")
        replace_find_label.setProperty("compact", True)
        replace_find_label.setFixedWidth(56)
        self.replace_find_input = QLineEdit()
        self.replace_find_input.setProperty("compact", True)
        self.replace_find_input.textChanged.connect(self._on_rules_changed)
        replace_find_row_layout.addWidget(replace_find_label)
        replace_find_row_layout.addWidget(self.replace_find_input, 1)
        replace_content_layout.addWidget(replace_find_row)

        replace_value_row = QWidget()
        replace_value_row_layout = QHBoxLayout(replace_value_row)
        replace_value_row_layout.setContentsMargins(0, 0, 0, 0)
        replace_value_row_layout.setSpacing(6)
        replace_value_label = QLabel("替换为")
        replace_value_label.setProperty("compact", True)
        replace_value_label.setFixedWidth(56)
        self.replace_value_input = QLineEdit()
        self.replace_value_input.setProperty("compact", True)
        self.replace_value_input.textChanged.connect(self._on_rules_changed)

        replace_value_info = QToolButton()
        replace_value_info.setAutoRaise(True)
        replace_value_info.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        replace_value_info.setFixedSize(22, 22)
        replace_value_info.setProperty("variant", "icon")

        replace_value_row_layout.addWidget(replace_value_label)
        replace_value_row_layout.addWidget(self.replace_value_input, 1)
        replace_value_row_layout.addWidget(replace_value_info, 0, Qt.AlignmentFlag.AlignVCenter)
        replace_content_layout.addWidget(replace_value_row)

        replace_container = QWidget()
        replace_container_layout = QVBoxLayout(replace_container)
        replace_container_layout.setContentsMargins(0, 0, 0, 0)
        replace_container_layout.setSpacing(2)
        replace_container_layout.addWidget(replace_header)
        replace_container_layout.addWidget(replace_content)
        replace_rules_layout.addWidget(replace_container)
        _bind_toggle(replace_header, replace_content)

        replace_rules_layout.addStretch(1)

        replace_layout.addWidget(replace_rules_group, 1)
        self.tab_widget.addTab(replace_tab, "替换")

        delete_tab = QWidget()
        delete_layout = QVBoxLayout(delete_tab)
        delete_layout.setContentsMargins(0, 0, 0, 0)
        delete_layout.setSpacing(3)

        delete_rules_group = QGroupBox("规则列表")
        delete_rules_group.setProperty("compact", True)
        delete_rules_layout = QVBoxLayout(delete_rules_group)
        delete_rules_layout.setContentsMargins(4, 3, 4, 3)
        delete_rules_layout.setSpacing(6)

        delete_header = QToolButton()
        delete_header.setText("① 删除字符")
        delete_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        delete_header.setCheckable(True)
        delete_header.setChecked(True)
        delete_header.setArrowType(Qt.ArrowType.DownArrow)
        delete_header.setAutoRaise(True)
        delete_header.setStyleSheet(header_style)

        delete_content = QWidget()
        delete_content_layout = QGridLayout(delete_content)
        delete_content_layout.setContentsMargins(0, 0, 0, 0)
        delete_content_layout.setHorizontalSpacing(14)
        delete_content_layout.setVerticalSpacing(8)

        self.delete_letters_checkbox = QCheckBox("所有英文字母")
        self.delete_digits_checkbox = QCheckBox("所有数字")
        self.delete_symbols_checkbox = QCheckBox("所有符号")
        self.delete_chinese_checkbox = QCheckBox("所有中文字符")
        for cb in (self.delete_letters_checkbox, self.delete_digits_checkbox, self.delete_symbols_checkbox, self.delete_chinese_checkbox):
            cb.setProperty("compact", True)
            cb.stateChanged.connect(self._on_rules_changed)

        delete_content_layout.addWidget(self.delete_letters_checkbox, 0, 0)
        delete_content_layout.addWidget(self.delete_digits_checkbox, 0, 1)
        delete_content_layout.addWidget(self.delete_symbols_checkbox, 1, 0)
        delete_content_layout.addWidget(self.delete_chinese_checkbox, 1, 1)

        self.delete_custom_checkbox = QCheckBox("移除指定字符")
        self.delete_custom_checkbox.setProperty("compact", True)
        self.delete_custom_checkbox.stateChanged.connect(self._on_rules_changed)

        self.delete_custom_input = QLineEdit()
        self.delete_custom_input.setPlaceholderText("请输入要移除的字符")
        self.delete_custom_input.setProperty("compact", True)
        self.delete_custom_input.textChanged.connect(self._on_rules_changed)

        def _sync_delete_custom_enabled(_=None):
            self.delete_custom_input.setEnabled(self.delete_custom_checkbox.isChecked())

        self.delete_custom_checkbox.stateChanged.connect(_sync_delete_custom_enabled)
        _sync_delete_custom_enabled()

        delete_custom_row = QWidget()
        delete_custom_row_layout = QHBoxLayout(delete_custom_row)
        delete_custom_row_layout.setContentsMargins(0, 0, 0, 0)
        delete_custom_row_layout.setSpacing(8)
        delete_custom_row_layout.addWidget(self.delete_custom_checkbox)
        delete_custom_row_layout.addWidget(self.delete_custom_input, 1)
        delete_content_layout.addWidget(delete_custom_row, 2, 0, 1, 2)

        delete_container = QWidget()
        delete_container_layout = QVBoxLayout(delete_container)
        delete_container_layout.setContentsMargins(0, 0, 0, 0)
        delete_container_layout.setSpacing(2)
        delete_container_layout.addWidget(delete_header)
        delete_container_layout.addWidget(delete_content)
        delete_rules_layout.addWidget(delete_container)
        _bind_toggle(delete_header, delete_content)

        keep_header = QToolButton()
        keep_header.setText("② 保留字符")
        keep_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        keep_header.setCheckable(True)
        keep_header.setChecked(True)
        keep_header.setArrowType(Qt.ArrowType.DownArrow)
        keep_header.setAutoRaise(True)
        keep_header.setStyleSheet(header_style)

        keep_content = QWidget()
        keep_content_layout = QVBoxLayout(keep_content)
        keep_content_layout.setContentsMargins(0, 0, 0, 0)
        keep_content_layout.setSpacing(8)

        keep_mode_group = QButtonGroup(self)
        self.keep_range_radio = QRadioButton("保留第")
        self.keep_range_radio.setChecked(True)
        self.keep_chars_radio = QRadioButton("保留指定字符")
        keep_mode_group.addButton(self.keep_range_radio)
        keep_mode_group.addButton(self.keep_chars_radio)
        self.keep_range_radio.toggled.connect(self._on_rules_changed)
        self.keep_chars_radio.toggled.connect(self._on_rules_changed)

        keep_range_row = QWidget()
        keep_range_row_layout = QHBoxLayout(keep_range_row)
        keep_range_row_layout.setContentsMargins(0, 0, 0, 0)
        keep_range_row_layout.setSpacing(6)
        keep_range_row_layout.addWidget(self.keep_range_radio)

        self.keep_range_input = QLineEdit()
        self.keep_range_input.setPlaceholderText("示例1-5")
        self.keep_range_input.setProperty("compact", True)
        self.keep_range_input.setFixedWidth(90)
        self.keep_range_input.textChanged.connect(self._on_rules_changed)
        keep_range_row_layout.addWidget(self.keep_range_input)

        keep_after_label = QLabel("个字符后")
        keep_after_label.setProperty("compact", True)
        keep_range_row_layout.addWidget(keep_after_label)

        self.keep_direction_combo = AutoPopupComboBox()
        self.keep_direction_combo.addItems(["从右往左", "从左往右"])
        self.keep_direction_combo.setCurrentText("从右往左")
        self.keep_direction_combo.setProperty("compact", True)
        self.keep_direction_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.keep_direction_combo.currentTextChanged.connect(self._on_rules_changed)
        keep_range_row_layout.addWidget(self.keep_direction_combo)
        keep_range_row_layout.addStretch(1)

        keep_content_layout.addWidget(keep_range_row)

        keep_chars_row = QWidget()
        keep_chars_row_layout = QHBoxLayout(keep_chars_row)
        keep_chars_row_layout.setContentsMargins(0, 0, 0, 0)
        keep_chars_row_layout.setSpacing(6)
        keep_chars_row_layout.addWidget(self.keep_chars_radio)

        self.keep_chars_input = QLineEdit()
        self.keep_chars_input.setPlaceholderText("请输入需要保留的字符")
        self.keep_chars_input.setProperty("compact", True)
        self.keep_chars_input.textChanged.connect(self._on_rules_changed)
        keep_chars_row_layout.addWidget(self.keep_chars_input, 1)
        keep_content_layout.addWidget(keep_chars_row)

        def _sync_keep_inputs(_=None):
            range_enabled = self.keep_range_radio.isChecked()
            self.keep_range_input.setEnabled(range_enabled)
            self.keep_direction_combo.setEnabled(range_enabled)
            self.keep_chars_input.setEnabled(self.keep_chars_radio.isChecked())

        self.keep_range_radio.toggled.connect(_sync_keep_inputs)
        self.keep_chars_radio.toggled.connect(_sync_keep_inputs)
        _sync_keep_inputs()

        keep_container = QWidget()
        keep_container_layout = QVBoxLayout(keep_container)
        keep_container_layout.setContentsMargins(0, 0, 0, 0)
        keep_container_layout.setSpacing(2)
        keep_container_layout.addWidget(keep_header)
        keep_container_layout.addWidget(keep_content)
        delete_rules_layout.addWidget(keep_container)
        _bind_toggle(keep_header, keep_content)

        delete_rules_layout.addStretch(1)

        delete_layout.addWidget(delete_rules_group, 1)
        self.tab_widget.addTab(delete_tab, "删除")

        smart_tab = QWidget()
        smart_layout = QVBoxLayout(smart_tab)
        smart_layout.setContentsMargins(0, 0, 0, 0)
        smart_layout.setSpacing(3)

        smart_rules_group = QGroupBox("规则列表")
        smart_rules_group.setProperty("compact", True)
        smart_rules_layout = QVBoxLayout(smart_rules_group)
        smart_rules_layout.setContentsMargins(4, 3, 4, 3)
        smart_rules_layout.setSpacing(6)

        smart_header = QToolButton()
        smart_header.setText("① 智能识别")
        smart_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        smart_header.setCheckable(True)
        smart_header.setChecked(True)
        smart_header.setArrowType(Qt.ArrowType.DownArrow)
        smart_header.setAutoRaise(True)
        smart_header.setStyleSheet(header_style)

        smart_content = QWidget()
        smart_content.setStyleSheet("""
            QRadioButton { font-size: 12px; line-height: 18px; spacing: 6px; }
        """)
        smart_content_layout = QVBoxLayout(smart_content)
        smart_content_layout.setContentsMargins(0, 0, 0, 0)
        smart_content_layout.setSpacing(8)

        smart_type_group = QButtonGroup(self)
        self.smart_content_title_radio = QRadioButton("内容标题")
        self.smart_invoice_info_radio = QRadioButton("发票信息（仅发票类型PDF文件可用）")
        self.smart_content_title_radio.setChecked(True)
        smart_type_group.addButton(self.smart_content_title_radio)
        smart_type_group.addButton(self.smart_invoice_info_radio)
        self.smart_content_title_radio.toggled.connect(self._on_rules_changed)
        self.smart_invoice_info_radio.toggled.connect(self._on_rules_changed)
        smart_content_layout.addWidget(self.smart_content_title_radio)
        smart_content_layout.addWidget(self.smart_invoice_info_radio)

        self.smart_insert_position_combo = AutoPopupComboBox()
        self.smart_insert_position_combo.addItems(["覆盖原名", "首位", "末位", "自定义"])
        self.smart_insert_position_combo.setCurrentText("覆盖原名")
        self.smart_insert_position_combo.currentTextChanged.connect(self._on_rules_changed)
        self.smart_insert_position_combo.setProperty("compact", True)
        self.smart_insert_position_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)

        self.smart_insert_index_spin = StyledSpinBox()
        self.smart_insert_index_spin.setRange(1, 9999)
        self.smart_insert_index_spin.setValue(1)
        self.smart_insert_index_spin.valueChanged.connect(self._on_rules_changed)
        self.smart_insert_index_spin.setProperty("compact", True)
        self.smart_insert_index_spin.setMaximumWidth(90)

        smart_position_field = QWidget()
        smart_position_field_layout = QHBoxLayout(smart_position_field)
        smart_position_field_layout.setContentsMargins(0, 0, 0, 0)
        smart_position_field_layout.setSpacing(6)
        smart_position_field_layout.addWidget(self.smart_insert_position_combo, 1)
        smart_position_field_layout.addWidget(self.smart_insert_index_spin, 0)

        smart_position_row = QWidget()
        smart_position_row_layout = QHBoxLayout(smart_position_row)
        smart_position_row_layout.setContentsMargins(0, 0, 0, 0)
        smart_position_row_layout.setSpacing(6)
        smart_position_label = QLabel("插入位置")
        smart_position_label.setProperty("compact", True)
        smart_position_label.setFixedWidth(56)
        smart_position_row_layout.addWidget(smart_position_label)
        smart_position_row_layout.addWidget(smart_position_field, 1)
        smart_content_layout.addWidget(smart_position_row)

        def _sync_smart_index_visibility(_=None):
            self.smart_insert_index_spin.setVisible(self.smart_insert_position_combo.currentText() == "自定义")

        self.smart_insert_position_combo.currentTextChanged.connect(_sync_smart_index_visibility)
        _sync_smart_index_visibility()

        smart_container = QWidget()
        smart_container_layout = QVBoxLayout(smart_container)
        smart_container_layout.setContentsMargins(0, 0, 0, 0)
        smart_container_layout.setSpacing(2)
        smart_container_layout.addWidget(smart_header)
        smart_container_layout.addWidget(smart_content)
        smart_rules_layout.addWidget(smart_container)
        _bind_toggle(smart_header, smart_content)

        smart_rules_layout.addStretch(1)
        smart_layout.addWidget(smart_rules_group, 1)
        self.tab_widget.addTab(smart_tab, "智能识别")

        custom_tab = QWidget()
        custom_layout = QVBoxLayout(custom_tab)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(3)

        custom_rules_group = QGroupBox("规则列表")
        custom_rules_group.setProperty("compact", True)
        custom_rules_layout = QVBoxLayout(custom_rules_group)
        custom_rules_layout.setContentsMargins(4, 3, 4, 3)
        custom_rules_layout.setSpacing(6)

        uniform_name_header = QToolButton()
        uniform_name_header.setText("① 统一名称")
        uniform_name_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        uniform_name_header.setCheckable(True)
        uniform_name_header.setChecked(True)
        uniform_name_header.setArrowType(Qt.ArrowType.DownArrow)
        uniform_name_header.setAutoRaise(True)
        uniform_name_header.setStyleSheet("""
            QToolButton {
                padding: 2px 2px;
                font-size: 12px;
                font-weight: 600;
                color: #212529;
                text-align: left;
            }
        """)

        uniform_name_content = QWidget()
        uniform_name_content_layout = QHBoxLayout(uniform_name_content)
        uniform_name_content_layout.setContentsMargins(0, 0, 0, 0)
        uniform_name_content_layout.setSpacing(6)

        self.custom_uniform_name_input = QLineEdit()
        self.custom_uniform_name_input.setPlaceholderText("请输入公共文件名")
        self.custom_uniform_name_input.textChanged.connect(self._on_rules_changed)
        self.custom_uniform_name_input.setProperty("compact", True)
        uniform_name_content_layout.addWidget(self.custom_uniform_name_input, 1)

        uniform_name_info = QToolButton()
        uniform_name_info.setAutoRaise(True)
        uniform_name_info.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        uniform_name_info.setFixedSize(22, 22)
        uniform_name_info.setStyleSheet("""
            QToolButton { padding: 0px; }
        """)
        uniform_name_content_layout.addWidget(uniform_name_info, 0, Qt.AlignmentFlag.AlignVCenter)

        uniform_name_container = QWidget()
        uniform_name_container_layout = QVBoxLayout(uniform_name_container)
        uniform_name_container_layout.setContentsMargins(0, 0, 0, 0)
        uniform_name_container_layout.setSpacing(2)
        uniform_name_container_layout.addWidget(uniform_name_header)
        uniform_name_container_layout.addWidget(uniform_name_content)
        custom_rules_layout.addWidget(uniform_name_container)

        insert_number_header = QToolButton()
        insert_number_header.setText("② 插入编号")
        insert_number_header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        insert_number_header.setCheckable(True)
        insert_number_header.setChecked(True)
        insert_number_header.setArrowType(Qt.ArrowType.DownArrow)
        insert_number_header.setAutoRaise(True)
        insert_number_header.setStyleSheet("""
            QToolButton {
                padding: 2px 2px;
                font-size: 12px;
                font-weight: 600;
                color: #212529;
                text-align: left;
            }
        """)

        insert_number_content = QWidget()
        insert_number_layout = QGridLayout(insert_number_content)
        insert_number_layout.setContentsMargins(0, 0, 0, 0)
        insert_number_layout.setHorizontalSpacing(10)
        insert_number_layout.setVerticalSpacing(6)

        self.custom_number_start_input = StyledSpinBox()
        self.custom_number_start_input.setRange(1, 999999)
        self.custom_number_start_input.setValue(1)
        self.custom_number_start_input.valueChanged.connect(self._on_rules_changed)
        self.custom_number_start_input.setProperty("compact", True)

        self.custom_number_step_input = StyledSpinBox()
        self.custom_number_step_input.setRange(1, 999999)
        self.custom_number_step_input.setValue(1)
        self.custom_number_step_input.valueChanged.connect(self._on_rules_changed)
        self.custom_number_step_input.setProperty("compact", True)

        self.custom_number_digits_input = StyledSpinBox()
        self.custom_number_digits_input.setRange(1, 6)
        self.custom_number_digits_input.setValue(1)
        self.custom_number_digits_input.valueChanged.connect(self._on_rules_changed)
        self.custom_number_digits_input.setProperty("compact", True)

        self.custom_number_position_combo = AutoPopupComboBox()
        self.custom_number_position_combo.addItems(["末位", "首位"])
        self.custom_number_position_combo.currentTextChanged.connect(self._on_rules_changed)
        self.custom_number_position_combo.setProperty("compact", True)
        self.custom_number_position_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)

        custom_label_start = QLabel("初始值")
        custom_label_start.setProperty("compact", True)
        insert_number_layout.addWidget(custom_label_start, 0, 0)
        insert_number_layout.addWidget(self.custom_number_start_input, 0, 1)

        custom_label_digits = QLabel("位数")
        custom_label_digits.setProperty("compact", True)
        insert_number_layout.addWidget(custom_label_digits, 0, 2)
        insert_number_layout.addWidget(self.custom_number_digits_input, 0, 3)

        custom_label_step = QLabel("递增量")
        custom_label_step.setProperty("compact", True)
        insert_number_layout.addWidget(custom_label_step, 1, 0)
        insert_number_layout.addWidget(self.custom_number_step_input, 1, 1)

        custom_label_pos = QLabel("位置")
        custom_label_pos.setProperty("compact", True)
        insert_number_layout.addWidget(custom_label_pos, 1, 2)
        insert_number_layout.addWidget(self.custom_number_position_combo, 1, 3)

        insert_number_container = QWidget()
        insert_number_container_layout = QVBoxLayout(insert_number_container)
        insert_number_container_layout.setContentsMargins(0, 0, 0, 0)
        insert_number_container_layout.setSpacing(2)
        insert_number_container_layout.addWidget(insert_number_header)
        insert_number_container_layout.addWidget(insert_number_content)
        custom_rules_layout.addWidget(insert_number_container)

        _bind_toggle(uniform_name_header, uniform_name_content)
        _bind_toggle(insert_number_header, insert_number_content)

        custom_rules_layout.addStretch(1)

        custom_layout.addWidget(custom_rules_group, 1)
        self.tab_widget.addTab(custom_tab, "自定义")
        
        options_group = QGroupBox("选项与操作")
        options_group.setProperty("compact", True)
        options_layout = QHBoxLayout(options_group)
        options_layout.setContentsMargins(8, 8, 8, 8)
        options_layout.setSpacing(12)
        
        save_method_group = QButtonGroup(self)
        save_method_widget = QWidget()
        save_method_layout = QHBoxLayout(save_method_widget)
        save_method_layout.setContentsMargins(0, 0, 0, 0)
        save_method_layout.setSpacing(8)

        self.overwrite_radio = QRadioButton("覆盖原文件")
        self.copy_radio = QRadioButton("另存为副本")
        self.copy_radio.setChecked(True)
        
        save_method_group.addButton(self.overwrite_radio)
        save_method_group.addButton(self.copy_radio)
        
        save_method_layout.addWidget(self.overwrite_radio)
        save_method_layout.addWidget(self.copy_radio)
        
        options_layout.addWidget(save_method_widget, 1)
        
        self.preview_checkbox = QCheckBox("实时预览")
        self.preview_checkbox.setChecked(True)
        self.preview_checkbox.stateChanged.connect(self._on_preview_changed)
        options_layout.addWidget(self.preview_checkbox)
        self.tab_widget.currentChanged.connect(self._on_rules_changed)
        
        self.execute_button = QPushButton("开始重命名")
        self.execute_button.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                font-size: 14px;
                line-height: 21px;
                font-weight: bold;
                padding: 12px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:disabled {
                background-color: #6c757d;
            }
        """)
        self.execute_button.clicked.connect(self._on_execute)
        
        self.undo_button = QPushButton("撤销上次操作")
        self.undo_button.setEnabled(False)
        self.undo_button.setFixedHeight(self.execute_button.sizeHint().height())

        options_layout.addWidget(self.execute_button)
        options_layout.addWidget(self.undo_button)
        
        layout.addWidget(options_group)
        
        layout.addStretch()
        
        return panel

    def _sync_tab_buttons(self, index: int):
        if not hasattr(self, "tab_buttons"):
            return
        if 0 <= index < len(self.tab_buttons):
            self.tab_buttons[index].setChecked(True)
    
    def _connect_signals(self):
        self.filesAdded.connect(self._on_files_added)
        self.undo_button.clicked.connect(self._on_undo)
    
    def _on_add_files(self):
        files = FilePicker.get_any_files(self)
        self._add_files(files)
    
    def _on_clear_files(self):
        self.files.clear()
        self.file_list.clear()
        self._update_header_summary()
        self._clear_preview()
    
    def _on_preview_changed(self, state):
        if state == Qt.CheckState.Checked.value:
            self._schedule_preview_update()
        else:
            self._clear_preview()
    
    def _on_execute(self):
        files = self._get_checked_files()
        if not files:
            QMessageBox.warning(self, "警告", "请先添加文件并勾选要重命名的项目")
            return

        rules = self._get_rules_config()
        if not rules:
            QMessageBox.warning(self, "警告", "请至少配置一个重命名规则")
            return
        
        save_method = "overwrite" if self.overwrite_radio.isChecked() else "copy"
        
        self.engine.set_rules(rules)

        self._executing = True
        self.execute_button.setEnabled(False)
        self.undo_button.setEnabled(False)

        worker = Worker(self.engine.execute_rename, files, save_method)
        self._active_workers.add(worker)

        def _on_finished(result):
            self._executing = False
            self.execute_button.setEnabled(True)
            if self._closing:
                self._active_workers.discard(worker)
                return
            if not isinstance(result, dict):
                QMessageBox.critical(self, "错误", "重命名任务返回结果异常")
            elif result.get("failed", 0) == 0:
                QMessageBox.information(self, "成功", f"成功处理 {result.get('successful', 0)} 个文件")
            else:
                error_msg = f"成功 {result.get('successful', 0)} 个，失败 {result.get('failed', 0)} 个\n"
                errs = result.get("errors") or []
                if errs:
                    error_msg += "\n错误信息:\n" + "\n".join(errs[:3])
                QMessageBox.warning(self, "部分失败", error_msg)
            if isinstance(result, dict) and result.get("successful", 0) > 0:
                self.undo_button.setEnabled(True)
            self._update_preview()
            self._active_workers.discard(worker)

        def _on_error(err):
            self._executing = False
            self.execute_button.setEnabled(True)
            if self._closing:
                self._active_workers.discard(worker)
                return
            message = getattr(err, "message", None) or str(err)
            exc_type = getattr(err, "exc_type", None)
            if exc_type:
                message = f"{exc_type}: {message}"
            QMessageBox.critical(self, "错误", f"执行重命名时发生错误:\n{message}")
            self._active_workers.discard(worker)

        worker.signals.finished.connect(_on_finished)
        worker.signals.error.connect(_on_error)
        self._thread_pool.start(worker)
    
    def _on_undo(self):
        if not self.engine.operation_records:
            QMessageBox.information(self, "提示", "没有可撤销的操作")
            return
        
        reply = QMessageBox.question(self, "确认撤销", 
                                    "确定要撤销上次的重命名操作吗？",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            result = self.engine.undo_last_operation()
            
            if result["failed"] == 0:
                QMessageBox.information(self, "成功", 
                                       f"成功撤销 {result['successful']} 个文件")
                self.undo_button.setEnabled(False)
                self._update_preview()
            else:
                error_msg = f"成功撤销 {result['successful']} 个，失败 {result['failed']} 个\n"
                if result["errors"]:
                    error_msg += "\n错误信息:\n" + "\n".join(result["errors"][:3])
                QMessageBox.warning(self, "部分失败", error_msg)
    
    def _on_files_added(self, files):
        self._bulk_check_update = True
        for file_path in files:
            file_name = os.path.basename(file_path)
            try:
                size = os.path.getsize(file_path)
            except Exception:
                size = 0
            item = FileListItem([file_name, ""], file_path, size)
            item._insert_index = self.file_list.topLevelItemCount()
            item.setData(0, Qt.ItemDataRole.UserRole, file_path)
            item.setCheckState(0, Qt.CheckState.Checked)
            self.file_list.addTopLevelItem(item)
        self._bulk_check_update = False
        self._update_header_summary()
        self._schedule_preview_update()
    
    def _update_preview(self):
        if self._executing:
            return
        if not hasattr(self, "preview_checkbox"):
            return
        if not self.preview_checkbox.isChecked():
            self._clear_preview()
            return
        
        if self.file_list.topLevelItemCount() == 0:
            return
        
        rules = self._get_rules_config()
        if not rules:
            self._clear_preview()
            return
        self.engine.set_rules(rules)

        files = self._get_checked_files()
        if not files:
            self._clear_preview()
            return

        new_name_map = {}
        try:
            for i, file_path in enumerate(files):
                original_name = os.path.basename(file_path)
                new_name = self.engine.generate_new_filename(original_name, i, file_path)
                new_name_map[file_path] = new_name
        except Exception:
            self._clear_preview()
            return

        self._updating_preview = True
        try:
            for i in range(self.file_list.topLevelItemCount()):
                item = self.file_list.topLevelItem(i)
                file_path = item.data(0, Qt.ItemDataRole.UserRole)
                if not file_path:
                    item.setText(1, "")
                    continue
                new_name = new_name_map.get(file_path, "")
                if new_name:
                    item.setText(1, new_name)
                    if new_name != item.text(0):
                        item.setForeground(1, QColor("#28a745"))
                    else:
                        item.setForeground(1, QColor("#6c757d"))
                else:
                    item.setText(1, "")
                    item.setForeground(1, QColor("#6c757d"))
        finally:
            self._updating_preview = False

    def _get_rules_config(self):
        current_tab_text = self.tab_widget.tabText(self.tab_widget.currentIndex())

        if current_tab_text == "智能识别":
            mode = "content_title" if self.smart_content_title_radio.isChecked() else "invoice_info"
            rules = [{
                "type": "smart_recognize",
                "mode": mode,
                "position": self.smart_insert_position_combo.currentText(),
                "index": self.smart_insert_index_spin.value(),
            }]
            return rules

        if current_tab_text == "替换":
            find_text = self.replace_find_input.text()
            replace_text = self.replace_value_input.text()
            if not find_text:
                return []
            return [{
                "type": "replace_text",
                "find": find_text,
                "replace": replace_text,
                "case_sensitive": False,
            }]

        if current_tab_text == "删除":
            rules = []

            targets = []
            if self.delete_letters_checkbox.isChecked():
                targets.append("letters")
            if self.delete_digits_checkbox.isChecked():
                targets.append("digits")
            if self.delete_symbols_checkbox.isChecked():
                targets.append("symbols")
            if self.delete_chinese_checkbox.isChecked():
                targets.append("chinese")

            custom_chars = self.delete_custom_input.text() if self.delete_custom_checkbox.isChecked() else ""
            custom_chars = (custom_chars or "").strip()

            if targets or custom_chars:
                rules.append({
                    "type": "delete_chars",
                    "delete_type": "delete_patterns",
                    "targets": targets,
                    "custom_chars": custom_chars,
                })

            if self.keep_range_radio.isChecked():
                range_text = self.keep_range_input.text().strip()
                if range_text:
                    rules.append({
                        "type": "keep_chars",
                        "mode": "range",
                        "range": range_text,
                        "direction": self.keep_direction_combo.currentText(),
                    })
            else:
                chars = self.keep_chars_input.text().strip()
                if chars:
                    rules.append({
                        "type": "keep_chars",
                        "mode": "specified",
                        "chars": chars,
                    })

            return rules

        if current_tab_text == "自定义":
            rules = []
            base_name = self.custom_uniform_name_input.text().strip()
            if base_name:
                rules.append({
                    "type": "uniform_name",
                    "base_name": base_name,
                })

            number_position_map = {
                "首位": "前缀",
                "末位": "后缀",
            }
            rules.append({
                "type": "insert_number",
                "position": number_position_map.get(self.custom_number_position_combo.currentText(), self.custom_number_position_combo.currentText()),
                "prefix": "",
                "start": self.custom_number_start_input.value(),
                "step": self.custom_number_step_input.value(),
                "digits": self.custom_number_digits_input.value()
            })
            return rules

        rules = []

        text = self.insert_text_input.text().strip()
        if text:
            text_position_map = {
                "首位": "前缀",
                "末位": "后缀",
                "指定位置": "指定位置",
            }
            rules.append({
                "type": "insert_text",
                "text": text,
                "position": text_position_map.get(self.insert_position_combo.currentText(), self.insert_position_combo.currentText()),
                "index": self.insert_index_spin.value()
            })

        number_position_map = {
            "首位": "前缀",
            "末位": "后缀",
        }
        rules.append({
            "type": "insert_number",
            "position": number_position_map.get(self.number_position_combo.currentText(), self.number_position_combo.currentText()),
            "prefix": "",
            "start": self.number_start_input.value(),
            "step": self.number_step_input.value(),
            "digits": self.number_digits_input.value()
        })

        return rules

    def _get_checked_files(self):
        files = []
        for i in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                file_path = item.data(0, Qt.ItemDataRole.UserRole)
                if file_path:
                    files.append(file_path)
        return files

    def _on_rules_changed(self, *_args):
        self._schedule_preview_update()

    def _on_file_item_changed(self, _item, column):
        if column == 0:
            if self._bulk_check_update:
                return
            self._update_header_summary()
            if self._updating_preview:
                return
            self._schedule_preview_update()
            return
        if self._updating_preview:
            return
        self._schedule_preview_update()

    def _on_header_check_state_changed(self, state_value: int):
        try:
            state = Qt.CheckState(state_value)
        except Exception:
            return
        self._bulk_check_update = True
        try:
            for i in range(self.file_list.topLevelItemCount()):
                item = self.file_list.topLevelItem(i)
                item.setCheckState(0, state)
        finally:
            self._bulk_check_update = False
        self._update_header_summary()
        self._schedule_preview_update()

    def _schedule_preview_update(self):
        if self._executing:
            return
        if not hasattr(self, "preview_checkbox") or not self.preview_checkbox.isChecked():
            return
        self._preview_timer.start()

    def _update_header_summary(self):
        if not hasattr(self, "file_list"):
            return
        total = self.file_list.topLevelItemCount()
        checked = 0
        for i in range(total):
            item = self.file_list.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                checked += 1
        if total == 0:
            state = Qt.CheckState.Unchecked
        elif checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == total:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        if hasattr(self, "file_list_header"):
            self.file_list_header.setCheckState(state)
            self.file_list_header.setLabelText(f"原文件名({checked}/{total})")
            self.file_list_header.setSortOrder(self._sort_order)
            self._ensure_file_list_header_layout()
        header_item = self.file_list.headerItem()
        if header_item is not None:
            header_item.setText(0, "")
            header_item.setText(1, "新文件名")

    def _ensure_file_list_header_layout(self):
        if not hasattr(self, "file_list") or not hasattr(self, "file_list_header"):
            return
        header = self.file_list.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        min_w = int(self.file_list_header.minimumFirstSectionWidth())
        header.setMinimumSectionSize(min_w)
        if header.sectionSize(0) < min_w:
            header.resizeSection(0, min_w)
        left_min = min_w + 220
        left_panel = self.file_list.parentWidget()
        if left_panel is not None and left_panel.minimumWidth() < left_min:
            left_panel.setMinimumWidth(left_min)

    def _build_name_sort_key(self, name: str, insert_index: int):
        text = os.path.splitext(name)[0]
        parts = re.findall(r"\d+|[^\d]+", text)
        key = []
        for p in parts:
            if p.isdigit():
                key.append((0, int(p)))
            else:
                try:
                    key.append((1, locale.strxfrm(p.lower())))
                except Exception:
                    key.append((1, p.lower()))
        key.append((2, insert_index))
        return tuple(key)

    def _apply_sort(self):
        self.file_list._sort_mode = self._sort_mode
        self.file_list.sortItems(0, self._sort_order)
        self._update_header_summary()

    def _on_header_sort_requested(self, _column: int):
        if self._sort_mode != "name":
            self._sort_mode = "name"
            self._sort_order = Qt.SortOrder.AscendingOrder
        else:
            self._sort_order = (
                Qt.SortOrder.DescendingOrder
                if self._sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        self._apply_sort()

    def _on_header_arrow_menu_requested(self, global_pos):
        menu = QMenu(self)
        name_asc = menu.addAction("按文件名升序")
        name_desc = menu.addAction("按文件名降序")
        menu.addSeparator()
        size_asc = menu.addAction("按文件大小升序")
        size_desc = menu.addAction("按文件大小降序")

        def _select(mode: str, order: Qt.SortOrder):
            self._sort_mode = mode
            self._sort_order = order
            self._apply_sort()

        action = menu.exec(global_pos)
        if action == name_asc:
            _select("name", Qt.SortOrder.AscendingOrder)
        elif action == name_desc:
            _select("name", Qt.SortOrder.DescendingOrder)
        elif action == size_asc:
            _select("size", Qt.SortOrder.AscendingOrder)
        elif action == size_desc:
            _select("size", Qt.SortOrder.DescendingOrder)

    def _clear_preview(self):
        if not hasattr(self, "file_list") or self.file_list.topLevelItemCount() == 0:
            return
        self._updating_preview = True
        try:
            for i in range(self.file_list.topLevelItemCount()):
                item = self.file_list.topLevelItem(i)
                item.setText(1, "")
                item.setForeground(1, QColor("#6c757d"))
        finally:
            self._updating_preview = False
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event):
        self._add_files(self._extract_paths_from_drop_event(event))
        event.acceptProposedAction()

    def prepare_close(self) -> bool:
        self._closing = True
        self._preview_timer.stop()
        self._executing = False
        return True

    def closeEvent(self, event):
        self.prepare_close()
        super().closeEvent(event)

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
            if p and os.path.isfile(p):
                paths.append(p)
        return paths

    def _add_files(self, files):
        if not files:
            return
        existing = set(self.files)
        new_files = []
        for f in files:
            if f and os.path.isfile(f) and f not in existing:
                new_files.append(f)
                existing.add(f)
        if not new_files:
            return
        self.files.extend(new_files)
        self.filesAdded.emit(new_files)
    
