"""SOVEREIGN — dark neon hacker theme QSS stylesheet."""
from src.constants import COLORS

def get_stylesheet() -> str:
    c = COLORS
    return f"""
/* ── Reset / global ────────────────────────────────────────────── */
QMainWindow, QWidget, QDialog {{
    background: {c['bg_dark']};
    color: {c['text_primary']};
    font-family: "JetBrains Mono","Fira Code","Consolas","Courier New",monospace;
    font-size: 12px;
    border: none;
}}
QLabel {{ color: {c['text_primary']}; background: transparent; }}

/* ── Scrollbars ────────────────────────────────────────────────── */
QScrollBar:vertical   {{ background: {c['bg_void']}; width: 6px; border: none; }}
QScrollBar:horizontal {{ background: {c['bg_void']}; height: 6px; border: none; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {c['border']}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {c['neon_blue']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ background: none; border: none; }}

/* ── Inputs ────────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {c['bg_input']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 3px;
    padding: 4px 8px;
    selection-background-color: {c['neon_blue']};
    font-family: "JetBrains Mono","Consolas",monospace;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {c['neon_blue']};
    background: {c['bg_highlight']};
}}
QPlainTextEdit, QTextEdit {{
    background: {c['bg_void']};
    color: {c['neon_green']};
    border: 1px solid {c['border']};
    border-radius: 3px;
    font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
    font-size: 11px;
    padding: 4px;
    selection-background-color: {c['neon_blue']};
}}
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {c['neon_blue']};
}}

/* ── ComboBox ──────────────────────────────────────────────────── */
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{
    background: {c['bg_card']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    selection-background-color: {c['bg_highlight']};
    selection-color: {c['neon_blue']};
    outline: none;
}}

/* ── Tables ────────────────────────────────────────────────────── */
QTableWidget, QTreeWidget, QListWidget {{
    background: {c['bg_void']};
    color: {c['text_primary']};
    gridline-color: {c['border']};
    border: 1px solid {c['border']};
    alternate-background-color: {c['bg_panel']};
    outline: none;
}}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{
    padding: 3px 6px;
    border-bottom: 1px solid {c['bg_panel']};
}}
QTableWidget::item:selected, QTreeWidget::item:selected,
QListWidget::item:selected {{
    background: {c['bg_highlight']};
    color: {c['neon_blue']};
}}
QHeaderView::section {{
    background: {c['bg_panel']};
    color: {c['text_muted']};
    border: none;
    border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    padding: 4px 8px;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QHeaderView::section:last {{ border-right: none; }}

/* ── Checkboxes ────────────────────────────────────────────────── */
QCheckBox {{ color: {c['text_primary']}; spacing: 6px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {c['border']};
    border-radius: 2px;
    background: {c['bg_input']};
}}
QCheckBox::indicator:checked {{
    background: {c['neon_blue']};
    border-color: {c['neon_blue']};
}}

/* ── Tabs ──────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    background: {c['bg_panel']};
}}
QTabBar::tab {{
    background: {c['bg_void']};
    color: {c['text_muted']};
    padding: 6px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 11px;
    letter-spacing: 0.5px;
}}
QTabBar::tab:selected {{
    color: {c['neon_blue']};
    border-bottom: 2px solid {c['neon_blue']};
    background: {c['bg_panel']};
}}
QTabBar::tab:hover:!selected {{ color: {c['text_primary']}; }}

/* ── Splitter ──────────────────────────────────────────────────── */
QSplitter::handle {{ background: {c['border']}; width: 1px; height: 1px; }}
QSplitter::handle:hover {{ background: {c['neon_blue']}; }}

/* ── Menus ─────────────────────────────────────────────────────── */
QMenuBar {{
    background: {c['bg_void']};
    color: {c['text_muted']};
    border-bottom: 1px solid {c['border']};
}}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ color: {c['neon_blue']}; }}
QMenu {{
    background: {c['bg_card']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
}}
QMenu::item {{ padding: 5px 20px; }}
QMenu::item:selected {{ background: {c['bg_highlight']}; color: {c['neon_blue']}; }}
QMenu::separator {{ background: {c['border']}; height: 1px; margin: 3px 0; }}

/* ── Status bar ────────────────────────────────────────────────── */
QStatusBar {{
    background: {c['bg_void']};
    color: {c['text_muted']};
    border-top: 1px solid {c['border']};
    font-size: 10px;
}}

/* ── Progress bar ──────────────────────────────────────────────── */
QProgressBar {{
    background: {c['bg_card']};
    border: 1px solid {c['border']};
    border-radius: 2px;
    text-align: center;
    color: {c['text_primary']};
    font-size: 10px;
    height: 8px;
}}
QProgressBar::chunk {{ background: {c['neon_blue']}; border-radius: 2px; }}

/* ── GroupBox ──────────────────────────────────────────────────── */
QGroupBox {{
    color: {c['text_muted']};
    border: 1px solid {c['border']};
    border-radius: 3px;
    margin-top: 14px;
    padding-top: 8px;
    font-size: 10px;
    letter-spacing: 0.5px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px; top: -2px;
    padding: 0 4px;
    color: {c['text_muted']};
    font-size: 10px;
    text-transform: uppercase;
}}

/* ── Tooltips ──────────────────────────────────────────────────── */
QToolTip {{
    background: {c['bg_card']};
    color: {c['text_primary']};
    border: 1px solid {c['neon_blue']};
    padding: 4px 8px;
    font-size: 11px;
}}
"""
