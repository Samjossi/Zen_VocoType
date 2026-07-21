"""T2.7 单元测试：托盘版本菜单/状态色、通知去重与降级通道。"""

import time

import pytest
from PySide6.QtWidgets import QApplication

from zen_vocotype_client import __version__
from zen_vocotype_client.tray.notifier import Notifier
from zen_vocotype_client.tray.tray import APP_DISPLAY_NAME, ClientTray, TrayStatus, status_icon
from zen_vocotype_client.tray.icon_loader import load_tray_icon


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestTrayMenu:
    def test_version_action_first_and_disabled(self, qapp):
        """版本项必须首行且禁用（与旧 GridChat 明确区分）。"""
        tray = ClientTray()
        menu = tray.tray_icon.contextMenu()
        actions = menu.actions()
        assert actions[0].text() == f"{APP_DISPLAY_NAME} v{__version__}"
        assert not actions[0].isEnabled()
        assert "GridChat" not in actions[0].text()

    def test_menu_structure(self, qapp):
        tray = ClientTray()
        texts = [a.text() for a in tray.tray_icon.contextMenu().actions()]
        assert "重试连接服务端" in texts
        assert "退出" in texts
        assert any(t.startswith("状态：") for t in texts)

    def test_status_updates_text_and_tooltip(self, qapp):
        tray = ClientTray()
        tray.set_status(TrayStatus.READY, "paraformer")
        texts = [a.text() for a in tray.tray_icon.contextMenu().actions()]
        assert "状态：就绪（paraformer）" in texts
        assert "就绪" in tray.tray_icon.toolTip()


class TestStatusIcon:
    def test_all_statuses_produce_nonnull_icons(self):
        base = load_tray_icon()
        for status in TrayStatus:
            icon = status_icon(base, status)
            assert not icon.isNull(), status

    def test_statuses_produce_distinct_pixmaps(self):
        """不同状态色点生成不同像素（状态色真实生效）。"""
        base = load_tray_icon()
        images = {
            s: status_icon(base, s).pixmap(64, 64).toImage() for s in TrayStatus
        }
        distinct = {bytes(img.constBits()) for img in images.values()}
        assert len(distinct) == len(TrayStatus)

    def test_null_base_icon_still_shows_dot(self):
        """基础图标缺失（降级）时状态色点仍可见（文字托盘兜底语义）。"""
        from PySide6.QtGui import QIcon

        icon = status_icon(QIcon(), TrayStatus.ERROR)
        assert not icon.isNull()


class _FakeTray:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.messages.append((title, message))


class TestNotifier:
    def test_dedup_within_window(self):
        tray = _FakeTray()
        n = Notifier(tray, dedup_seconds=5.0)
        assert n.notify("错误", "服务端未运行") is True
        assert n.notify("错误", "服务端未运行") is False  # 同类去重
        assert tray.messages == [("错误", "服务端未运行")]

    def test_dedup_by_explicit_key(self):
        tray = _FakeTray()
        n = Notifier(tray, dedup_seconds=5.0)
        n.notify("错误", "细节A", key="same-error")
        assert n.notify("错误", "细节B", key="same-error") is False
        assert n.notify("错误", "细节B", key="other-error") is True

    def test_dedup_window_expires(self):
        tray = _FakeTray()
        n = Notifier(tray, dedup_seconds=0.05)
        n.notify("错误", "x")
        time.sleep(0.06)
        assert n.notify("错误", "x") is True
        assert len(tray.messages) == 2

    def test_fallback_to_notify_send_when_no_tray(self, monkeypatch):
        """无托盘降级：走 notify-send 并（可用性缺失时）仅落日志。"""
        calls: list[list[str]] = []

        class _Result:
            returncode = 0

        monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a[0]) or _Result())
        n = Notifier(None, dedup_seconds=5.0)
        if n._notify_send_available:
            assert n.notify("标题", "内容") is True
            assert calls and calls[0][0] == "notify-send"
        else:
            assert n.notify("标题", "内容") is True  # 仅落日志路径不抛异常
