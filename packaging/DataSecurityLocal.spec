# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_submodules


datas = []
binaries = []
hiddenimports = []

for package_name in (
    "en_core_web_sm",
    "presidio_analyzer",
    "spacy",
    "weasel",
    "thinc",
    "pywebview",
    "webview",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += collect_submodules("webview")
hiddenimports += collect_submodules("presidio_analyzer")


a = Analysis(
    ["../desktop_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Data Security Local",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Data Security Local",
)

app = BUNDLE(
    coll,
    name="Data Security Local.app",
    icon=None,
    bundle_identifier="com.ameliekong.datasecuritylocal",
    info_plist={
        "CFBundleName": "Data Security Local",
        "CFBundleDisplayName": "Data Security Local",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
    },
)
