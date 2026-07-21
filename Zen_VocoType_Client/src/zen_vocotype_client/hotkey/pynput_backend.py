"""pynput 热键后端（选型三：keyboard.Listener + 自维护组合键状态机）。

- press/release 原始事件齐全，实现真正按住/松开语义
- 事件经 :class:`~.backend.ComboTracker` 去抖与配对后产出
- 🔴 红线（选型一）：pynput 回调线程内仅调用注入的回调（装配层注册
  Qt Signal.emit），禁止直接触碰业务状态
- 后端创建失败（如无 X11 显示）→ 启动即抛明确异常（C4）
"""

from __future__ import annotations

from loguru import logger
from pynput import keyboard

from .backend import ComboTracker, HotkeyBackend


class HotkeyBackendError(Exception):
    """热键后端启动/运行失败（🔴 禁止静默降级为无热键空跑）。"""


class PynputBackend(HotkeyBackend):
    """X11 环境的 pynput 监听后端。"""

    def __init__(self, combo, on_press, on_release) -> None:
        super().__init__(combo, on_press, on_release)
        self._tracker = ComboTracker(combo)
        self._listener: keyboard.Listener | None = None

    def start(self) -> None:
        try:
            self._listener = keyboard.Listener(
                on_press=self._handle_press,
                on_release=self._handle_release,
            )
            self._listener.start()
        except Exception as exc:
            self._listener = None
            raise HotkeyBackendError(
                f"pynput 热键监听启动失败（需要 X11 显示环境）: {exc}"
            ) from exc
        logger.info("热键监听已启动：{}（pynput 后端）", self._combo.expression)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.debug("热键监听已停止")

    # ------------------------------------------------------------------ 回调
    # 🔴 pynput 回调线程：仅经 ComboTracker 判定后调用注入回调，零业务逻辑

    def _handle_press(self, key) -> None:
        try:
            if self._tracker.press(key):
                self._on_press()
        except Exception:  # 回调线程异常不得炸掉监听线程
            logger.exception("热键 press 事件处理异常")

    def _handle_release(self, key) -> None:
        try:
            if self._tracker.release(key):
                self._on_release()
        except Exception:
            logger.exception("热键 release 事件处理异常")
