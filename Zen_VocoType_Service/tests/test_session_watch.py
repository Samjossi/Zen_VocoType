"""T42 关机优雅退出单测（计划 2026-0730-0221）。

覆盖：

- ``SessionShutdownWatcher`` 回调接线（直接触发 slot → 回调被调用；false 忽略）
- 系统总线连接失败静默降级（构造不抛、仅记 warning）
- ``shutdown_event.set`` 回调连通（线程安全事件直接作 Qt 回调）

🔴 offscreen 平台必须在 PySide6 导入前设置（headless CI 兼容）。
"""

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from zen_vocotype_service.session_watch import SessionShutdownWatcher


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestSessionShutdownWatcher:
    def test_slot_triggers_callback_on_true(self, qapp):
        """PrepareForShutdown(true) → 回调被调用。"""
        called = []
        watcher = SessionShutdownWatcher(lambda: called.append(True), parent=qapp)
        watcher._on_prepare_for_shutdown(True)
        assert called == [True]

    def test_slot_ignores_false(self, qapp):
        """PrepareForShutdown(false)=关机取消 → 不触发回调。"""
        called = []
        watcher = SessionShutdownWatcher(lambda: called.append(True), parent=qapp)
        watcher._on_prepare_for_shutdown(False)
        assert called == []

    def test_shutdown_event_set_as_callback(self, qapp):
        """threading.Event.set 可直接作回调（与 SIGTERM/托盘退出汇流，天然幂等）。"""
        event = threading.Event()
        watcher = SessionShutdownWatcher(event.set, parent=qapp)
        watcher._on_prepare_for_shutdown(True)
        assert event.is_set()
        watcher._on_prepare_for_shutdown(True)  # 重复触发无害
        assert event.is_set()

    def test_system_bus_error_degrades_silently(self, qapp, monkeypatch):
        """systemBus() 抛错 → 构造不抛异常（容器/CI 无总线环境，🔴 不得影响启动）。"""
        from PySide6.QtDBus import QDBusConnection

        def _boom():
            raise RuntimeError("模拟无系统总线")

        monkeypatch.setattr(QDBusConnection, "systemBus", staticmethod(_boom))
        watcher = SessionShutdownWatcher(lambda: None, parent=qapp)
        assert watcher.connected is False

    def test_disconnected_bus_degrades_silently(self, qapp, monkeypatch):
        """总线未连接 → 静默降级，不订阅信号、不抛异常。"""
        from PySide6.QtDBus import QDBusConnection

        class _FakeBus:
            def isConnected(self):
                return False

            def connect(self, *args):
                raise AssertionError("总线未连接时不应尝试订阅信号")

        monkeypatch.setattr(
            QDBusConnection, "systemBus", staticmethod(lambda: _FakeBus())
        )
        watcher = SessionShutdownWatcher(lambda: None, parent=qapp)
        assert watcher.connected is False

    def test_real_bus_subscription(self, qapp):
        """真实系统总线：可达时订阅必须成功（固化 slot 字符串接线——PySide6
        传 bytes 形式 SLOT 签名会被错误拒绝，回归防护）；不可达则验证降级。"""
        from PySide6.QtDBus import QDBusConnection

        watcher = SessionShutdownWatcher(lambda: None, parent=qapp)
        if QDBusConnection.systemBus().isConnected():
            assert watcher.connected is True
        else:  # CI/容器无总线：静默降级
            assert watcher.connected is False
