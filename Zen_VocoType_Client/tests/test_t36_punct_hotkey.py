"""T36 单元测试：快捷键捕获支持标点主键（Ctrl+反引号）。

覆盖：白名单标点映射转换、parse_hotkey 全量 round-trip（固化 pynput
解析行为，防升级漂移）、ComboTracker 合成事件触发、对话框捕获流、
YAML 落盘 round-trip（反斜杠/引号转义）、白名单边界防回归。
"""

import pytest
from pynput import keyboard
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog

from zen_vocotype_client.config import Settings
from zen_vocotype_client.hotkey.backend import ComboTracker, _canonical_key
from zen_vocotype_client.hotkey.combo import format_hotkey_display, parse_hotkey
from zen_vocotype_client.tray.hotkey_dialog import (
    _QT_TO_PYNPUT,
    HotkeyCaptureDialog,
    qt_event_to_expression,
)
from zen_vocotype_protocol.user_config import (
    load_user_config,
    set_user_config_value,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_CTRL = Qt.KeyboardModifier.ControlModifier
_ALT = Qt.KeyboardModifier.AltModifier
_SHIFT = Qt.KeyboardModifier.ShiftModifier

#: T36 新增标点映射的期望清单（Qt.Key → pynput 字符段），与生产映射表
#: 互相独立书写——测试若直接遍历生产表则丧失校验意义
_PUNCT_KEYS: tuple[tuple[Qt.Key, str], ...] = (
    (Qt.Key.Key_QuoteLeft, "`"),
    (Qt.Key.Key_AsciiTilde, "~"),
    (Qt.Key.Key_Minus, "-"),
    (Qt.Key.Key_Underscore, "_"),
    (Qt.Key.Key_Equal, "="),
    (Qt.Key.Key_Plus, "+"),
    (Qt.Key.Key_BracketLeft, "["),
    (Qt.Key.Key_BracketRight, "]"),
    (Qt.Key.Key_BraceLeft, "{"),
    (Qt.Key.Key_BraceRight, "}"),
    (Qt.Key.Key_Backslash, "\\"),
    (Qt.Key.Key_Bar, "|"),
    (Qt.Key.Key_Semicolon, ";"),
    (Qt.Key.Key_Colon, ":"),
    (Qt.Key.Key_Apostrophe, "'"),
    (Qt.Key.Key_QuoteDbl, '"'),
    (Qt.Key.Key_Comma, ","),
    (Qt.Key.Key_Less, "<"),
    (Qt.Key.Key_Period, "."),
    (Qt.Key.Key_Greater, ">"),
    (Qt.Key.Key_Slash, "/"),
    (Qt.Key.Key_Question, "?"),
)


# ---------------------------------------------------------------------- 映射转换


class TestPunctMapping:
    @pytest.mark.parametrize("qt_key, char", _PUNCT_KEYS)
    def test_punct_roundtrip(self, qt_key, char):
        """22 个标点映射：转换 → parse_hotkey 解析 → 主键 char 一致。

        🔴 固化 pynput 对标点表达式（含 +、< 等语法敏感字符）的解析行为，
        未来 pynput 升级破坏解析时本用例当场报警。
        """
        expression = qt_event_to_expression(int(qt_key), _CTRL)
        assert expression == f"<ctrl>+{char}"
        combo = parse_hotkey(expression)
        assert combo.modifiers == frozenset({"ctrl"})
        assert _canonical_key(combo.key) == ("char", char.lower())

    def test_shifted_punct_with_shift_modifier(self):
        """Shift+反引号（Qt 上报移位键符 Key_AsciiTilde）→ <ctrl>+<shift>+~。"""
        assert (
            qt_event_to_expression(int(Qt.Key.Key_AsciiTilde), _CTRL | _SHIFT)
            == "<ctrl>+<shift>+~"
        )
        assert (
            qt_event_to_expression(int(Qt.Key.Key_Underscore), _CTRL | _SHIFT)
            == "<ctrl>+<shift>+_"
        )

    def test_unshifted_punct_with_other_modifier(self):
        assert qt_event_to_expression(int(Qt.Key.Key_Slash), _ALT) == "<alt>+/"
        assert qt_event_to_expression(int(Qt.Key.Key_QuoteLeft), _CTRL) == "<ctrl>+`"

    def test_all_punct_keys_in_whitelist(self):
        """期望清单 22 项全部存在于生产白名单且映射值一致。"""
        for qt_key, char in _PUNCT_KEYS:
            assert _QT_TO_PYNPUT.get(int(qt_key)) == char

    def test_whitelist_boundary_unchanged(self):
        """白名单边界防回归：媒体键、Enter 仍明确拒绝（🔴 禁止放开任意键）。"""
        assert qt_event_to_expression(int(Qt.Key.Key_VolumeUp), _CTRL) is None
        assert qt_event_to_expression(int(Qt.Key.Key_Return), _CTRL) is None
        assert qt_event_to_expression(int(Qt.Key.Key_Enter), _CTRL) is None


# ---------------------------------------------------------------------- 触发与展示


class TestPunctComboTrigger:
    def test_backquote_triggers(self):
        """用户原始诉求：<ctrl>+` 合成事件 press/release 均产出。"""
        combo = parse_hotkey("<ctrl>+`")
        tracker = ComboTracker(combo)
        tracker.press(keyboard.Key.ctrl_l)
        assert tracker.press(keyboard.KeyCode.from_char("`")) is True
        assert tracker.release(keyboard.KeyCode.from_char("`")) is True

    def test_shift_tilde_triggers(self):
        """<ctrl>+<shift>+~：物理 Shift+反引号上报 char='~'，两端一致可触发。"""
        combo = parse_hotkey("<ctrl>+<shift>+~")
        tracker = ComboTracker(combo)
        tracker.press(keyboard.Key.ctrl_l)
        tracker.press(keyboard.Key.shift_l)
        assert tracker.press(keyboard.KeyCode.from_char("~")) is True
        assert tracker.release(keyboard.KeyCode.from_char("~")) is True

    def test_punct_display(self):
        assert format_hotkey_display("<ctrl>+`") == "Ctrl+`"
        assert format_hotkey_display("<ctrl>+<shift>+_") == "Ctrl+Shift+_"
        assert format_hotkey_display("<alt>+/") == "Alt+/"


# ---------------------------------------------------------------------- 对话框捕获


class TestPunctCaptureDialog:
    def test_backquote_capture_flow(self, qapp):
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        event = QKeyEvent(
            QEvent.Type.KeyPress, int(Qt.Key.Key_QuoteLeft), _CTRL
        )
        dialog.keyPressEvent(event)
        assert dialog._candidate == "<ctrl>+`"
        assert dialog._ok_button.isEnabled()
        assert dialog._display_label.text() == "Ctrl+`"
        dialog._on_accept()
        assert dialog.expression == "<ctrl>+`"
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_shift_tilde_capture_flow(self, qapp):
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        event = QKeyEvent(
            QEvent.Type.KeyPress, int(Qt.Key.Key_AsciiTilde), _CTRL | _SHIFT
        )
        dialog.keyPressEvent(event)
        assert dialog._candidate == "<ctrl>+<shift>+~"
        assert dialog._display_label.text() == "Ctrl+Shift+~"
        dialog._on_accept()
        assert dialog.expression == "<ctrl>+<shift>+~"


# ---------------------------------------------------------------------- 落盘 round-trip


class TestPunctPersistence:
    @pytest.mark.parametrize(
        "expression", ["<ctrl>+`", "<ctrl>+\\", "<ctrl>+\"", "<ctrl>+<shift>+~"]
    )
    def test_yaml_roundtrip(self, tmp_path, monkeypatch, expression):
        """标点表达式（含反斜杠/引号）经 YAML 落盘→重载原样复原，
        且 Settings 从用户配置层拾取（防转义失真导致重启后快捷键失效）。"""
        monkeypatch.setattr(
            "zen_vocotype_protocol.paths.DEFAULT_USER_CONFIG_PATH",
            tmp_path / "zen_vocotype" / "user_config.yaml",
        )
        set_user_config_value("hotkey", expression)
        assert load_user_config()["hotkey"] == expression
        assert Settings().hotkey == expression
        # 复原产物仍可被解析器接受（🔴 单一出处兜底）
        assert parse_hotkey(Settings().hotkey).expression == expression
