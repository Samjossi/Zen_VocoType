"""录音模块（选型四：InputStream 回调 + 线程安全队列累积）。

- 16kHz/16bit/单声道（契约库 ``paths`` 冻结常量，🔴 禁止本组件另写数值）
- 🔴 流生命周期：每次 ``start()`` 新建 ``InputStream``、``stop()`` 即 ``close()``
  释放设备（按下占用、松开释放）。2026-07-21 实测：若实例复用仅 stop 不 close，
  ALSA PCM 保持打开 → PipeWire 捕获节点以 ``[paused]`` 残留 →
  GNOME 麦克风隐私指示常亮不灭 + 设备被本进程持续独占（其他应用抢不到麦克风）
- 🔴 红线（选型一）：sounddevice 回调线程内**零业务调用**——回调只做
  「音频块写入 ``queue.Queue`` + 样本计数 + 达上限置 ``threading.Event``」，
  上限通知经 ``on_max_reached`` 钩子（装配层注册 Qt Signal.emit）
- 设备缺席**启动即明确报错**（🔴 禁止录音时才发现）：``probe_device`` 于
  应用启动序列调用
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd
from loguru import logger

from zen_vocotype_protocol.paths import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_WIDTH,
)

#: numpy 采样 dtype（16bit 有符号整数，与 DEFAULT_SAMPLE_WIDTH=2 对应）
SAMPLE_DTYPE = "int16"


class DeviceUnavailableError(Exception):
    """录音设备缺席或打开失败（启动即报错，🔴 禁止延迟到录音时）。"""


class Recorder:
    """不定长录音器：按住多久录多久，上限自动截断。"""

    def __init__(
        self,
        *,
        device: str | int | None = None,
        max_record_seconds: int = 60,
        on_max_reached: Callable[[], None] | None = None,
    ) -> None:
        self._device = device
        self._max_samples = max_record_seconds * DEFAULT_SAMPLE_RATE
        self._on_max_reached = on_max_reached
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._samples_seen = 0
        self._max_reached = threading.Event()

    # ------------------------------------------------------------------ 探测

    def probe_device(self) -> dict:
        """启动期设备探测：返回实际设备信息，缺席即抛明确异常。

        :raises DeviceUnavailableError: 无可用输入设备或设备不支持的采样参数
        """
        try:
            info = sd.query_devices(self._device, "input")
        except Exception as exc:
            raise DeviceUnavailableError(
                f"录音输入设备不可用（device={self._device!r}）: {exc}"
            ) from exc
        if int(info["max_input_channels"]) < DEFAULT_CHANNELS:
            raise DeviceUnavailableError(
                f"设备 {info['name']!r} 无输入声道（max_input_channels="
                f"{info['max_input_channels']}）"
            )
        try:
            sd.check_input_settings(
                device=self._device,
                channels=DEFAULT_CHANNELS,
                dtype=SAMPLE_DTYPE,
                samplerate=DEFAULT_SAMPLE_RATE,
            )
        except Exception as exc:
            raise DeviceUnavailableError(
                f"设备 {info['name']!r} 不支持 "
                f"{DEFAULT_SAMPLE_RATE}Hz/{SAMPLE_DTYPE}/{DEFAULT_CHANNELS}ch: {exc}"
            ) from exc
        logger.info(
            "录音设备探测通过：{}（实际采样率 {}Hz）",
            info["name"],
            info["default_samplerate"],
        )
        return {"name": str(info["name"]), "default_samplerate": float(info["default_samplerate"])}

    # ------------------------------------------------------------------ 启停

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def max_reached(self) -> bool:
        return self._max_reached.is_set()

    def start(self) -> None:
        """开始录音（每次新建流：按下才占用设备）。"""
        if self._recording:
            logger.warning("录音进行中，忽略重复 start")
            return
        self._drain_queue()
        self._samples_seen = 0
        self._max_reached.clear()
        self._stream = self._create_stream()
        self._stream.start()
        self._recording = True
        logger.debug("录音开始")

    def _create_stream(self) -> sd.InputStream:
        """构造新的输入流（测试可替换此方法注入替身，不依赖真实声卡）。"""
        return sd.InputStream(
            samplerate=DEFAULT_SAMPLE_RATE,
            channels=DEFAULT_CHANNELS,
            dtype=SAMPLE_DTYPE,
            device=self._device,
            callback=self._audio_callback,
        )

    def stop(self) -> bytes:
        """停止录音、关闭流释放设备（GNOME 指示即灭/设备解除独占），
        拼接返回完整 PCM（16bit 小端字节流，尾音完整）。"""
        if not self._recording:
            logger.warning("未在录音，stop 返回空音频")
            return b""
        assert self._stream is not None
        self._stream.stop()
        self._stream.close()  # 🔴 必须 close：仅 stop 会使 PipeWire 节点 [paused] 残留
        self._stream = None
        self._recording = False
        chunks: list[bytes] = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        pcm = b"".join(chunks)
        logger.info(
            "录音停止：{:.1f} 秒，{} 字节{}",
            len(pcm) / DEFAULT_SAMPLE_WIDTH / DEFAULT_SAMPLE_RATE,
            len(pcm),
            "（已达上限截断）" if self._max_reached.is_set() else "",
        )
        return pcm

    def close(self) -> None:
        """释放流实例（应用退出序列）。"""
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._recording = False

    # ------------------------------------------------------------------ 回调

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """sounddevice 回调线程入口。🔴 红线：零业务调用，仅入队/计数/置标志。"""
        if status:
            logger.warning("录音回调状态异常：{}", status)
        self._queue.put(indata.copy().tobytes())
        self._samples_seen += frames
        if self._samples_seen >= self._max_samples and not self._max_reached.is_set():
            self._max_reached.set()
            if self._on_max_reached is not None:
                self._on_max_reached()  # 装配层注册 Qt Signal.emit（线程安全）

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
