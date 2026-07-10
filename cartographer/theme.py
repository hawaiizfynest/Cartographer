"""
theme.py - A dark, operator-grade Qt stylesheet (zinc neutrals + cyan accent).

Written by LJ "HawaiizFynest" Eblacas
"""

DARK_QSS = """
* {
    font-family: "Segoe UI", "Inter", "SF Pro Text", "Cantarell", sans-serif;
    font-size: 13px;
    color: #e4e4e7;
}
QWidget#root, QMainWindow, QDialog {
    background-color: #18181b;
}
QGroupBox {
    border: 1px solid #27272a;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 12px 12px 12px;
    background-color: #1c1c1f;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: #a1a1aa;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 1px;
}
QLabel { color: #d4d4d8; }
QLabel#mono, QPlainTextEdit#log {
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
}
QLabel#statusOk { color: #22d3ee; font-weight: 600; }
QLabel#statusBad { color: #f87171; font-weight: 600; }
QLabel#hint { color: #71717a; font-size: 11px; }
QLabel#footer { color: #52525b; font-size: 10px; padding: 4px 2px 0 2px;
                border-top: 1px solid #27272a; }

QPushButton {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 7px 14px;
    color: #e4e4e7;
}
QPushButton:hover { background-color: #323237; border-color: #52525b; }
QPushButton:pressed { background-color: #3f3f46; }
QPushButton:disabled { color: #52525b; background-color: #202023; border-color: #2a2a2e; }
QPushButton#primary {
    background-color: #0891b2;
    border: 1px solid #06b6d4;
    color: #ecfeff;
    font-weight: 600;
}
QPushButton#primary:hover { background-color: #0aa5c4; }
QPushButton#primary:disabled { background-color: #164e5b; border-color: #155e6e; color: #5b7e87; }
QPushButton#danger {
    background-color: #3f1d20;
    border: 1px solid #7f1d1d;
    color: #fecaca;
}
QPushButton#danger:hover { background-color: #531f23; }

QComboBox {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 6px 10px;
    min-width: 120px;
}
QComboBox:hover { border-color: #52525b; }
QComboBox QAbstractItemView {
    background-color: #1c1c1f;
    border: 1px solid #3f3f46;
    selection-background-color: #0891b2;
    selection-color: #ecfeff;
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #3f3f46; border-radius: 4px; background: #27272a;
}
QCheckBox::indicator:checked { background: #0891b2; border-color: #06b6d4; }

QProgressBar {
    border: 1px solid #27272a;
    border-radius: 6px;
    background-color: #1c1c1f;
    text-align: center;
    color: #a1a1aa;
    height: 20px;
}
QProgressBar::chunk {
    background-color: #0891b2;
    border-radius: 5px;
}
QPlainTextEdit#log {
    background-color: #111113;
    border: 1px solid #27272a;
    border-radius: 8px;
    color: #a1a1aa;
    font-size: 12px;
}
QToolButton {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 6px 8px;
}
QToolButton:hover { background-color: #323237; }
"""
