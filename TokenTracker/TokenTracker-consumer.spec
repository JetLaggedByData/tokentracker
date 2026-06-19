# -*- mode: python ; coding: utf-8 -*-
# Consumer edition - browser extension driven, no API keys
# Build: uv run python build_consumer.py

block_cipher = None

a = Analysis(
    ['main_consumer.py'],
    pathex=['.', 'src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pystray._win32', 'pystray._darwin', 'pystray._xorg',
        'PIL._imaging', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL._tkinter_finder',
        'certifi',
        'keyring', 'keyring.backends', 'keyring.errors',
        'keyring.backends.Windows',
        'keyring.backends.fail',
        'requests',
        'cryptography', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.backends.openssl',
        # our package
        'tokentracker',
        'tokentracker.core', 'tokentracker.core.config',
        'tokentracker.core.platform',
        'tokentracker.core.browsers',
        'tokentracker.core.auth',
        'tokentracker.core.counter',
        'tokentracker.core.server',
        'tokentracker.core.updater',
        'tokentracker.core.secrets',
        'tokentracker.ui', 'tokentracker.ui.icon',
        'tokentracker.ui.tray_browser',
        'tokentracker.ui.dashboard',
        'tokentracker.ui.setup',
        'tkinter', 'tkinter.ttk',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'scipy', 'pandas',
        'msal',      # no API auth needed
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='TokenTracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    icon='tokentracker.ico',
    target_arch=None,
    version='version_info.txt',
)
