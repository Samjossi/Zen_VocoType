"""真实语音样本识别质量旁证测试（slow，非验收项）。

以 ``参考代码/示例语音和文字`` 的 9 组真实录音（16kHz/16bit/单声道）走完整
协议链路（recognize action），断言：
1. 全部样本识别成功（ok=true）
2. 识别文本非空
3. 与参考文本的字符级重合率 ≥ 0.5（宽松 sanity 阈值，识别质量非阶段 1 验收项）

结果打印供阶段 1 验收记录引用。
"""

import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

import pytest

from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_service.config import COMPONENT_ROOT

pytestmark = pytest.mark.slow

SAMPLES_DIR = COMPONENT_ROOT.parent / "参考代码" / "示例语音和文字"
TEST_SOCKET = str(
    Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zen_vocotype_sample_test.sock"
)

#: 字符级重合率宽松阈值（sanity check，非质量验收）
OVERLAP_THRESHOLD = 0.5


def _read_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        return w.readframes(w.getnframes())


def _overlap(ref: str, hyp: str) -> float:
    """去标点空白后的字符级重合率（ref 中被 hyp 覆盖的字符比例）。"""
    clean = lambda s: re.sub(r"[\s，。,.!！?？、~～…—\-]+", "", s)
    ref_c, hyp_c = clean(ref), clean(hyp)
    if not ref_c:
        return 1.0
    hits = sum(1 for ch in ref_c if ch in hyp_c)
    return hits / len(ref_c)


def _rpc(sock_path: str, action: str, body: bytes = b"", **extra) -> dict:
    header = {
        "action": action,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str(uuid.uuid4()),
    }
    header.update(extra)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(90)
        conn.connect(sock_path)
        conn.sendall(encode_frame(header, body))
        buf = MessageBuffer()
        while True:
            data = conn.recv(1 << 20)
            assert data, f"{action}: 连接被关闭但未收到响应"
            buf.feed(data)
            frame = buf.next_frame()
            if frame is not None:
                return frame[0]


def _wait_ready(sock_path: str, timeout: float = 120.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            resp = _rpc(sock_path, "ready")
            if resp["ok"] and resp["payload"].get("ready"):
                return
            if not resp["ok"]:
                raise RuntimeError(f"模型加载失败: {resp['error']}")
        except OSError:
            pass
        time.sleep(0.2)
    raise TimeoutError("服务未就绪")


@pytest.fixture(scope="module")
def service():
    if os.path.exists(TEST_SOCKET):
        os.unlink(TEST_SOCKET)
    env = dict(os.environ)
    env["ZEN_VOCOTYPE_SERVICE_SOCKET_PATH"] = TEST_SOCKET
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(COMPONENT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(TEST_SOCKET)
        yield proc
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=15)


def test_sample_recognition(service, capsys):
    wavs = sorted(SAMPLES_DIR.glob("*.wav"))
    assert len(wavs) >= 9, f"样本不足: {len(wavs)}"
    rows = []
    for wav_path in wavs:
        txt_path = wav_path.with_suffix(".txt")
        ref = txt_path.read_text(encoding="utf-8").strip()
        pcm = _read_pcm(wav_path)
        resp = _rpc(
            TEST_SOCKET,
            "recognize",
            pcm,
            audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
            audio_bytes=len(pcm),
        )
        assert resp["ok"] is True, f"{wav_path.name} 识别失败: {resp.get('error')}"
        hyp = resp["payload"]["text"]
        assert hyp, f"{wav_path.name} 识别文本为空"
        ratio = _overlap(ref, hyp)
        rows.append((wav_path.name, round(ratio, 3), ref[:30], hyp[:30]))
    with capsys.disabled():
        print("\n样本识别对照（文件 | 重合率 | 参考 | 识别）:")
        for row in rows:
            print("  ", row)
    low = [r for r in rows if r[1] < OVERLAP_THRESHOLD]
    assert not low, f"以下样本重合率低于 {OVERLAP_THRESHOLD}: {low}"
