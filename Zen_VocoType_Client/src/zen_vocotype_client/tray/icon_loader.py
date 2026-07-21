"""托盘图标加载（C6：开发与打包环境均须正常显示）。

路径解析双环境（大纲 §5-13 / §3.5 约束 3）：

- 源码布局：基于本文件自身位置推算组件根（``src/<pkg>/tray/icon_loader.py``
  → 上三级为组件根），取 ``<组件根>/assets/``
- PyInstaller 打包：``sys._MEIPASS`` 指向打包内嵌数据目录，取 ``_MEIPASS/assets/``

🔴 禁止相对 cwd——旧 Launcher ``asset/star_64.png`` 相对路径在 AppImage 中
静默失效为反面案例。图标缺失记 warning 不崩溃（大纲 §5-13）。
"""

import sys
from pathlib import Path

from loguru import logger

#: 托盘图标文件名（四档尺寸；命名映射见 文档/资产迁移清单_v1.0.md §2.2）
ICON_FILENAMES: tuple[str, ...] = (
    "zen_vocotype_client_icon_32.png",
    "zen_vocotype_client_icon_64.png",
    "zen_vocotype_client_icon_128.png",
    "zen_vocotype_client_icon_256.png",
)


def assets_dir() -> Path:
    """返回 assets 目录（双环境解析，见模块 docstring）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:  # PyInstaller 打包形态
        return Path(meipass) / "assets"
    return Path(__file__).resolve().parents[3] / "assets"


def load_tray_icon():
    """加载托盘图标（QIcon，含全部四档尺寸）。

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
