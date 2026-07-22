"""三组件 spec 共用模板段（选型一方案 A：结构同构，差异仅入口/名称/资产）。

共用内容：项目根推算、协议契约库与资产的 datas 收编、排除项分组。
🔴 禁止各组件目录内再长私有打包脚本/规格——新增打包逻辑一律进本目录。
"""

from pathlib import Path

#: 项目根（tools/specs/_common.py → 上两级）
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Qt 系排除项（仅用于明确不依赖 Qt 的组件；🔴 Service 不适用——其托盘依赖 PySide6）
QT_EXCLUDES: list[str] = ["PyQt5", "PyQt6", "PySide2"]

#: 机器学习栈排除项（Client/Launcher 用；Service 为推理主体🔴 不得排除）
ML_EXCLUDES: list[str] = [
    "torch",
    "torchaudio",
    "torchvision",
    "funasr",
    "modelscope",
]


def component_pathex(component: str) -> list[str]:
    """组件包与协议契约库的源码路径（editable .pth 之外的显式保障）。"""
    return [
        str(PROJECT_ROOT / component / "src"),
        str(PROJECT_ROOT / "Zen_VocoType_Protocol" / "src"),
    ]


def component_datas(component: str) -> list[tuple[str, str]]:
    """随包数据：资产目录 + 包内默认配置（双环境解析见契约库 settings）。

    - ``assets/`` → ``_MEIPASS/assets/``（各组件 icon_loader 既有约定）
    - ``config.yaml`` → ``_MEIPASS/`` 根（包内默认配置只读随包；运行时
      持久化走用户配置文件层，🔴 禁止依赖包内配置可写）
    """
    comp_dir = PROJECT_ROOT / component
    return [
        (str(comp_dir / "assets"), "assets"),
        (str(comp_dir / "config.yaml"), "."),
    ]
