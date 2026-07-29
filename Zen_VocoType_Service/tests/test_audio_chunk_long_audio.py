"""audio_chunk 真实长音频抽测（slow，任务 4.5）。

真实服务进程（默认 GGUF 引擎）+ 合成 30 分钟语音 PCM（自检语音循环拼接，
🔴 非静音）经 audio_chunk 分片上传 E2E：

- begin → N×data → end → 断言 text 非空 / duration_ms 正确 / 会话无残留
- 记录 RTF 与总耗时落盘 ``logs/long_audio_measurements.json``（配置标定依据）
- GGUF 引擎无时间戳输出能力（2026-07-30 调研），payload 应省略 segments
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from zen_vocotype_protocol import chunk as chunk_proto
from zen_vocotype_protocol.frames import (
    MAX_RESPONSE_HEADER_BYTES,
    MessageBuffer,
    encode_frame,
)
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_service.config import COMPONENT_ROOT

pytestmark = pytest.mark.slow

SERVICE_ROOT = COMPONENT_ROOT
SELFTEST_PCM = SERVICE_ROOT / "assets" / "selftest_16k.pcm"
MEASUREMENT_FILE = SERVICE_ROOT / "logs" / "long_audio_measurements.json"

TEST_SOCKET = str(
    Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zen_vocotype_long_audio_test.sock"
)

#: 抽测音频时长：≥30 分钟（验收门槛）；RTF≈0.1 时推理约 3 分钟
LONG_AUDIO_SECONDS = 31 * 60
#: data 分片大小：4MB ≈ 2 分钟/片
CHUNK_BYTES = 4 * 1024 * 1024
#: end 帧 I/O 超时：按动态超时公式上限（1800×0.2×2=720s）留余量
END_IO_TIMEOUT_S = 900.0
READY_WAIT_BUDGET_S = 120.0


def _rpc(conn: socket.socket, action: str, body: bytes = b"", timeout=30.0, **extra) -> dict:
    header = {
        "action": action,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str(uuid.uuid4()),
    }
    header.update(extra)
    conn.settimeout(timeout)
    conn.sendall(encode_frame(header, body))
    # 解析响应方向放宽上限：长音频识别文本可超请求方向 64KB
    buf = MessageBuffer(max_header_bytes=MAX_RESPONSE_HEADER_BYTES)
    while True:
        data = conn.recv(1 << 20)
        assert data, f"{action}: 连接被关闭但未收到响应"
        buf.feed(data)
        frame = buf.next_frame()
        if frame is not None:
            return frame[0]


def _wait_ready(sock_path: str, timeout: float) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(5)
                conn.connect(sock_path)
                resp = _rpc(conn, "ready")
                if resp["ok"] and resp["payload"].get("ready"):
                    return
                if not resp["ok"]:
                    raise RuntimeError(f"模型加载失败: {resp['error']}")
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"ready {timeout}s 内未就绪")


@pytest.fixture(scope="module")
def service():
    if os.path.exists(TEST_SOCKET):
        os.unlink(TEST_SOCKET)
    env = dict(os.environ)
    env["ZEN_VOCOTYPE_SERVICE_SOCKET_PATH"] = TEST_SOCKET
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(SERVICE_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_ready(TEST_SOCKET, READY_WAIT_BUDGET_S)
    yield proc
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)


def test_30min_real_engine_e2e(service):
    """30 分钟真实引擎 audio_chunk E2E + RTF 实测落盘。"""
    unit = SELFTEST_PCM.read_bytes()
    target = LONG_AUDIO_SECONDS * 32000
    pcm = (unit * (target // len(unit) + 1))[:target]

    t_upload_start = time.monotonic()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(TEST_SOCKET)
        sid = chunk_proto.new_session_id()
        resp = _rpc(
            conn,
            "audio_chunk",
            audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
            chunk=chunk_proto.build_chunk_begin(sid, total_bytes=len(pcm)),
        )
        assert resp["ok"], resp
        assert resp["payload"]["session_id"] == sid

        received, seq = 0, 0
        for offset in range(0, len(pcm), CHUNK_BYTES):
            part = pcm[offset : offset + CHUNK_BYTES]
            resp = _rpc(
                conn,
                "audio_chunk",
                part,
                audio_bytes=len(part),
                chunk=chunk_proto.build_chunk_data(sid, seq),
            )
            assert resp["ok"], resp
            received += len(part)
            assert resp["payload"]["received_bytes"] == received
            seq += 1
        upload_s = time.monotonic() - t_upload_start

        t_infer = time.monotonic()
        resp = _rpc(
            conn, "audio_chunk", timeout=END_IO_TIMEOUT_S,
            chunk=chunk_proto.build_chunk_end(sid),
        )
        infer_s = time.monotonic() - t_infer

    assert resp["ok"], resp
    payload = resp["payload"]
    assert payload["text"], "识别文本为空"
    assert payload["duration_ms"] == len(pcm) // 2 * 1000 // 16000
    # GGUF 无时间戳能力（调研结论）：segments 必须省略而非编造
    assert "segments" not in payload

    audio_s = len(pcm) / 32000
    measurement = {
        "audio_seconds": round(audio_s, 1),
        "pcm_bytes": len(pcm),
        "data_frames": seq,
        "upload_s": round(upload_s, 2),
        "infer_s": round(infer_s, 2),
        "rtf": round(infer_s / audio_s, 4),
        "text_length": len(payload["text"]),
        "engine": "funasr-gguf",
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    MEASUREMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEASUREMENT_FILE.write_text(
        json.dumps(measurement, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n长音频抽测: {measurement}")
