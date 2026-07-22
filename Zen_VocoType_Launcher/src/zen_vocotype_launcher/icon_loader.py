"""通知图标加载（开发与打包环境均须可用）。

路径解析双环境（大纲 §5-13 / §3.5 约束 3）：

- 源码布局：基于本文件自身位置推算组件根（``src/<pkg>/icon_loader.py``
  → 上两级为组件根），取 ``<组件根>/assets/``
- PyInstaller 打包：``sys._MEIPASS`` 指向打包内嵌数据目录，取 ``_MEIPASS/assets/``

🔴 禁止相对 cwd——旧 Launcher ``asset/star_64.png`` 相对路径在 AppImage 中
静默失效为反面案例。图标缺失记 warning 不崩溃（大纲 §5-13）。
"""

import sys
from pathlib import Path

from loguru import logger

#: 通知图标文件名（四档尺寸；命名映射见 文档/资产迁移清单_v1.0.md §2.3）
ICON_FILENAMES: dict[int, str] = {
    32: "zen_vocotype_launcher_icon_32.png",
    64: "zen_vocotype_launcher_icon_64.png",
    128: "zen_vocotype_launcher_icon_128.png",
    256: "zen_vocotype_launcher_icon_256.png",
}

#: notify-send 默认使用的图标尺寸（依据：桌面通知常用显示尺寸中档，
#: 过小模糊、过大部分通知服务会二次缩放）
DEFAULT_ICON_SIZE: int = 64


def assets_dir() -> Path:
    """返回 assets 目录（双环境解析，见模块 docstring）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:  # PyInstaller 打包形态
        return Path(meipass) / "assets"
    return Path(__file__).resolve().parents[2] / "assets"


def icon_path(size: int = DEFAULT_ICON_SIZE) -> Path | None:
    """返回指定尺寸图标文件路径；缺失时记 warning 并返回 ``None``（不崩溃）。

    :param size: 图标尺寸档位（32/64/128/256）
    """
    name = ICON_FILENAMES.get(size)
    if name is None:
        logger.warning("未知图标尺寸档位 {}，回退默认档 {}", size, DEFAULT_ICON_SIZE)
        name = ICON_FILENAMES[DEFAULT_ICON_SIZE]
    path = assets_dir() / name
    if not path.is_file():
        logger.warning("通知图标缺失：{}（路径解析基准：{}）", path, assets_dir())
        return None
    return path
