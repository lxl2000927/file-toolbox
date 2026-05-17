from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QComboBox, QAbstractItemView


class AutoPopupComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_popup_height_ratio = 0.6
        self._arrow_area_width = 26
        self._hover_arrow = False
        self._popup_open = False
        self.setMouseTracking(True)
        self.setStyleSheet("QComboBox::drop-down { width: 0px; border: 0px; } QComboBox::down-arrow { image: none; width: 0px; height: 0px; }")
        self.view().setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def mouseMoveEvent(self, event):
        hover_arrow = self._arrow_rect().contains(event.position().toPoint())
        if hover_arrow != self._hover_arrow:
            self._hover_arrow = hover_arrow
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_arrow:
            self._hover_arrow = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._arrow_rect()
        if self._hover_arrow or self._popup_open:
            painter.fillRect(rect.adjusted(2, 2, -2, -2), QColor("#f1f3f5"))
        color = QColor("#adb5bd") if not self.isEnabled() else QColor("#6c757d")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        cx = rect.center().x()
        cy = rect.center().y()
        size = 5.0
        path = QPainterPath()
        path.moveTo(cx, cy + size / 2)
        path.lineTo(cx - size, cy - size / 2)
        path.lineTo(cx + size, cy - size / 2)
        path.closeSubpath()
        painter.drawPath(path)

    def showPopup(self):
        self._adjust_popup_height()
        self._popup_open = True
        self.update()
        super().showPopup()
        QTimer.singleShot(0, self._move_popup_below)

    def hidePopup(self):
        self._popup_open = False
        self.update()
        super().hidePopup()

    def _arrow_rect(self):
        rect = self.rect().adjusted(1, 1, -1, -1)
        width = min(self._arrow_area_width, max(18, rect.width() // 3))
        return rect.adjusted(rect.width() - width, 0, 0, 0)

    def _available_popup_height(self, pos_y: int, screen_rect) -> int:
        if screen_rect is None:
            return 360
        max_height = max(120, int(screen_rect.height() * self._max_popup_height_ratio))
        available_below = max(0, screen_rect.bottom() - pos_y + 1)
        available_above = max(0, pos_y - self.height() - screen_rect.top())
        return max(1, min(max_height, max(available_below, available_above)))

    def _adjust_popup_height(self):
        view = self.view()
        count = self.count()
        if count <= 0:
            return

        row_height = view.sizeHintForRow(0)
        if row_height <= 0:
            row_height = max(24, self.fontMetrics().height() + 14)

        screen = self.screen() or QApplication.primaryScreen()
        screen_rect = screen.availableGeometry() if screen else None
        below_top = self.mapToGlobal(QPoint(0, self.height())).y()
        available_height = self._available_popup_height(below_top, screen_rect)

        max_rows = max(1, available_height // row_height)
        visible_rows = min(count, max_rows)

        margins = view.contentsMargins()
        extra = view.frameWidth() * 2 + margins.top() + margins.bottom()
        view.setFixedHeight(max(row_height, visible_rows * row_height + extra))
        view.setMinimumWidth(self.width())

    def _move_popup_below(self):
        view = self.view()
        popup = view.window()
        if popup is None or not popup.isVisible():
            return
        pos = self.mapToGlobal(QPoint(0, self.height()))
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            screen_rect = screen.availableGeometry()
            available_below = max(0, screen_rect.bottom() - pos.y() + 1)
            if available_below < popup.height():
                above_y = self.mapToGlobal(QPoint(0, 0)).y() - popup.height()
                if above_y >= screen_rect.top() and available_below < popup.height():
                    pos.setY(above_y)
                else:
                    height = max(1, min(popup.height(), self._available_popup_height(pos.y(), screen_rect)))
                    popup.resize(max(popup.width(), self.width()), height)
                    pos.setY(min(pos.y(), screen_rect.bottom() - height + 1))
            if pos.x() + popup.width() > screen_rect.right() + 1:
                pos.setX(max(screen_rect.left(), screen_rect.right() - popup.width() + 1))
            pos.setY(max(screen_rect.top(), pos.y()))
        popup.move(pos)
