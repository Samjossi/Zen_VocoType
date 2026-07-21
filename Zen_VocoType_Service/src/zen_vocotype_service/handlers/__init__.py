"""action 处理器包：每 action 一个处理器函数，经 ``dispatch`` 路由。

处理器签名::

    handler(header: dict, body: bytes, ctx: ServiceContext) -> dict  # 返回 payload

- 业务错误抛 ``ProtocolError(code, message)``，分发层转为协议错误响应
- 🔴 任何未预期异常也会被分发层捕获为 4002/500 系错误响应，禁止连接悬挂
"""

from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.handlers import health, model_info, model_switch, ready, recognize

#: action → 处理器路由表（audio_chunk 故意缺席：分发层对未实现 action 返回 1005）
HANDLERS = {
    "health": health.handle,
    "ready": ready.handle,
    "recognize": recognize.handle,
    "model_info": model_info.handle,
    "model_switch": model_switch.handle,
}

__all__ = ["HANDLERS", "ServiceContext"]
