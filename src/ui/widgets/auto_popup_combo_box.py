from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QAbstractItemView


class AutoPopupComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_popup_height_ratio = 0.6
        self.view().setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.view().isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def showPopup(self):
        self._adjust_popup_height()
        super().showPopup()

    def _adjust_popup_height(self):
        view = self.view()
        count = self.count()
        if count <= 0:
            return

        row_height = view.sizeHintForRow(0)
        if row_height <= 0:
            row_height = max(24, self.fontMetrics().height() + 14)

        screen = self.screen() or QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 800
        max_popup_height = max(120, int(available_height * self._max_popup_height_ratio))

        max_rows = max(1, max_popup_height // row_height)
        visible_rows = min(count, max_rows)

        margins = view.contentsMargins()
        extra = view.frameWidth() * 2 + margins.top() + margins.bottom()
        view.setFixedHeight(visible_rows * row_height + extra)
