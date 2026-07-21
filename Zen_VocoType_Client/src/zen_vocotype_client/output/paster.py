"""Ctrl+V 粘贴模拟（选型七：pynput Controller 主用，xdotool 降级）。

降级链与阶段 0 选型四一致；降级记 warning + 通知事件（C4）。
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod

from loguru import logger


class PasteError(Exception):
    """粘贴模拟失败。"""


class PasterBackend(ABC):
    """粘贴后端抽象。"""

    @abstractmethod
    def paste(self) -> None: ...


class PynputPaster(PasterBackend):
    """pynput Controller 模拟 Ctrl+V（与热键监听同栈，零新增依赖）。"""

    def __init__(self) -> None:
        try:
            from pynput.keyboard import Controller, Key
        except Exception as exc:
            raise PasteError(f"pynput Controller 初始化失败: {exc}") from exc
        self._keyboard = Controller()
        self._key = Key

    def paste(self) -> None:
        try:
            with self._keyboard.pressed(self._key.ctrl):
                self._keyboard.press("v")
                self._keyboard.release("v")
        except Exception as exc:
            raise PasteError(f"pynput 粘贴模拟失败: {exc}") from exc
        logger.debug("Ctrl+V 粘贴已模拟（pynput）")


class XdotoolPaster(PasterBackend):
    """xdotool 降级后端（C4：降级必须记 warning）。"""

    def __init__(self) -> None:
        if shutil.which("xdotool") is None:
            raise PasteError("xdotool 不可用（pynput 与 xdotool 均缺失）")
        logger.warning("pynput 粘贴不可用，降级使用 xdotool（降级路径，C4）")

    def paste(self) -> None:
        result = subprocess.run(
            ["xdotool", "key", "ctrl+v"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise PasteError(f"xdotool 粘贴失败: {result.stderr.decode(errors='replace')}")
        logger.debug("Ctrl+V 粘贴已模拟（xdotool 降级）")


def create_paster() -> PasterBackend:
    """创建粘贴后端：pynput 主用，xdotool 降级（降级记 warning）。"""
    try:
        return PynputPaster()
    except PasteError as exc:
        logger.warning("pynput 粘贴后端创建失败（{}），尝试 xdotool 降级", exc)
        return XdotoolPaster()
