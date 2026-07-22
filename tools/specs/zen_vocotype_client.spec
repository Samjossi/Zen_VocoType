# -*- mode: python ; coding: utf-8 -*-
"""Zen_VocoType_Client onedir spec（选型一方案 A；共用模板段见 _common.py）。

排除机器学习栈（torch/FunASR 系）控制体积——识别推理全部经 Socket
由 Service 承担，客户端零 ML 依赖。
"""

import sys

sys.path.insert(0, SPECPATH)  # noqa: F821（SPECPATH 由 PyInstaller 注入）
from _common import ML_EXCLUDES, PROJECT_ROOT, QT_EXCLUDES, component_datas, component_pathex

COMPONENT = "Zen_VocoType_Client"
NAME = "zen_vocotype_client"


a = Analysis(
    [str(PROJECT_ROOT / COMPONENT / "main.py")],
    pathex=component_pathex(COMPONENT),
    binaries=[],
    datas=component_datas(COMPONENT),
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
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
    console=True,  # stderr 兜底可见（T4.6 日志不可写注入的验收口径）
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NAME,
)
