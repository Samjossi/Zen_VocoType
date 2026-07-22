# -*- mode: python ; coding: utf-8 -*-
"""Zen_VocoType_Launcher onedir spec（选型一方案 A；共用模板段见 _common.py）。

Launcher 为零 Qt、零 ML 的纯编排进程，两类重型栈全部排除控制体积。
"""

import sys

sys.path.insert(0, SPECPATH)  # noqa: F821（SPECPATH 由 PyInstaller 注入）
from _common import ML_EXCLUDES, PROJECT_ROOT, QT_EXCLUDES, component_datas, component_pathex

COMPONENT = "Zen_VocoType_Launcher"
NAME = "zen_vocotype_launcher"


a = Analysis(
    [str(PROJECT_ROOT / COMPONENT / "main.py")],
    pathex=component_pathex(COMPONENT),
    binaries=[],
    datas=component_datas(COMPONENT),
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Launcher 无 GUI（通知走 notify-send / 桌面服务），Qt 与 ML 栈全部排除
    excludes=QT_EXCLUDES + ["PySide6"] + ML_EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NAME,
)
