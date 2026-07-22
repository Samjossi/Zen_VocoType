"""T3.3 单元测试：托盘「修改快捷键」——捕获对话框、热切换与持久化。"""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog

import zen_vocotype_client.app as app_mod
from zen_vocotype_client.app import (
    ClientApp,
    MSG_HOTKEY_BUSY,
    MSG_HOTKEY_ENV_OVERRIDE,
    MSG_HOTKEY_PERSIST_FAILED,
)
from zen_vocotype_client.config import Settings
from zen_vocotype_client.hotkey.combo import format_hotkey_display, parse_hotkey
from zen_vocotype_client.hotkey.pynput_backend import HotkeyBackendError
from zen_vocotype_client.state_machine import State
from zen_vocotype_client.tray import hotkey_dialog as dialog_mod
from zen_vocotype_client.tray.hotkey_dialog import (
    HotkeyCaptureDialog,
    qt_event_to_expression,
)
from zen_vocotype_client.tray.tray import ClientTray


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_CTRL = Qt.KeyboardModifier.ControlModifier
_ALT = Qt.KeyboardModifier.AltModifier
_SHIFT = Qt.KeyboardModifier.ShiftModifier
_META = Qt.KeyboardModifier.MetaModifier


def _key_event(key: int, modifiers=_CTRL) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


# ---------------------------------------------------------------------- 转换函数


class TestQtEventToExpression:
    def test_letter_with_modifiers(self):
        assert qt_event_to_expression(int(Qt.Key.Key_O), _CTRL | _ALT) == "<ctrl>+<alt>+o"

    def test_modifier_fixed_order(self):
        """修饰键按 ctrl/alt/shift/cmd 固定序拼接（与配置书写习惯一致）。"""
        assert qt_event_to_expression(int(Qt.Key.Key_A), _SHIFT | _CTRL) == "<ctrl>+<shift>+a"
        assert (
            qt_event_to_expression(int(Qt.Key.Key_B), _META | _ALT | _CTRL)
            == "<ctrl>+<alt>+<cmd>+b"
        )

    def test_digit_and_function_key(self):
        assert qt_event_to_expression(int(Qt.Key.Key_5), _CTRL) == "<ctrl>+5"
        assert qt_event_to_expression(int(Qt.Key.Key_F9), _ALT) == "<alt>+<f9>"

    def test_special_keys(self):
        assert qt_event_to_expression(int(Qt.Key.Key_Space), _CTRL) == "<ctrl>+<space>"
        assert qt_event_to_expression(int(Qt.Key.Key_Up), _SHIFT) == "<shift>+<up>"
        assert qt_event_to_expression(int(Qt.Key.Key_Tab), _CTRL) == "<ctrl>+<tab>"
        # Shift+Tab 在 Qt 中上报为 Key_Backtab（Shift 已在 modifiers 中）
        assert qt_event_to_expression(int(Qt.Key.Key_Backtab), _SHIFT) == "<shift>+<tab>"

    def test_pure_modifier_returns_none(self):
        assert qt_event_to_expression(int(Qt.Key.Key_Control), _CTRL) is None
        assert qt_event_to_expression(int(Qt.Key.Key_Shift), _SHIFT) is None

    def test_unsupported_key_returns_none(self):
        """白名单外按键（如媒体键）明确拒绝，🔴 禁止猜测映射。"""
        assert qt_event_to_expression(int(Qt.Key.Key_VolumeUp), _CTRL) is None

    def test_expression_roundtrip_parseable(self):
        """转换产物必须能被 parse_hotkey 解析（单一出处兜底）。"""
        expr = qt_event_to_expression(int(Qt.Key.Key_K), _CTRL | _ALT)
        assert parse_hotkey(expr).expression == "<ctrl>+<alt>+k"


# ---------------------------------------------------------------------- 展示格式化


class TestFormatHotkeyDisplay:
    def test_common_combos(self):
        assert format_hotkey_display("<ctrl>+<alt>+o") == "Ctrl+Alt+O"
        assert format_hotkey_display("<f9>") == "F9"
        assert format_hotkey_display("<ctrl>+<space>") == "Ctrl+Space"
        assert format_hotkey_display("<shift>+<f5>") == "Shift+F5"
        # 下划线键名分段大写（str.capitalize 整串调用会产出 Page_up）
        assert format_hotkey_display("<ctrl>+<page_up>") == "Ctrl+Page_Up"

    def test_invalid_expression_echoed(self):
        """非法表达式原样回显，不抛异常。"""
        assert format_hotkey_display("not a key!!") == "not a key!!"


# ---------------------------------------------------------------------- 托盘菜单


class TestTrayMenuHotkey:
    def test_menu_structure(self, qapp):
        tray = ClientTray()
        texts = [a.text() for a in tray.tray_icon.contextMenu().actions()]
        assert "修改快捷键…" in texts
        assert tray._hotkey_action.text().startswith("快捷键：")
        assert not tray._hotkey_action.isEnabled()
        # 展示行位于状态行之后、「修改快捷键…」之前
        assert texts.index(tray._hotkey_action.text()) < texts.index("修改快捷键…")

    def test_set_hotkey_label(self, qapp):
        tray = ClientTray()
        tray.set_hotkey_label("<ctrl>+<alt>+k")
        assert tray._hotkey_action.text() == "快捷键：Ctrl+Alt+K"

    def test_change_action_emits_signal(self, qapp):
        tray = ClientTray()
        received: list[bool] = []
        tray.hotkey_change_requested.connect(lambda: received.append(True))
        tray._hotkey_change_action.trigger()
        assert received == [True]


# ---------------------------------------------------------------------- 捕获对话框


class TestHotkeyCaptureDialog:
    def test_capture_flow(self, qapp):
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        assert not dialog._ok_button.isEnabled()
        dialog.keyPressEvent(_key_event(int(Qt.Key.Key_K), _CTRL | _ALT))
        assert dialog._candidate == "<ctrl>+<alt>+k"
        assert dialog._ok_button.isEnabled()
        assert dialog._display_label.text() == "Ctrl+Alt+K"
        dialog._on_accept()
        assert dialog.expression == "<ctrl>+<alt>+k"
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_escape_rejects(self, qapp):
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        dialog.keyPressEvent(_key_event(int(Qt.Key.Key_Escape), Qt.KeyboardModifier.NoModifier))
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.expression is None

    def test_unsupported_key_blocks_ok(self, qapp):
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        dialog.keyPressEvent(_key_event(int(Qt.Key.Key_VolumeUp), _CTRL))
        assert dialog._candidate is None
        assert not dialog._ok_button.isEnabled()
        assert "不支持的按键" in dialog._display_label.text()

    def test_invalid_expression_blocked(self, qapp, monkeypatch):
        """parse_hotkey 兜底校验失败时拦截 accept 并回显错误。"""
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        dialog.keyPressEvent(_key_event(int(Qt.Key.Key_K), _CTRL | _ALT))

        def _boom(expression):
            raise ValueError("模拟非法")

        monkeypatch.setattr(dialog_mod, "parse_hotkey", _boom)
        dialog._on_accept()
        assert dialog.expression is None
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert "非法快捷键" in dialog._display_label.text()

    def test_restore_default_uses_settings_default(self, qapp):
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        dialog._on_restore_default()
        assert dialog.expression == Settings.model_fields["hotkey"].default
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_tab_routed_to_capture(self, qapp):
        """Tab 经 event() 拦截路由到捕获逻辑（Qt 焦点导航不再吞掉）。"""
        dialog = HotkeyCaptureDialog(current="<ctrl>+<alt>+o")
        event = QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Tab), _CTRL)
        assert dialog.event(event) is True
        assert dialog._candidate == "<ctrl>+<tab>"
        assert dialog._ok_button.isEnabled()


# ---------------------------------------------------------------------- Shift+字母触发（评审修复固化）


class TestShiftLetterCombo:
    def test_shift_letter_triggers(self):
        """pynput X11 上报大写 char（Shift+a → 'A'），须与小写解析产物匹配。"""
        from pynput import keyboard

        from zen_vocotype_client.hotkey.backend import ComboTracker

        combo = parse_hotkey("<ctrl>+<shift>+a")
        tracker = ComboTracker(combo)
        tracker.press(keyboard.Key.ctrl_l)
        tracker.press(keyboard.Key.shift_l)
        # 物理按键事件：Shift 按下时 char 为大写（_xorg.py 实测行为）
        assert tracker.press(keyboard.KeyCode.from_char("A")) is True
        assert tracker.release(keyboard.KeyCode.from_char("A")) is True


# ---------------------------------------------------------------------- 热切换编排


class _RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str, key: str | None = None) -> bool:
        self.messages.append((title, message))
        return True


class _FakeTray:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.statuses: list = []

    def set_hotkey_label(self, expression: str) -> None:
        self.labels.append(expression)

    def set_status(self, status, detail: str = "") -> None:
        self.statuses.append((status, detail))


class _FakeBackend:
    """热键后端替身：记录 stop/start 调用，可按需让 start 抛错。"""

    instances: list["_FakeBackend"] = []
    start_error: Exception | None = None

    def __init__(self, combo, on_press=None, on_release=None) -> None:
        self._combo = combo
        self.started = False
        self.stopped = False
        _FakeBackend.instances.append(self)

    def start(self) -> None:
        if _FakeBackend.start_error is not None:
            raise _FakeBackend.start_error
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def _make_client() -> ClientApp:
    """构造未启动的 ClientApp：替身通知器/托盘/热键后端，零外部依赖。"""
    client = ClientApp(Settings(socket_path="/nonexistent/zen_t33.sock"))
    client._notifier = _RecordingNotifier()
    client._tray = _FakeTray()
    old = _FakeBackend(parse_hotkey(client._settings.hotkey))
    client._hotkey = old
    return client


@pytest.fixture
def fake_backends(monkeypatch):
    _FakeBackend.instances = []
    _FakeBackend.start_error = None
    monkeypatch.setattr(app_mod, "PynputBackend", _FakeBackend)
    yield _FakeBackend
    _FakeBackend.start_error = None


class TestApplyHotkey:
    def test_success_path(self, qapp, monkeypatch, fake_backends):
        client = _make_client()
        persisted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        old_backend = client._hotkey

        client._apply_hotkey("<ctrl>+<alt>+k")

        assert persisted == [("hotkey", "<ctrl>+<alt>+k")]  # 先落盘
        assert old_backend.stopped  # 旧监听已停
        new_backend = fake_backends.instances[-1]
        assert new_backend is not old_backend and new_backend.started
        assert new_backend._combo.expression == "<ctrl>+<alt>+k"
        assert client._settings.hotkey == "<ctrl>+<alt>+k"  # 内存同步
        assert client._tray.labels == ["<ctrl>+<alt>+k"]  # 托盘展示行刷新
        assert any("快捷键已更新为 Ctrl+Alt+K" in m for _, m in client._notifier.messages)

    def test_persist_failure_aborts(self, qapp, monkeypatch, fake_backends):
        """落盘失败 → 整体放弃：旧监听不停、内存不变（🔴 禁止知行分裂）。"""
        client = _make_client()
        old_backend = client._hotkey

        def _boom(key, value):
            raise OSError("磁盘只读")

        monkeypatch.setattr(app_mod, "set_user_config_value", _boom)
        before = len(fake_backends.instances)
        client._apply_hotkey("<ctrl>+<alt>+k")

        assert not old_backend.stopped
        assert len(fake_backends.instances) == before  # 未建新后端
        assert client._settings.hotkey != "<ctrl>+<alt>+k"
        assert any(MSG_HOTKEY_PERSIST_FAILED.split("：")[0] in m
                   for _, m in client._notifier.messages)

    def test_invalid_expression_rejected(self, qapp, monkeypatch, fake_backends):
        client = _make_client()
        persisted: list = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        client._apply_hotkey("<ctrl>+<alt>")  # 无主键，非法
        assert persisted == []
        assert not client._hotkey.stopped
        assert any("快捷键表达式非法" in m for _, m in client._notifier.messages)

    def test_busy_state_recheck_aborts(self, qapp, monkeypatch, fake_backends):
        """dialog.exec() 嵌套事件循环期间进入 RECORDING → _apply_hotkey 复查拦截。

        （评审修复：入口 IDLE 检查后状态可变，切换前必须复查，
        否则切掉激活中的 tracker 丢失 release 卡死状态机）
        """
        client = _make_client()
        persisted: list = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        client._sm._state = State.RECORDING  # 测试直接置位（避免触发录音监听器）
        old_backend = client._hotkey

        client._apply_hotkey("<ctrl>+<alt>+k")

        assert persisted == []
        assert not old_backend.stopped
        assert client._settings.hotkey != "<ctrl>+<alt>+k"
        assert any(MSG_HOTKEY_BUSY in m for _, m in client._notifier.messages)

    def test_start_failure_restores_old_backend(self, qapp, monkeypatch, fake_backends):
        """新监听启动失败 → 恢复原后端 + 回滚落盘（避免配置/运行态知行分裂）。"""
        client = _make_client()
        persisted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        old_backend = client._hotkey

        class _FlakyBackend(_FakeBackend):
            def start(self) -> None:
                # 首个新实例（非旧 combo）失败，恢复实例成功
                if self._combo.expression == "<ctrl>+<alt>+k":
                    raise HotkeyBackendError("模拟启动失败")
                self.started = True

        monkeypatch.setattr(app_mod, "PynputBackend", _FlakyBackend)
        client._apply_hotkey("<ctrl>+<alt>+k")

        assert old_backend.stopped
        restored = _FakeBackend.instances[-1]
        assert restored._combo.expression == old_backend._combo.expression
        assert restored.started
        assert client._hotkey is restored
        assert client._settings.hotkey != "<ctrl>+<alt>+k"
        # 落盘回滚：先写新值，失败后写回旧值
        assert persisted == [
            ("hotkey", "<ctrl>+<alt>+k"),
            ("hotkey", old_backend._combo.expression),
        ]
        assert any("已恢复原快捷键" in m for _, m in client._notifier.messages)

    def test_restore_also_fails_sets_error(self, qapp, monkeypatch, fake_backends):
        """新监听与恢复均失败 → 托盘 ERROR + 明确通知（🔴 禁止无热键静默空跑）。"""
        client = _make_client()
        monkeypatch.setattr(app_mod, "set_user_config_value", lambda k, v: None)
        _FakeBackend.start_error = HotkeyBackendError("全部失败")

        client._apply_hotkey("<ctrl>+<alt>+k")

        from zen_vocotype_client.tray.tray import TrayStatus

        assert client._tray.statuses[-1][0] is TrayStatus.ERROR
        assert any("热键监听失效" in m for _, m in client._notifier.messages)

    def test_env_override_warning(self, qapp, monkeypatch, fake_backends):
        """环境变量优先级高于用户配置文件：切换成功但通知如实提醒。"""
        client = _make_client()
        monkeypatch.setattr(app_mod, "set_user_config_value", lambda k, v: None)
        monkeypatch.setenv("ZEN_VOCOTYPE_CLIENT_HOTKEY", "<ctrl>+<alt>+z")

        client._apply_hotkey("<ctrl>+<alt>+k")

        assert any(MSG_HOTKEY_ENV_OVERRIDE in m for _, m in client._notifier.messages)


class TestBusyGuard:
    def test_busy_state_blocks_dialog(self, qapp, monkeypatch):
        """录音/识别中禁止打开对话框（🔴 禁止忙碌中热切换）。"""
        client = _make_client()
        client._sm._state = State.RECORDING  # 测试直接置位（避免触发录音监听器）
        created: list = []

        class _FakeDialog:
            def __init__(self, current):
                created.append(current)

        monkeypatch.setattr(app_mod, "HotkeyCaptureDialog", _FakeDialog)
        client._on_change_hotkey()

        assert created == []
        assert any(MSG_HOTKEY_BUSY in m for _, m in client._notifier.messages)
