# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

block_cipher = None

# Project root
project_root = os.path.abspath(SPECPATH)

# Data files to include
datas = [
    (os.path.join(project_root, "templates"), "templates"),
    (os.path.join(project_root, "assets"), "assets"),
    (os.path.join(project_root, "config"), "config"),
]

# Check if icon exists
icon_path = os.path.join(project_root, "assets", "icon.ico")
if not os.path.exists(icon_path):
    icon_path = None

a = Analysis(
    [os.path.join(project_root, "main.py")],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # PyQt6
        "PyQt6",
        "PyQt6.sip",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        # Async
        "asyncio",
        "aiohttp",
        "aiohttp._http_writer",
        "aiohttp._http_parser",
        "aiohttp._websocket",
        "aiofiles",
        # Database
        "sqlite3",
        "aiosqlite",
        # Templating
        "jinja2",
        "jinja2.async_utils",
        # YAML
        "yaml",
        # Crypto
        "cryptography",
        "cryptography.fernet",
        # AST
        "ast",
        # Other
        "pathlib",
        "uuid",
        "json",
        "logging",
        "tempfile",
        "subprocess",
        "difflib",
        "hashlib",
        # Project modules
        "orchestrator",
        "orchestrator.core",
        "orchestrator.state_machine",
        "orchestrator.decomposer",
        "orchestrator.scheduler",
        "orchestrator.quality_gate",
        "orchestrator.retry_engine",
        "orchestrator.token_resonance",
        "infrastructure",
        "infrastructure.state_store",
        "infrastructure.api_pool",
        "infrastructure.template_engine",
        "infrastructure.powershell_bridge",
        "infrastructure.sandbox_executor",
        "gui",
        "gui.main_window",
        "gui.panels.task_input",
        "gui.panels.reasoning_viewer",
        "gui.panels.agent_monitor",
        "gui.panels.log_viewer",
        "gui.panels.api_status",
        "gui.panels.quality_stats",
        "gui.panels.settings",
        "gui.widgets.agent_card",
        "gui.widgets.reasoning_popup",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "tkinter",
        "PIL",
        "pytest",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UltraOrchestrator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # --noconsole (GUI app)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if icon_path else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="UltraOrchestrator",
)
