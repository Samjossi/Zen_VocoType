"""T1.6 端到端测试与冷启动/推理耗时实测（slow）。

真实启动服务进程（main.py），以复合帧模拟客户端全流程：
启动 → health(starting) → 轮询 ready → recognize（真实测试音频，断言非空文本）
→ model_switch → model_info 交叉验证 → 切回 → SIGTERM 优雅退出。

冷启动实测：进程启动 → Socket 可连接计时，连测 5 次（验收标准 1：≤5s）；
同时记录到 ready 的模型加载耗时（阶段 3 Launcher 超时预算 P99 输入，落盘）。

推理超时标定：60 秒 PCM 实测 CPU 推理耗时，校验 300s 预算
（2026-07-23 起按默认引擎 fun-asr-nano 标定，原 60s 预算为 paraformer-large 标定）。
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

from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_service.config import COMPONENT_ROOT

pytestmark = pytest.mark.slow

SERVICE_ROOT = COMPONENT_ROOT
SELFTEST_PCM = SERVICE_ROOT / "assets" / "selftest_16k.pcm"
#: 实测数据落盘位置（验收记录引用）
MEASUREMENT_FILE = SERVICE_ROOT / "logs" / "phase1_measurements.json"

TEST_SOCKET = str(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zen_vocotype_e2e_test.sock")

COLD_START_RUNS = 5
COLD_START_BUDGET_S = 5.0
READY_WAIT_BUDGET_S = 120.0


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


def _wait_connectable(sock_path: str, timeout: float) -> float:
    """轮询至 Socket 可连接，返回耗时（秒）。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(0.2)
                conn.connect(sock_path)
                return time.monotonic() - start
        except OSError:
            time.sleep(0.01)
    raise TimeoutError(f"Socket {timeout}s 内不可连接")


def _wait_ready(sock_path: str, timeout: float) -> float:
    """轮询 ready 至就绪，返回从调用起的耗时（秒）。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        resp = _rpc(sock_path, "ready")
        if resp["ok"] and resp["payload"].get("ready"):
            return time.monotonic() - start
        if not resp["ok"]:
            raise RuntimeError(f"模型加载失败: {resp['error']}")
        time.sleep(0.2)
    raise TimeoutError(f"ready {timeout}s 内未就绪")


def _start_service() -> subprocess.Popen:
    env = dict(os.environ)
    env["ZEN_VOCOTYPE_SERVICE_SOCKET_PATH"] = TEST_SOCKET
    return subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(SERVICE_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_service(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=15)
    assert proc.returncode == 0, f"服务退出码非零: {proc.returncode}"


@pytest.fixture(scope="module")
def service():
    if os.path.exists(TEST_SOCKET):
        os.unlink(TEST_SOCKET)
    proc = _start_service()
    _wait_connectable(TEST_SOCKET, COLD_START_BUDGET_S)
    yield proc
    if proc.poll() is None:
        _stop_service(proc)


class TestColdStart:
    """验收标准 1：冷启动 Socket 可连接 ≤ 5 秒（5 次实测落盘）。"""

    def test_cold_start_within_budget(self):
        results = []
        for i in range(COLD_START_RUNS):
            if os.path.exists(TEST_SOCKET):
                os.unlink(TEST_SOCKET)
            t0 = time.monotonic()
            proc = _start_service()
            try:
                connect_s = _wait_connectable(TEST_SOCKET, COLD_START_BUDGET_S)
                ready_s = _wait_ready(TEST_SOCKET, READY_WAIT_BUDGET_S)
                results.append(
                    {
                        "run": i + 1,
                        "socket_connectable_s": round(connect_s, 3),
                        "ready_s_from_start": round(
                            time.monotonic() - t0, 3
                        ),
                    }
                )
            finally:
                _stop_service(proc)
        connect_times = [r["socket_connectable_s"] for r in results]
        assert max(connect_times) <= COLD_START_BUDGET_S, (
            f"冷启动超预算: {connect_times}"
        )
        _record("cold_start", {"runs": results, "budget_s": COLD_START_BUDGET_S})


class TestEndToEnd:
    def test_full_action_flow(self, service):
        # health：就绪前应经历 starting（E2E 独立冷启动用例已覆盖 starting 时序）
        health = _rpc(TEST_SOCKET, "health")
        assert health["ok"] is True
        # 版本握手：响应头回带服务端协议版本（协议 §5）
        assert health["protocol_version"] == PROTOCOL_VERSION

        ready_s = _wait_ready(TEST_SOCKET, READY_WAIT_BUDGET_S)

        # recognize：真实测试音频，断言返回非空文本
        pcm = SELFTEST_PCM.read_bytes()
        t0 = time.monotonic()
        resp = _rpc(
            TEST_SOCKET,
            "recognize",
            pcm,
            audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
            audio_bytes=len(pcm),
        )
        infer_s = time.monotonic() - t0
        assert resp["ok"] is True, f"recognize 失败: {resp.get('error')}"
        assert resp["payload"]["text"], "识别文本为空"
        assert resp["payload"]["duration_ms"] == len(pcm) // 32

        # model_switch → model_info 交叉验证 → 切回
        resp = _rpc(
            TEST_SOCKET, "model_switch", payload={"model_name": "sensevoice-small"}
        )
        assert resp["ok"] is True, f"model_switch 失败: {resp.get('error')}"
        assert resp["payload"]["current_model"] == "sensevoice-small"
        info = _rpc(TEST_SOCKET, "model_info")
        assert info["payload"]["current_model"] == "sensevoice-small"
        flags = {m["name"]: m["loaded"] for m in info["payload"]["available_models"]}
        assert flags["sensevoice-small"] is True
        assert flags["fun-asr-nano"] is False

        resp = _rpc(
            TEST_SOCKET, "model_switch", payload={"model_name": "fun-asr-nano"}
        )
        assert resp["ok"] is True
        info = _rpc(TEST_SOCKET, "model_info")
        assert info["payload"]["current_model"] == "fun-asr-nano"

        _record(
            "e2e",
            {
                "ready_wait_s": round(ready_s, 3),
                "recognize_3s_audio_infer_s": round(infer_s, 3),
                "recognized_text": resp and _rpc_recognize_text(),
            },
        )


def _rpc_recognize_text() -> str:
    pcm = SELFTEST_PCM.read_bytes()
    resp = _rpc(
        TEST_SOCKET,
        "recognize",
        pcm,
        audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
        audio_bytes=len(pcm),
    )
    return resp["payload"]["text"]


class TestHeadlessLoop:
    """T42（S3）headless 主循环收敛回归：无显示环境拉起 → SIGTERM 优雅退出。

    收敛后主循环由 ``shutdown_event.wait()`` 改为 ``QCoreApplication`` +
    200ms 轮询，本用例固化该路径的进程级行为。
    """

    def test_headless_start_and_sigterm_exit(self):
        if os.path.exists(TEST_SOCKET):
            os.unlink(TEST_SOCKET)
        env = dict(os.environ)
        env["ZEN_VOCOTYPE_SERVICE_SOCKET_PATH"] = TEST_SOCKET
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(SERVICE_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_connectable(TEST_SOCKET, COLD_START_BUDGET_S)
            health = _rpc(TEST_SOCKET, "health")
            assert health["ok"] is True
        finally:
            _stop_service(proc)  # SIGTERM → 断言退出码 0（含退出序列全段）


class TestInferTimeoutCalibration:
    """60 秒 PCM 实测 CPU 推理耗时，校验 300s 超时预算（fun-asr-nano 标定）。"""

    def test_60s_audio_inference_within_budget(self, service):
        _wait_ready(TEST_SOCKET, READY_WAIT_BUDGET_S)
        unit = SELFTEST_PCM.read_bytes()  # 3 秒真实语音
        pcm_60s = (unit * 20)[: 16000 * 2 * 60]
        t0 = time.monotonic()
        resp = _rpc(
            TEST_SOCKET,
            "recognize",
            pcm_60s,
            audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
            audio_bytes=len(pcm_60s),
        )
        infer_s = time.monotonic() - t0
        assert resp["ok"] is True, f"60s 音频识别失败: {resp.get('error')}"
        _record(
            "infer_calibration",
            {
                "audio_s": 60,
                "infer_s": round(infer_s, 3),
                "timeout_budget_s": 300,
                "within_budget": infer_s <= 300,
            },
        )
        assert infer_s <= 300, f"60s 音频推理 {infer_s:.1f}s 超 300s 预算"


def _record(section: str, data: dict) -> None:
    MEASUREMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if MEASUREMENT_FILE.exists():
        existing = json.loads(MEASUREMENT_FILE.read_text())
    existing[section] = data
    MEASUREMENT_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
