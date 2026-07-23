# -*- mode: python ; coding: utf-8 -*-
"""Zen_VocoType_Service onedir spec（选型一方案 A；共用模板段见 _common.py）。

🔴 不排除 PySide6：Service 托盘（切换模型 / 设置模型目录…，验收标准 7）
依赖 Qt——与计划 T4.2 条目「Service 排除 Qt 系」的原文假设（Service 无 Qt）
不符，实施时按托盘既有事实修正（记录于阶段 4 验收记录）。
torch/FunASR 收编依赖 PyInstaller 成熟 hook；缺漏按 R2 增量补齐并注释。
"""

import sys

sys.path.insert(0, SPECPATH)  # noqa: F821（SPECPATH 由 PyInstaller 注入）
from PyInstaller.utils.hooks import collect_all

from _common import PROJECT_ROOT, QT_EXCLUDES, component_datas, component_pathex

COMPONENT = "Zen_VocoType_Service"
NAME = "zen_vocotype_service"

# ML 栈显式收编（R2 落地）：funasr/modelscope/torch 经 models/loader.py
# 延迟 import（MODELSCOPE_CACHE 顺序红线，main.py 首行硬设置），PyInstaller
# 静态分析不可见，🔴 必须 collect_all 显式收编——首轮构建实测三者全部漏收
# （产物 345MiB 无 torch，--version 探针不触发模型加载无法暴露）。
# torch 的二进制/动态库由 contrib hook-torch 随 hiddenimports 触发收编。
# qwen_asr（Qwen3-ASR 引擎）同在 loader.py 延迟导入，同款收编；
# 🔴 其 cli 子模块引入 gradio/vllm（演示/部署用，本项目不用且 vllm 未安装），
# 必须剔除——否则打包膨胀数百 MiB 且 serve 模块 import vllm 直接失败。
# nagisa/dynet 为 forced aligner 专属重依赖（死路径，见 rhook_qwen_asr.py），
# 分析期排除 + 运行期占位模块兜底。
_ml_datas, _ml_binaries, _ml_hiddenimports = [], [], []
for _pkg in ("torch", "funasr", "modelscope", "qwen_asr"):
    _d, _b, _h = collect_all(_pkg)
    if _pkg == "qwen_asr":
        _h = [m for m in _h if not m.startswith("qwen_asr.cli")]
    _ml_datas += _d
    _ml_binaries += _b
    _ml_hiddenimports += _h


a = Analysis(
    [str(PROJECT_ROOT / COMPONENT / "main.py")],
    pathex=component_pathex(COMPONENT),
    # bin/：vendor 的 llama-funasr-cli（funasr-gguf 引擎子进程运行时，
    # 双环境解析 _MEIPASS/bin，见 models/loader.py _gguf_cli_path）
    datas=component_datas(COMPONENT)
    + [(str(PROJECT_ROOT / COMPONENT / "bin"), "bin")]
    + _ml_datas,
    hiddenimports=_ml_hiddenimports,
    binaries=_ml_binaries,
    hookspath=[],
    runtime_hooks=[SPECPATH + "/rhook_qwen_asr.py"],  # noqa: F821
    excludes=QT_EXCLUDES + ["nagisa", "dynet"],
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
    upx=False,  # 🔴 禁用 UPX：torch 等大型二进制压缩反而拖慢启动（旧 onefile 解压教训）
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
