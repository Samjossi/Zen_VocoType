"""``audio_chunk`` 处理器（协议 v1.1 §3.6，长音频流式通道）。

校验链：未就绪（2001）→ chunk 字段（缺 1004 / 结构非法 4003）→ 阶段分支：

- **begin**：体必须空 + 音频格式（4001）→ 会话状态（4003）→ 预告超上限（4004）
  → 返回 ``{"session_id", "max_session_bytes", "max_session_seconds"}``
- **data**：体长一致性/采样整除（4001）→ 会话状态（4003）→ 累计超上限（4004）
  → 返回 ``{"received_bytes"}``（累计进度）
- **end**：体必须空（4001）→ 切换中（2002，🔴 会话保留，客户端可稍后重发 end）
  → 会话状态（4003）→ worker 动态超时识别 → recognize 同款 payload

会话一经 end 受理即终结（WAV 识别完成后删除，识别成败均为会话终局）。
"""

from zen_vocotype_protocol import chunk as chunk_proto
from zen_vocotype_protocol import errors

from zen_vocotype_service import state as state_mod
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.handlers.recognize import _EXPECTED_FORMAT
from zen_vocotype_service.inference.chunk_session import (
    PCM_BYTES_PER_SECOND,
    SessionStateError,
    SessionTooLargeError,
)
from zen_vocotype_service.inference.worker import QueueFullError, TaskTimeoutError
from zen_vocotype_service.logging_setup import logger
from zen_vocotype_service.models.loader import EngineLimitError
from zen_vocotype_service.protocol_io import ProtocolError


def handle(header: dict, body: bytes, ctx: ServiceContext, owner: object = None) -> dict:
    """三阶段分发入口；``owner`` 为连接令牌（会话绑定连接，🔴 禁止跨连接续传）。"""
    if ctx.state.status != state_mod.STATUS_READY:
        raise ProtocolError(
            errors.ERR_NOT_READY, f"服务未就绪（status={ctx.state.status}）"
        )
    if ctx.worker is None:
        raise ProtocolError(errors.ERR_NOT_READY, "推理 worker 未就位")
    raw_chunk = header.get("chunk")
    if raw_chunk is None:
        raise ProtocolError(errors.ERR_MISSING_FIELD, "请求头缺少必填字段: chunk")
    try:
        parsed = chunk_proto.parse_chunk(raw_chunk)
    except chunk_proto.ChunkError as exc:
        raise ProtocolError(errors.ERR_SESSION_STATE, str(exc)) from exc
    if parsed["phase"] == chunk_proto.PHASE_BEGIN:
        return _begin(header, body, ctx, owner, parsed)
    if parsed["phase"] == chunk_proto.PHASE_DATA:
        return _data(header, body, ctx, owner, parsed)
    return _end(header, body, ctx, owner, parsed)


def _validate_audio_format(header: dict) -> None:
    """begin 阶段音频格式校验（与 recognize 同约定，选型六：服务端仅校验）。"""
    audio_format = header.get("audio_format")
    if not isinstance(audio_format, dict):
        raise ProtocolError(errors.ERR_INVALID_AUDIO, "缺少 audio_format 字段")
    for key, expected in _EXPECTED_FORMAT.items():
        if audio_format.get(key) != expected:
            raise ProtocolError(
                errors.ERR_INVALID_AUDIO,
                f"audio_format.{key}={audio_format.get(key)!r} 非法，"
                f"协议约定 {expected}",
            )


def _begin(header: dict, body: bytes, ctx: ServiceContext, owner, parsed: dict) -> dict:
    if body:
        raise ProtocolError(errors.ERR_INVALID_AUDIO, "begin 阶段不允许携带音频体")
    _validate_audio_format(header)
    try:
        session = ctx.chunk_sessions.begin(
            owner, parsed["session_id"], parsed.get("total_bytes")
        )
    except SessionStateError as exc:
        raise ProtocolError(errors.ERR_SESSION_STATE, str(exc)) from exc
    except SessionTooLargeError as exc:
        raise ProtocolError(errors.ERR_SESSION_TOO_LARGE, str(exc)) from exc
    max_bytes = ctx.settings.chunk_session_max_bytes
    return {
        "session_id": session.session_id,
        "max_session_bytes": max_bytes,
        "max_session_seconds": int(max_bytes / PCM_BYTES_PER_SECOND),
    }


def _data(header: dict, body: bytes, ctx: ServiceContext, owner, parsed: dict) -> dict:
    if header.get("audio_bytes") != len(body):
        raise ProtocolError(
            errors.ERR_INVALID_AUDIO,
            f"体长 {len(body)} 与声明 audio_bytes={header.get('audio_bytes')!r} 不符",
        )
    if len(body) % 2 != 0:
        raise ProtocolError(
            errors.ERR_INVALID_AUDIO, "音频体采样点数字节数不整除（16bit = 2 字节）"
        )
    try:
        received = ctx.chunk_sessions.append(
            owner, parsed["session_id"], parsed["seq"], body
        )
    except SessionStateError as exc:
        raise ProtocolError(errors.ERR_SESSION_STATE, str(exc)) from exc
    except SessionTooLargeError as exc:
        raise ProtocolError(errors.ERR_SESSION_TOO_LARGE, str(exc)) from exc
    return {"received_bytes": received}


def _end(header: dict, body: bytes, ctx: ServiceContext, owner, parsed: dict) -> dict:
    if body:
        raise ProtocolError(errors.ERR_INVALID_AUDIO, "end 阶段不允许携带音频体")
    if ctx.worker.switching:
        # 🔴 会话保留：切换完成前不重取会话，客户端可稍后重发 end
        raise ProtocolError(
            errors.ERR_BUSY, "模型切换中（model_switching），会话保留，可稍后重发 end"
        )
    try:
        session = ctx.chunk_sessions.finish(owner, parsed["session_id"])
    except SessionStateError as exc:
        raise ProtocolError(errors.ERR_SESSION_STATE, str(exc)) from exc
    try:
        return ctx.worker.submit_recognize_file(session.wav_path, session.received_bytes)
    except QueueFullError as exc:
        raise ProtocolError(errors.ERR_BUSY, str(exc)) from exc
    except TaskTimeoutError as exc:
        raise ProtocolError(
            errors.ERR_RECOGNITION_FAILED, f"推理超时（timeout）: {exc}"
        ) from exc
    except EngineLimitError as exc:
        raise ProtocolError(errors.ERR_INVALID_AUDIO, str(exc)) from exc
    except Exception as exc:
        raise ProtocolError(
            errors.ERR_RECOGNITION_FAILED, f"推理失败: {exc}"
        ) from exc
    finally:
        ctx.chunk_sessions.discard(session)
        logger.debug("audio_chunk 会话 WAV 已清理: {}", session.wav_path)
