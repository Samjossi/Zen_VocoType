"""T1.3 集成测试：Socket 服务 + 协议层 + §7.1 访问控制。

本机起真实服务（无模型，状态 starting / 手动推进），socket 直发复合帧验证：
health/ready/错误帧/版本握手/Socket 权限 0600/audio_chunk → 1005。
SO_PEERCRED 异 UID 拒绝以单元测试覆盖（同机无法伪造他 UID 连接）。
"""

import os
import socket
import stat
import time
import uuid

import pytest

from zen_vocotype_protocol import errors
from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_service.config import Settings
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.server import SocketPathError, SocketServer
from zen_vocotype_service.state import ServiceState


def _request(sock_path: str, header: dict, body: bytes = b"") -> dict:
    """测试客户端：发一帧收一帧。"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(sock_path)
        conn.sendall(encode_frame(header, body))
        buf = MessageBuffer()
        while True:
            data = conn.recv(65536)
            assert data, "连接被关闭但未收到响应"
            buf.feed(data)
            frame = buf.next_frame()
            if frame is not None:
                return frame[0]


def _make_header(action: str, **extra) -> dict:
    header = {
        "action": action,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str(uuid.uuid4()),
    }
    header.update(extra)
    return header


@pytest.fixture()
def running_server(tmp_path):
    sock_path = str(tmp_path / "svc.sock")
    settings = Settings(socket_path=sock_path, log_dir=tmp_path / "logs")
    ctx = ServiceContext(settings, ServiceState())
    server = SocketServer(settings, ctx)
    server.start(background=True)
    deadline = time.monotonic() + 5
    while not os.path.exists(sock_path):
        if time.monotonic() > deadline:
            raise RuntimeError("服务未及时 bind")
        time.sleep(0.01)
    yield server, ctx, sock_path
    server.shutdown()


class TestHealthReady:
    def test_health_starting(self, running_server):
        _, ctx, sock_path = running_server
        resp = _request(sock_path, _make_header("health"))
        assert resp["ok"] is True
        assert resp["protocol_version"] == PROTOCOL_VERSION
        assert resp["payload"]["status"] == "starting"
        assert resp["payload"]["model_loaded"] is False
        assert resp["payload"]["current_model"] is None

    def test_ready_false_while_starting(self, running_server):
        resp = _request(running_server[2], _make_header("ready"))
        assert resp["ok"] is True
        assert resp["payload"]["ready"] is False

    def test_ready_true_after_mark_ready(self, running_server):
        _, ctx, sock_path = running_server
        ctx.state.mark_ready("fun-asr-nano")
        resp = _request(sock_path, _make_header("ready"))
        assert resp["payload"] == {"ready": True, "current_model": "fun-asr-nano"}
        resp = _request(sock_path, _make_header("health"))
        assert resp["payload"]["status"] == "ready"
        assert resp["payload"]["current_model"] == "fun-asr-nano"

    def test_ready_error_state_returns_3002(self, running_server):
        _, ctx, sock_path = running_server
        ctx.state.mark_error("磁盘满")
        resp = _request(sock_path, _make_header("ready"))
        assert resp["ok"] is False
        assert resp["error"]["code"] == errors.ERR_MODEL_LOAD_FAILED
        assert "磁盘满" in resp["error"]["message"]

    def test_request_id_echoed(self, running_server):
        header = _make_header("health")
        resp = _request(running_server[2], header)
        assert resp["request_id"] == header["request_id"]


class TestProtocolErrors:
    def test_unknown_action_1002(self, running_server):
        resp = _request(running_server[2], _make_header("no_such_action"))
        assert resp["ok"] is False
        assert resp["error"]["code"] == errors.ERR_UNKNOWN_ACTION

    def test_audio_chunk_1005(self, running_server):
        """已定义未实现的 audio_chunk 必须返回 1005（区别于 1002）。"""
        resp = _request(running_server[2], _make_header("audio_chunk"))
        assert resp["ok"] is False
        assert resp["error"]["code"] == errors.ERR_ACTION_NOT_SUPPORTED

    def test_version_mismatch_1003(self, running_server):
        header = _make_header("health", protocol_version="9.9")
        resp = _request(running_server[2], header)
        assert resp["ok"] is False
        assert resp["error"]["code"] == errors.ERR_PROTOCOL_VERSION_MISMATCH

    def test_missing_field_1004(self, running_server):
        header = _make_header("health")
        del header["request_id"]
        resp = _request(running_server[2], header)
        assert resp["ok"] is False
        assert resp["error"]["code"] == errors.ERR_MISSING_FIELD

    def test_malformed_json_header_1001(self, running_server):
        bad = b"not-json!!"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(running_server[2])
            conn.sendall(len(bad).to_bytes(4, "big") + bad)
            buf = MessageBuffer()
            data = conn.recv(65536)
            buf.feed(data)
            header, _ = buf.next_frame()
        assert header["ok"] is False
        assert header["error"]["code"] == errors.ERR_MALFORMED_FRAME

    def test_recognize_not_ready_2001(self, running_server):
        pcm = b"\x00\x00" * 160
        header = _make_header(
            "recognize",
            audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
            audio_bytes=len(pcm),
        )
        resp = _request(running_server[2], header, pcm)
        assert resp["ok"] is False
        assert resp["error"]["code"] == errors.ERR_NOT_READY


class TestSocketAccessControl:
    """协议 §7.1 v1 强制项实测。"""

    def test_socket_file_permission_0600(self, running_server):
        mode = stat.S_IMODE(os.stat(running_server[2]).st_mode)
        assert mode == 0o600

    def test_symlink_path_rejected(self, tmp_path):
        target = tmp_path / "real.sock"
        target.touch()
        link = tmp_path / "link.sock"
        link.symlink_to(target)
        settings = Settings(socket_path=str(link), log_dir=tmp_path / "logs")
        server = SocketServer(settings, ServiceContext(settings, ServiceState()))
        with pytest.raises(SocketPathError, match="符号链接"):
            server.bind()

    def test_stale_socket_owned_by_self_replaced(self, tmp_path):
        sock_path = tmp_path / "svc.sock"
        sock_path.touch()  # 自身遗留陈旧文件
        settings = Settings(socket_path=str(sock_path), log_dir=tmp_path / "logs")
        server = SocketServer(settings, ServiceContext(settings, ServiceState()))
        server.bind()
        server.shutdown()

    def test_peer_uid_check_rejects_other_uid(self, running_server):
        """SO_PEERCRED 异 UID 拒绝（单测模拟：同机无法伪造他 UID 连接）。"""
        server, _, sock_path = running_server
        original = SocketServer._peer_uid
        SocketServer._peer_uid = staticmethod(lambda conn: os.getuid() + 1)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(5)
                conn.connect(sock_path)
                buf = MessageBuffer()
                data = conn.recv(65536)
                buf.feed(data)
                header, _ = buf.next_frame()
            assert header["ok"] is False
            assert header["error"]["code"] == errors.ERR_UNAUTHORIZED_PEER
        finally:
            SocketServer._peer_uid = original

    def test_socket_file_removed_on_shutdown(self, running_server):
        server, _, sock_path = running_server
        server.shutdown()
        assert not os.path.exists(sock_path)
