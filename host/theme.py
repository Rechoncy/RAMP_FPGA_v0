from pathlib import Path


STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #263d5c; background: transparent; }
QMainWindow, QWidget#workspace { background: #f2f6fd; }
QDialog, QMessageBox { background: #f7faff; }
QFrame#sidebar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #ffffff,stop:1 #eaf2ff); border: 1px solid #dce7f7; border-radius: 24px; }
QFrame#sidebar QLabel { background: transparent; color: #607798; }
QLabel { background: transparent; }
QFrame#sidebar QLabel#brand { font-size: 25px; font-weight: 700; color: #2563eb; }
QLabel#title { font-size: 28px; font-weight: 700; color: #17365c; padding-bottom: 3px; }
QLabel#subtitle { color: #687f9e; font-size: 12px; }
QLabel#section { font-size: 15px; font-weight: 600; color: #2a496f; }
QLabel#metric { font-size: 27px; font-weight: 700; color: #2563eb; }
QLabel#badge { background: #e7f0ff; color: #285fb3; padding: 10px 18px; border-radius: 18px; }
QLabel#notice { color: #8b682c; padding: 10px 13px; background: #fff7e8; border-radius: 12px; }
QFrame#card { background: #ffffff; border: 1px solid #e1eaf7; border-radius: 22px; }
QPushButton { background: #ffffff; border: 1px solid #dce7f7; border-radius: 13px; padding: 9px 16px; font-weight: 600; min-height: 18px; }
QPushButton:hover { background: #edf4ff; border-color: #adc9f7; }
QPushButton:pressed { background: #dfeaff; }
QPushButton:focus { border-color: #6195ee; }
QPushButton:disabled { color: #94a4bd; background: #f3f6fb; border-color: #e9eff8; }
QPushButton#primary { background: #2563eb; color: white; border-color: #2563eb; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#primary:disabled { background: #e8effa; color: #8ba1c0; border-color: #e8effa; }
QPushButton#danger { background: #fff5f2; color: #b3594b; border-color: #efdcd5; }
QPushButton#nav { background: transparent; color: #536e94; border: none; border-radius: 14px; text-align: left; padding: 14px 15px; }
QPushButton#nav:checked { background: #dfebff; color: #1d4ed8; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #f7faff; border: 1px solid #dfe8f6; border-radius: 12px; padding: 7px 11px; min-height: 22px; selection-background-color: #dbeafe; selection-color: #174593; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: #bdd1ee; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { background: #ffffff; border: 1px solid #6195ee; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { background: #f3f6fb; color: #8ba1bd; border-color: #e8eef7; }
QSpinBox, QDoubleSpinBox { padding-right: 29px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: padding; subcontrol-position: top right; width: 23px; border: none; background: transparent; margin-right: 5px; margin-top: 2px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: padding; subcontrol-position: bottom right; width: 23px; border: none; background: transparent; margin-right: 5px; margin-bottom: 2px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url('@ASSETS@/chevron-up.svg'); width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url('@ASSETS@/chevron-down.svg'); width: 12px; height: 12px; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
QComboBox::down-arrow { image: url('@ASSETS@/chevron-down.svg'); width: 15px; height: 15px; }
QComboBox#timeUnit { padding-left: 9px; padding-right: 18px; }
QComboBox#timeUnit::drop-down { width: 20px; }
QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #dce7f7; border-radius: 12px; padding: 6px; outline: 0; selection-background-color: #e4eeff; selection-color: #1e54ab; }
QComboBox QAbstractItemView::item { min-height: 29px; padding: 3px 10px; border-radius: 8px; }
QTableWidget { background: white; alternate-background-color: #f8faff; border: 1px solid #e1eaf7; border-radius: 15px; padding: 6px; gridline-color: #edf2fa; selection-background-color: #e4eeff; selection-color: #1e54ab; outline: 0; }
QTableWidget::item { padding: 5px; border: none; }
QHeaderView { background: transparent; }
QHeaderView::section { background: #f0f5fd; border: none; padding: 10px; font-weight: 600; color: #5e7698; }
QTableCornerButton::section { background: #f0f5fd; border: none; }
QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { padding: 13px 11px; border-radius: 12px; margin: 3px 0; color: #4b6385; }
QListWidget::item:hover { background: #f0f5ff; }
QListWidget::item:selected { background: #e4eeff; color: #245bc1; }
QPlainTextEdit { background: #f7faff; color: #355278; border: 1px solid #dce7f7; border-radius: 16px; padding: 14px; font-family: Consolas; font-size: 12px; selection-background-color: #dbeafe; }
QTabWidget::pane { border: 1px solid #e1eaf7; border-radius: 20px; background: #ffffff; top: 8px; padding: 10px; }
QTabBar { background: transparent; }
QTabBar::tab { padding: 10px 18px; margin: 0 5px 5px 0; border-radius: 13px; background: #eaf1fc; color: #607998; border: none; }
QTabBar::tab:hover { background: #e0ebfc; color: #2859a3; }
QTabBar::tab:selected { background: #dce9ff; color: #2059bb; font-weight: 600; }
QScrollArea, QScrollArea > QWidget > QWidget { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 8px 0; }
QScrollBar::handle:vertical { background: #ceddf1; min-height: 36px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #9ab9e7; }
QScrollBar:horizontal { height: 8px; background: transparent; margin: 0 8px; }
QScrollBar::handle:horizontal { background: #ceddf1; min-width: 36px; border-radius: 4px; }
QScrollBar::handle:horizontal:hover { background: #9ab9e7; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QCheckBox { spacing: 9px; background: transparent; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 6px; background: #f8faff; border: 1px solid #c5d5ed; }
QCheckBox::indicator:checked { background: #2563eb; border-color: #2563eb; image: url('@ASSETS@/check.svg'); }
QToolTip { background: #23446f; color: white; border: none; border-radius: 8px; padding: 9px 12px; }
QMenu { background: #ffffff; border: 1px solid #dce7f7; border-radius: 12px; padding: 8px; }
QMenu::item { padding: 8px 18px; border-radius: 8px; }
QMenu::item:selected { background: #e4eeff; color: #1d4ed8; }
QMenu::item:disabled { color: #94a4bd; }
QMenu::separator { height: 1px; background: #e1eaf7; margin: 5px 8px; }
QStatusBar { background: #f2f6fd; color: #687f9e; padding: 1px 12px; font-size: 11px; }
QStatusBar::item { border: none; }
QMenu { background: #ffffff; border: 1px solid #dce7f7; border-radius: 10px; padding: 5px; }
QMenu::item { padding: 7px 16px; border-radius: 6px; }
QMenu::item:selected { background: #e4eeff; color: #1e54ab; }
""".replace('@ASSETS@',(Path(__file__).parent/'assets').as_posix())
