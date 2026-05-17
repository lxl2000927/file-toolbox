from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSpinBox


class _StyledSpinBoxMixin:
    _arrow_area_width = 22

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setMouseTracking(True)
        self._hover_arrow = ""
        self._pressed_arrow = ""

    def _arrow_rects(self) -> tuple[QRect, QRect]:
        rect = self.rect().adjusted(1, 1, -1, -1)
        width = min(self._arrow_area_width, max(16, rect.width() // 3))
        x = rect.right() - width + 1
        half = rect.height() // 2
        up_rect = QRect(x, rect.top(), width, half)
        down_rect = QRect(x, rect.top() + half, width, rect.height() - half)
        return up_rect, down_rect

    def _arrow_at(self, pos) -> str:
        up_rect, down_rect = self._arrow_rects()
        if up_rect.contains(pos):
            return "up"
        if down_rect.contains(pos):
            return "down"
        return ""

    def mouseMoveEvent(self, event):
        hover_arrow = self._arrow_at(event.position().toPoint()) if self.isEnabled() else ""
        if hover_arrow != self._hover_arrow:
            self._hover_arrow = hover_arrow
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_arrow or self._pressed_arrow:
            self._hover_arrow = ""
            self._pressed_arrow = ""
            self.update()
        super().leaveEvent(event)

    def focusOutEvent(self, event):
        if self._hover_arrow or self._pressed_arrow:
            self._hover_arrow = ""
            self._pressed_arrow = ""
            self.update()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            arrow = self._arrow_at(event.position().toPoint())
            if arrow:
                self._pressed_arrow = arrow
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pressed_arrow:
            arrow = self._arrow_at(event.position().toPoint())
            pressed = self._pressed_arrow
            self._pressed_arrow = ""
            self._hover_arrow = arrow
            if arrow == pressed:
                if pressed == "up":
                    self.stepUp()
                else:
                    self.stepDown()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        up_rect, down_rect = self._arrow_rects()
        self._paint_arrow_button(painter, up_rect, "up")
        self._paint_arrow_button(painter, down_rect, "down")

    def _paint_arrow_button(self, painter: QPainter, rect: QRect, arrow: str):
        if rect.width() <= 0 or rect.height() <= 0:
            return
        if self._pressed_arrow == arrow:
            painter.fillRect(rect.adjusted(2, 1, -2, -1), QColor("#e9ecef"))
        elif self._hover_arrow == arrow:
            painter.fillRect(rect.adjusted(2, 1, -2, -1), QColor("#f1f3f5"))
        color = QColor("#adb5bd") if not self.isEnabled() else QColor("#6c757d")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        cx = rect.center().x()
        cy = rect.center().y()
        size = 4.5
        path = QPainterPath()
        if arrow == "up":
            path.moveTo(cx, cy - size / 2)
            path.lineTo(cx - size, cy + size / 2)
            path.lineTo(cx + size, cy + size / 2)
        else:
            path.moveTo(cx, cy + size / 2)
            path.lineTo(cx - size, cy - size / 2)
            path.lineTo(cx + size, cy - size / 2)
        path.closeSubpath()
        painter.drawPath(path)


class StyledSpinBox(_StyledSpinBoxMixin, QSpinBox):
    pass


class StyledDoubleSpinBox(_StyledSpinBoxMixin, QDoubleSpinBox):
    pass
