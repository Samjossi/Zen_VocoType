"""T3.6 就绪等待测试：模拟服务端桩 + 两阶段等待五场景。"""

import socket
import threading
import time
from pathlib import Path

import pytest
from zen_vocotype_protocol import actions
from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_launcher.readiness import (
    ProtocolClient,
    ReadyTimeoutError,
    ServiceUnavailableError,
    VersionMismatchError,
    wait_for_readiness,
    wait_until,
)

#: 测试轮询间隔（秒）：缩小以加速超时场景
_POLL = 0.02


class StubServer:
    """基于契约库 frames 的模拟服务端桩（Launcher 自有实现，参照 Client
    测试桩设计——🔴 禁止跨组件 import）。

    行为可配：``ready_after_s`` 秒后 ready 变为 True；``never_ready`` 永不就绪；
    ``protocol_version`` 可伪造版本不一致。
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        ready_after_s: float = 0.0,
        never_ready: bool = False,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        self._path = str(socket_path)
        self._ready_after = time.monotonic() + ready_after_s
        self._never_ready = never_ready
        self._version = protocol_version
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self._path)
        sock.listen(4)
        sock.settimeout(0.1)
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = MessageBuffer()
        conn.settimeout(0.1)
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buf.feed(chunk)
                while True:
                    frame = buf.next_frame()
                    if frame is None:
                        break
                    header, _ = frame
                    action = header.get("action")
                    if action == actions.ACTION_HEALTH:
                        payload = {
                            "status": "ready",
                            "service_version": "1.0",
                            "model_loaded": True,
                            "current_model": "stub-model",
                        }
                        ok = True
                    elif action == actions.ACTION_READY:
                        ready = (not self._never_ready) and (
                            time.monotonic() >= self._ready_after
                        )
                        payload = {"ready": ready, "current_model": "stub-model"}
                        ok = True
                    else:
                        payload = {}
                        ok = False
                    resp = {
                        "action": action,
                        "protocol_version": self._version,
                        "request_id": header.get("request_id"),
                        "ok": ok,
                        "payload": payload,
                    }
                    if not ok:
                        resp["error"] = {"code": 1002, "message": "unknown action"}
                    try:
                        conn.sendall(encode_frame(resp))
                    except OSError:
                        return

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def sock_path(tmp_path):
    yield tmp_path / "stub.sock"


def _wait_kwargs(**over):
    base = {
        "socket_wait_timeout_s": 3.0,
        "model_ready_timeout_s": 3.0,
        "poll_interval_s": _POLL,
    }
    base.update(over)
    return base


class TestWaitUntil:
    def test_predicate_true_immediately(self):
        wait_until(lambda: True, timeout_s=1.0, interval_s=_POLL, timeout_message="x")

    def test_timeout_raises(self):
        with pytest.raises(ReadyTimeoutError, match="阶段X"):
            wait_until(lambda: False, timeout_s=0.1, interval_s=_POLL, timeout_message="阶段X 超时")

    def test_eventual_true(self):
        calls = []

        def pred():
            calls.append(1)
            return len(calls) >= 3

        wait_until(pred, timeout_s=2.0, interval_s=_POLL, timeout_message="x")
        assert len(calls) >= 3


class TestReadinessScenarios:
    def test_immediate_ready(self, sock_path):
        server = StubServer(sock_path, ready_after_s=0.0)
        server.start()
        try:
            client = ProtocolClient(str(sock_path))
            wait_for_readiness(client, **_wait_kwargs())
            assert not client.connected  # 等待结束后连接已关闭
        finally:
            server.stop()

    def test_delayed_ready_succeeds(self, sock_path):
        server = StubServer(sock_path, ready_after_s=0.3)
        server.start()
        try:
            client = ProtocolClient(str(sock_path))
            start = time.monotonic()
            wait_for_readiness(client, **_wait_kwargs())
            assert time.monotonic() - start >= 0.3
        finally:
            server.stop()

    def test_never_ready_times_out(self, sock_path):
        server = StubServer(sock_path, never_ready=True)
        server.start()
        try:
            client = ProtocolClient(str(sock_path))
            with pytest.raises(ReadyTimeoutError, match="阶段二"):
                wait_for_readiness(client, **_wait_kwargs(model_ready_timeout_s=0.2))
        finally:
            server.stop()

    def test_connection_refused_times_out_phase1(self, sock_path):
        client = ProtocolClient(str(sock_path))  # 无服务端监听
        with pytest.raises(ReadyTimeoutError, match="阶段一"):
            wait_for_readiness(client, **_wait_kwargs(socket_wait_timeout_s=0.2))

    def test_process_death_fails_fast(self, sock_path):
        """子进程等待期间死亡 → 立即失败，🔴 禁止干等超时。"""
        server = StubServer(sock_path, never_ready=True)
        server.start()
        try:
            client = ProtocolClient(str(sock_path))
            start = time.monotonic()
            with pytest.raises(ServiceUnavailableError, match="已退出"):
                wait_for_readiness(
                    client,
                    process_alive=lambda: False,  # 进程已死
                    process_exit_info=lambda: "code=1，日志尾部：boom",
                    **_wait_kwargs(model_ready_timeout_s=30.0),
                )
            assert time.monotonic() - start < 2.0  # 快速失败而非等满 30s
        finally:
            server.stop()

    def test_version_mismatch_raises(self, sock_path):
        server = StubServer(sock_path, protocol_version="9.9")
        server.start()
        try:
            client = ProtocolClient(str(sock_path))
            with pytest.raises(VersionMismatchError):
                wait_for_readiness(client, **_wait_kwargs())
        finally:
            server.stop()


class TestProtocolClientBasics:
    def test_health_payload(self, sock_path):
        server = StubServer(sock_path)
        server.start()
        try:
            client = ProtocolClient(str(sock_path))
            client.connect()
            try:
                payload = client.health()
                assert payload["status"] == "ready"
                assert payload["model_loaded"] is True
            finally:
                client.close()
        finally:
            server.stop()

    def test_request_without_connect_raises(self, sock_path):
        client = ProtocolClient(str(sock_path))
        with pytest.raises(ServiceUnavailableError):
            client.health()
