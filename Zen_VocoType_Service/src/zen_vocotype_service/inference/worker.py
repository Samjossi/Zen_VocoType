"""专用推理队列 + 单 worker 线程（选型四）。

- 请求入队即带超时预算：v1.4 起按音频时长动态计算
  （``timeout = max(infer_timeout_s 基础值, 音频秒 × RTF保守值 × 安全系数)``，
  静态标定必然随音频时长失准；切换任务仍用基础值——兼作模型下载预算）
- 队列积压超阈值（``Settings.queue_max_pending``）直接拒绝（2002）
- worker 单线程串行执行推理与模型切换，二者天然互斥（选型三的切换锁免费获得）

错误码映射定稿（T1.5 红线，🔴 禁止擅自新增码）：

- 推理超时 → 4002（message 注明 ``timeout``）
- 队列满 / 切换中收到 recognize → 2002
- 目标不在注册表 → 3001；加载失败 → 3002；自检失败回滚 → 3003
- 引擎能力上限（EngineLimitError）→ 4001（message 注明 ``engine_limit``）
"""

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from zen_vocotype_protocol.paths import DEFAULT_SAMPLE_RATE, DEFAULT_SAMPLE_WIDTH

from zen_vocotype_service.config import DEFAULT_RTF_ESTIMATE, Settings
from zen_vocotype_service.logging_setup import logger
from zen_vocotype_service.models.loader import (
    pcm_to_float_array,
    run_inference,
    run_inference_file,
)
from zen_vocotype_service.models.manager import ModelManager

#: worker 从队列取任务的阻塞超时（秒）：周期性检查停止标志
_DEQUEUE_TIMEOUT_S: float = 0.2


class QueueFullError(Exception):
    """队列积压超阈值。"""


class TaskTimeoutError(Exception):
    """任务在超时预算内未完成。"""


@dataclass
class _Task:
    """入队任务：kind 为 recognize / recognize_file / switch；result 回传 (ok, value)。

    ``timeout_s`` 为 per-task 超时预算（v1.4 动态化）：提交方按音频时长算好，
    ``_submit`` 等待与 GGUF 子进程超时均使用本值。
    """

    kind: str
    arg: Any
    timeout_s: float
    done: threading.Event = field(default_factory=threading.Event)
    ok: bool = False
    value: Any = None


class InferenceWorker:
    """单 worker 推理队列：识别与模型切换的统一串行点。"""

    def __init__(
        self,
        settings: Settings,
        model_manager: ModelManager,
        on_model_switched: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._manager = model_manager
        self._on_model_switched = on_model_switched
        self._queue: queue.Queue[_Task] = queue.Queue()
        self._stop_event = threading.Event()
        #: 切换进行中标记（提交即置位，worker 执行完清除）：
        #: 切换期间 recognize 直接拒绝 2002（选型三，🔴 禁止排队静默等待）
        self._switching = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="inference-worker", daemon=True
        )

    @property
    def switching(self) -> bool:
        return self._switching.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)

    # ------------------------------------------------------------------
    # 入队接口（连接线程调用）
    # ------------------------------------------------------------------

    def _submit(self, task: _Task) -> Any:
        """入队并等待结果；满队列/超时显式报错。"""
        if self._queue.qsize() >= self._settings.queue_max_pending:
            raise QueueFullError(
                f"推理队列积压 {self._queue.qsize()} 超阈值 "
                f"{self._settings.queue_max_pending}"
            )
        self._queue.put(task)
        if not task.done.wait(timeout=task.timeout_s):
            raise TaskTimeoutError(
                f"任务 {task.kind} 超时（预算 {task.timeout_s:.0f}s）"
            )
        if not task.ok:
            raise task.value
        return task.value

    def _calc_recognize_timeout(self, pcm_bytes: int) -> float:
        """按音频时长动态计算识别超时（公式单一出处）。

        ``timeout = max(infer_timeout_s 基础值, 音频秒 × RTF保守值 × 安全系数)``；
        RTF 取当前模型条目 ``rtf_estimate``，未标定取保守缺省。
        """
        seconds = pcm_bytes / (DEFAULT_SAMPLE_RATE * DEFAULT_SAMPLE_WIDTH)
        rtf = DEFAULT_RTF_ESTIMATE
        current = self._manager.current
        if current is not None and current.entry.rtf_estimate is not None:
            rtf = current.entry.rtf_estimate
        return max(
            self._settings.infer_timeout_s,
            seconds * rtf * self._settings.rtf_safety_factor,
        )

    def submit_recognize(self, pcm: bytes) -> dict:
        """入队识别请求，返回 ``{"text", "confidence", "duration_ms"}``
        （引擎支持时追加 ``segments``/``language``）。"""
        return self._submit(
            _Task(
                kind="recognize",
                arg=pcm,
                timeout_s=self._calc_recognize_timeout(len(pcm)),
            )
        )

    def submit_recognize_file(self, wav_path: Path, pcm_bytes: int) -> dict:
        """入队会话 WAV 识别（audio_chunk end 路径），返回结构同 ``submit_recognize``。

        :param wav_path: 会话 WAV 路径（funasr-gguf 直喂 CLI；其他引擎读回转 float32）
        :param pcm_bytes: 会话累计 PCM 字节数（时长/超时计算依据）
        """
        return self._submit(
            _Task(
                kind="recognize_file",
                arg=(wav_path, pcm_bytes),
                timeout_s=self._calc_recognize_timeout(pcm_bytes),
            )
        )

    def submit_switch(self, model_name: str) -> None:
        """入队模型切换（与推理天然互斥）；提交即置切换标记。"""
        self._switching.set()
        try:
            # 切换任务用基础值预算：兼作首次切换未缓存大模型的下载时间覆盖
            return self._submit(
                _Task(
                    kind="switch",
                    arg=model_name,
                    timeout_s=self._settings.infer_timeout_s,
                )
            )
        except Exception:
            # 入队失败（满队列/超时）：任务未执行，清除标记
            self._switching.clear()
            raise

    # ------------------------------------------------------------------
    # worker 主循环（单线程，模型操作唯一执行点）
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=_DEQUEUE_TIMEOUT_S)
            except queue.Empty:
                continue
            try:
                if task.kind == "recognize":
                    task.value = self._do_recognize(task.arg, task.timeout_s)
                elif task.kind == "recognize_file":
                    task.value = self._do_recognize_file(task.arg, task.timeout_s)
                elif task.kind == "switch":
                    task.value = self._do_switch(task.arg)
                else:
                    raise ValueError(f"未知任务类型: {task.kind!r}")
                task.ok = True
            except Exception as exc:  # 结果经 task 回传，由提交方映射错误码
                task.ok = False
                task.value = exc
            finally:
                task.done.set()

    @staticmethod
    def _build_payload(outcome: dict, duration_ms: int) -> dict:
        """识别结果 → 协议 payload（segments/language 纯追加，给不出即省略）。"""
        payload = {
            "text": outcome["text"],
            "confidence": outcome["confidence"],
            "duration_ms": duration_ms,
        }
        if outcome.get("segments"):
            payload["segments"] = outcome["segments"]
        if outcome.get("language"):
            payload["language"] = outcome["language"]
        return payload

    def _do_recognize(self, pcm: bytes, timeout_s: float) -> dict:
        current = self._manager.current
        if current is None:
            raise RuntimeError("模型未加载")
        audio = pcm_to_float_array(pcm)
        duration_ms = len(pcm) // 2 * 1000 // 16000
        outcome = run_inference(current, audio, timeout_s=timeout_s)
        return self._build_payload(outcome, duration_ms)

    def _do_recognize_file(self, arg: tuple, timeout_s: float) -> dict:
        """audio_chunk end：会话 WAV 识别（GGUF 直喂，其他引擎读回转 float32）。"""
        wav_path, pcm_bytes = arg
        current = self._manager.current
        if current is None:
            raise RuntimeError("模型未加载")
        duration_ms = pcm_bytes // 2 * 1000 // 16000
        outcome = run_inference_file(current, wav_path, timeout_s=timeout_s)
        return self._build_payload(outcome, duration_ms)

    def _do_switch(self, model_name: str) -> None:
        try:
            self._manager.switch(model_name)
        finally:
            self._switching.clear()
        if self._on_model_switched is not None:
            self._on_model_switched(model_name)
