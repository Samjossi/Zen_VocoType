"""Launcher 托盘（PySide6 QSystemTrayIcon，T40）。

菜单结构（自上而下）：

- ① 版本项（禁用态展示，🔴 必须首行）：``Zen_VocoType Launcher v<版本>``
- ② 状态行（禁用态展示）：两端运行状态（装配层组串注入）
- ③ 进度行（禁用态展示）：编排阶段文本 / 倒计时（「将于 N 秒后启动服务端」）
- ④ 立即启动 / 重新检测状态
- ⑤ 三个延迟设置项（当前值编入文本）：服务端启动延迟 / 客户端启动间隔 /
  成功后自动退出
- ⑥ 组件位置设置项 + 恢复自动解析子项
- ⑦ 退出启动器（不影响已启动组件）

边界（与 Service/Client 托盘同范式）：

- 🔴 托盘零业务逻辑——全部动作经 Signal 外抛，编排/持久化在装配层
- 🔴 action 引用全部持久持有（PySide6 GC 陷阱教训：临时包装被 GC 会连带
  销毁 C++ 侧对象）；测试不经 ``contextMenu().actions()`` 查找
- 图标复用 ``icon_loader``（双环境解析、缺失降级不崩溃）
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from zen_vocotype_launcher import icon_loader
from zen_vocotype_launcher.version import LAUNCHER_VERSION

#: 应用展示名（版本项与 tooltip 共用，单一出处）
APP_DISPLAY_NAME = "Zen_VocoType Launcher"


def _load_icon() -> QIcon:
    """加载托盘图标（四档尺寸，缺失降级为空 QIcon，不崩溃）。"""
    icon = QIcon()
    for size in (32, 64, 128, 256):
        path = icon_loader.icon_path(size)
        if path is not None:
            icon.addFile(str(path))
    return icon


class LauncherTray(QObject):
    """Launcher 托盘封装：菜单 + 状态/进度刷新 + 设置入口信号外抛。"""

    #: 用户点击「立即启动」（编排进行中由装配层置灰拦截）
    start_requested = Signal()
    #: 用户点击「重新检测状态」
    refresh_requested = Signal()
    #: 三个延迟设置项
    service_delay_change_requested = Signal()
    client_interval_change_requested = Signal()
    auto_exit_change_requested = Signal()
    #: 组件位置设置 / 恢复自动解析
    service_binary_change_requested = Signal()
    service_binary_reset_requested = Signal()
    client_binary_change_requested = Signal()
    client_binary_reset_requested = Signal()
    #: 用户点击「退出启动器」（🔴 不终止两端，装配层仅退出 Launcher 自身）
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_load_icon())
        self._tray.setToolTip(APP_DISPLAY_NAME)
        self._menu = QMenu()

        # ① 版本项（禁用态展示，🔴 必须首行）
        self._title_action = self._menu.addAction(
            f"{APP_DISPLAY_NAME} v{LAUNCHER_VERSION}"
        )
        self._title_action.setEnabled(False)

        # ② 状态行 / ③ 进度行（禁用态展示，装配层注入文本）
        self._status_action = self._menu.addAction("状态：检测中…")
        self._status_action.setEnabled(False)
        self._progress_action = self._menu.addAction("")
        self._progress_action.setEnabled(False)
        self._progress_action.setVisible(False)
        self._menu.addSeparator()

        # ④ 立即启动 / 重新检测状态
        self._start_action = self._menu.addAction("立即启动")
        self._start_action.triggered.connect(self.start_requested)
        self._refresh_action = self._menu.addAction("重新检测状态")
        self._refresh_action.triggered.connect(self.refresh_requested)
        self._menu.addSeparator()

        # ⑤ 三个延迟设置项（当前值编入文本，占位由装配层注入实值）
        self._service_delay_action = self._menu.addAction("服务端启动延迟（—）…")
        self._service_delay_action.triggered.connect(
            self.service_delay_change_requested
        )
        self._client_interval_action = self._menu.addAction("客户端启动间隔（—）…")
        self._client_interval_action.triggered.connect(
            self.client_interval_change_requested
        )
        self._auto_exit_action = self._menu.addAction("成功后自动退出（—）…")
        self._auto_exit_action.triggered.connect(self.auto_exit_change_requested)
        self._menu.addSeparator()

        # ⑥ 组件位置设置 + 恢复自动解析
        self._service_binary_action = self._menu.addAction("Service 位置：—…")
        self._service_binary_action.triggered.connect(
            self.service_binary_change_requested
        )
        self._service_reset_action = self._menu.addAction("恢复 Service 自动解析")
        self._service_reset_action.triggered.connect(
            self.service_binary_reset_requested
        )
        self._client_binary_action = self._menu.addAction("Client 位置：—…")
        self._client_binary_action.triggered.connect(
            self.client_binary_change_requested
        )
        self._client_reset_action = self._menu.addAction("恢复 Client 自动解析")
        self._client_reset_action.triggered.connect(
            self.client_binary_reset_requested
        )
        self._menu.addSeparator()

        # ⑦ 退出启动器（🔴 不终止两端——选型七红线，文案明示）
        self._quit_action = self._menu.addAction("退出启动器（不影响已启动组件）")
        self._quit_action.triggered.connect(self.quit_requested)

        self._tray.setContextMenu(self._menu)

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def show(self) -> None:
        self._tray.show()

    # ------------------------------------------------------------------
    # 状态 / 进度刷新（装配层注入，托盘零业务逻辑）
    # ------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        """刷新状态行（如「Service：●运行中   Client：○未启动」）。"""
        self._status_action.setText(text)

    def set_progress(self, text: str | None) -> None:
        """刷新进度行（阶段文本/倒计时）；None 或空串隐藏该行。"""
        if text:
            self._progress_action.setText(text)
            self._progress_action.setVisible(True)
        else:
            self._progress_action.setVisible(False)

    def set_busy(self, busy: bool) -> None:
        """编排进行中置灰「立即启动」（防重入；🔴 不打断进行中编排）。"""
        self._start_action.setEnabled(not busy)

    # ------------------------------------------------------------------
    # 设置项标签刷新（当前值编入文本，随切换刷新）
    # ------------------------------------------------------------------

    def set_service_delay_label(self, seconds: float) -> None:
        self._service_delay_action.setText(f"服务端启动延迟（{_fmt(seconds)}）…")

    def set_client_interval_label(self, seconds: float) -> None:
        self._client_interval_action.setText(f"客户端启动间隔（{_fmt(seconds)}）…")

    def set_auto_exit_label(self, seconds: float) -> None:
        self._auto_exit_action.setText(f"成功后自动退出（{_fmt(seconds)}）…")

    def set_service_binary_label(self, path: str | None) -> None:
        self._service_binary_action.setText(
            f"Service 位置：{_binary_label(path)}…"
        )
        self._service_reset_action.setEnabled(path is not None)

    def set_client_binary_label(self, path: str | None) -> None:
        self._client_binary_action.setText(
            f"Client 位置：{_binary_label(path)}…"
        )
        self._client_reset_action.setEnabled(path is not None)


def _fmt(seconds: float) -> str:
    """秒数展示格式化（0.0 →「0 秒」，2.5 →「2.5 秒」）。"""
    return f"{int(seconds)} 秒" if float(seconds).is_integer() else f"{seconds} 秒"


def _binary_label(path: str | None) -> str:
    """位置项文本：未设置显式标注自动解析，已设置显示文件名。"""
    if path is None:
        return "未设置（自动）"
    from pathlib import Path

    return Path(path).name
