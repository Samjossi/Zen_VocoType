"""``audio_chunk`` 会话管理（协议 v1.1 §3.6）。

- 会话表：``session_id → ChunkSession``，会话绑定连接（``owner`` 为
  ``ConnectionHandler`` 实例），单连接单活跃会话，🔴 禁止跨连接续传
- PCM 落盘：begin 创建会话 WAV（默认 XDG data，🔴 禁 tmpfs 与全量内存驻留），
  data 帧到达即 append，end 关闭修头，销毁删文件
- 空闲清理：惰性（收到该会话下一帧时报 ``SessionStateError`` → 4003）
  + 周期兜底（连接线程 recv 空转时 ``sweep_idle``）
- 错误语义单一出处：``SessionStateError`` → 4003、``SessionTooLargeError`` → 4004，
  错误码映射在 ``handlers/audio_chunk.py`` 完成
"""

import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from zen_vocotype_protocol.paths import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_WIDTH,
)

from zen_vocotype_service.config import Settings
from zen_vocotype_service.logging_setup import logger

#: PCM 字节/秒（16kHz × 16bit 单声道）：时长与上限换算唯一出处
PCM_BYTES_PER_SECOND: int = DEFAULT_SAMPLE_RATE * DEFAULT_SAMPLE_WIDTH


class SessionStateError(Exception):
    """会话状态非法（→ 4003）：不存在/已销毁/begin 重复/seq 乱序跳号重复。"""


class SessionTooLargeError(Exception):
    """会话累计音频超服务端上限（→ 4004）。"""


@dataclass
class ChunkSession:
    """单条进行中会话：WAV 句柄 + 进度状态（end 关闭后仍持有路径供识别）。"""

    session_id: str
    owner: object
    wav_path: Path
    wav_file: wave.Wave_write
    expected_seq: int = 0
    received_bytes: int = 0
    last_active: float = field(default_factory=time.monotonic)

    @property
    def pcm_seconds(self) -> float:
        """已收音频时长（秒），超时动态化与引擎上限校验的输入。"""
        return self.received_bytes / PCM_BYTES_PER_SECOND


class ChunkSessionRegistry:
    """audio_chunk 全局会话表（线程安全；每连接仅一条活跃会话）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._sessions: dict[str, ChunkSession] = {}

    # ------------------------------------------------------------------
    # 三阶段（连接线程调用）
    # ------------------------------------------------------------------

    def begin(
        self, owner: object, session_id: str, total_bytes: int | None = None
    ) -> ChunkSession:
        """begin：创建会话与临时 WAV（16kHz/16bit/单声道头先行写入）。

        :raises SessionStateError: 本连接已有活跃会话 / session_id 冲突
        :raises SessionTooLargeError: total_bytes 预告即超上限（会话不创建）
        """
        with self._lock:
            self._sweep_idle_locked()
            for session in self._sessions.values():
                if session.owner is owner:
                    raise SessionStateError(
                        f"本连接已有进行中的会话（session_id={session.session_id}），"
                        "单连接单活跃会话"
                    )
            if session_id in self._sessions:
                raise SessionStateError(f"session_id 冲突（已被其他会话占用）: {session_id}")
            max_bytes = self._settings.chunk_session_max_bytes
            if total_bytes is not None and total_bytes > max_bytes:
                raise SessionTooLargeError(
                    f"预告总量 {total_bytes} 字节超上限 {max_bytes}（"
                    f"≈{max_bytes / PCM_BYTES_PER_SECOND / 3600:.1f} 小时），会话未创建"
                )
            session_dir = Path(self._settings.chunk_session_dir)
            session_dir.mkdir(parents=True, exist_ok=True)
            wav_path = session_dir / f"{session_id}.wav"
            wav_file = wave.open(str(wav_path), "wb")
            wav_file.setnchannels(DEFAULT_CHANNELS)
            wav_file.setsampwidth(DEFAULT_SAMPLE_WIDTH)
            wav_file.setframerate(DEFAULT_SAMPLE_RATE)
            session = ChunkSession(
                session_id=session_id, owner=owner, wav_path=wav_path, wav_file=wav_file
            )
            self._sessions[session_id] = session
            logger.info("audio_chunk 会话开始: {}（WAV 落盘 {}）", session_id, wav_path)
            return session

    def append(self, owner: object, session_id: str, seq: int, body: bytes) -> int:
        """data：append PCM 帧体，返回累计 received_bytes（进度反馈）。

        :raises SessionStateError: 会话不存在/已销毁/seq 乱序跳号重复
        :raises SessionTooLargeError: 累计超上限（会话已销毁）
        """
        with self._lock:
            session = self._get_locked(owner, session_id)
            if seq != session.expected_seq:
                raise SessionStateError(
                    f"seq 乱序/跳号/重复: 期望 {session.expected_seq}，收到 {seq}"
                )
            new_total = session.received_bytes + len(body)
            max_bytes = self._settings.chunk_session_max_bytes
            if new_total > max_bytes:
                self._destroy_locked(session)
                raise SessionTooLargeError(
                    f"会话累计 {new_total} 字节超上限 {max_bytes}，会话已销毁"
                )
            session.wav_file.writeframes(body)
            session.received_bytes = new_total
            session.expected_seq += 1
            session.last_active = time.monotonic()
            return new_total

    def finish(self, owner: object, session_id: str) -> ChunkSession:
        """end：关闭 WAV（修正头长度），会话从表中移除，返回会话供识别。

        会话一经受理即终结：识别结果（成/败）为该会话终局，WAV 文件由
        调用方（handler）在识别完成后删除。

        :raises SessionStateError: 会话不存在/已销毁
        """
        with self._lock:
            session = self._get_locked(owner, session_id)
            del self._sessions[session_id]
        session.wav_file.close()
        logger.info(
            "audio_chunk 会话进入识别: {}（{:.1f} 秒音频）",
            session_id,
            session.pcm_seconds,
        )
        return session

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def discard(self, session: ChunkSession) -> None:
        """删除已 finish 会话的 WAV 文件（识别完成后调用）。"""
        session.wav_path.unlink(missing_ok=True)

    def destroy_for_connection(self, owner: object) -> None:
        """连接断开钩子：销毁该连接全部活跃会话（🔴 禁止断连后会话残留）。"""
        with self._lock:
            for session in list(self._sessions.values()):
                if session.owner is owner:
                    logger.info("连接断开，销毁 audio_chunk 会话: {}", session.session_id)
                    self._destroy_locked(session)

    def destroy_all(self) -> None:
        """进程退出全局清理（S4，T42）：销毁全部活跃会话。

        断连钩子只覆盖「客户端断开」场景；进程直接退出（SIGTERM/logind 关机）
        时活跃会话 WAV 会残留。与断连钩子/空闲清理幂等共存——重复清理同一
        会话不得报错。
        """
        with self._lock:
            for session in list(self._sessions.values()):
                logger.info(
                    "进程退出全局清理，销毁 audio_chunk 会话: {}", session.session_id
                )
                self._destroy_locked(session)

    def sweep_idle(self) -> None:
        """周期兜底清理（连接线程 recv 空转驱动）。"""
        with self._lock:
            self._sweep_idle_locked()

    @property
    def active_count(self) -> int:
        """活跃会话数（测试观察口）。"""
        with self._lock:
            return len(self._sessions)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get_locked(self, owner: object, session_id: str) -> ChunkSession:
        """取会话并校验归属与活性（惰性空闲清理在此生效）。"""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionStateError(f"会话不存在或已销毁: {session_id}")
        if session.owner is not owner:
            raise SessionStateError(f"会话不属于本连接（🔴 禁止跨连接续传）: {session_id}")
        idle = time.monotonic() - session.last_active
        if idle > self._settings.chunk_session_idle_timeout_s:
            self._destroy_locked(session)
            raise SessionStateError(
                f"会话空闲 {idle:.0f}s 超阈值已销毁: {session_id}"
            )
        return session

    def _sweep_idle_locked(self) -> None:
        now = time.monotonic()
        timeout = self._settings.chunk_session_idle_timeout_s
        for session in list(self._sessions.values()):
            if now - session.last_active > timeout:
                logger.info("audio_chunk 会话空闲超时，周期兜底销毁: {}", session.session_id)
                self._destroy_locked(session)

    def _destroy_locked(self, session: ChunkSession) -> None:
        """销毁会话：移出表 + 关句柄 + 删文件（幂等）。"""
        self._sessions.pop(session.session_id, None)
        try:
            session.wav_file.close()
        except Exception:  # 清理路径失败不遮蔽主流程，仅记日志
            logger.warning("关闭会话 WAV 失败: {}", session.wav_path)
        session.wav_path.unlink(missing_ok=True)
