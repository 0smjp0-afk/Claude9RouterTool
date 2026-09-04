# -*- mode: python ; coding: utf-8 -*-
# بیلد Claude9RouterTool — GUI (PySide6) + دارایی جاسازی‌شده (setup + فونت وزیر)


a = Analysis(
    ['claude9router_tool.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/Claude Setup.exe', 'assets'),
        ('assets/fonts/Vazirmatn-Regular.ttf', 'assets/fonts'),
        ('assets/fonts/Vazirmatn-Medium.ttf', 'assets/fonts'),
        ('assets/fonts/Vazirmatn-Bold.ttf', 'assets/fonts'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Claude9RouterTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
