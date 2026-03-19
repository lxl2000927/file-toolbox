from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint, QRect, QEvent
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
    QMessageBox,
    QRubberBand,
)

from utils.style_manager import StyleManager


class RoiSelectView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setMouseTracking(True)

        self._original_pixmap: QPixmap | None = None
        self._zoom = 1.0
        self._selection: tuple[int, int, int, int] | None = None

        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._dragging = False
        self._start = QPoint()
        self._end = QPoint()

    def set_image(self, pixmap: QPixmap):
        self._original_pixmap = pixmap
        self._zoom = 1.0
        self._selection = None
        self._apply_zoom()

    def set_zoom(self, zoom: float):
        self._zoom = max(0.05, min(10.0, float(zoom)))
        self._apply_zoom()

    def zoom(self) -> float:
        return float(self._zoom)

    def selection(self) -> tuple[int, int, int, int] | None:
        return self._selection

    def clear_selection(self):
        self._selection = None
        self._rubber.hide()

    def _apply_zoom(self):
        if self._original_pixmap is None:
            self.setPixmap(QPixmap())
            return
        pix = self._original_pixmap
        scaled = pix.scaled(
            int(pix.width() * self._zoom),
            int(pix.height() * self._zoom),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        self.resize(scaled.size())
        self._sync_rubberband()

    def _sync_rubberband(self):
        if not self._selection:
            self._rubber.hide()
            return
        x, y, w, h = self._selection
        rect = QRect(
            int(x * self._zoom),
            int(y * self._zoom),
            max(1, int(w * self._zoom)),
            max(1, int(h * self._zoom)),
        )
        self._rubber.setGeometry(rect)
        self._rubber.show()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if self._original_pixmap is None:
            return
        self._dragging = True
        self._start = event.position().toPoint()
        self._end = self._start
        self._rubber.setGeometry(QRect(self._start, self._end).normalized())
        self._rubber.show()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return super().mouseMoveEvent(event)
        self._end = event.position().toPoint()
        self._rubber.setGeometry(QRect(self._start, self._end).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        if not self._dragging:
            return
        self._dragging = False
        rect = QRect(self._start, self._end).normalized()
        if rect.width() < 6 or rect.height() < 6:
            self.clear_selection()
            return
        x = int(rect.x() / self._zoom)
        y = int(rect.y() / self._zoom)
        w = int(rect.width() / self._zoom)
        h = int(rect.height() / self._zoom)
        self._selection = (max(0, x), max(0, y), max(1, w), max(1, h))
        self._sync_rubberband()


class RoiSelectDialog(QDialog):
    def __init__(self, *, image_path: str, initial_roi: tuple[int, int, int, int] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("框选区域")
        self.setModal(True)
        self.resize(880, 640)

        pix = QPixmap(image_path)
        if pix.isNull():
            raise RuntimeError("无法读取参考图像")
        self._pix = pix

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("按住鼠标左键拖动框选区域（Ctrl+滚轮缩放）")
        title.setFont(StyleManager.get_font("small"))
        title.setStyleSheet(f"color: {StyleManager.get_color('gray_700')};")
        root.addWidget(title)

        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.view = RoiSelectView()
        self.view.set_image(self._pix)
        self.scroll.setWidget(self.view)
        host_layout.addWidget(self.scroll, 1)
        root.addWidget(host, 1)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        self.zoom_out_button = QPushButton("－")
        self.zoom_out_button.setFixedWidth(36)
        self.zoom_out_button.setProperty("variant", "outline")
        self.zoom_in_button = QPushButton("＋")
        self.zoom_in_button.setFixedWidth(36)
        self.zoom_in_button.setProperty("variant", "outline")
        self.fit_button = QPushButton("适合")
        self.fit_button.setFixedWidth(54)
        self.fit_button.setProperty("variant", "outline")
        self.clear_button = QPushButton("清除")
        self.clear_button.setProperty("variant", "outline")

        self.ok_button = QPushButton("确定")
        self.ok_button.setProperty("variant", "primary")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setProperty("variant", "outline")

        bottom_layout.addWidget(self.zoom_out_button)
        bottom_layout.addWidget(self.zoom_in_button)
        bottom_layout.addWidget(self.fit_button)
        bottom_layout.addWidget(self.clear_button)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.cancel_button)
        bottom_layout.addWidget(self.ok_button)
        root.addWidget(bottom)

        self.zoom_out_button.clicked.connect(lambda: self._change_zoom(step_down=True))
        self.zoom_in_button.clicked.connect(lambda: self._change_zoom(step_down=False))
        self.fit_button.clicked.connect(self._fit)
        self.clear_button.clicked.connect(self.view.clear_selection)
        self.ok_button.clicked.connect(self._accept)
        self.cancel_button.clicked.connect(self.reject)

        self.scroll.viewport().installEventFilter(self)

        if initial_roi:
            self.view._selection = tuple(int(v) for v in initial_roi)
            self.view._sync_rubberband()

        self._fit()

    def selected_roi(self) -> tuple[int, int, int, int] | None:
        return self.view.selection()

    def _fit(self):
        pix = self._pix
        viewport = self.scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        scale = min(viewport.width() / pix.width(), viewport.height() / pix.height())
        scale = min(1.0, max(0.05, float(scale)))
        self.view.set_zoom(scale)

    def _change_zoom(self, *, step_down: bool):
        z = self.view.zoom()
        factor = 1.0 / 1.15 if step_down else 1.15
        self.view.set_zoom(z * factor)

    def _accept(self):
        if not self.view.selection():
            QMessageBox.information(self, "提示", "请先框选一个区域，或点击“清除”后取消即可")
            return
        self.accept()

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            try:
                modifiers = event.modifiers()
            except Exception:
                modifiers = Qt.KeyboardModifier.NoModifier
            if modifiers == Qt.KeyboardModifier.ControlModifier:
                delta = int(event.angleDelta().y() or 0)
                if delta != 0:
                    self._change_zoom(step_down=delta < 0)
                    return True
        return super().eventFilter(obj, event)
