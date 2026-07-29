"""audio_chunk 长音频集成测试（v1.4，CP5 任务 4.3/4.4）。

真实 Socket 服务 + 假模型 worker，复合帧分片上传 E2E：

- 合成 ≥30 分钟 PCM（自检语音循环拼接，🔴 禁止纯静音——静音不触发 VAD 分段路径）
  begin → N×data → end → 校验 text/duration_ms/segments 结构
- 乱序 4003、超上限 4004、中断重连后会话已销毁 4003
- 并发/互斥：A 连接会话上传中，B 连接单帧 recognize 正常识别（worker 串行互斥语义）
"""

import os
import socket
import time
import uuid

import pytest

from zen_vocotype_protocol import chunk as chunk_proto
from zen_vocotype_protocol import errors
from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_service.config import COMPONENT_ROOT, ModelEntry, Settings
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.inference.worker import InferenceWorker
from zen_vocotype_service.server import SocketServer
from zen_vocotype_service.state import ServiceState

SELFTEST_PCM = COMPONENT_ROOT / "assets" / "selftest_16k.pcm"

#: 长音频合成目标：≥30 分钟（任务 4.3 验收门槛）
LONG_AUDIO_SECONDS = 31 * 60
#: data 分片大小（字节）：1MB ≈ 32 秒/片，远低于 MAX_BODY_BYTES
CHUNK_BYTES = 1024 * 1024


def _synthesize_long_pcm(seconds: int) -> bytes:
    """自检语音循环拼接合成长 PCM（🔴 非静音：真实语音片段循环）。"""
    unit = SELFTEST_PCM.read_bytes()
    target = seconds * 32000
    repeats = target // len(unit) + 1
    return (unit * repeats)[:target]


class _SessionClient:
    """测试客户端：一条连接收发多帧（begin → N×data → end）。"""

    def __init__(self, sock_path: str) -> None:
        self._conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._conn.settimeout(30)
        self._conn.connect(sock_path)
        self._buf = MessageBuffer()

    def request(self, header: dict, body: bytes = b"") -> dict:
        header = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": str(uuid.uuid4()),
            **header,
        }
        self._conn.sendall(encode_frame(header, body))
        while True:
            data = self._conn.recv(65536)
            assert data, "连接被关闭但未收到响应"
            self._buf.feed(data)
            frame = self._buf.next_frame()
            if frame is not None:
                return frame[0]

    def close(self) -> None:
        self._conn.close()


class _FakeModel:
    """假 funasr 模型：返回带 sentence_info 的结构（覆盖 segments 解析路径）。"""

    def generate(self, **kwargs):
        return [
            {
                "text": "长音频识别结果",
                "sentence_info": [
                    {"start": 0, "end": 1000, "text": "长音频识别结果"},
                ],
            }
        ]


class _FakeLoaded:
    def __init__(self, name, entry=None):
        self.name = name
        self.model = _FakeModel()
        self.entry = entry or ModelEntry(model_id="fake")

    def release(self):
        pass


class _FakeManager:
    def __init__(self):
        self.current = _FakeLoaded("fake-model")

    def switch(self, model_name):
        pass

    def release(self):
        pass


@pytest.fixture()
def running_server(tmp_path):
    sock_path = str(tmp_path / "svc.sock")
    settings = Settings(
        socket_path=sock_path,
        log_dir=tmp_path / "logs",
        chunk_session_dir=tmp_path / "sessions",
    )
    ctx = ServiceContext(settings, ServiceState())
    ctx.model_manager = _FakeManager()
    worker = InferenceWorker(settings, ctx.model_manager)
    worker.start()
    ctx.worker = worker
    ctx.state.mark_ready("fake-model")
    server = SocketServer(settings, ctx)
    server.start(background=True)
    deadline = time.monotonic() + 5
    while not os.path.exists(sock_path):
        if time.monotonic() > deadline:
            raise RuntimeError("服务未及时 bind")
        time.sleep(0.01)
    yield server, ctx, sock_path
    server.shutdown()
    worker.stop()


class TestLongAudioE2E:
    def test_30min_chunk_upload(self, running_server):
        """≥30 分钟合成 PCM 分片上传：begin → N×data → end → 结构校验。"""
        _, ctx, sock_path = running_server
        pcm = _synthesize_long_pcm(LONG_AUDIO_SECONDS)
        assert len(pcm) >= 30 * 60 * 32000  # ≥30 分钟
        client = _SessionClient(sock_path)
        try:
            sid = chunk_proto.new_session_id()
            resp = client.request(
                {
                    "action": "audio_chunk",
                    "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
                    "chunk": chunk_proto.build_chunk_begin(sid, total_bytes=len(pcm)),
                }
            )
            assert resp["ok"] is True
            assert resp["payload"]["session_id"] == sid
            assert resp["payload"]["max_session_bytes"] == 256 * 1024 * 1024

            received = 0
            seq = 0
            for offset in range(0, len(pcm), CHUNK_BYTES):
                part = pcm[offset : offset + CHUNK_BYTES]
                resp = client.request(
                    {
                        "action": "audio_chunk",
                        "audio_bytes": len(part),
                        "chunk": chunk_proto.build_chunk_data(sid, seq),
                    },
                    part,
                )
                assert resp["ok"] is True
                received += len(part)
                assert resp["payload"]["received_bytes"] == received  # 累计进度反馈
                seq += 1

            resp = client.request(
                {"action": "audio_chunk", "chunk": chunk_proto.build_chunk_end(sid)}
            )
            assert resp["ok"] is True
            payload = resp["payload"]
            assert payload["text"] == "长音频识别结果"
            assert payload["duration_ms"] == len(pcm) // 2 * 1000 // 16000
            assert payload["segments"] == [
                {"start_ms": 0, "end_ms": 1000, "text": "长音频识别结果"}
            ]
            assert ctx.chunk_sessions.active_count == 0
            # 会话 WAV 已清理（🔴 无资源残留）
            assert list((ctx.settings.chunk_session_dir).glob("*.wav")) == []
        finally:
            client.close()


class TestAbnormalSessions:
    def test_out_of_order_4003(self, running_server):
        client = _SessionClient(running_server[2])
        try:
            sid = chunk_proto.new_session_id()
            client.request(
                {
                    "action": "audio_chunk",
                    "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
                    "chunk": chunk_proto.build_chunk_begin(sid),
                }
            )
            resp = client.request(
                {
                    "action": "audio_chunk",
                    "audio_bytes": 2,
                    "chunk": chunk_proto.build_chunk_data(sid, 1),  # 跳号
                },
                b"\x00\x00",
            )
            assert resp["ok"] is False
            assert resp["error"]["code"] == errors.ERR_SESSION_STATE
        finally:
            client.close()

    def test_too_large_4004(self, running_server):
        _, ctx, sock_path = running_server
        ctx.settings.chunk_session_max_bytes = 1024  # 运行时收紧上限
        client = _SessionClient(sock_path)
        try:
            sid = chunk_proto.new_session_id()
            resp = client.request(
                {
                    "action": "audio_chunk",
                    "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
                    "chunk": chunk_proto.build_chunk_begin(sid, total_bytes=2048),
                }
            )
            assert resp["ok"] is False
            assert resp["error"]["code"] == errors.ERR_SESSION_TOO_LARGE
        finally:
            client.close()

    def test_disconnect_destroys_session(self, running_server):
        """中断重连后会话已销毁：重连续传 data → 4003。"""
        _, ctx, sock_path = running_server
        sid = chunk_proto.new_session_id()
        client = _SessionClient(sock_path)
        client.request(
            {
                "action": "audio_chunk",
                "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
                "chunk": chunk_proto.build_chunk_begin(sid),
            }
        )
        client.request(
            {
                "action": "audio_chunk",
                "audio_bytes": 2,
                "chunk": chunk_proto.build_chunk_data(sid, 0),
            },
            b"\x00\x00",
        )
        assert ctx.chunk_sessions.active_count == 1
        client.close()
        deadline = time.monotonic() + 5
        while ctx.chunk_sessions.active_count != 0:  # 等待断连钩子生效
            if time.monotonic() > deadline:
                raise RuntimeError("断连后会话未销毁")
            time.sleep(0.05)
        # 会话 WAV 已删除（🔴 无资源残留）
        assert list(ctx.settings.chunk_session_dir.glob("*.wav")) == []
        # 重连续传同 session → 4003（🔴 禁止跨连接续传）
        client2 = _SessionClient(sock_path)
        try:
            resp = client2.request(
                {
                    "action": "audio_chunk",
                    "audio_bytes": 2,
                    "chunk": chunk_proto.build_chunk_data(sid, 1),
                },
                b"\x00\x00",
            )
            assert resp["ok"] is False
            assert resp["error"]["code"] == errors.ERR_SESSION_STATE
        finally:
            client2.close()


class TestConcurrency:
    def test_recognize_during_upload(self, running_server):
        """A 连接会话上传中，B 连接单帧 recognize 正常识别（任务 4.4）。"""
        _, _, sock_path = running_server
        client_a = _SessionClient(sock_path)
        client_b = _SessionClient(sock_path)
        try:
            sid = chunk_proto.new_session_id()
            resp = client_a.request(
                {
                    "action": "audio_chunk",
                    "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
                    "chunk": chunk_proto.build_chunk_begin(sid),
                }
            )
            assert resp["ok"] is True
            pcm = b"\x01\x00" * 16000
            client_a.request(
                {
                    "action": "audio_chunk",
                    "audio_bytes": len(pcm),
                    "chunk": chunk_proto.build_chunk_data(sid, 0),
                },
                pcm,
            )
            # 上传进行中，另一连接 recognize 正常（不同连接各自独立）
            resp = client_b.request(
                {
                    "action": "recognize",
                    "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
                    "audio_bytes": len(pcm),
                },
                pcm,
            )
            assert resp["ok"] is True
            assert resp["payload"]["text"] == "长音频识别结果"
            assert resp["payload"]["duration_ms"] == 1000
        finally:
            client_a.close()
            client_b.close()
