from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel


class StatusBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("variant", "statusBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.title_label = QLabel("")
        self.title_label.setProperty("variant", "statusBannerTitle")
        layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.text_label = QLabel("")
        self.text_label.setProperty("variant", "statusBannerText")
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.setVisible(False)

    def set_message(self, title: str, text: str = ""):
        self.title_label.setText(str(title or ""))
        self.text_label.setText(str(text or ""))
        self.setVisible(bool(title or text))

