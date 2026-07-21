"""T2.6 单元测试：输出时序、指纹比对恢复、降级路径。"""

import pytest

from zen_vocotype_client.output.clipboard import ClipboardBackend
from zen_vocotype_client.output.paster import PasterBackend
from zen_vocotype_client.output.restore import OutputPipeline


class FakeClipboard(ClipboardBackend):
    """内存剪贴板替身（可模拟用户中途复制）。"""

    def __init__(self, initial: str = "") -> None:
        self.content = initial
        self.writes: list[str] = []

    def read_text(self) -> str:
        return self.content

    def write_text(self, text: str) -> None:
        self.content = text
        self.writes.append(text)


class FakePaster(PasterBackend):
    def __init__(self) -> None:
        self.count = 0

    def paste(self) -> None:
        self.count += 1


class ImmediateScheduler:
    """测试调度替身：记录延迟并立即执行（验证零裸 sleep 之外的调度注入）。"""

    def __init__(self) -> None:
        self.delays: list[int] = []

    def __call__(self, delay_ms, callback) -> None:
        self.delays.append(delay_ms)
        callback()


def _pipeline(initial: str = "用户原文"):
    clipboard = FakeClipboard(initial)
    paster = FakePaster()
    scheduler = ImmediateScheduler()
    events: list[str] = []
    pipe = OutputPipeline(
        clipboard,
        paster,
        restore_delay_ms=500,
        scheduler=scheduler,
        on_restored=lambda: events.append("restored"),
        on_restore_skipped=lambda: events.append("skipped"),
    )
    return pipe, clipboard, paster, scheduler, events


class TestOutputSequence:
    def test_full_sequence_backup_write_paste_restore(self):
        """完整时序：备份→写入→粘贴→调度恢复→原内容恢复。"""
        pipe, clipboard, paster, scheduler, events = _pipeline("用户原文")
        pipe.output("识别文本")
        assert paster.count == 1
        assert scheduler.delays == [500]  # 延迟经注入调度器，非裸 sleep
        assert clipboard.content == "用户原文"  # 指纹一致 → 已恢复
        assert events == ["restored"]
        assert clipboard.writes == ["识别文本", "用户原文"]

    def test_restore_skipped_when_user_copied(self):
        """延迟窗口内用户复制新内容 → 放弃恢复（竞态根除核心）。"""
        captured = []
        clipboard = FakeClipboard("用户原文")
        pipe = OutputPipeline(
            clipboard,
            FakePaster(),
            restore_delay_ms=500,
            scheduler=lambda ms, cb: captured.append(cb),  # 挂起不执行
        )
        pipe.output("识别文本")
        clipboard.write_text("用户新复制的内容")  # 模拟用户中途复制
        captured[0]()  # 到点执行恢复校验
        assert clipboard.content == "用户新复制的内容"  # 用户内容未被覆盖

    def test_restore_skipped_on_third_party_changed(self):
        """dataChanged 提前标记 → 到点直接跳过恢复。"""
        captured = []
        clipboard = FakeClipboard("用户原文")
        events: list[str] = []
        pipe = OutputPipeline(
            clipboard,
            FakePaster(),
            restore_delay_ms=500,
            scheduler=lambda ms, cb: captured.append(cb),
            on_restore_skipped=lambda: events.append("skipped"),
        )
        pipe.output("识别文本")
        pipe.mark_third_party_changed()  # Qt dataChanged 钩子
        captured[0]()
        assert events == ["skipped"]
        assert clipboard.content == "识别文本"  # 未恢复（内容已被标记替换）

    def test_empty_backup_restored_as_empty(self):
        pipe, clipboard, *_ = _pipeline("")
        pipe.output("识别文本")
        assert clipboard.content == ""


class TestBackends:
    def test_qt_clipboard_smoke(self):
        """Qt 剪贴板后端读写冒烟（当前桌面会话）。"""
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from zen_vocotype_client.output.clipboard import QtClipboard

        cb = QtClipboard()
        original = cb.read_text()
        try:
            cb.write_text("zen_vocotype_测试写入")
            assert cb.read_text() == "zen_vocotype_测试写入"
        finally:
            cb.write_text(original)  # 还原用户剪贴板

    def test_pynput_paster_constructible(self):
        from zen_vocotype_client.output.paster import PynputPaster

        assert PynputPaster() is not None
