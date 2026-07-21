"""``ready`` 处理器：协议级就绪确认（协议 §3.2）。

- 加载中：``ok: true`` 且 ``ready: false``（正常状态，Launcher 继续轮询）
- 就绪：``ok: true`` 且 ``ready: true`` + 当前模型
- 加载失败：``ok: false`` + 3002 及真实原因
"""

from zen_vocotype_service import state as state_mod
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.protocol_io import ProtocolError

from zen_vocotype_protocol import errors


def handle(header: dict, body: bytes, ctx: ServiceContext) -> dict:
    status = ctx.state.status
    if status == state_mod.STATUS_ERROR:
        raise ProtocolError(
            errors.ERR_MODEL_LOAD_FAILED,
            f"模型加载失败: {ctx.state.error_detail}",
        )
    if status == state_mod.STATUS_READY:
        return {"ready": True, "current_model": ctx.state.current_model}
    return {"ready": False}
