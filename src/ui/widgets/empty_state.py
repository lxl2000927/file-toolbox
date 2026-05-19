from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QStyle


class EmptyStateIcon(QWidget):
    def __init__(self, icon: QStyle.StandardPixmap | None = None, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._pixmap = QPixmap()
        if icon is not None:
            self._pixmap = self.style().standardIcon(icon).pixmap(28, 28)
        self.setFixedSize(56, 56)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(QRectF(0.5, 0.5, 55.0, 55.0))
        painter.fillPath(path, QColor("#f0f0f0"))
        if not self._pixmap.isNull():
            x = int((self.width() - self._pixmap.width()) / 2)
            y = int((self.height() - self._pixmap.height()) / 2)
            if self._icon == QStyle.StandardPixmap.SP_FileDialogStart:
                x -= 2
                y -= 1
            painter.drawPixmap(x, y, self._pixmap)


class EmptyStateWidget(QWidget):
    def __init__(
        self,
        *,
        title: str,
        subtitle: str = "",
        action_text: str = "",
        action_callback=None,
        icon: QStyle.StandardPixmap | None = None,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_widget = EmptyStateIcon(icon)
        layout.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(str(title or ""))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setProperty("variant", "statusBannerTitle")
        layout.addWidget(title_label)

        subtitle_label = QLabel(str(subtitle or ""))
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label.setWordWrap(True)
        subtitle_label.setProperty("variant", "statusBannerText")
        layout.addWidget(subtitle_label)

        self.action_button = QPushButton(str(action_text or ""))
        self.action_button.setVisible(bool(action_text))
        self.action_button.setProperty("variant", "compact")
        self.action_button.setMinimumHeight(34)
        if callable(action_callback):
            self.action_button.clicked.connect(action_callback)
        layout.addWidget(self.action_button)

