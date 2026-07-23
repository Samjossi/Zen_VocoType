# -*- mode: python ; coding: utf-8 -*-
"""Zen_VocoType_Launcher onedir spec（选型一方案 A；共用模板段见 _common.py）。

T40 修订：Launcher 新增系统托盘（设置/观察窗口），PySide6 移出排除项
（收编走 PyInstaller 既有 hook，Service spec 已验证）；ML 栈仍全部排除
控制体积——Launcher 无推理，🔴 ML 排除不变。
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
    # T40：托盘依赖 PySide6（不排除）；其余 Qt 绑定与 ML 栈全部排除
    excludes=QT_EXCLUDES + ML_EXCLUDES,
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
