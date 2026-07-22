"""T2.9 单元测试：LOADING 态 health 轮询（托盘黄灯「连接中…」误报修复）。

修复背景：probe 仅启动时执行一次，服务端模型加载期间拿到 ``starting`` 后
状态永远停在 LOADING（托盘橙灯「连接中…」）。修复后 LOADING 态由 QTimer
周期投递 health 探测（网络 I/O 仍在 worker 线程，主线程仅调度），直至
终态（READY/ERROR/DISCONNECTED）或达 ``loading_poll_max_count`` 上限。

测试不跑完整 ``start()``（避免音频设备/X11 依赖）：worker 留在主线程，
``_request_probe`` 的 QueuedConnection 投递由事件循环就地执行，语义等价。
"""

import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from zen_vocotype_client.app import ClientApp
from zen_vocotype_client.config import Settings
from zen_vocotype_client.transcribe import worker as worker_mod
from zen_vocotype_client.tray.tray import TrayStatus

from stub_server import StubServer

#: 测试用轮询间隔（毫秒）：配置项下限 500ms（ge=500），取下限加速事件循环推进
TEST_POLL_INTERVAL_MS = 500

#: 「不再投递探测」断言的观察窗口：需覆盖 2 倍以上轮询间隔
FROZEN_OBSERVE_SECONDS = 1.2


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class RecordingNotifier:
    """通知记录替身（托盘通知属视觉通道，断言改走本记录）。"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None]] = []

    def notify(self, title: str, message: str, *, key: str | None = None) -> bool:
        self.messages.append((message, key))
        return True


def wait_until(predicate, timeout: float, interval: float = 0.02) -> bool:
    """Qt 事件循环内等待条件成立（条件轮询 + 超时上限，🔴 非固定 sleep）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_app(stub: StubServer, **overrides) -> ClientApp:
    settings = Settings(
        socket_path=stub.socket_path,
        loading_poll_interval_ms=TEST_POLL_INTERVAL_MS,
        **overrides,
    )
    app = ClientApp(settings)
    notifier = RecordingNotifier()
    app._notifier = notifier
    app.test_notifier = notifier  # 防 GC + 断言入口
    # _request_probe 仅要求 _qthread 非 None；worker 留主线程，
    # QueuedConnection 投递经 processEvents 就地执行 probe
    app._qthread = QThread()
    return app


def _health_count(stub: StubServer) -> int:
    return stub.request_log.count("health")


class TestLoadingPoll:
    def test_loading_starts_polling(self, qapp, tmp_path):
        """收到 LOADING 后定时器激活，并按间隔投递 health 探测。"""
        stub = StubServer(tmp_path / "s.sock", health_status="starting")
        stub.start()
        try:
            app = _make_app(stub)
            try:
                app._on_service_status(worker_mod.STATUS_LOADING, "模型加载中（starting）")
                assert app._loading_poll_timer.isActive()
                assert wait_until(lambda: _health_count(stub) >= 1, 3)
            finally:
                app.shutdown()
        finally:
            stub.stop()

    def test_ready_stops_polling(self, qapp, tmp_path):
        """模型就绪后（starting → ready）定时器停止，托盘状态转 READY。"""
        stub = StubServer(tmp_path / "s.sock", health_status="starting")
        stub.start()
        try:
            app = _make_app(stub)
            try:
                app._on_service_status(worker_mod.STATUS_LOADING, "模型加载中（starting）")
                assert app._loading_poll_timer.isActive()
                stub.health_status = "ready"  # 模拟服务端模型加载完成
                assert wait_until(
                    lambda: not app._loading_poll_timer.isActive(), 3
                )
                assert app._service_tray_status is TrayStatus.READY
            finally:
                app.shutdown()
        finally:
            stub.stop()

    def test_disconnected_stops_polling(self, qapp, tmp_path):
        """LOADING → DISCONNECTED（服务端崩溃）：停止轮询，不再投递探测（选型二红线）。"""
        stub = StubServer(tmp_path / "s.sock", health_status="starting")
        stub.start()
        try:
            app = _make_app(stub)
            try:
                app._on_service_status(worker_mod.STATUS_LOADING, "模型加载中（starting）")
                assert app._loading_poll_timer.isActive()
                app._on_service_status(worker_mod.STATUS_DISCONNECTED, "连接中断")
                assert not app._loading_poll_timer.isActive()
                frozen = _health_count(stub)
                # 等待 2 倍以上轮询间隔，确认探测调用次数冻结
                assert not wait_until(
                    lambda: _health_count(stub) > frozen, FROZEN_OBSERVE_SECONDS
                )
            finally:
                app.shutdown()
        finally:
            stub.stop()

    def test_max_count_stops_with_notification(self, qapp, tmp_path):
        """达轮询上限：停止 + 一次性「加载超时」通知 + 托盘 detail「模型加载超时」。"""
        stub = StubServer(tmp_path / "s.sock", health_status="starting")
        stub.start()
        try:
            app = _make_app(stub, loading_poll_max_count=2)
            try:
                app._on_service_status(worker_mod.STATUS_LOADING, "模型加载中（starting）")
                assert wait_until(
                    lambda: any(k == "loading-timeout" for _, k in app.test_notifier.messages),
                    5,
                )
                assert not app._loading_poll_timer.isActive()
                timeout_msgs = [
                    m for m, k in app.test_notifier.messages if k == "loading-timeout"
                ]
                assert len(timeout_msgs) == 1  # 一次性通知
                if app._tray is not None:
                    texts = [a.text() for a in app._tray.tray_icon.contextMenu().actions()]
                    assert any(
                        t.startswith("状态：") and "模型加载超时" in t for t in texts
                    )
            finally:
                app.shutdown()
        finally:
            stub.stop()

    def test_shutdown_stops_active_polling(self, qapp, tmp_path):
        """轮询激活中调用 shutdown()：定时器停止，无悬挂投递、正常返回。"""
        stub = StubServer(tmp_path / "s.sock", health_status="starting")
        stub.start()
        try:
            app = _make_app(stub)
            app._on_service_status(worker_mod.STATUS_LOADING, "模型加载中（starting）")
            assert app._loading_poll_timer.isActive()
            app.shutdown()
            assert not app._loading_poll_timer.isActive()
            frozen = _health_count(stub)
            assert not wait_until(
                lambda: _health_count(stub) > frozen, FROZEN_OBSERVE_SECONDS
            )
        finally:
            stub.stop()
