from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QFrame, QLabel, QStyle
)
from PyQt6.QtCore import Qt, QSettings, QSize
from PyQt6.QtGui import QGuiApplication

import os

from utils.style_manager import StyleManager
from utils.history_manager import HistoryManager


class MainWindow(QMainWindow):
    WINDOW_GEOMETRY_VERSION = 4

    def __init__(self):
        super().__init__()

        base_dir = os.getenv("APPDATA") or os.path.expanduser("~")
        history_path = os.path.join(base_dir, "FileToolbox", "history.json")
        self.history_manager = HistoryManager(storage_path=history_path)
        self._panels: dict[str, QWidget] = {}
        self.setAcceptDrops(True)
        
        self._setup_ui()
        self.setMinimumSize(1180, 760)
        self._connect_signals()
        self._restore_window_state()
        self._switch_panel("rename")

    def _log_internal_error(self, where: str, exc: BaseException):
        if not self.history_manager:
            return
        try:
            from utils.history_manager import OperationType

            self.history_manager.add_record(
                operation_type=OperationType.INTERNAL,
                description=f"内部错误：{where}",
                details={
                    "type": type(exc).__name__,
                    "error": str(exc),
                },
                success=False,
                error_message=str(exc),
            )
        except Exception:
            return
    
    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        left_nav = self._create_left_navigation()
        main_layout.addWidget(left_nav, 1)
        
        self.content_area = self._create_content_area()
        main_layout.addWidget(self.content_area, 4)
    
    def _create_left_navigation(self):
        nav_frame = QFrame()
        nav_frame.setObjectName("leftNavigation")
        nav_frame.setFixedWidth(96)
        nav_frame.setStyleSheet(f"""
            QFrame#leftNavigation {{
                background-color: {StyleManager.COLORS["gray_100"]};
                border-right: 1px solid {StyleManager.COLORS["border"]};
            }}
        """)
        
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(6, 12, 6, 12)
        nav_layout.setSpacing(10)
        
        title_label = QLabel("工具箱")
        title_label.setFont(StyleManager.get_font("caption"))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet(f"color: {StyleManager.COLORS['gray_700']}; margin-bottom: 12px; padding-bottom: 6px; border-bottom: 1px solid {StyleManager.COLORS['border']};")
        nav_layout.addWidget(title_label)
        
        nav_layout.addStretch(1)
        
        self.scan_split_button = self._create_nav_button("扫描拆分", "scan_split")
        self.pdf_split_button = self._create_nav_button("普通拆分", "pdf_split")
        self.rename_button = self._create_nav_button("重命名", "rename")
        self.about_button = self._create_nav_button("设置", "settings")
        
        nav_layout.addWidget(self.scan_split_button)
        nav_layout.addWidget(self.pdf_split_button)
        nav_layout.addWidget(self.rename_button)
        
        nav_layout.addStretch(1)
        nav_layout.addWidget(self.about_button)
        
        return nav_frame
    
    def _create_nav_button(self, text, button_type):
        button = QPushButton(text)
        button.setObjectName(f"navButton_{button_type}")
        button.setFixedHeight(40)
        button.setMinimumWidth(0)
        button.setProperty("variant", "nav")
        button.setCheckable(True)
        button.setIconSize(QSize(18, 18))

        icon_map = {
            "scan_split": QStyle.StandardPixmap.SP_BrowserReload,
            "pdf_split": QStyle.StandardPixmap.SP_FileDialogContentsView,
            "rename": QStyle.StandardPixmap.SP_FileDialogDetailedView,
            "settings": QStyle.StandardPixmap.SP_FileDialogInfoView,
        }
        sp = icon_map.get(button_type)
        if sp is not None:
            button.setIcon(self.style().standardIcon(sp))
        
        if button_type == "rename":
            button.setChecked(True)
        
        return button
    
    def _create_content_area(self):
        content_frame = QFrame()
        content_frame.setObjectName("contentArea")
        content_frame.setStyleSheet("""
            QFrame#contentArea {
                background-color: white;
            }
        """)
        
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(0)
        
        self.stacked_widget = QStackedWidget()
        content_layout.addWidget(self.stacked_widget)

        return content_frame
    
    def _ensure_panel(self, key: str) -> QWidget:
        key = str(key or "").strip()
        if key in self._panels:
            return self._panels[key]

        if key == "rename":
            from ui.rename_panel import RenamePanel

            panel = RenamePanel(self.history_manager)
        elif key == "scan_split":
            from ui.pdf_scan_split_panel import PdfScanSplitPanel

            panel = PdfScanSplitPanel(self.history_manager)
        elif key == "pdf_split":
            from ui.pdf_split_panel import PdfSplitPanel

            panel = PdfSplitPanel(self.history_manager)
        elif key == "settings":
            from ui.about_panel import AboutPanel

            panel = AboutPanel(self.history_manager)
        else:
            panel = QWidget()

        self._panels[key] = panel
        self.stacked_widget.addWidget(panel)
        return panel
    
    def _connect_signals(self):
        self.rename_button.clicked.connect(lambda: self._switch_panel("rename"))
        self.scan_split_button.clicked.connect(lambda: self._switch_panel("scan_split"))
        self.pdf_split_button.clicked.connect(lambda: self._switch_panel("pdf_split"))
        self.about_button.clicked.connect(lambda: self._switch_panel("settings"))
    
    def _switch_panel(self, key: str):
        key = str(key or "").strip()
        panel = self._ensure_panel(key)
        self.stacked_widget.setCurrentWidget(panel)

        self.rename_button.setChecked(key == "rename")
        self.scan_split_button.setChecked(key == "scan_split")
        self.pdf_split_button.setChecked(key == "pdf_split")
        self.about_button.setChecked(key == "settings")

        if key == "settings":
            try:
                about_panel = self._ensure_panel("settings")
                if hasattr(about_panel, "_refresh_logs"):
                    about_panel._refresh_logs()
            except Exception as e:
                self._log_internal_error("刷新日志", e)

        if key == "scan_split":
            try:
                scan_panel = self._ensure_panel("scan_split")
                pdf_split_panel = self._panels.get("pdf_split")
                if (
                    pdf_split_panel
                    and getattr(pdf_split_panel, "pdf_files", None)
                    and pdf_split_panel.pdf_files
                    and hasattr(scan_panel, "pdf_path_input")
                    and not scan_panel.pdf_path_input.text().strip()
                ):
                    scan_panel.set_pdf_path(pdf_split_panel.pdf_files[0])
            except Exception as e:
                self._log_internal_error("面板联动", e)
    
    def _restore_window_state(self):
        try:
            settings = QSettings("FileToolbox", "MainWindow")
            stored_version = settings.value("geometryVersion", 0)
            try:
                stored_version = int(stored_version)
            except Exception:
                stored_version = 0
            if stored_version != self.WINDOW_GEOMETRY_VERSION:
                settings.remove("geometry")
                settings.remove("windowState")
                settings.setValue("geometryVersion", self.WINDOW_GEOMETRY_VERSION)
                settings.sync()
                self._apply_default_geometry()
                return
            geometry = settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
            else:
                self._apply_default_geometry()
            window_state = settings.value("windowState")
            if window_state:
                self.restoreState(window_state)
            if not self._is_geometry_visible():
                self._apply_default_geometry()
        except Exception as e:
            self._log_internal_error("恢复窗口状态", e)
            self._apply_default_geometry()
    
    def _save_window_state(self):
        try:
            settings = QSettings("FileToolbox", "MainWindow")
            settings.setValue("geometry", self.saveGeometry())
            settings.setValue("windowState", self.saveState())
            settings.setValue("geometryVersion", self.WINDOW_GEOMETRY_VERSION)
            settings.sync()
        except Exception as e:
            self._log_internal_error("保存窗口状态", e)
    
       
    
    def center_window(self):
        screen = self.screen() or (self.windowHandle().screen() if self.windowHandle() else None) or QGuiApplication.primaryScreen()
        if not screen:
            return
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(screen.availableGeometry().center())
        self.move(frame_geometry.topLeft())
    
    def _apply_default_geometry(self):
        screen = self.screen() or (self.windowHandle().screen() if self.windowHandle() else None) or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            target_width = int(available.width() * 0.72)
            target_height = int(available.height() * 0.76)
            width = min(max(target_width, 1180), available.width())
            height = min(max(target_height, 760), available.height())
            self.resize(width, height)
        else:
            self.resize(1180, 760)
        self.center_window()
    
    def _is_geometry_visible(self):
        rect = self.frameGeometry()
        if rect.isNull():
            return False
        app = QGuiApplication.instance()
        if not app:
            return True
        for screen in app.screens():
            if screen.availableGeometry().intersects(rect):
                return True
        return False
    
    def reset_window_state(self):
        try:
            settings = QSettings("FileToolbox", "MainWindow")
            settings.remove("geometry")
            settings.remove("windowState")
            settings.sync()
        except Exception as e:
            self._log_internal_error("重置窗口状态", e)
        self.showNormal()
        self._apply_default_geometry()
    
    def closeEvent(self, event):
        self._save_window_state()
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        current = self.stacked_widget.currentWidget() if hasattr(self, "stacked_widget") else None
        if current and hasattr(current, "dropEvent"):
            current.dropEvent(event)
