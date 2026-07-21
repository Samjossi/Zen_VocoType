"""剪贴板读写（选型六：Qt QClipboard 主用，xclip 降级）。

- Qt ``QClipboard`` 读写必须在 Qt 主线程（装配层经信号槽满足）
- Qt 剪贴板不可用时降级 xclip（子进程），降级记 warning + 通知事件（C4）
- 文本域备份/恢复（与旧版语义一致；图像等富 MIME 内容 v1 不备份，
  README 故障排查节声明）
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod

from loguru import logger


class ClipboardError(Exception):
    """剪贴板读写失败。"""


class ClipboardBackend(ABC):
    """剪贴板后端抽象（Qt 主用 / xclip 降级）。"""

    @abstractmethod
    def read_text(self) -> str: ...

    @abstractmethod
    def write_text(self, text: str) -> None: ...


class QtClipboard(ClipboardBackend):
    """Qt QClipboard 后端（主线程使用）。"""

    def __init__(self) -> None:
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            raise ClipboardError("Qt 剪贴板不可用（QGuiApplication 未就绪）")
        self._clipboard = clipboard

    def read_text(self) -> str:
        return self._clipboard.text()

    def write_text(self, text: str) -> None:
        self._clipboard.setText(text)


class XclipClipboard(ClipboardBackend):
    """xclip 子进程降级后端（极端环境；C4：降级必须记 warning）。"""

    def __init__(self) -> None:
        if shutil.which("xclip") is None:
            raise ClipboardError("xclip 不可用（Qt 剪贴板与 xclip 均缺失）")
        logger.warning("Qt 剪贴板不可用，降级使用 xclip（降级路径，C4）")

    def read_text(self) -> str:
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-out"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise ClipboardError(f"xclip 读取失败: {result.stderr.decode(errors='replace')}")
        return result.stdout.decode("utf-8", errors="replace")

    def write_text(self, text: str) -> None:
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-in"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise ClipboardError(f"xclip 写入失败: {result.stderr.decode(errors='replace')}")


def create_clipboard() -> ClipboardBackend:
    """创建剪贴板后端：Qt 主用，xclip 降级（降级记 warning）。"""
    try:
        return QtClipboard()
    except ClipboardError as exc:
        logger.warning("Qt 剪贴板创建失败（{}），尝试 xclip 降级", exc)
        return XclipClipboard()
