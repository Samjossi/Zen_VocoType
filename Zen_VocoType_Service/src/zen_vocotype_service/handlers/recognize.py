"""``recognize`` 处理器（协议 v1.1 §3.3）。

校验链：未就绪（2001）→ 切换中（2002，message 注明 model_switching）
→ 音频格式/体长校验（4001）→ 入队推理；队列满 2002、超时/推理失败 4002、
引擎能力上限 4001（message 注明 engine_limit）。
响应 payload 含 ``text``/``confidence``/``duration_ms``，引擎支持时
纯追加 ``segments``/``language``（worker 统一构建，本处理器零额外分支）。
"""

from zen_vocotype_service import state as state_mod
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.inference.worker import QueueFullError, TaskTimeoutError
from zen_vocotype_service.models.loader import EngineLimitError
from zen_vocotype_service.protocol_io import ProtocolError

from zen_vocotype_protocol import errors
from zen_vocotype_protocol.paths import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_WIDTH,
)

_EXPECTED_FORMAT = {
    "sample_rate": DEFAULT_SAMPLE_RATE,
    "channels": DEFAULT_CHANNELS,
    "sample_width": DEFAULT_SAMPLE_WIDTH,
}


def _validate_audio(header: dict, body: bytes) -> None:
    """音频格式与体长一致性校验（选型六：客户端定格式，服务端仅校验）。"""
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
    if header.get("audio_bytes") != len(body):
        raise ProtocolError(
            errors.ERR_INVALID_AUDIO,
            f"体长 {len(body)} 与声明 audio_bytes={header.get('audio_bytes')!r} 不符",
        )
    if not body or len(body) % DEFAULT_SAMPLE_WIDTH != 0:
        raise ProtocolError(
            errors.ERR_INVALID_AUDIO, "音频体为空或采样点数字节数不整除"
        )


def handle(header: dict, body: bytes, ctx: ServiceContext) -> dict:
    if ctx.state.status != state_mod.STATUS_READY:
        raise ProtocolError(
            errors.ERR_NOT_READY, f"服务未就绪（status={ctx.state.status}）"
        )
    if ctx.worker is None:
        raise ProtocolError(errors.ERR_NOT_READY, "推理 worker 未就位")
    if ctx.worker.switching:
        raise ProtocolError(
            errors.ERR_BUSY, "模型切换中（model_switching），拒绝并发识别"
        )
    _validate_audio(header, body)
    try:
        return ctx.worker.submit_recognize(body)
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
