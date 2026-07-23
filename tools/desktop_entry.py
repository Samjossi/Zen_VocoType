""".desktop 桌面入口安装/卸载共享逻辑（选型五方案 A）。

- 模板 ``tools/desktop/zen-vocotype.desktop.template`` 的 ``@EXEC@`` 占位符
  按实际摆放路径渲染（🔴 仓库内零绝对路径硬编码——旧 grid_chat.desktop
  脱节教训）
- 图标从 Launcher.AppImage 提取（``--appimage-extract``，hicolor 四档），
  安装至 ``$XDG_DATA_HOME/icons/hicolor/<size>/apps/zen-vocotype.png``
- 纯用户态（🔴 无需 root）；幂等（重复执行结果一致）
- T43：``--autostart`` 追加 GNOME 自启动条目（``$XDG_CONFIG_HOME/autostart/``）
  ——自启动走桌面环境机制（图形会话就绪后执行，DISPLAY 天然齐备）；
  🔴 禁止改用/加回 systemd 用户服务：2026-07-23 实机事故，systemd 服务
  早于图形会话环境注入启动，Launcher 无 DISPLAY 回退 CLI、托盘全灭
  （见 work plans 两份 2026-07-23 诊断报告）
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

#: 桌面条目 ID（.desktop 文件名与图标名族）
DESKTOP_ID: str = "zen-vocotype"

#: AppImage 内 hicolor 图标名（AppDir 打包约定，见 tools/appimage.py）
_LAUNCHER_ICON_NAME: str = "zen_vocotype_launcher"

ICON_SIZES: tuple[int, ...] = (32, 64, 128, 256)

_TEMPLATE_PATH: Path = PROJECT_ROOT / "tools" / "desktop" / "zen-vocotype.desktop.template"


def _xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def _xdg_config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


def desktop_file_path() -> Path:
    return _xdg_data_home() / "applications" / f"{DESKTOP_ID}.desktop"


def autostart_file_path() -> Path:
    """GNOME 自启动条目路径（T43；遵循 XDG_CONFIG_HOME）。"""
    return _xdg_config_home() / "autostart" / f"{DESKTOP_ID}.desktop"


def icon_path(size: int) -> Path:
    return (
        _xdg_data_home()
        / "icons"
        / "hicolor"
        / f"{size}x{size}"
        / "apps"
        / f"{DESKTOP_ID}.png"
    )


def render_desktop(exec_path: Path, *, autostart: bool = False) -> str:
    """渲染 .desktop 内容（Exec= 按实际摆放路径；含空格/特殊字符按规范加引号）。

    :param autostart: True 时追加 ``X-GNOME-Autostart-enabled=true``（T43；
        🔴 与菜单条目共享同一模板渲染，禁止复制模板防双源脱节）
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    exec_str = str(exec_path)
    if any(c in exec_str for c in " \t\"'\\"):
        exec_str = '"' + exec_str.replace('"', '\\"') + '"'
    content = template.replace("@EXEC@", exec_str)
    if autostart:
        if not content.endswith("\n"):
            content += "\n"
        content += "X-GNOME-Autostart-enabled=true\n"
    return content


def _extract_icons(launcher_appimage: Path, staging: Path) -> dict[int, Path]:
    """从 Launcher.AppImage 提取 hicolor 四档图标到暂存目录。"""
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    subprocess.run(
        [str(launcher_appimage), "--appimage-extract"],
        check=True,
        cwd=staging,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    root = staging / "squashfs-root"
    icons: dict[int, Path] = {}
    for size in ICON_SIZES:
        src = (
            root
            / "usr/share/icons/hicolor"
            / f"{size}x{size}"
            / "apps"
            / f"{_LAUNCHER_ICON_NAME}.png"
        )
        if not src.is_file():
            raise RuntimeError(f"AppImage 内 hicolor 图标缺失：{src}")
        icons[size] = src
    return icons


def install(app_dir: Path, staging_root: Path, *, autostart: bool = False) -> list[Path]:
    """安装桌面入口与图标，返回写入的文件清单（幂等）。

    :param app_dir: 三 AppImage 摆放目录（须含 Zen_VocoType_Launcher.AppImage）
    :param staging_root: 提取暂存根（🔴 项目内/用户指定目录，禁系统临时目录）
    :param autostart: T43：同时安装 GNOME 自启动条目
        （``$XDG_CONFIG_HOME/autostart/zen-vocotype.desktop``）
    """
    launcher = app_dir / "Zen_VocoType_Launcher.AppImage"
    if not launcher.is_file():
        raise RuntimeError(f"Launcher.AppImage 缺失：{launcher}")

    written: list[Path] = []

    desktop_dst = desktop_file_path()
    desktop_dst.parent.mkdir(parents=True, exist_ok=True)
    desktop_dst.write_text(render_desktop(launcher.resolve()), encoding="utf-8")
    written.append(desktop_dst)

    if autostart:
        autostart_dst = autostart_file_path()
        autostart_dst.parent.mkdir(parents=True, exist_ok=True)
        autostart_dst.write_text(
            render_desktop(launcher.resolve(), autostart=True), encoding="utf-8"
        )
        written.append(autostart_dst)

    staging = staging_root / "icon_extract"
    icons = _extract_icons(launcher, staging)
    for size, src in icons.items():
        dst = icon_path(size)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(dst)
    shutil.rmtree(staging)

    # 桌面数据库/图标缓存刷新（工具缺席容错——不影响安装本体）
    for cmd in (
        ["update-desktop-database", str(desktop_dst.parent)],
        ["gtk-update-icon-cache", "-q", str(_xdg_data_home() / "icons" / "hicolor")],
    ):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, check=False, capture_output=True)
    return written


def uninstall() -> list[Path]:
    """卸载桌面入口与图标，返回删除的文件清单（幂等：缺席不报错）。

    目标清单恒含 autostart 条目（T43）——卸载对称且与安装期是否启用
    ``--autostart`` 解耦。
    """
    removed: list[Path] = []
    targets = [
        desktop_file_path(),
        autostart_file_path(),
        *(icon_path(s) for s in ICON_SIZES),
    ]
    for path in targets:
        if path.exists():
            path.unlink()
            removed.append(path)
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(desktop_file_path().parent)],
            check=False,
            capture_output=True,
        )
    return removed


__all__ = ["install", "render_desktop", "uninstall"]
