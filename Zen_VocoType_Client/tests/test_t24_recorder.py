"""T2.4 单元测试：录音队列协议、拼接完整性、上限截断、设备探测。"""

import numpy as np
import pytest

from zen_vocotype_client.recorder.recorder import (
    SAMPLE_DTYPE,
    DeviceUnavailableError,
    Recorder,
)

from zen_vocotype_protocol.paths import DEFAULT_SAMPLE_RATE, DEFAULT_SAMPLE_WIDTH


def _sine_block(seconds: float, freq: float = 440.0) -> np.ndarray:
    """合成 int16 正弦音频块（形状与 sounddevice 回调一致：(frames, 1)）。"""
    frames = int(DEFAULT_SAMPLE_RATE * seconds)
    t = np.arange(frames) / DEFAULT_SAMPLE_RATE
    wave = (np.sin(2 * np.pi * freq * t) * 32767 * 0.5).astype(np.int16)
    return wave.reshape(-1, 1)


def _feed(recorder: Recorder, block: np.ndarray) -> None:
    recorder._audio_callback(block, len(block), None, None)


class TestCallbackQueueProtocol:
    def test_blocks_enqueue_lossless(self):
        rec = Recorder()
        block = _sine_block(0.1)
        _feed(rec, block)
        assert rec._queue.get_nowait() == block.tobytes()
        assert rec._queue.empty()

    def test_concat_integrity(self):
        """多块拼接后字节序/长度与注入序列完全一致（尾音完整）。"""
        rec = Recorder()
        blocks = [_sine_block(0.05, f) for f in (440.0, 550.0, 660.0)]
        for b in blocks:
            _feed(rec, b)
        expected = b"".join(b.tobytes() for b in blocks)
        # 经 start/stop 路径拼接（绕过真实流，直接置态）
        rec._recording = True
        rec._stream = _FakeStream()
        pcm = rec.stop()
        assert pcm == expected
        assert len(pcm) == sum(len(b) for b in blocks) * DEFAULT_SAMPLE_WIDTH

    def test_max_reached_event_and_hook(self):
        """到达上限：置事件 + 触发钩子（仅一次），回调不抛异常。"""
        hits: list[int] = []
        rec = Recorder(max_record_seconds=1, on_max_reached=lambda: hits.append(1))
        _feed(rec, _sine_block(0.6))
        assert not rec.max_reached
        _feed(rec, _sine_block(0.6))  # 累计 1.2s > 1s 上限
        assert rec.max_reached
        _feed(rec, _sine_block(0.6))  # 重复超限不重复触发
        assert hits == [1]

    def test_start_resets_state(self):
        rec = Recorder(max_record_seconds=1)
        _feed(rec, _sine_block(1.2))
        assert rec.max_reached
        rec._stream = _FakeStream()
        rec.start()
        assert not rec.max_reached
        assert rec._queue.empty()
        assert rec.recording
        rec.stop()
        assert not rec.recording

    def test_stop_without_start_returns_empty(self):
        assert Recorder().stop() == b""


class _FakeStream:
    """绕过真实声卡的流替身（仅支撑 start/stop 状态路径）。"""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


class TestDeviceProbe:
    def test_nonexistent_device_rejected(self):
        rec = Recorder(device="no_such_device_zen_vocotype")
        with pytest.raises(DeviceUnavailableError):
            rec.probe_device()

    def test_default_device_probe(self):
        """本机默认输入设备探测（有设备须通过；无设备须明确报错而非静默）。"""
        rec = Recorder()
        try:
            info = rec.probe_device()
        except DeviceUnavailableError as exc:
            pytest.skip(f"本机无输入设备（明确报错路径已覆盖）: {exc}")
        assert info["name"]

    def test_real_stream_smoke(self):
        """真实流冒烟：start→短暂录音→stop 得非空 PCM（设备可用时）。"""
        import time

        rec = Recorder()
        try:
            rec.probe_device()
        except DeviceUnavailableError as exc:
            pytest.skip(f"本机无输入设备: {exc}")
        rec.start()
        time.sleep(0.3)
        pcm = rec.stop()
        rec.close()
        assert len(pcm) > 0
        assert len(pcm) % DEFAULT_SAMPLE_WIDTH == 0
