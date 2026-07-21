"""T2.3 单元测试：协议客户端与网络 worker（模拟服务端桩隔离测试）。"""

import pytest

from zen_vocotype_client.transcribe.client import (
    ProtocolClient,
    RequestFailedError,
    ServiceUnavailableError,
    VersionMismatchError,
)
from zen_vocotype_client.transcribe.worker import (
    STATUS_DISCONNECTED,
    STATUS_LOADING,
    STATUS_READY,
    NetworkWorker,
)

from stub_server import StubServer


@pytest.fixture()
def stub(tmp_path):
    server = StubServer(tmp_path / "stub.sock")
    server.start()
    yield server
    server.stop()


class Collector:
    """信号收集器（同线程直连，无需事件循环）。"""

    def __init__(self, worker: NetworkWorker) -> None:
        self.status: list[tuple[str, str]] = []
        self.done: list[dict] = []
        self.failed: list[tuple[int, str]] = []
        self.mismatch: list[str] = []
        worker.sig_service_status.connect(lambda s, d: self.status.append((s, d)))
        worker.sig_recognize_done.connect(self.done.append)
        worker.sig_recognize_failed.connect(lambda c, m: self.failed.append((c, m)))
        worker.sig_version_mismatch.connect(self.mismatch.append)


# ---------------------------------------------------------------------------
# ProtocolClient（纯同步客户端）
# ---------------------------------------------------------------------------

class TestProtocolClient:
    def test_health_roundtrip(self, stub):
        client = ProtocolClient(stub.socket_path)
        client.connect()
        payload = client.health()
        assert payload["status"] == "ready"
        assert payload["current_model"] == "stub-model"
        client.close()

    def test_long_connection_reuse(self, stub):
        client = ProtocolClient(stub.socket_path)
        client.connect()
        client.health()
        client.recognize(b"\x00\x01" * 160)
        assert stub.connection_count == 1  # 长连复用，未新建连接
        client.close()

    def test_recognize_roundtrip(self, stub):
        client = ProtocolClient(stub.socket_path)
        client.connect()
        payload = client.recognize(b"\x00\x01" * 160)
        assert payload["text"] == "桩识别文本"
        client.close()

    def test_error_code_passthrough(self, tmp_path):
        stub = StubServer(tmp_path / "s.sock", recognize_error=(2001, "服务未就绪"))
        stub.start()
        try:
            client = ProtocolClient(stub.socket_path)
            client.connect()
            with pytest.raises(RequestFailedError) as exc_info:
                client.recognize(b"\x00\x01" * 160)
            assert exc_info.value.code == 2001  # 冻结码原样透传
            assert "未就绪" in exc_info.value.message
        finally:
            stub.stop()

    def test_version_mismatch_by_header(self, tmp_path):
        stub = StubServer(tmp_path / "s.sock", protocol_version="9.9.9")
        stub.start()
        try:
            client = ProtocolClient(stub.socket_path)
            client.connect()
            with pytest.raises(VersionMismatchError):
                client.health()
            assert not client.connected  # 版本不符必须断开
        finally:
            stub.stop()

    def test_connect_refused(self, tmp_path):
        client = ProtocolClient(str(tmp_path / "absent.sock"))
        with pytest.raises(ServiceUnavailableError):
            client.connect()

    def test_peer_closes_connection(self, tmp_path):
        stub = StubServer(tmp_path / "s.sock", close_first_n_connections=1)
        stub.start()
        try:
            client = ProtocolClient(stub.socket_path)
            client.connect()
            with pytest.raises(ServiceUnavailableError, match="连接中断|对端关闭"):
                client.health()
        finally:
            stub.stop()


# ---------------------------------------------------------------------------
# NetworkWorker（连接管理语义）
# ---------------------------------------------------------------------------

class TestNetworkWorker:
    def test_probe_ready(self, stub):
        worker = NetworkWorker(stub.socket_path)
        seen = Collector(worker)
        worker.probe()
        assert seen.status == [(STATUS_READY, "stub-model")]

    def test_probe_loading(self, tmp_path):
        stub = StubServer(tmp_path / "s.sock", health_status="starting")
        stub.start()
        try:
            worker = NetworkWorker(stub.socket_path)
            seen = Collector(worker)
            worker.probe()
            assert seen.status[0][0] == STATUS_LOADING
        finally:
            stub.stop()

    def test_probe_server_absent(self, tmp_path):
        worker = NetworkWorker(str(tmp_path / "absent.sock"))
        seen = Collector(worker)
        worker.probe()
        assert seen.status == [(STATUS_DISCONNECTED, "服务端未运行")]

    def test_probe_version_mismatch(self, tmp_path):
        stub = StubServer(tmp_path / "s.sock", protocol_version="9.9.9")
        stub.start()
        try:
            worker = NetworkWorker(stub.socket_path)
            seen = Collector(worker)
            worker.probe()
            assert len(seen.mismatch) == 1
            assert seen.status == []  # 版本不符不走常规状态通道
        finally:
            stub.stop()

    def test_recognize_lazy_connect_and_reuse(self, stub):
        worker = NetworkWorker(stub.socket_path)
        seen = Collector(worker)
        worker.recognize(b"\x00\x01" * 160)
        worker.recognize(b"\x00\x01" * 160)
        assert len(seen.done) == 2
        assert stub.connection_count == 1  # 懒连接一次后长连复用

    def test_recognize_server_absent_single_retry(self, tmp_path):
        worker = NetworkWorker(str(tmp_path / "absent.sock"))
        seen = Collector(worker)
        worker.recognize(b"\x00\x01" * 160)
        assert seen.failed == [(0, "服务端未运行，无法识别")]
        assert seen.status == [(STATUS_DISCONNECTED, "服务端未运行")]

    def test_recognize_reconnect_once_after_drop(self, tmp_path):
        """首个连接被对端断开 → 自动重连一次并成功（🔴 仅一次）。"""
        stub = StubServer(tmp_path / "s.sock", close_first_n_connections=1)
        stub.start()
        try:
            worker = NetworkWorker(stub.socket_path)
            seen = Collector(worker)
            worker.recognize(b"\x00\x01" * 160)
            assert len(seen.done) == 1
            assert stub.connection_count == 2  # 首次 + 唯一一次重连
        finally:
            stub.stop()

    def test_recognize_error_passthrough(self, tmp_path):
        stub = StubServer(tmp_path / "s.sock", recognize_error=(2002, "model_switching"))
        stub.start()
        try:
            worker = NetworkWorker(stub.socket_path)
            seen = Collector(worker)
            worker.recognize(b"\x00\x01" * 160)
            assert seen.failed == [(2002, "model_switching")]
        finally:
            stub.stop()
