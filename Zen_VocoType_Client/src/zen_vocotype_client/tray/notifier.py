"""错误提示通道（选型九：托盘 showMessage 主 + notify-send 兜底 + 声音可选）。

- 瞬时错误（识别失败、服务端未就绪、已达最大录音时长）走通知；
  持续状态走托盘图标色 + 菜单状态页（tray.py 职责）
- **同类错误 ``dedup_seconds`` 内去重**（🔴 禁止通知轰炸）
- 托盘不可用（无托盘降级模式）→ 自动切 notify-send 并记 warning（C4）
- 声音提示为可选配置（``enable_sound_notify``，默认关闭）
"""

from __future__ import annotations

import shutil
import subprocess
import time

from loguru import logger


class Notifier:
    """通知分发器：托盘主通道 + notify-send 兜底 + 去重。"""

    def __init__(
        self,
        tray=None,
        *,
        dedup_seconds: float = 5.0,
        enable_sound: bool = False,
    ) -> None:
        """
        :param tray: ``ClientTray`` 实例；None 表示无托盘降级模式
        """
        self._tray = tray
        self._dedup_seconds = dedup_seconds
        self._enable_sound = enable_sound
        self._last_sent: dict[str, float] = {}
        self._notify_send_available = shutil.which("notify-send") is not None
        if tray is None:
            logger.warning("托盘不可用，通知通道降级为 notify-send（C4）")
            if not self._notify_send_available:
                logger.warning("notify-send 亦不可用，通知仅能记日志（降级路径，C4）")

    def notify(self, title: str, message: str, *, key: str | None = None) -> bool:
        """发出瞬时通知；同类在去重窗口内抑制。

        :param key: 去重键（默认 title+message；同类错误传同一 key）
        :returns: True=已发出，False=被去重抑制
        """
        dedup_key = key or f"{title}|{message}"
        now = time.monotonic()
        last = self._last_sent.get(dedup_key)
        if last is not None and now - last < self._dedup_seconds:
            logger.debug("通知去重抑制（窗口 {}s）：{}", self._dedup_seconds, dedup_key)
            return False
        self._last_sent[dedup_key] = now

        if self._tray is not None:
            self._tray.notify(title, message)
        elif self._notify_send_available:
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            logger.warning("无通知通道可用，通知仅落日志：{} — {}", title, message)
        logger.info("通知：{} — {}", title, message)

        if self._enable_sound:
            self._beep()
        return True

    @staticmethod
    def _beep() -> None:
        """声音辅助提示（Qt beep；失败仅记日志，🔴 禁止声音失败影响主流程）。"""
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.beep()
        except Exception as exc:
            logger.warning("声音提示失败：{}", exc)
