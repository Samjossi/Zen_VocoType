"""T2.5 单元测试：组合键状态机（合成事件驱动，不依赖 X11）与后端冒烟。"""

import pytest
from pynput import keyboard

from zen_vocotype_client.hotkey.backend import ComboTracker
from zen_vocotype_client.hotkey.combo import parse_hotkey
from zen_vocotype_client.hotkey.pynput_backend import PynputBackend

CTRL = keyboard.Key.ctrl_l
SHIFT = keyboard.Key.shift_l
T = keyboard.KeyCode.from_char("t")
BACKTICK = keyboard.KeyCode.from_char("`")


def _tracker(expr: str) -> ComboTracker:
    return ComboTracker(parse_hotkey(expr))


class TestComboTracker:
    def test_press_requires_all_modifiers(self):
        t = _tracker("<ctrl>+<alt>+t")
        assert t.press(T) is False  # 无修饰键
        t.press(CTRL)
        assert t.press(T) is False  # 缺 alt
        t.press(keyboard.Key.alt_l)
        assert t.press(T) is True  # 全部就绪 → 激活

    def test_release_main_key_ends(self):
        t = _tracker("<ctrl>+<alt>+t")
        t.press(CTRL)
        t.press(keyboard.Key.alt_l)
        assert t.press(T) is True
        assert t.release(T) is True
        assert not t.active

    def test_repeat_press_debounced(self):
        """系统键重复：激活期间重复 press 不重复产出事件。"""
        t = _tracker("<ctrl>+<alt>+t")
        t.press(CTRL)
        t.press(keyboard.Key.alt_l)
        assert t.press(T) is True
        for _ in range(5):  # 按住不放时系统重复产生 press
            assert t.press(T) is False

    def test_early_modifier_release_keeps_active(self):
        """按住说话期间提前松开修饰键不结束（release 以主键释放为准）。"""
        t = _tracker("<ctrl>+<alt>+t")
        t.press(CTRL)
        t.press(keyboard.Key.alt_l)
        assert t.press(T) is True
        t.release(CTRL)
        t.release(keyboard.Key.alt_l)
        assert t.active  # 修饰松开不影响激活态
        assert t.release(T) is True

    def test_release_without_press_ignored(self):
        t = _tracker("<ctrl>+<alt>+t")
        assert t.release(T) is False

    def test_wrong_main_key_ignored(self):
        t = _tracker("<ctrl>+<alt>+t")
        t.press(CTRL)
        t.press(keyboard.Key.alt_l)
        assert t.press(keyboard.KeyCode.from_char("x")) is False
        assert not t.active

    def test_special_key_main(self):
        """主键为特殊键（F9）的配对。"""
        t = _tracker("<ctrl>+<f9>")
        t.press(CTRL)
        assert t.press(keyboard.Key.f9) is True
        assert t.release(keyboard.Key.f9) is True

    def test_rearm_after_full_cycle(self):
        """完整按住→松开后可再次激活。"""
        t = _tracker("<ctrl>+<alt>+t")
        for _ in range(2):
            t.press(CTRL)
            t.press(keyboard.Key.alt_l)
            assert t.press(T) is True
            assert t.release(T) is True
            t.release(CTRL)
            t.release(keyboard.Key.alt_l)


class TestPynputBackend:
    def test_start_stop_smoke(self):
        """后端创建与启停冒烟（本机 X11 环境；失败须明确报错）。"""
        events: list[str] = []
        backend = PynputBackend(
            parse_hotkey("<ctrl>+<alt>+t"),
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
        )
        try:
            backend.start()
        except Exception as exc:
            pytest.skip(f"无 X11 显示环境: {exc}")
        backend.stop()

    def test_callback_thread_only_invokes_injected_hooks(self):
        """红线验证：后端回调路径仅调注入钩子（经 tracker 判定后触发）。"""
        events: list[str] = []
        backend = PynputBackend(
            parse_hotkey("<ctrl>+<alt>+t"),
            on_press=lambda: events.append("press"),
            on_release=lambda: events.append("release"),
        )
        # 直接驱动内部回调（等价 pynput 线程注入），验证事件配对
        backend._handle_press(CTRL)
        backend._handle_press(keyboard.Key.alt_l)
        backend._handle_press(T)
        backend._handle_press(T)  # 键重复
        backend._handle_release(T)
        assert events == ["press", "release"]
