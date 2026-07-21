"""客户端托盘（PySide6 QSystemTrayIcon）。

菜单结构（v1 交互仅托盘菜单 + 通知，大纲边界）：

- 首行版本项（禁用态展示）：``Zen_VocoType v<版本>``——与旧 GridChat 托盘明确区分
- 状态行（禁用态展示）：当前服务/状态机状态文本
- 手动重试连接（服务端断连时用户主动触发，选型二：禁止后台无限重试）
- 退出

状态色（选型九）：灰=服务端未连接 / 绿=就绪 / 红=错误 / 蓝=录音中 / 青=识别中，
以基础图标右下角叠加色点实现（不重绘整套图标资产）。
"""

from __future__ import annotations

import enum

from loguru import logger
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import __version__
from .icon_loader import load_tray_icon

#: 应用展示名（版本项与通知标题共用，单一出处）
APP_DISPLAY_NAME = "Zen_VocoType"


class TrayStatus(enum.Enum):
    """托盘持续状态（选型九：持续状态走图标色 + 菜单状态页）。"""

    DISCONNECTED = ("服务端未连接", QColor(0x88, 0x88, 0x88))  # 灰
    CONNECTING = ("连接中…", QColor(0xE6, 0xA2, 0x3C))  # 橙
    READY = ("就绪", QColor(0x3C, 0xA5, 0x55))  # 绿
    RECORDING = ("录音中…", QColor(0x33, 0x66, 0xCC))  # 蓝
    TRANSCRIBING = ("识别中…", QColor(0x2A, 0xA8, 0xA8))  # 青
    ERROR = ("错误", QColor(0xCC, 0x33, 0x33))  # 红

    def __init__(self, label: str, color: QColor) -> None:
        self.label = label
        self.color = color


#: 状态色点直径占图标边长比例（右下角叠加）
_STATUS_DOT_RATIO = 0.35


def status_icon(base: QIcon, status: TrayStatus, size: int = 64) -> QIcon:
    """在基础图标右下角叠加状态色点，返回新图标。"""
    pixmap = base.pixmap(size, size)
    if pixmap.isNull():  # 基础图标缺失（降级）：以纯色底生成，保证色点仍可见
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    d = int(size * _STATUS_DOT_RATIO)
    margin = max(1, int(size * 0.04))
    painter.setBrush(status.color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(size - d - margin, size - d - margin, d, d)
    painter.end()
    return QIcon(pixmap)


class ClientTray(QObject):
    """托盘封装：菜单 + 状态色 + 通知（通知去重逻辑在 notifier 模块，T2.7 接入）。"""

    #: 用户点击「手动重试连接」（装配层接网络 worker）
    retry_requested = Signal()
    #: 用户点击「退出」
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._base_icon: QIcon = load_tray_icon()
        self._status = TrayStatus.DISCONNECTED

        self._tray = QSystemTrayIcon(self)
        self._menu = QMenu()

        # 版本项（禁用态展示，🔴 必须首行——与旧 GridChat 托盘明确区分）
        self._version_action = self._menu.addAction(
            f"{APP_DISPLAY_NAME} v{__version__}"
        )
        self._version_action.setEnabled(False)
        self._menu.addSeparator()

        # 状态行（禁用态展示，持续状态的菜单状态页）
        self._status_action = self._menu.addAction("")
        self._status_action.setEnabled(False)
        self._menu.addSeparator()

        self._retry_action = self._menu.addAction("重试连接服务端")
        self._retry_action.triggered.connect(self.retry_requested)
        self._quit_action = self._menu.addAction("退出")
        self._quit_action.triggered.connect(self.quit_requested)

        self._tray.setContextMenu(self._menu)
        self.set_status(TrayStatus.DISCONNECTED)

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def show(self) -> None:
        self._tray.show()

    def set_status(self, status: TrayStatus, detail: str = "") -> None:
        """更新状态色与菜单状态行（主线程调用）。"""
        self._status = status
        self._tray.setIcon(status_icon(self._base_icon, status))
        text = f"状态：{status.label}" + (f"（{detail}）" if detail else "")
        self._status_action.setText(text)
        self._tray.setToolTip(f"{APP_DISPLAY_NAME} — {status.label}")
        logger.debug("托盘状态更新：{} {}", status.name, detail)

    def notify(self, title: str, message: str) -> None:
        """托盘通知（瞬时错误通道；去重由 notifier 负责，T2.7 接线）。"""
        self._tray.showMessage(title, message, self._tray.icon(), 5000)
