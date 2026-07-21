"""``model_switch`` 处理器（协议 §3.5，选型三：原子切换）。

错误码映射：目标不在注册表 3001；加载失败 3002（旧模型不受影响）；
自检失败回滚 3003。切换成功后应以 ``model_info`` 交叉验证。
"""

from zen_vocotype_service import state as state_mod
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.inference.worker import QueueFullError, TaskTimeoutError
from zen_vocotype_service.models.manager import ModelSwitchError
from zen_vocotype_service.models.registry import ModelNotRegisteredError
from zen_vocotype_service.protocol_io import ProtocolError

from zen_vocotype_protocol import errors


def handle(header: dict, body: bytes, ctx: ServiceContext) -> dict:
    if ctx.state.status != state_mod.STATUS_READY:
        raise ProtocolError(
            errors.ERR_NOT_READY, f"服务未就绪（status={ctx.state.status}）"
        )
    if ctx.worker is None:
        raise ProtocolError(errors.ERR_NOT_READY, "推理 worker 未就位")
    payload = header.get("payload")
    model_name = payload.get("model_name") if isinstance(payload, dict) else None
    if not model_name:
        raise ProtocolError(
            errors.ERR_MISSING_FIELD, "model_switch 缺少 payload.model_name"
        )
    try:
        ctx.worker.submit_switch(model_name)
    except ModelNotRegisteredError as exc:
        raise ProtocolError(errors.ERR_MODEL_NOT_FOUND, str(exc)) from exc
    except ModelSwitchError as exc:
        message = str(exc)
        if message.startswith("SELFTEST_FAILED:"):
            raise ProtocolError(
                errors.ERR_MODEL_SWITCH_FAILED,
                f"切换后自检不通过，已回滚: {message.removeprefix('SELFTEST_FAILED: ')}",
            ) from exc
        raise ProtocolError(
            errors.ERR_MODEL_LOAD_FAILED,
            message.removeprefix("LOAD_FAILED: "),
        ) from exc
    except QueueFullError as exc:
        raise ProtocolError(errors.ERR_BUSY, str(exc)) from exc
    except TaskTimeoutError as exc:
        raise ProtocolError(
            errors.ERR_MODEL_LOAD_FAILED, f"模型切换超时: {exc}"
        ) from exc
    return {"current_model": model_name}
