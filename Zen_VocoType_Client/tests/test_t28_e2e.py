"""T2.8 端到端集成测试：真实服务端 + 装配层全流程。

- 真实服务端子进程（独立测试 Socket 路径，与生产/dev 隔离）
- 测试钩子注入热键事件（``inject_press/inject_release`` 等价热键按住/松开）
- 真实语音样本（``参考代码/示例语音和文字``，16kHz/16bit/单声道）验证识别往返
- 服务端缺席 / 版本不一致场景的明确提示（验收标准 2）

粘贴模拟以 fake paster 替身（🔴 避免向当前焦点窗口真实注入按键；
真实粘贴链路属人工验证项）。
"""

import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from zen_vocotype_client.app import ClientApp, MSG_SERVER_ABSENT
from zen_vocotype_client.config import Settings
from zen_vocotype_client.output.paster import PasterBackend
from zen_vocotype_client.state_machine import State
from zen_vocotype_client.transcribe.client import ProtocolClient

from stub_server import StubServer

pytestmark = pytest.mark.slow

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = PROJECT_ROOT / "参考代码" / "示例语音和文字"
SERVICE_MAIN = PROJECT_ROOT / "Zen_VocoType_Service" / "main.py"
TEST_SOCKET = str(
    Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zen_vocotype_client_e2e.sock"
)


class FakePaster(PasterBackend):
    def __init__(self) -> None:
        self.count = 0

    def paste(self) -> None:
        self.count += 1


class RecordingNotifier:
    """通知记录替身（托盘通知属视觉通道，E2E 断言改走本记录）。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def notify(self, title: str, message: str, *, key: str | None = None) -> bool:
        self.messages.append(message)
        return True


def wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Qt 事件循环内等待条件成立（🔴 非固定 sleep：条件轮询 + 超时上限）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


def read_sample_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        return w.readframes(w.getnframes())


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def service():
    """真实服务端子进程：等待协议级就绪后交付，测试结束精确回收。"""
    env = os.environ | {"ZEN_VOCOTYPE_SERVICE_SOCKET_PATH": TEST_SOCKET}
    proc = subprocess.Popen(
        [str(PROJECT_ROOT / ".venv/bin/python"), str(SERVICE_MAIN)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        client = ProtocolClient(TEST_SOCKET)
        deadline = time.monotonic() + 120  # 模型加载预算（阶段1实测约 15s，留足余量）
        ready = False
        while time.monotonic() < deadline and not ready:
            try:
                client.connect()
                ready = client.health().get("status") == "ready"
            except Exception:
                time.sleep(0.5)
        client.close()
        if not ready:
            proc.terminate()
            pytest.fail("服务端 120s 内未就绪")
        yield TEST_SOCKET
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        Path(TEST_SOCKET).unlink(missing_ok=True)


def _make_app(socket_path: str) -> ClientApp:
    settings = Settings(socket_path=socket_path)
    client = ClientApp(settings)
    fake_paster = FakePaster()
    recorder = RecordingNotifier()
    client._pipeline._paster = fake_paster
    client._notifier = recorder
    client.test_hooks = (fake_paster, recorder)  # 防 GC + 断言入口
    return client


class TestFullFlow:
    def test_press_record_release_output_cycle(self, qapp, service):
        """全流程：注入按住→录音→松开→识别→输出→状态归位（验收标准 1）。"""
        client = _make_app(service)
        fake_paster, notifier = client.test_hooks
        assert client.start() == 0
        try:
            assert wait_until(lambda: client.state is State.IDLE, 5)
            client.inject_press()
            assert wait_until(lambda: client.state is State.RECORDING, 2)
            time.sleep(0.5)  # 真实录音窗口（麦克风环境声，文本不可预期但流程必须打通）
            client.inject_release()
            assert wait_until(lambda: client.state is State.TRANSCRIBING, 2)
            assert wait_until(lambda: client.state is State.IDLE, 30)
            assert fake_paster.count == 1  # 输出流水线执行（剪贴板+粘贴+恢复）
        finally:
            client.shutdown()

    def test_recognize_real_sample_via_client(self, qapp, service):
        """协议级：客户端直发真实语音样本，返回非空文本且与参考重合。"""
        samples = sorted(SAMPLES_DIR.glob("*.wav"))
        assert samples, "示例语音目录为空"
        pcm = read_sample_pcm(samples[0])
        reference = samples[0].with_suffix(".txt").read_text(encoding="utf-8").strip()

        client = ProtocolClient(service)
        client.connect()
        payload = client.recognize(pcm)
        client.close()
        assert payload["text"], "识别文本为空"
        clean = lambda s: re.sub(r"[\s，。,.!！?？]+", "", s)
        hits = sum(1 for ch in clean(reference) if ch in clean(payload["text"]))
        assert hits / max(1, len(clean(reference))) >= 0.5, (
            f"识别文本与参考重合率过低：{payload['text']!r} vs {reference!r}"
        )


class TestFailureScenarios:
    def test_server_absent_explicit_notification(self, qapp, tmp_path):
        """服务端缺席：托盘灰态 + 明确提示，无崩溃无静默（验收标准 2）。"""
        client = _make_app(str(tmp_path / "absent.sock"))
        _, notifier = client.test_hooks
        assert client.start() == 0
        try:
            assert wait_until(lambda: any("服务端未运行" in m for m in notifier.messages), 5)
            # 缺席下按住说话：识别失败 → 明确提示 → 状态归位
            client.inject_press()
            assert wait_until(lambda: client.state is State.RECORDING, 2)
            time.sleep(0.2)
            client.inject_release()
            assert wait_until(lambda: client.state is State.IDLE, 15)
            assert any("服务端未运行" in m for m in notifier.messages)
        finally:
            client.shutdown()

    def test_version_mismatch_explicit_error(self, qapp, tmp_path):
        """版本不一致：断开 + 明确报错（🔴 禁止静默继续）。"""
        stub = StubServer(tmp_path / "stub.sock", protocol_version="9.9.9")
        stub.start()
        try:
            client = _make_app(stub.socket_path)
            _, notifier = client.test_hooks
            assert client.start() == 0
            try:
                assert wait_until(
                    lambda: any("版本不兼容" in m for m in notifier.messages), 5
                )
            finally:
                client.shutdown()
        finally:
            stub.stop()

    def test_model_switching_message(self, qapp, tmp_path):
        """模型切换中（2002 model_switching）：专属提示文案。"""
        stub = StubServer(
            tmp_path / "stub.sock", recognize_error=(2002, "model_switching")
        )
        stub.start()
        try:
            client = _make_app(stub.socket_path)
            _, notifier = client.test_hooks
            assert client.start() == 0
            try:
                client.inject_press()
                assert wait_until(lambda: client.state is State.RECORDING, 2)
                time.sleep(0.2)
                client.inject_release()
                assert wait_until(lambda: client.state is State.IDLE, 15)
                assert any("模型切换中" in m for m in notifier.messages)
            finally:
                client.shutdown()
        finally:
            stub.stop()
