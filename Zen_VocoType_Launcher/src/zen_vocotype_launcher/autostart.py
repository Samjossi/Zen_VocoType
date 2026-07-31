"""Launcher 登录自启动管理模块（XDG Autostart，T45）。

机制：维护 ``$XDG_CONFIG_HOME/autostart/zen-vocotype.desktop``（缺省
``~/.config/autostart/``），纯文件操作，无需 root / systemd / IPC；
采用原子写入（同目录 ``*.desktop.new`` 临时文件 + rename）避免文件
写一半被桌面环境读到（同目录临时文件不触系统临时目录红线）。

命名口径（🔴 单一出处）：desktop ID ``zen-vocotype`` 与 T43 安装器
``tools/desktop_entry.py`` 的 ``DESKTOP_ID`` 同值（常量独立定义，
不跨组件 import——``tools/`` 非包内模块），卸载时安装器恒删同文件，
两处语义对称。历史遗留的下划线旧名条目由 ``LEGACY_DESKTOP_IDS``
白名单清理（白名单制，不扫描不猜测，避免误删用户自有条目）。

模块零三方依赖（仅 stdlib），不依赖 PySide6。
参考实现：``参考代码/CopyQ_python/autostart_manager.py``。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

#: desktop ID（🔴 唯一出处为 tools/desktop_entry.py 的 DESKTOP_ID，此处同值拷贝）
DEFAULT_APP_NAME = "zen-vocotype"

#: 应用展示名（与托盘 tray.APP_DISPLAY_NAME 同值；🔴 独立常量不反向 import
#: tray——本模块须保持零 PySide6 依赖，供 --version 探针等无 Qt 路径使用）
DEFAULT_APP_DISPLAY_NAME = "Zen_VocoType Launcher"

#: 历史遗留自启动条目 ID（下划线旧名，2026-07-31 实机确认生效中）。
#: 启动装配层在一致性校验前清理，保证系统内自启入口唯一
LEGACY_DESKTOP_IDS: tuple[str, ...] = ("zen_vocotype",)


class AutostartManager:
    """管理 Launcher 的登录自启动 desktop 条目。

    对外 5 方法：``is_supported`` / ``is_enabled`` / ``set_enabled`` /
    ``remove_desktop_file`` / ``remove_legacy_desktop_files``。
    """

    def __init__(
        self,
        app_name: str = DEFAULT_APP_NAME,
        app_display_name: str = DEFAULT_APP_DISPLAY_NAME,
    ) -> None:
        self.app_name = app_name
        self.app_display_name = app_display_name
        self.desktop_path = self._autostart_dir() / f"{app_name}.desktop"

    # ---------- 公共接口 ----------

    @staticmethod
    def is_supported() -> bool:
        """判断当前平台是否支持自启动管理（仅 Linux）。"""
        return sys.platform.startswith("linux")

    def is_enabled(self) -> bool:
        """判断自启动是否已启用（三态）。

        - 文件不存在 → 未启用
        - 含 ``Hidden=true`` → 未启用（保留文件仅禁用，用户自定义行不丢）
        - 含 ``Hidden=false`` 或无 ``Hidden`` 行 → 已启用
        """
        if not self.desktop_path.exists():
            return False

        hidden_re = re.compile(r"^Hidden\s*=\s*([a-zA-Z01]+)")
        try:
            text = self.desktop_path.read_text(encoding="utf-8")
        except OSError:
            return False

        for line in text.splitlines():
            m = hidden_re.match(line.strip())
            if m:
                value = m.group(1)
                return not (value.lower().startswith("true") or value == "0")

        # 无 Hidden 行且文件存在 → 默认启用（XDG 规范缺省语义）
        return True

    def set_enabled(self, enable: bool) -> bool:
        """启用或禁用自启动（原子写入）。

        已存在文件时过滤替换 ``Hidden`` / ``X-GNOME-Autostart-enabled`` /
        ``Exec`` 三行，其余用户自定义行原样保留；不存在时按模板新建。

        :param enable: ``True`` 启用，``False`` 禁用（保留文件置 Hidden=true）
        :return: 操作是否成功（OSError 返回 False，不留临时文件残留）
        """
        if self.is_enabled() == enable:
            return True

        self.desktop_path.parent.mkdir(parents=True, exist_ok=True)

        new_path = self.desktop_path.with_suffix(".desktop.new")

        replace_re = re.compile(r"^(Hidden|X-GNOME-Autostart-enabled|Exec)\s*=\s*")

        lines_to_write: list[str] = []
        if self.desktop_path.exists():
            try:
                text = self.desktop_path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            for line in text.splitlines(keepends=True):
                if not replace_re.match(line):
                    lines_to_write.append(line)
            if lines_to_write and not lines_to_write[-1].endswith("\n"):
                lines_to_write.append("\n")
        else:
            lines_to_write.append(self._default_template())

        # Exec 每次重写：指向当前运行形态（dev/onedir/AppImage 切换后纠偏）
        lines_to_write.append(f"Exec={self._get_exec_cmd()}\n")
        lines_to_write.append(f"Hidden={'false' if enable else 'true'}\n")
        lines_to_write.append(
            f"X-GNOME-Autostart-enabled={'true' if enable else 'false'}\n"
        )

        try:
            new_path.write_text("".join(lines_to_write), encoding="utf-8")
            # POSIX rename 原子覆盖同名文件
            new_path.replace(self.desktop_path)
            return True
        except OSError:
            try:
                new_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def remove_desktop_file(self) -> bool:
        """彻底删除自启动 desktop 文件（卸载提示用；幂等）。"""
        try:
            self.desktop_path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def remove_legacy_desktop_files(self) -> list[Path]:
        """清理历史遗留自启动条目（``LEGACY_DESKTOP_IDS`` 白名单）。

        逐个删除 autostart 目录下 ``<id>.desktop``；不存在则跳过，幂等。
        🔴 不影响 ``zen-vocotype.desktop`` 本体；仅 autostart 目录，
        菜单条目目录（applications/）不在范围内。

        :return: 实际删除的路径列表（供装配层记日志）
        """
        removed: list[Path] = []
        autostart_dir = self._autostart_dir()
        for legacy_id in LEGACY_DESKTOP_IDS:
            legacy_path = autostart_dir / f"{legacy_id}.desktop"
            if not legacy_path.exists():
                continue
            try:
                legacy_path.unlink()
            except OSError:
                continue
            removed.append(legacy_path)
        return removed

    # ---------- 内部方法 ----------

    @staticmethod
    def _autostart_dir() -> Path:
        """XDG autostart 目录（``$XDG_CONFIG_HOME/autostart``，缺省 ``~/.config``）。"""
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        return base / "autostart"

    def _default_template(self) -> str:
        """默认 desktop 文件模板（不含 Exec/Hidden 行，由 set_enabled 追加）。"""
        return (
            "[Desktop Entry]\n"
            f"Name={self.app_display_name}\n"
            f"Icon={self.app_name}\n"
            "GenericName=Voice Input Launcher\n"
            "Type=Application\n"
            "Terminal=false\n"
            "X-KDE-autostart-after=panel\n"
            "X-GNOME-Autostart-Delay=3\n"
        )

    def _get_exec_cmd(self) -> str:
        """生成当前运行形态的 Exec 命令（三档优先级，所有路径加双引号）。

        1. AppImage：``$APPIMAGE`` 存在且是文件 → ``"$APPIMAGE"``
        2. onedir 打包：``sys.frozen`` 冻结可执行文件 → ``"sys.executable"``
        3. 开发模式：``"sys.executable" "<组件根>/main.py"``
           （组件根按契约库 ``component_root`` 同口径由 ``__file__`` 推算，
           保持本模块零三方依赖、不 import config）
        """
        appimage_path = os.environ.get("APPIMAGE")
        if appimage_path and os.path.isfile(appimage_path):
            return f'"{appimage_path}"'

        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'

        component_root = Path(__file__).resolve().parents[2]
        return f'"{sys.executable}" "{component_root / "main.py"}"'
