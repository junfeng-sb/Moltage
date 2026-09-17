# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


PROJECT_ROOT = Path(SPECPATH).resolve().parent

analysis = Analysis(
    [str(PROJECT_ROOT / "moltage_gui.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / "resources"), "resources"),
        (str(PROJECT_ROOT / "LICENSE"), "resources/legal"),
        (
            str(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"),
            "resources/legal",
        ),
        (str(PROJECT_ROOT / "LICENSES"), "resources/legal/LICENSES"),
    ],
    hiddenimports=["keyring.backends.Windows"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtPdf",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtVirtualKeyboard",
    ],
    noarchive=False,
    optimize=0,
)

# QtGui's generic plugin collector includes two optional plugins that Moltage
# does not use. Their dependency chains otherwise pull Qt PDF, QML, Quick, and
# Virtual Keyboard into the Windows distribution even when the corresponding
# Python modules are excluded above.
UNUSED_QT_BINARY_DESTINATIONS = {
    "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlModels.dll",
    "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
}
filtered_binaries = [
    entry
    for entry in analysis.binaries
    if entry[0].replace("\\", "/")
    not in UNUSED_QT_BINARY_DESTINATIONS
]
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Moltage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(
        PROJECT_ROOT / "resources" / "icons" / "moltage.ico"
    ),
    version=str(PROJECT_ROOT / "packaging" / "version_info.txt"),
)

bundle = COLLECT(
    exe,
    filtered_binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Moltage",
)
