"""录音与识别文本落盘（T34，纯逻辑模块，零 Qt 依赖）。

数据流（装配见 ``app.py``）：

1. 录音停止 → ``save_wav(pcm)`` → ``<目录>/YYYYMMDD_HHMMSS.wav``
   （同秒冲突追加 ``_2``/``_3`` 序号兜底）
2. 识别完成 → ``save_txt(wav_path, text)`` → 同基名 ``.txt`` 同目录存放
   （对齐参考实现 GridChat 语义：识别产物是文本，wav/txt 一一对应）

设计约束：

- 音频参数由装配层注入（契约库 ``paths`` 冻结常量为唯一出处），
  本模块不 import 契约库——保持纯逻辑、测试零依赖
- 失败一律抛 ``OSError``，由调用方转通知告警；🔴 禁止静默吞错
- 空 PCM（极短录音）仍如实落盘，不做时长门限（GridChat 同语义）
"""

from __future__ import annotations

import wave
from datetime import datetime
from pathlib import Path

#: 录音文件名时间戳格式（与参考实现 GridChat 一致）
_FILENAME_TS_FORMAT = "%Y%m%d_%H%M%S"


class RecordingStore:
    """录音 WAV + 识别文本 TXT 的落盘器（目录可运行期切换）。"""

    def __init__(
        self,
        directory: Path,
        sample_rate: int,
        sample_width: int,
        channels: int,
    ) -> None:
        self._directory = Path(directory)
        self._sample_rate = sample_rate
        self._sample_width = sample_width
        self._channels = channels

    @property
    def directory(self) -> Path:
        return self._directory

    def set_directory(self, directory: Path) -> None:
        """切换保存目录（托盘「选择保存路径…」生效后调用）。"""
        self._directory = Path(directory)

    def save_wav(self, pcm: bytes) -> Path:
        """写 ``YYYYMMDD_HHMMSS.wav``（同秒冲突追加 ``_2``/``_3``），返回路径。

        目录不存在则创建；失败抛 ``OSError`` 由调用方转通知。
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        base = datetime.now().strftime(_FILENAME_TS_FORMAT)
        path = self._directory / f"{base}.wav"
        seq = 2
        while path.exists():
            path = self._directory / f"{base}_{seq}.wav"
            seq += 1
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(self._sample_width)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(pcm)
        return path

    def save_txt(self, wav_path: Path, text: str) -> Path:
        """与 wav 同基名同目录写 ``.txt``（utf-8），返回路径。

        :param wav_path: ``save_wav`` 的返回路径（仅取其基名与目录）
        """
        txt_path = wav_path.with_suffix(".txt")
        txt_path.write_text(text, encoding="utf-8")
        return txt_path
