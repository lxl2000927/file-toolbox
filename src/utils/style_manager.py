from PyQt6.QtGui import QFont


class StyleManager:
    COLORS = {
        "primary": "#0078d4",
        "primary_dark": "#005a9e",
        "primary_light": "#e8f4ff",
        "secondary": "#6c757d",
        "success": "#28a745",
        "danger": "#dc3545",
        "warning": "#ffc107",
        "info": "#17a2b8",
        "light": "#f8f9fa",
        "dark": "#343a40",
        "white": "#ffffff",
        "gray_100": "#f8f9fa",
        "gray_200": "#e9ecef",
        "gray_300": "#dee2e6",
        "gray_400": "#ced4da",
        "gray_500": "#adb5bd",
        "gray_600": "#6c757d",
        "gray_700": "#495057",
        "gray_800": "#343a40",
        "gray_900": "#212529",
        "border": "#dee2e6",
        "hover": "#e9ecef",
        "active": "#dee2e6",
    }
    
    @staticmethod
    def _create_font(point_size, weight=QFont.Weight.Normal, style_hint=QFont.StyleHint.SansSerif):
        font = QFont()
        font.setStyleHint(style_hint)
        font.setPointSize(point_size)
        font.setWeight(weight)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font
    
    FONTS = {
        "h1": _create_font(19, QFont.Weight.Bold),
        "h2": _create_font(17, QFont.Weight.Bold),
        "h3": _create_font(15, QFont.Weight.Bold),
        "body": _create_font(14, QFont.Weight.Medium),
        "small": _create_font(12, QFont.Weight.Medium),
        "caption": _create_font(12, QFont.Weight.Medium),
        "monospace": QFont("Courier New", 11),
    }
    
    SIZES = {
        "border_radius": "8px",
        "padding_small": "4px",
        "padding": "8px",
        "padding_large": "12px",
        "margin_small": "4px",
        "margin": "8px",
        "margin_large": "12px",
    }
    
    @classmethod
    def get_stylesheet(cls, widget_type="default"):
        base_style = f"""
            QMainWindow {{
                background-color: {cls.COLORS["gray_100"]};
                font-family: sans-serif;
            }}
            
            QPushButton {{
                background-color: {cls.COLORS["primary"]};
                color: {cls.COLORS["white"]};
                border: none;
                border-radius: {cls.SIZES["border_radius"]};
                padding: {cls.SIZES["padding"]} {cls.SIZES["padding_large"]};
                font-size: 15px;
                font-weight: 600;
                line-height: 22px;
            }}
            
            QPushButton:hover {{
                background-color: {cls.COLORS["primary_dark"]};
            }}
            
            QPushButton:disabled {{
                background-color: {cls.COLORS["gray_300"]};
                color: {cls.COLORS["gray_500"]};
            }}

            QPushButton[variant="primary"] {{
                background-color: {cls.COLORS["primary"]};
                color: {cls.COLORS["white"]};
                border: none;
                font-weight: 600;
            }}

            QPushButton[variant="primary"]:hover {{
                background-color: {cls.COLORS["primary_dark"]};
            }}

            QPushButton[variant="primary"]:disabled {{
                background-color: {cls.COLORS["gray_300"]};
                color: {cls.COLORS["gray_500"]};
            }}

            QPushButton[variant="outline"] {{
                background-color: {cls.COLORS["white"]};
                color: {cls.COLORS["gray_900"]};
                border: 1px solid {cls.COLORS["border"]};
                font-weight: 600;
                padding: 6px 10px;
                font-size: 13px;
                line-height: 19px;
            }}

            QPushButton[variant="outline"]:hover {{
                background-color: {cls.COLORS["gray_100"]};
                border-color: {cls.COLORS["gray_400"]};
            }}

            QPushButton[variant="tab"] {{
                background-color: {cls.COLORS["gray_100"]};
                color: {cls.COLORS["gray_900"]};
                border: 1px solid {cls.COLORS["border"]};
                border-radius: 8px;
                padding: 6px 0px;
                font-size: 14px;
                font-weight: 600;
            }}

            QPushButton[variant="tab"]:checked {{
                background-color: {cls.COLORS["white"]};
                border-color: {cls.COLORS["border"]};
                border-bottom: 2px solid {cls.COLORS["primary"]};
            }}

            QPushButton[variant="nav"] {{
                background-color: {cls.COLORS["white"]};
                color: {cls.COLORS["gray_900"]};
                border: 1px solid {cls.COLORS["border"]};
                border-radius: 8px;
                padding: 6px 4px;
                font-size: 12px;
                font-weight: 600;
                line-height: 18px;
                text-align: center;
            }}

            QPushButton[variant="nav"]:hover {{
                background-color: {cls.COLORS["hover"]};
                border-color: {cls.COLORS["primary"]};
            }}

            QPushButton[variant="nav"]:checked {{
                background-color: {cls.COLORS["primary_light"]};
                color: {cls.COLORS["primary_dark"]};
                border-color: {cls.COLORS["primary"]};
                border-left: 4px solid {cls.COLORS["primary"]};
                font-weight: 700;
            }}

            QPushButton[variant="outline"] {{
                background-color: {cls.COLORS["white"]};
                color: {cls.COLORS["gray_900"]};
                border: 1px solid {cls.COLORS["border"]};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 600;
            }}

            QPushButton[variant="outline"]:hover {{
                background-color: {cls.COLORS["hover"]};
                border-color: {cls.COLORS["primary"]};
            }}

            QPushButton[variant="outline"]:disabled {{
                color: {cls.COLORS["gray_500"]};
                background-color: {cls.COLORS["gray_100"]};
                border-color: {cls.COLORS["border"]};
            }}

            QFrame[variant="statusBanner"] {{
                background-color: {cls.COLORS["gray_100"]};
                border: 1px solid {cls.COLORS["border"]};
                border-radius: 10px;
            }}

            QLabel[variant="statusBannerTitle"] {{
                color: {cls.COLORS["gray_900"]};
                font-size: 13px;
                font-weight: 700;
            }}

            QLabel[variant="statusBannerText"] {{
                color: {cls.COLORS["gray_700"]};
                font-size: 12px;
                font-weight: 600;
            }}
            
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: {cls.SIZES["border_radius"]};
                padding: 10px 12px;
                background-color: {cls.COLORS["white"]};
                font-size: 15px;
                line-height: 22px;
                min-height: 38px;
            }}

            QComboBox {{
                padding-right: 34px;
            }}
            
            QComboBox:hover {{
                background-color: {cls.COLORS["gray_100"]};
                border-color: {cls.COLORS["gray_400"]};
            }}
            
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
                border: 2px solid {cls.COLORS["primary"]};
                padding: 8px 10px;
            }}
            
            QComboBox QAbstractItemView {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: 8px;
                background-color: {cls.COLORS["white"]};
                selection-background-color: transparent;
                selection-color: {cls.COLORS["dark"]};
                font-size: 15px;
                line-height: 22px;
                padding: 4px;
                outline: 0px;
                min-height: 38px;
            }}
            
            QComboBox QAbstractItemView::item {{
                padding: 8px 12px;
                min-height: 36px;
                border-radius: 6px;
                margin: 2px 4px;
                color: {cls.COLORS["gray_800"]};
            }}
            
            QComboBox QAbstractItemView::item:hover {{
                background-color: {cls.COLORS["gray_100"]};
            }}

            QComboBox QAbstractItemView::item:selected {{
                background-color: {cls.COLORS["primary_light"]};
                color: {cls.COLORS["primary_dark"]};
                font-weight: 600;
            }}
            
            QLabel {{
                color: {cls.COLORS["gray_800"]};
                font-size: 15px;
                line-height: 22px;
                margin-bottom: 8px;
            }}

            QGroupBox[compact="true"] QLabel {{
                margin-bottom: 0px;
                font-size: 13px;
                line-height: 19px;
            }}
            
            QLabel.form-label {{
                color: {cls.COLORS["gray_700"]};
                font-size: 13px;
                line-height: 19.5px;
                font-weight: 500;
                margin-bottom: 12px;
            }}
            
            QSpinBox, QDoubleSpinBox {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: {cls.SIZES["border_radius"]};
                padding: 10px 12px;
                background-color: {cls.COLORS["white"]};
                font-size: 15px;
                line-height: 22px;
                min-height: 38px;
            }}

            QSpinBox, QDoubleSpinBox {{
                padding-right: 34px;
            }}
            
            QSpinBox:focus, QDoubleSpinBox:focus {{
                border: 2px solid {cls.COLORS["primary"]};
                padding: 8px 10px;
            }}
            
            QLineEdit[compact="true"], QComboBox[compact="true"], QSpinBox[compact="true"], QDoubleSpinBox[compact="true"] {{
                padding: 4px 6px;
                font-size: 13px;
                line-height: 19px;
                min-height: 26px;
            }}

            QComboBox[compact="true"], QSpinBox[compact="true"], QDoubleSpinBox[compact="true"] {{
                padding-right: 28px;
            }}
            
            QLineEdit[compact="true"]:focus, QComboBox[compact="true"]:focus, QSpinBox[compact="true"]:focus, QDoubleSpinBox[compact="true"]:focus {{
                border: 2px solid {cls.COLORS["primary"]};
                padding: 2px 4px;
            }}
            
            QRadioButton, QCheckBox {{
                spacing: 8px;
                font-size: 15px;
                line-height: 22px;
                color: {cls.COLORS["gray_800"]};
            }}

            QToolButton {{
                background-color: transparent;
                border: none;
                color: {cls.COLORS["gray_900"]};
                font-size: 14px;
                font-weight: 600;
                padding: 6px 8px;
                border-radius: 6px;
                text-align: left;
            }}

            QToolButton:hover {{
                background-color: {cls.COLORS["gray_100"]};
            }}

            QToolButton[variant="icon"] {{
                padding: 0px;
                border-radius: 11px;
            }}

            QToolButton[variant="icon"]:hover {{
                background-color: {cls.COLORS["hover"]};
            }}
            
            QProgressBar {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: {cls.SIZES["border_radius"]};
                background-color: {cls.COLORS["gray_100"]};
                text-align: center;
                font-size: 13px;
                line-height: 19px;
            }}
            
            QProgressBar::chunk {{
                background-color: {cls.COLORS["primary"]};
                border-radius: {cls.SIZES["border_radius"]};
            }}
            
            QGroupBox {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: {cls.SIZES["border_radius"]};
                margin-top: 1em;
                padding: 1em 1.2em 1.2em 1.2em;
                font-size: 15px;
                font-weight: bold;
                line-height: 22px;
                background-color: {cls.COLORS["white"]};
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background-color: {cls.COLORS["white"]};
                border-bottom: 1px solid {cls.COLORS["border"]};
                padding-bottom: 4px;
                margin-bottom: 8px;
            }}
            
            QGroupBox[compact="true"] {{
                margin-top: 0.6em;
                padding: 6px 8px 8px 8px;
                font-size: 13px;
                font-weight: 600;
                line-height: 19px;
            }}
            
            QGroupBox[compact="true"]::title {{
                left: 8px;
                padding: 0 4px 0 4px;
                border-bottom-width: 1px;
                padding-bottom: 2px;
                margin-bottom: 4px;
            }}
            
            QFormLayout {{
                margin: 8px 0px;
            }}
            
            QFormLayout QLabel {{
                margin-bottom: 12px;
                color: {cls.COLORS["gray_700"]};
                font-size: 14px;
                font-weight: 600;
            }}
            
            QFormLayout QWidget {{
                margin-bottom: 12px;
            }}

            QGroupBox[compact="true"] QFormLayout QLabel {{
                margin-bottom: 0px;
            }}

            QGroupBox[compact="true"] QFormLayout QWidget {{
                margin-bottom: 0px;
            }}
            
            QListWidget {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: {cls.SIZES["border_radius"]};
                background-color: {cls.COLORS["white"]};
                alternate-background-color: {cls.COLORS["gray_100"]};
            }}

            QListWidget::item {{
                padding: 10px 12px;
                border-radius: 6px;
            }}

            QListWidget::item:hover {{
                background-color: {cls.COLORS["hover"]};
            }}

            QTreeWidget {{
                border: 1px solid {cls.COLORS["border"]};
                border-radius: {cls.SIZES["border_radius"]};
                background-color: {cls.COLORS["white"]};
                alternate-background-color: {cls.COLORS["gray_100"]};
                font-size: 15px;
                line-height: 22px;
            }}

            QTreeWidget::item {{
                padding: 8px 10px;
                border-radius: 6px;
            }}

            QTreeWidget::item:hover {{
                background-color: {cls.COLORS["hover"]};
            }}

            QHeaderView::section {{
                background-color: {cls.COLORS["gray_100"]};
                color: {cls.COLORS["gray_800"]};
                border: 1px solid {cls.COLORS["border"]};
                padding: 6px 8px;
                font-size: 15px;
                font-weight: 600;
            }}

            QMenu {{
                background-color: {cls.COLORS["white"]};
                border: 1px solid {cls.COLORS["border"]};
                border-radius: 8px;
                padding: 6px;
            }}

            QMenu::item {{
                padding: 8px 10px;
                border-radius: 6px;
            }}

            QMenu::item:selected {{
                background-color: {cls.COLORS["primary_light"]};
                color: {cls.COLORS["dark"]};
            }}
            
            QListWidget::item:selected {{
                background-color: {cls.COLORS["primary_light"]};
                color: {cls.COLORS["dark"]};
                border-left: 3px solid {cls.COLORS["primary"]};
            }}
            
            QScrollBar:vertical {{
                background-color: {cls.COLORS["gray_100"]};
                width: 12px;
                margin: 0px;
            }}
            
            QScrollBar::handle:vertical {{
                background-color: {cls.COLORS["gray_400"]};
                border-radius: 6px;
                min-height: 20px;
            }}
            
            QScrollBar::handle:vertical:hover {{
                background-color: {cls.COLORS["gray_500"]};
            }}

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                subcontrol-origin: margin;
            }}

            QScrollBar:horizontal {{
                background-color: {cls.COLORS["gray_100"]};
                height: 12px;
                margin: 0px;
            }}

            QScrollBar::handle:horizontal {{
                background-color: {cls.COLORS["gray_400"]};
                border-radius: 6px;
                min-width: 20px;
            }}

            QScrollBar::handle:horizontal:hover {{
                background-color: {cls.COLORS["gray_500"]};
            }}

            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
                subcontrol-origin: margin;
            }}
        """
        
        if widget_type == "card":
            return f"""
                QFrame {{
                    background-color: {cls.COLORS["white"]};
                    border: 1px solid {cls.COLORS["border"]};
                    border-radius: {cls.SIZES["border_radius"]};
                    padding: {cls.SIZES["padding_large"]};
                }}
            """
        elif widget_type == "nav_button":
            return f"""
                QPushButton {{
                    background-color: {cls.COLORS["white"]};
                    color: {cls.COLORS["gray_800"]};
                    border: 1px solid {cls.COLORS["border"]};
                    border-radius: {cls.SIZES["border_radius"]};
                    padding: {cls.SIZES["padding"]} {cls.SIZES["padding_large"]};
                    text-align: left;
                    font-size: 15px;
                    font-weight: 600;
                }}
                
                QPushButton:hover {{
                    background-color: {cls.COLORS["primary_light"]};
                    border-color: {cls.COLORS["primary"]};
                }}
                
                QPushButton:checked {{
                    background-color: {cls.COLORS["primary"]};
                    color: {cls.COLORS["white"]};
                    border-color: {cls.COLORS["primary_dark"]};
                }}
            """
        
        return base_style
    
    @classmethod
    def apply_global_style(cls, app):
        app.setStyle("Fusion")
        app.setStyleSheet(cls.get_stylesheet())
    
    @classmethod
    def get_color(cls, color_name):
        return cls.COLORS.get(color_name, cls.COLORS["primary"])
    
    @classmethod
    def get_font(cls, font_name):
        return cls.FONTS.get(font_name, cls.FONTS["body"])
