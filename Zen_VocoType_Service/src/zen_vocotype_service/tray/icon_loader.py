"""托盘图标加载（双环境解析，与客户端 icon_loader 同一方案）。

路径解析双环境：

- 源码布局：基于本文件自身位置推算组件根（``src/<pkg>/tray/icon_loader.py``
  → 上三级为组件根），取 ``<组件根>/assets/``
- PyInstaller 打包：``sys._MEIPASS`` 指向打包内嵌数据目录，取 ``_MEIPASS/assets/``

🔴 禁止相对 cwd——参考代码 GridChat_Service ``app/tray/utils.py`` 以相对路径
``"asset"`` 搜索图标在打包形态静默失效为反面案例（只复制其 PNG 资产，不复制
其路径解析方式）。图标缺失记 warning 不崩溃。
"""

import sys
from pathlib import Path

from loguru import logger

#: 托盘图标文件名（复制自 GridChat_Service/asset 四档尺寸；
#: 同目录 icon.png 为另一套图形（树状），经审定为错误资产，不采用已删除）
ICON_FILENAMES: tuple[str, ...] = (
    "icon_32.png",
    "icon_64.png",
    "icon_128.png",
    "icon_256.png",
)


def assets_dir() -> Path:
    """返回 assets 目录（双环境解析，见模块 docstring）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:  # PyInstaller 打包形态
        return Path(meipass) / "assets"
    return Path(__file__).resolve().parents[3] / "assets"


def load_tray_icon():
    """加载托盘图标（QIcon，含全部尺寸档）。

    :returns: ``QIcon``；全部缺失时返回空 QIcon 并记 warning（不崩溃）
    """
    from PySide6.QtGui import QIcon

    base = assets_dir()
    icon = QIcon()
    found = 0
    for name in ICON_FILENAMES:
        path = base / name
        if path.is_file():
            icon.addFile(str(path))
            found += 1
        else:
            logger.warning("托盘图标缺失：{}（路径解析基准：{}）", path, base)
    if found == 0:
        logger.warning("托盘图标全部缺失，托盘将以空图标显示（降级，不崩溃）")
    else:
        logger.debug("托盘图标加载完成：{}/{} 档，目录 {}", found, len(ICON_FILENAMES), base)
    return icon
