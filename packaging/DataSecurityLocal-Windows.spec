# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path.cwd().resolve()

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


def add_data_tree(source_dir, target_dir):
    source = Path(source_dir)
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.is_file():
            relative_parent = path.relative_to(source).parent
            datas.append((str(path), str(Path(target_dir) / relative_parent)))


tesseract_dir = os.environ.get("TESSERACT_DIR")
if tesseract_dir:
    add_data_tree(tesseract_dir, "tesseract")


a = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
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
    a.binaries,
    a.datas,
    [],
    name="Data Security Local",
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
