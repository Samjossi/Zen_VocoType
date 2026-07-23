"""Launcher tray 子包：托盘图标与托盘封装（PySide6 QSystemTrayIcon，T40）。

托盘定位「设置与观察的窗口」（非常驻守护）：编排成功后 Launcher 自行退出，
失败路径托盘停留防静默出错。
"""

from .tray import LauncherTray

__all__ = ["LauncherTray"]
