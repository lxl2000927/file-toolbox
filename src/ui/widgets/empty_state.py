from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QStyle


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

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon is not None:
            pm = self.style().standardIcon(icon).pixmap(QSize(28, 28))
            icon_label.setPixmap(pm)
        icon_label.setFixedSize(56, 56)
        icon_label.setStyleSheet(
            "background-color: #f0f0f0; border-radius: 28px; padding: 0px;"
        )
        layout.addWidget(icon_label)

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

