# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    binaries=playwright_binaries,
    datas=playwright_datas + [("README.md", "."), ("assets/app-icon.png", "assets")],
    hiddenimports=playwright_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
incompatible_runtime_dlls = {"icuuc.dll", "icudt78.dll"}
package_binaries = [
    entry for entry in a.binaries if entry[0].lower() not in incompatible_runtime_dlls
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XianyuLinkCollector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app-icon.ico",
)

coll = COLLECT(
    exe,
    package_binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="XianyuLinkCollector",
)
