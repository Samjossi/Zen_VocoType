"""剪贴板恢复竞态根除（选型八，C3：旧版固定 sleep 0.5s 竞态的重新设计）。

完整时序::

    备份原剪贴板 → 写入识别文本并记录指纹（文本哈希 + 写入时间戳）
    → 模拟 Ctrl+V → 延迟 paste_restore_delay_ms（命名常量 + 可配置）
    → 恢复前比对指纹：一致则恢复原备份；不一致（用户已复制新内容）
      则放弃恢复并记日志

🔴 全链路除 ``paste_restore_delay_ms`` 外零固定 sleep（C2）；
延迟的调度由装配层注入（主线程 ``QTimer.singleShot``），本模块保持可单测。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from .clipboard import ClipboardBackend, ClipboardError
from .paster import PasteError, PasterBackend


@dataclass(frozen=True)
class Fingerprint:
    """写入剪贴板时的指纹：文本哈希 + 写入时间戳。"""

    text_hash: str
    written_at: float


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: 调度器签名：延迟毫秒数 + 回调（主线程 QTimer.singleShot 或测试替身）
Scheduler = Callable[[int, Callable[[], None]], None]


class OutputPipeline:
    """文字输出编排：写入 → 粘贴 → 校验后恢复。"""

    def __init__(
        self,
        clipboard: ClipboardBackend,
        paster: PasterBackend,
        *,
        restore_delay_ms: int,
        scheduler: Scheduler,
        on_restored: Callable[[], None] | None = None,
        on_restore_skipped: Callable[[], None] | None = None,
    ) -> None:
        self._clipboard = clipboard
        self._paster = paster
        self._restore_delay_ms = restore_delay_ms
        self._scheduler = scheduler
        self._on_restored = on_restored
        self._on_restore_skipped = on_restore_skipped
        # dataChanged 辅助：第三方在延迟窗口内替换内容时提前标记（等价于
        # 恢复时比对失败的早退优化，恢复时比对仍为最终裁决）
        self._third_party_changed = False

    def mark_third_party_changed(self) -> None:
        """Qt ``dataChanged`` 信号钩子：内容已被第三方替换（装配层接线）。"""
        self._third_party_changed = True

    def set_restore_delay_ms(self, ms: int) -> None:
        """运行态更新恢复延迟（托盘设置项热切换用，T35）；仅影响后续 ``output()`` 调度。

        已排程的恢复任务按排程时的旧延迟完成（期望语义，不追溯）。
        """
        if ms < 0:
            raise ValueError(f"恢复延迟非法：{ms}")
        self._restore_delay_ms = ms

    def output(self, text: str) -> None:
        """执行输出时序。

        :raises ClipboardError/PasteError: 写入或粘贴失败（装配层转错误提示）
        """
        backup = self._clipboard.read_text()
        self._clipboard.write_text(text)
        fingerprint = Fingerprint(text_hash=_hash_text(text), written_at=time.monotonic())
        self._third_party_changed = False
        self._paster.paste()
        logger.info(
            "识别文本已写入剪贴板并模拟粘贴（{} 字符），{}ms 后校验恢复",
            len(text),
            self._restore_delay_ms,
        )
        self._scheduler(
            self._restore_delay_ms,
            lambda: self._restore(backup, fingerprint),
        )

    def _restore(self, backup: str, fingerprint: Fingerprint) -> None:
        """恢复前指纹比对：一致才恢复，不一致放弃（竞态根除核心）。"""
        if self._third_party_changed:
            logger.info("剪贴板已被第三方替换（dataChanged），提前跳过恢复")
            self._notify_skipped()
            return
        try:
            current = self._clipboard.read_text()
        except ClipboardError as exc:
            logger.warning("恢复前读取剪贴板失败（{}），放弃恢复", exc)
            self._notify_skipped()
            return
        if _hash_text(current) != fingerprint.text_hash:
            logger.info("剪贴板内容已变更（用户复制了新内容），放弃恢复原备份")
            self._notify_skipped()
            return
        self._clipboard.write_text(backup)
        elapsed_ms = (time.monotonic() - fingerprint.written_at) * 1000
        logger.debug("原剪贴板内容已恢复（写入后 {:.0f}ms）", elapsed_ms)
        if self._on_restored is not None:
            self._on_restored()

    def _notify_skipped(self) -> None:
        if self._on_restore_skipped is not None:
            self._on_restore_skipped()
